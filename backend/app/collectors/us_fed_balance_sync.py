#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""美联储资产负债表采集器（us_fed_balance_weekly）—— DBnomics 主口径 + 官网 H.4.1 交叉校验

蓝图的哪一块：**货币流动性 · 美国数量维度（央行资产表）**（补源方案 v2.0 §7 批次 A）。
这是「美国放了多少水」的总闸门。v1.0 曾判定「美国数量维度 🔴 拿不到」，
实测推翻：DBnomics `FED/H41` 免 Key 直取 **1241 期（2002-12-18 起）**，
总资产实测 **6,747,704** 百万美元 @2026-09-23，且与美联储官网 HTML 解析**逐位一致**。

★ 双轨制与本文件的**一处实测修正**（重要，勿改回）：

  v1.0/v2.0 文档写「主口径 = 美联储官网 H.4.1 HTML 解析，校验 = DBnomics」。
  **实测不可行**：官网当期页（`/releases/h41/current/h41.htm`）**只含最近数周**，
  无法回填 20 年历史（逐期归档页需 1000+ 次请求，且归档 URL 不稳定）。
  ⇒ 修正为：**主口径 = DBnomics（历史完整）**，官网 = **当期交叉校验**（仅最新一期，
    写 `official_check` 列）。两轨都免 Key，任一失效可自动降级，且偏差可被 DQ 捕捉。

⚠️ 两条实测纪律：

  1. 🔴 **必须显式剥离 -999999 哨兵**。H41 的**已停报细分子项**（`_F01`~`_F12` 那批）
     含 **341/1241** 个哨兵；本表只取核心汇总序列（实测 0 哨兵），但 `_dbnomics` 层仍
     一律剔除并计数 —— 哨兵是**合法数字**，混进去会被当 −10 亿级真实余额、污染同比。

  2. **证券持仓合计无单一源序列**，由美债 + 机构债 + MBS 三项相加派生；逆回购合计同理
     （外国官方 + 其他）。派生列在入库前算好（源侧无此序列，不会与源值冲突）。

幂等：PRIMARY KEY(trade_date=周三水平日)。源为单序列端点、一次返回全史，
      故采用「**水位 − 8 周重叠**」重写策略（H.4.1 偶有历史修订，8 周重叠足以覆盖）。
"""
import html
import logging
import re
from datetime import date, timedelta

import pymysql

from ..db import get_db_config
from ._common import with_steps
from ._dbnomics import fetch_series, to_map
from ._http import make_session

logger = logging.getLogger(__name__)

RUN_STEPS = [
    {"no": 1, "name": "拉美联储资产负债表 9 条核心序列（FED/H41）",
     "params": "DBnomics FED/H41：RESPPMA_N.WW 总资产 / RESPPALGUO_N.WW 美债 / RESPPALGAM_N.WW 机构债 / "
               "RESPPALGASMO_N.WW MBS / RESPPLLRF_N.WW+RESPPLLRD_N.WW 逆回购 / RESTBC_N.WW 流通中货币 / "
               "RESH4R_N.WW 准备金 / RESPPLLDD_N.WW 其他存款（均 Wednesday level，2002-12-18 起 1241 期）"},
    {"no": 2, "name": "算派生列",
     "params": "securities_total = 美债 + 机构债 + MBS；reverse_repo_total = 外国官方 + 其他（源无合计序列）"},
    {"no": 3, "name": "哨兵过滤自检",
     "params": "统计并剔除 -999999 缺失哨兵（H41 停报子项含 341/1241，本表核心序列实测 0）"},
    {"no": 4, "name": "官网 H.4.1 交叉校验",
     "params": "解析 federalreserve.gov/releases/h41/current/h41.htm 的 Total assets → official_check（仅最新一期）"},
    {"no": 5, "name": "增量 Upsert（水位 − 8 周重叠）",
     "params": "PRIMARY KEY(trade_date)；H.4.1 偶有历史修订，故回写最近 8 周 + 新增行"},
]

# 目标列 → DBnomics 序列代码
SERIES: dict[str, str] = {
    "total_assets": "RESPPMA_N.WW",
    "treasury_securities": "RESPPALGUO_N.WW",
    "agency_debt": "RESPPALGAM_N.WW",
    "mbs": "RESPPALGASMO_N.WW",
    "reverse_repo_foreign": "RESPPLLRF_N.WW",
    "reverse_repo_other": "RESPPLLRD_N.WW",
    "currency_in_circ": "RESTBC_N.WW",
    "reserve_balances": "RESH4R_N.WW",
    "other_deposits": "RESPPLLDD_N.WW",
}
_TGT = ["trade_date", "total_assets", "securities_total", "treasury_securities", "agency_debt",
        "mbs", "reverse_repo_total", "reverse_repo_foreign", "reverse_repo_other",
        "currency_in_circ", "reserve_balances", "other_deposits", "official_check"]
_OVERLAP_WEEKS = 8
_H41_URL = "https://www.federalreserve.gov/releases/h41/current/h41.htm"


def _fetch_official_total(timeout: float = 40, ref: float | None = None):
    """解析美联储官网 H.4.1 当期页 → (Total assets 百万美元, 说明)。失败返回 (None, 原因)，不抛。

    ⚠️ **必须 `html.unescape`**（2026-09-25 实测踩坑）：官网单元格里的空白是**未解码的
       HTML 实体 `&#xa0;`**（不是 `&nbsp;`、也不是 `\\xa0` 字符）。若不先 unescape，
       单元格会变成 `'&#xa0; 6,747,704'`，`float()` 直接失败 ⇒ 校验恒为 None 且不报错。
       实测解码后 `['Total assets', '(0)', '6,747,704', ...]`，第二格即目标值。

    ref：主口径（DBnomics）最新总资产，用于**新鲜度断言** —— 官网解析值若与主口径
    相差 >30%，判为「页面陈旧/结构变更」并丢弃（避免写入错误的校验值）。
    本项目已有前科：`h6/current/h6.htm` 实测停在 **2013 年**，若不加断言会把
    2013 年的读成「官网校验通过」（见 us_money_supply_sync 的同类处理）。
    """
    try:
        doc = make_session().get(_H41_URL, timeout=timeout).text
    except Exception as e:                      # noqa: BLE001
        return None, f"{type(e).__name__}: {str(e)[:60]}"
    if not doc or len(doc) < 10000:
        return None, f"响应过短({len(doc)}B)"

    cells_re = re.compile(r"<t[hd][^>]*>(.*?)</t[hd]>", re.S)
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", doc, re.S):
        cells = []
        for c in cells_re.findall(tr):
            t = html.unescape(re.sub(r"<[^>]+>", " ", c)).replace("\xa0", " ")
            cells.append(re.sub(r"\s+", " ", t).strip())
        if not cells or not cells[0].startswith("Total assets"):
            continue
        for c in cells[1:]:
            s = c.replace(",", "").replace("*", "").replace("+", "").strip()
            if not s:
                continue
            try:
                v = float(s)
            except ValueError:
                continue
            if 1e5 < v < 2e7:                   # 百万美元量级（实测 6,747,704）
                if ref and ref > 0 and abs(v / ref - 1) > 0.30:
                    return None, f"官网值 {v:,.0f} 与主口径 {ref:,.0f} 差 >30%，判为陈旧页，丢弃"
                return v, "ok"
        return None, "Total assets 行未找到可用数值"
    return None, "未找到 Total assets 行"


class UsFedBalanceSyncCollector:
    """美联储资产负债表周度同步（DBnomics 主 + 官网 H.4.1 校验）"""

    def __init__(self, timeout_sec: float = 150):
        self.timeout_sec = float(timeout_sec)

    def run(self) -> dict:
        errors: list[str] = []
        got, sent_total = {}, 0
        for col, code in SERIES.items():
            sd = fetch_series("FED", "H41", code, timeout=self.timeout_sec)
            sent_total += sd.sentinel_count
            if sd.error or not sd.points:
                errors.append(f"{col} 取数失败：{sd.error or '无观测值'}")
            else:
                got[col] = sd
        if "total_assets" not in got:
            raise RuntimeError(f"总资产序列取数失败，无法继续：{errors[:3]}")

        merged: dict = {}
        for col, sd in got.items():
            for d, v in to_map(sd).items():
                merged.setdefault(d, {})[col] = v

        # 派生列（源无合计序列）
        for d, m in merged.items():
            t, a, b = m.get("treasury_securities"), m.get("agency_debt"), m.get("mbs")
            m["securities_total"] = round(t + a + b, 2) if None not in (t, a, b) else None
            f_, o = m.get("reverse_repo_foreign"), m.get("reverse_repo_other")
            m["reverse_repo_total"] = round(f_ + o, 2) if None not in (f_, o) else None

        # 官网校验（仅最新一期）；传主口径最新值作新鲜度断言的基准
        ref_total = (merged[max(merged)].get("total_assets") if merged else None)
        off_total, off_note = _fetch_official_total(ref=ref_total)

        conn = pymysql.connect(**get_db_config().to_dict(), cursorclass=pymysql.cursors.DictCursor)
        try:
            with conn.cursor() as cur:
                # ⚠️ 水位按列取：本表暂只有一条腿，但沿用项目规范，避免未来并表时静默跳日
                cur.execute("SELECT MAX(trade_date) AS d FROM us_fed_balance_weekly "
                            "WHERE total_assets IS NOT NULL")
                last = cur.fetchone()["d"]
                floor = (last - timedelta(weeks=_OVERLAP_WEEKS)) if last else date(1900, 1, 1)

                latest = max(merged) if merged else None
                rows = []
                for d in sorted(merged):
                    if d < floor:
                        continue
                    m = merged[d]
                    slot = dict(m)
                    slot["official_check"] = off_total if d == latest else None
                    rows.append((d, *(slot.get(k) for k in _TGT[1:])))

                sql = (
                    f"INSERT INTO us_fed_balance_weekly ({', '.join(_TGT)}, update_time, data_source) "
                    f"VALUES ({', '.join(['%s'] * len(_TGT))}, NOW(), %s) "
                    "ON DUPLICATE KEY UPDATE "
                    + ", ".join(f"{c}=COALESCE(VALUES({c}), {c})" for c in _TGT if c != "trade_date")
                    + ", update_time=NOW()"
                )
                if rows:
                    cur.executemany(sql, [(*r, "DBNOMICS_FED_H41") for r in rows])
                cur.execute(
                    "SELECT COUNT(*) AS n, MIN(trade_date) AS d0, MAX(trade_date) AS d1, "
                    "SUM(total_assets IS NOT NULL) AS n_ta, "
                    "MAX(CASE WHEN official_check IS NOT NULL THEN trade_date END) AS d_off "
                    "FROM us_fed_balance_weekly")
                st = cur.fetchone()
                cur.execute("SELECT trade_date, total_assets, official_check FROM us_fed_balance_weekly "
                            "ORDER BY trade_date DESC LIMIT 1")
                tail = cur.fetchone()
            conn.commit()
        finally:
            conn.close()

        if sent_total:
            errors.append(f"源含 {sent_total} 个 -999999 缺失哨兵，已剔除（未入库）")
        dev = None
        if tail and tail["total_assets"] and tail["official_check"]:
            dev = round((tail["total_assets"] / tail["official_check"] - 1) * 100, 6)
            if abs(dev) > 0.01:
                errors.append(f"跨通道偏差超阈：DBnomics {tail['total_assets']} vs 官网 "
                              f"{tail['official_check']}（{dev}%）")
        msg = (f"美联储资产负债表写 {len(rows)} 行（水位重写窗口 {floor}）｜表内 {st['n']} 行"
               f"（{st['d0']} ~ {st['d1']}）｜总资产有值 {st['n_ta']} 行"
               f"｜最新 {tail['trade_date'] if tail else '-'} 总资产={tail['total_assets'] if tail else '-'}"
               f" 百万美元｜官网校验={tail['official_check'] if tail else None}（偏差 {dev}%）")
        logger.info("✅ %s", msg)
        return with_steps(
            {"records_written": len(rows), "error_count": len(errors), "errors": errors[:20], "note": msg},
            RUN_STEPS,
            {
                1: f"9 条序列取到 {len(got)} 条；总资产末值 {got['total_assets'].points[-1][1] if 'total_assets' in got else '-'}"
                   f" @{got['total_assets'].d1 if 'total_assets' in got else '-'}",
                2: f"派生 securities_total / reverse_repo_total 各 {len(merged)} 行",
                3: f"哨兵 {sent_total} 个已剔除（命中 0 才算正常）",
                4: (f"官网总资产={off_total:.0f}（偏差 {dev}%）" if off_total is not None
                    else f"官网校验不可用：{off_note}"),
                5: f"upsert {len(rows)} 行（重叠 {_OVERLAP_WEEKS} 周，表内 {st['n']} 行）",
            },
        )
