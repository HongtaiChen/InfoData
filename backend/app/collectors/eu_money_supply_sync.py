#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""欧元区货币供应量采集器（eu_money_supply_monthly）—— DBnomics ECB/BSI

蓝图的哪一块：**货币流动性 · 欧元区数量维度**（补源方案 v2.0 §7 批次 B）。
欧元区是全球第二大货币区，「欧元区 M2/M3 增速 vs 美国」是判断全球流动性相对松紧的
直接读数 —— 此前本库只有中国与美国两层，欧元区完全空白。

★ 为什么必须走 DBnomics（2026-09-25 实测，勿改回直连）：

  ECB 的三个直连域**全部阻断**：
    · `data-api.ecb.europa.eu`      SSL handshake timeout
    · `data.ecb.europa.eu`          SSL handshake timeout
    · `sdw-wsrest.ecb.europa.eu`    DNS 失败
  而 DBnomics 侧的 `ECB/BSI` 完全可用，实测取到 M3 = **17,613,983** 百万欧元 @2026-07。
  ⇒ 这是**唯一免费通道**，因此本采集器必须做「源失效即告警」（见下）。

⚠️ 源单一风险（本文件最重要的一条）：
  本表数据**只有 DBnomics 一条通道**，没有第二口径可交叉校验。一旦 DBnomics 下架
  ECB 供应商（**同类前科**：DBnomics 的 `FRED` 供应商已被移除，报
  `Could not find storage directory for provider 'FRED'`），本表会静默停更。
  ⇒ 故 `run()` 在「三条序列全失败」时 raise（让任务记 failed 而非 success）；
    单条失败则记 error 并继续（其余两条照写）。
  ⇒ 另配 DQ freshness 规则守「超过 N 天没有新月度数据」。

幂等：PRIMARY KEY(stat_month) + 全量 upsert（源是月度序列、每国 559 期，量小；
      用 COALESCE 保留已有非空值，避免某次源侧回吐 NULL 把好数据覆盖掉）。
"""
import logging
from datetime import datetime

import pymysql

from ..db import get_db_config
from ._common import with_steps
from ._dbnomics import SeriesData, fetch_series, to_map, yoy_pct

logger = logging.getLogger(__name__)

RUN_STEPS = [
    {"no": 1, "name": "拉欧元区 M1/M2/M3（ECB/BSI）",
     "params": "DBnomics ECB/BSI：M.U2.Y.V.M10/M20/M30.X.1.U2.2300.Z01.E（工作日与季调，1980-01 起 559 期）"},
    {"no": 2, "name": "算同比",
     "params": "按「日期 − 365 天 ±15 天容差」匹配去年同月（源侧各序列月末对齐不一致，严格匹配会全空）"},
    {"no": 3, "name": "全量 Upsert",
     "params": "PRIMARY KEY(stat_month)；COALESCE 保留已有非空值；哨兵 -999999 已在解析层剔除"},
]

# ECB/BSI 序列代码（实测 2026-07：M3=17,613,983 / M2=16,436,438 / M1=11,292,842，单位百万欧元）
SERIES: dict[str, str] = {
    "m3": "M.U2.Y.V.M30.X.1.U2.2300.Z01.E",
    "m2": "M.U2.Y.V.M20.X.1.U2.2300.Z01.E",
    "m1": "M.U2.Y.V.M10.X.1.U2.2300.Z01.E",
}
_TGT = ["stat_month", "m1", "m2", "m3", "m1_yoy", "m2_yoy", "m3_yoy"]


def _round(v, nd=2):
    return None if v is None else round(v, nd)


class EuMoneySupplySyncCollector:
    """欧元区 M1/M2/M3 月度同步（DBnomics ECB/BSI，唯一免费通道）"""

    def __init__(self, timeout_sec: float = 120):
        self.timeout_sec = float(timeout_sec)

    def run(self) -> dict:
        errors: list[str] = []
        got: dict[str, SeriesData] = {}
        for col, code in SERIES.items():
            sd = fetch_series("ECB", "BSI", code, timeout=self.timeout_sec)
            if sd.error:
                errors.append(f"{col} 取数失败：{sd.error}")
            elif not sd.points:
                errors.append(f"{col} 无观测值")
            else:
                got[col] = sd

        # 源单一 ⇒ 全失败必须 raise（不能记 success 让表静默停更）
        if not got:
            raise RuntimeError(f"ECB/BSI 三条序列全部取数失败：{errors[:3]}")

        merged: dict = {}
        for col, sd in got.items():
            for d, v in to_map(sd).items():
                merged.setdefault(d, {})[col] = v

        # 同比（对每条序列单独算，容差 15 天）
        yoy: dict[str, dict] = {}
        for col, sd in got.items():
            yoy[col] = yoy_pct(sd.points)
        for d, slot in merged.items():
            for col in got:
                y = yoy[col].get(d)
                if y is not None:
                    slot[f"{col}_yoy"] = y

        rows = [(d, *(m.get(k) for k in _TGT[1:])) for d, m in sorted(merged.items())]

        conn = pymysql.connect(**get_db_config().to_dict(), cursorclass=pymysql.cursors.DictCursor)
        try:
            with conn.cursor() as cur:
                sql = (
                    f"INSERT INTO eu_money_supply_monthly ({', '.join(_TGT)}, update_time, data_source) "
                    f"VALUES ({', '.join(['%s'] * len(_TGT))}, NOW(), %s) "
                    "ON DUPLICATE KEY UPDATE "
                    + ", ".join(f"{c}=COALESCE(VALUES({c}), {c})" for c in _TGT if c != "stat_month")
                    + ", update_time=NOW()"
                )
                if rows:
                    cur.executemany(sql, [(*r, "DBNOMICS_ECB_BSI") for r in rows])
                cur.execute(
                    "SELECT COUNT(*) AS n, MIN(stat_month) AS d0, MAX(stat_month) AS d1, "
                    "SUM(m3 IS NOT NULL) AS n_m3, MAX(CASE WHEN m3 IS NOT NULL THEN stat_month END) AS d_m3, "
                    "SUM(m3_yoy IS NOT NULL) AS n_yoy FROM eu_money_supply_monthly")
                st = cur.fetchone()
            conn.commit()
        finally:
            conn.close()

        sent = sum(sd.sentinel_count for sd in got.values())
        if sent:
            errors.append(f"源含 {sent} 个 -999999 缺失哨兵，已在解析层剔除（未入库）")
        msg = (f"欧元区货币总量写 {len(rows)} 行｜表内 {st['n']} 行（{st['d0']} ~ {st['d1']}）"
               f"｜M3 有值 {st['n_m3']} 行、最新 {st['d_m3']}｜同比 {st['n_yoy']} 行"
               f"｜源 = DBnomics ECB/BSI（ECB 直连三域全断，此为唯一免费通道）")
        logger.info("✅ %s", msg)
        return with_steps(
            {"records_written": len(rows), "error_count": len(errors), "errors": errors[:20], "note": msg},
            RUN_STEPS,
            {
                1: " · ".join(f"{c}={sd.d1} 末值{sd.points[-1][1]}" for c, sd in got.items()),
                2: f"三条序列各算同比，共 {st['n_yoy']} 行",
                3: f"upsert {len(rows)} 行（表内 {st['n']} 行，最新 {st['d1']}）",
            },
        )
