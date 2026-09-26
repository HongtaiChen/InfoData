#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""美国货币供应量采集器（us_money_supply_monthly）—— DBnomics 主口径 + 官网 H.6 交叉校验

蓝图的哪一块：**货币流动性 · 美国数量维度 M1/M2**（补源方案 v2.0 §7 批次 A）。
v1.0 原计划靠 FRED 拿这一块，而 FRED Key 申请不到 ⇒ 实测改走 DBnomics `FED/H6_H6_M2`
（免 Key，1959-01 起 812 期），实测 M2 = **23,342.8** / M1 = **19,991.1** 十亿美元 @2026-08。

★ 双轨制（本采集器的核心设计）：
  · 主口径 = DBnomics（历史完整，1959 起 812 期）
  · 校验口径 = 美联储官网 H.6 发布页 HTML 解析（**仅最新一期**，写 `official_m2` 列）
  ⇒ 两通道都可以失效而不互相影响；DQ 用「最新行 |official_m2 − m2| 」守跨通道偏差。

⚠️ 三条口径纪律（实测得出，勿回退）：

  1. **单位是十亿美元**（billion USD），不是亿美元、不是亿人民币。M2 = 23,342.8 即 23.3 万亿。
  2. **M1 有口径断点**：2020-05 起美国 M1 口径改革（纳入储蓄存款），M1 从约 5 万亿跳到约 18 万亿。
     ⇒ 序列在 2020 前后**不可比绝对值**，一律看同比；`m1_yoy` 在 2021 年会显示异常高增速，
        那是口径而非货币扩张，UI 需带说明。
  3. **恒等式可做 DQ**：准备金余额 + 流通中货币 = 基础货币（实测 2,936.0 + 2,475.6 = 5,411.6 ✅）。

幂等：PRIMARY KEY(stat_month，取源月末日所在月的 1 日) + 全量 upsert。
"""
import html
import logging
import re
from datetime import date

import pymysql

from ..db import get_db_config
from ._common import with_steps
from ._dbnomics import fetch_series, to_map, yoy_pct
from ._http import make_session

logger = logging.getLogger(__name__)

RUN_STEPS = [
    {"no": 1, "name": "拉美国 M1/M2（FED/H6_H6_M2）",
     "params": "DBnomics FED/H6_H6_M2：M2.M / M2_N.M / M1.M / M1_N.M（月度，1959-01 起 812 期，季调与未季调各取）"},
    {"no": 2, "name": "拉基础货币三项（FED/H6_H6_MBASE）",
     "params": "RESMO14A_N.M=基础货币 / RESMOB14A_N.M=准备金余额 / RESMOC14A_N.M=流通中货币（均未季调，十亿美元）"},
    {"no": 3, "name": "算同比 + 恒等式自检",
     "params": "同比按「日期 − 365 天 ±15 天」容差匹配；自检 准备金 + 流通中货币 = 基础货币"},
    {"no": 4, "name": "官网 H.6 交叉校验",
     "params": "解析 federalreserve.gov/releases/h6/current/h6.htm 的 M2 → official_m2（仅最新一期）"},
    {"no": 5, "name": "全量 Upsert",
     "params": "PRIMARY KEY(stat_month=源月末日所在月 1 日)；COALESCE 保留已有非空值"},
]

_M2_DS = "H6_H6_M2"
_MB_DS = "H6_H6_MBASE"
SERIES: dict[str, tuple[str, str]] = {
    # 目标列 → (dataset, series_code)
    "m2": (_M2_DS, "M2.M"),
    "m2_nsa": (_M2_DS, "M2_N.M"),
    "m1": (_M2_DS, "M1.M"),
    "m1_nsa": (_M2_DS, "M1_N.M"),
    "monetary_base": (_MB_DS, "RESMO14A_N.M"),
    "reserve_balances": (_MB_DS, "RESMOB14A_N.M"),
    "currency_in_circ": (_MB_DS, "RESMOC14A_N.M"),
}
_TGT = ["stat_month", "m1", "m2", "m1_nsa", "m2_nsa", "monetary_base",
        "reserve_balances", "currency_in_circ", "m1_yoy", "m2_yoy", "base_yoy",
        "official_m2", "update_source"]

_H6_URL = "https://www.federalreserve.gov/releases/h6/current/default.htm"
_MONTH_ROW = re.compile(r"^[A-Z][a-z]{2}\.?\s+\d{4}")


def _fetch_official_m2(ref: float | None = None, timeout: float = 30):
    """解析美联储官网 H.6 当期页 → (M2(SA) 十亿美元, 说明)。失败返回 (None, 原因)，不抛。

    ⚠️⚠️ **当期页 URL 必须是 `current/default.htm`**（2026-09-25 实测踩坑，勿改回）：
      · `current/h6.htm`  → 实测**停在 2013 年**（数据行首格 `Jul 15, 2013`），是陈旧归档页；
      · `current/default.htm` → 当期有效（实测含 `Aug. 2026` 行，M2(SA) = 23,342.8）。
      用错 URL 不会报错、只会静默解析出 2013 年的值 ⇒ 故必须配合 ref 新鲜度断言兜底。

    ⚠️ 单元格含**未解码 HTML 实体 `&#xa0;`**（不是 `&nbsp;`），必须 `html.unescape` 后再解析，
       否则 `float('&#xa0; 6,747,704')` 失败、校验恒为 None（同 H.4.1 的坑）。

    表结构（实测 `default.htm`）：第 1 张表「Money Stock Measures」的表头 3 行，
    数据行首格形如 `Aug. 2026`；列序 = [日期, M1(SA), **M2(SA)**, 流通中货币, 准备金, 基础货币,
    M1(NSA), M2(NSA), 准备金]。第 2/3 张表是 M2 的构成分解（同样以 `Date` 表头分隔）。
    ⇒ 只取**第一张表的最后一行**（最新月）的 `index 2` = M2(SA)。
    实测交叉验证：官网 23,342.8 == DBnomics `M2.M` 23,342.8（逐位一致）。

    ref：主口径最新 M2，用于新鲜度断言（陈旧页或口径变更时丢弃，避免写入错误校验值）。
    """
    try:
        doc = make_session().get(_H6_URL, timeout=timeout).text
    except Exception as e:                      # noqa: BLE001
        return None, f"{type(e).__name__}: {str(e)[:60]}"
    if not doc or len(doc) < 5000:
        return None, f"响应过短({len(doc)}B)"

    cells_re = re.compile(r"<t[hd][^>]*>(.*?)</t[hd]>", re.S)
    blocks: list[list[list[str]]] = []
    cur = None
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", doc, re.S):
        cs = []
        for c in cells_re.findall(tr):
            t = html.unescape(re.sub(r"<[^>]+>", " ", c)).replace("\xa0", " ")
            cs.append(re.sub(r"\s+", " ", t).strip())
        if not cs or not any(cs):
            continue
        if cs[0] == "Date":
            cur = []
            blocks.append(cur)
            continue
        if cur is not None and _MONTH_ROW.match(cs[0]):
            cur.append(cs)

    for blk in blocks:
        if not blk:
            continue
        last = blk[-1]
        if len(last) < 3:
            continue
        s = last[2].replace(",", "").replace("*", "").strip()
        try:
            v = float(s)
        except ValueError:
            continue
        if not (1000 < v < 100000):           # 十亿美元量级合理性
            continue
        if ref and ref > 0 and abs(v / ref - 1) > 0.05:
            return None, (f"官网 M2 {v:,.1f} 与主口径 {ref:,.1f} 差 >5%，"
                          f"判为陈旧页/口径不符，丢弃（实测 current/h6.htm 停在 2013 年）")
        return v, f"ok（源行 {last[0]}）"
    return None, "未找到 Money Stock Measures 数据行"


class UsMoneySupplySyncCollector:
    """美国 M1/M2/基础货币月度同步（DBnomics 主 + 官网 H.6 校验）"""

    def __init__(self, timeout_sec: float = 120):
        self.timeout_sec = float(timeout_sec)

    def run(self) -> dict:
        errors: list[str] = []
        got = {}
        for col, (ds, code) in SERIES.items():
            sd = fetch_series("FED", ds, code, timeout=self.timeout_sec)
            if sd.error or not sd.points:
                errors.append(f"{col} 取数失败：{sd.error or '无观测值'}")
            else:
                got[col] = sd
        if not got:
            raise RuntimeError(f"H.6 全部序列取数失败：{errors[:3]}")

        merged: dict = {}
        for col, sd in got.items():
            for d, v in to_map(sd).items():
                merged.setdefault(d.replace(day=1), {})[col] = v

        yoy = {col: yoy_pct(sd.points) for col, sd in got.items()}
        # 同比的 key 是源日期（月末日），要归一化到月初再挂
        yoy_norm = {col: {d.replace(day=1): y for d, y in m.items()} for col, m in yoy.items()}
        for d, slot in merged.items():
            for col in got:
                y = yoy_norm[col].get(d)
                if y is not None:
                    slot[f"{col}_yoy"] = y

        # 恒等式自检（只记录，不改数）：准备金 + 流通中货币 = 基础货币
        identity_bad = []
        for d, m in merged.items():
            rb, cc, mb = m.get("reserve_balances"), m.get("currency_in_circ"), m.get("monetary_base")
            if rb is not None and cc is not None and mb is not None and abs(rb + cc - mb) > 0.25:
                identity_bad.append(f"{d}({rb:.1f}+{cc:.1f}≠{mb:.1f})")
        if identity_bad:
            errors.append(f"恒等式不符 {len(identity_bad)} 期：{identity_bad[:3]}")

        # 官网校验（仅最新一期）；ref = 主口径最新 M2，用于新鲜度断言
        cand = [d for d, m in merged.items() if m.get("m2") is not None]
        ref_m2 = merged[max(cand)]["m2"] if cand else None
        off_m2, off_note = _fetch_official_m2(ref=ref_m2)
        official_row_date = max(cand) if (off_m2 is not None and cand) else None

        rows = []
        for d, m in sorted(merged.items()):
            slot = dict(m)
            slot["official_m2"] = off_m2 if (official_row_date and d == official_row_date) else None
            slot["update_source"] = "dbnomics"
            rows.append((d, *(slot.get(k) for k in _TGT[1:])))

        conn = pymysql.connect(**get_db_config().to_dict(), cursorclass=pymysql.cursors.DictCursor)
        try:
            with conn.cursor() as cur:
                sql = (
                    f"INSERT INTO us_money_supply_monthly ({', '.join(_TGT)}, update_time, data_source) "
                    f"VALUES ({', '.join(['%s'] * len(_TGT))}, NOW(), %s) "
                    "ON DUPLICATE KEY UPDATE "
                    + ", ".join(f"{c}=COALESCE(VALUES({c}), {c})" for c in _TGT if c != "stat_month")
                    + ", update_time=NOW()"
                )
                if rows:
                    cur.executemany(sql, [(*r, "DBNOMICS_FED_H6") for r in rows])
                cur.execute(
                    "SELECT COUNT(*) AS n, MIN(stat_month) AS d0, MAX(stat_month) AS d1, "
                    "SUM(m2 IS NOT NULL) AS n_m2, SUM(m2_yoy IS NOT NULL) AS n_yoy, "
                    "MAX(CASE WHEN official_m2 IS NOT NULL THEN stat_month END) AS d_off "
                    "FROM us_money_supply_monthly")
                st = cur.fetchone()
                cur.execute("SELECT stat_month, m2, official_m2 FROM us_money_supply_monthly "
                            "ORDER BY stat_month DESC LIMIT 1")
                last = cur.fetchone()
            conn.commit()
        finally:
            conn.close()

        sent = sum(sd.sentinel_count for sd in got.values())
        if sent:
            errors.append(f"源含 {sent} 个 -999999 哨兵，已剔除")
        dev = None
        if last and last["m2"] is not None and last["official_m2"] is not None:
            dev = round((last["m2"] / last["official_m2"] - 1) * 100, 4)
        msg = (f"美国货币总量写 {len(rows)} 行｜表内 {st['n']} 行（{st['d0']} ~ {st['d1']}）"
               f"｜M2 有值 {st['n_m2']} 行、最新 {last['stat_month'] if last else '-'}"
               f" = {last['m2'] if last else '-'}｜同比 {st['n_yoy']} 行"
               f"｜官网校验 official_m2={last['official_m2'] if last else None}"
               f"（跨通道偏差 {dev}%）")
        logger.info("✅ %s", msg)
        return with_steps(
            {"records_written": len(rows), "error_count": len(errors), "errors": errors[:20], "note": msg},
            RUN_STEPS,
            {
                1: " · ".join(f"{c}={sd.d1}/{(to_map(sd).get(sd.points[-1][0]) if sd.points else None)}"
                              for c, sd in got.items() if c.startswith("m")),
                2: " · ".join(f"{c}={sd.points[-1][1]:.1f}" for c, sd in got.items() if not c.startswith("m")),
                3: f"恒等式不符 {len(identity_bad)} 期；同比 {st['n_yoy']} 行",
                4: (f"官网 M2={off_m2}（偏差 {dev}%）" if off_m2 is not None
                    else f"官网校验不可用：{off_note}"),
                5: f"upsert {len(rows)} 行（表内 {st['n']} 行，最新 {st['d1']}）",
            },
        )
