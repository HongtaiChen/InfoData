#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""全球流动性采集器（global_liquidity_bis）—— BIS 全球流动性指标 WS_GLI

蓝图的哪一块：**货币流动性 · 全球层数量维度**（补源方案 v2.0 §7 批次 D）。
这是「**全球美元有多少**」的**官方口径**，替代 v1.0 原计划的自建全球 M2 汇总
（后者要拼 N 国 × 汇率换算、且非官方）。核心用途：`BORROWERS_CTY != US` 的美元信贷
= **离岸美元** —— 回答「美元流出美国了没有」，与 A5「美元净流动性」（美国国内口径）
互补：A5 问「水放进美国银行体系没有」，A7 问「美元流出美国了没有」。

★ 两处**对 v1.0 / v2.0 文档结论的实测勘误**（重要，勿改回）：

  ① v2.0 §3 写「`/all` 全量形式 200 可用；按维度过滤的 key（`Q.US.N.A.USDO.3P.A`）404、写法待定」。
     **实测恰好相反**：
       · `/all?format=csv&startPeriod=...` → **404**
       · `/Q.USD`（**3 段 key**）→ **200**，返回正确 CSV（832 行 / 2023-Q1~2026-Q1）
     ⇒ resource key 的有效形式是 **`FREQ.CURR_DENOM.BORROWERS_CTY`（只给 3 段）**，
       **多给一段即 404**（`Q.USD.US.N`、`Q.USD.US.N.A` … 全部 404）。
       所以本采集器用 `Q.USD` 一次拿全「美元计价的全球信贷」，再按维度在库里区分。

  ② BIS 的借款人国家用**自有码**，不是 ISO2：`Q.USD.JP`、`Q.USD.GB` 实测 **404**，
     `Q.USD.CN` 200。故**不要**按 ISO2 猜国家代码。
     另：美国借款人（`Q.USD.US`）只报 `G`/`P` 两个部门、**无 `N`（非银行）**
     ⇒ 「离岸美元」不能用「全量 − 美国」简单推导，须**按 borrowers_cty 直接筛非美组**。

⚠️ 源侧一条工程纪律：BIS 会**修订历史季度**，故增量采用「水位 − 4 个季度重叠重写」。

⚠️ 单位（2026-09-26 实测校正，勿改回）：`UNIT_MEASURE='USD'` 时 `OBS_VALUE` 是
   **百万美元（存量）**；`'771'` 是 **"Year-on-year changes, in per cent"＝同比增速（%）**
   —— **不是「占比」**（我先前按值域 0~100 误判为占比，读 BIS 官方系列页的
   `Unit of measure` 维度定义才定案）。两套并列存储、以 `unit_measure` 区分。

🔴 一条**必须遵守的取数纪律**（2026-09-26 复核实测，勿回退）：

   离岸美元 / 全球美元信贷的**唯一正确口径**：
       borrowers_cty='3P' AND borrowers_sector='N' AND lenders_sector='A'
       AND l_pos_type='I' AND l_instr='B' AND unit_measure='USD'
     @2026-Q1 = 14,747,700 百万美元（14.75 万亿）

   三重重复陷阱 —— 三个条件**缺一个就静默算错**：
     ① `l_instr` 的 **`B` 是合计**（"Credit (loans & debt securities)"），
        **已包含** `D`（只 IDS 债券）与 `G`（只银行贷款）⇒ B+D+G 相加是**重复**。
        实测 3P 下 D+G = 8.0646+6.683 = 14.7476 ≈ B = 14.7477，**逐位吻合**，铁证。
     ② `borrowers_cty` 里 **聚合码与明细国并存**：`3P` = "All countries excluding
        residents"（全体非美合计），另有 13 个新兴市场国明细 ⇒ 「3P + 各国」相加是**重复**。
        ⚠️ 本库明细只含新兴市场（无欧元区/日本/英国），**总量必须取 3P**。
     ③ `unit_measure='771'` 是**同比 %**，混进金额求和无意义。

   错口径复现（`borrowers_cty<>'US'` 全量 SUM）= **45,951,383 百万美元 = 真值 3.12 倍**。
   ⚠️ 这个错值曾写进 v2.0 方案 §2.1/§3、分析层设计文档 §二、本采集器旧 note、
   `table_meta.flow_desc`，2026-09-26 全部勘误。

   ⚠️ 另：`l_pos_type='I'` 官方定义是 **"Cross-border & Local in FCY"**
   （跨境 **+ 借款人本地的外币**信贷），比「跨境信贷」更宽 ⇒ 中文表述写
   「境外美元信贷」，**不要**简化成「跨境美元信贷」。

幂等：UNIQUE(time_period + 7 个维度) + 重叠重写。
"""
import csv
import io
import logging
from datetime import date, datetime

import pymysql

from ..db import get_db_config
from ._common import with_steps
from ._dbnomics import period_to_date
from ._http import get_text

logger = logging.getLogger(__name__)

RUN_STEPS = [
    {"no": 1, "name": "拉 BIS 全球美元信贷（WS_GLI / Q.USD）",
     "params": "stats.bis.org/api/v1/data/BIS,WS_GLI,1.0/Q.USD?format=csv —— ⚠️ 3 段 key；/all 与 4 段 key 实测均 404"},
    {"no": 2, "name": "解析 CSV 并转期",
     "params": "季度期 `2026-Q1` → 2026-03-31；按 8 维去重；title 截断至 255 字符"},
    {"no": 3, "name": "重叠重写 Upsert",
     "params": "UNIQUE(time_period+7 维)；BIS 会修订历史季度，故重写最近 4 个季度 + 新增行"},
]

# ⚠️ key 只有 3 段：FREQ.CURR_DENOM.BORROWERS_CTY（多一段即 404，见模块头 ①）
_BIS_URL = "https://stats.bis.org/api/v1/data/BIS,WS_GLI,1.0/Q.USD?format=csv&startPeriod=1990"

_DIMS = ["curr_denom", "borrowers_cty", "borrowers_sector", "lenders_sector",
         "l_pos_type", "l_instr", "unit_measure"]
_TGT = ["time_period", "freq"] + _DIMS + ["obs_value", "title"]
_OVERLAP_QUARTERS = 4


def _f(v):
    if v is None:
        return None
    s = str(v).strip()
    if s == "" or s.lower() in ("null", "nan"):
        return None
    try:
        x = float(s)
    except (TypeError, ValueError):
        return None
    return None if x != x else round(x, 4)


def _fetch_rows(timeout: float = 90) -> tuple[list[dict], str]:
    """拉取并解析 BIS CSV → (行列表, 元信息)。失败抛异常（全球层只有这一条通道）。"""
    txt = get_text(_BIS_URL, timeout=timeout, headers={"Accept": "text/csv"})
    if not txt or "FREQ" not in txt[:400]:
        raise RuntimeError(f"BIS 响应异常（前 160 字）：{(txt or '')[:160]}")
    rdr = list(csv.DictReader(io.StringIO(txt.lstrip("\ufeff"))))
    return rdr, f"{len(txt)}B / {len(rdr)} 行"


class GlobalLiquiditySyncCollector:
    """BIS 全球流动性指标（美元计价跨境信贷）季度同步"""

    def __init__(self, timeout_sec: float = 90):
        self.timeout_sec = float(timeout_sec)

    def run(self) -> dict:
        errors: list[str] = []
        raw, meta = _fetch_rows(self.timeout_sec)

        merged: dict = {}
        bad_period = 0
        for r in raw:
            tp = period_to_date(r.get("TIME_PERIOD"))
            if tp is None:
                bad_period += 1
                continue
            key = (tp, r.get("FREQ", "Q"), r.get("CURR_DENOM"), r.get("BORROWERS_CTY"),
                   r.get("BORROWERS_SECTOR"), r.get("LENDERS_SECTOR"), r.get("L_POS_TYPE"),
                   r.get("L_INSTR"), r.get("UNIT_MEASURE"))
            merged[key] = {
                "time_period": tp, "freq": r.get("FREQ", "Q"),
                **{_DIMS[0]: r.get("CURR_DENOM"), _DIMS[1]: r.get("BORROWERS_CTY"),
                   _DIMS[2]: r.get("BORROWERS_SECTOR"), _DIMS[3]: r.get("LENDERS_SECTOR"),
                   _DIMS[4]: r.get("L_POS_TYPE"), _DIMS[5]: r.get("L_INSTR"),
                   _DIMS[6]: r.get("UNIT_MEASURE")},
                "obs_value": _f(r.get("OBS_VALUE")),
                "title": (r.get("TITLE") or "")[:255],
            }
        if not merged:
            raise RuntimeError(f"BIS 解析后无有效行（原始 {len(raw)} 行，期解析失败 {bad_period}）")
        if bad_period:
            errors.append(f"{bad_period} 行的 TIME_PERIOD 无法解析，已跳过")

        conn = pymysql.connect(**get_db_config().to_dict(), cursorclass=pymysql.cursors.DictCursor)
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT MAX(time_period) AS d FROM global_liquidity_bis")
                last = cur.fetchone()["d"]
                floor = date(last.year - 1, last.month, 1) if last else date(1900, 1, 1)

                rows = []
                for _, m in sorted(merged.items(), key=lambda kv: (kv[1]["time_period"],
                                                                  str(kv[1]["borrowers_cty"]))):
                    if m["time_period"] < floor:
                        continue
                    rows.append(tuple(m.get(k) for k in _TGT))

                sql = (
                    f"INSERT INTO global_liquidity_bis ({', '.join(_TGT)}, update_time, data_source) "
                    f"VALUES ({', '.join(['%s'] * len(_TGT))}, NOW(), %s) "
                    "ON DUPLICATE KEY UPDATE obs_value=VALUES(obs_value), title=VALUES(title), "
                    "update_time=NOW()"
                )
                cur.executemany(sql, [(*r, "BIS_WS_GLI") for r in rows])

                cur.execute(
                    "SELECT COUNT(*) AS n, MIN(time_period) AS d0, MAX(time_period) AS d1, "
                    "COUNT(DISTINCT borrowers_cty) AS n_cty, "
                    "SUM(unit_measure='USD') AS n_usd, SUM(unit_measure='771') AS n_pct "
                    "FROM global_liquidity_bis")
                st = cur.fetchone()
                # ⭐ 离岸美元 = 3P(全体非美) × N(非银) × A(全体贷款方) × I(境外) × B(合计) × USD
                #    ⚠️ 不可写成 `borrowers_cty<>'US'` 全量 SUM（三重重复，实测虚高 3.12 倍，
                #    详见模块头「取数纪律」）。3P 是全体非美合计，每期唯一一条 ⇒ 直接取值。
                _off_where = (
                    "borrowers_cty='3P' AND borrowers_sector='N' AND lenders_sector='A' "
                    "AND l_pos_type='I' AND l_instr='B' AND unit_measure='USD'")
                cur.execute("SELECT MAX(time_period) AS d FROM global_liquidity_bis "
                            "WHERE " + _off_where)
                d_off = cur.fetchone()["d"]
                offshore = None
                if d_off:
                    cur.execute("SELECT obs_value AS v FROM global_liquidity_bis "
                                "WHERE time_period=%s AND " + _off_where, (d_off,))
                    r_off = cur.fetchone()
                    offshore = r_off["v"] if r_off else None
            conn.commit()
        finally:
            conn.close()

        off_txt = (f"｜离岸美元（3P 全体非美·非银·境外·贷款&债券合计，{d_off}）"
                   f"= {float(offshore) / 1e6:,.4f} 万亿美元"
                   if offshore else "｜离岸美元口径取数失败（见模块头取数纪律）")
        msg = (f"BIS 全球流动性写 {len(rows)} 行（重叠 {_OVERLAP_QUARTERS} 季）｜表内 {st['n']} 行"
               f"（{st['d0']} ~ {st['d1']}）｜借款人 {st['n_cty']} 个｜USD 存量 {st['n_usd']} 行 / "
               f"同比% {st['n_pct']} 行{off_txt}")
        logger.info("✅ %s", msg)
        return with_steps(
            {"records_written": len(rows), "error_count": len(errors), "errors": errors[:20], "note": msg},
            RUN_STEPS,
            {
                1: meta,
                2: f"去重后 {len(merged)} 个 (期×8 维) 组合；期解析失败 {bad_period}",
                3: f"upsert {len(rows)} 行（表内 {st['n']} 行，最新 {st['d1']}）",
            },
        )
