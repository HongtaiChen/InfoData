#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
InvestBuddy 经营性现金流采集器（stock_cash_flow_ths，同花顺现金流量表经 akshare）

背景（2026-10-20，投资环境·盈利质量与成长章节）：
  该章节要补「经营性现金流」指标（盈利含金量 = 经营现金流净额 / 净利润，
  以及经营现金流净额绝对值的季度趋势）。库内 stock_financial_abstract_ths 只有
  净利润/营收/ROE/EPS/每股净资产，**无现金流字段**，故新建本采集器补上分母/分子。

- 源：ak.stock_financial_cash_ths(symbol, indicator='按报告期') —— 单只返回全历史期
- 取列：`*经营活动产生的现金流量净额`（同花顺「报告期」口径，形如 '2150.12亿'）
- 写表：stock_cash_flow_ths（stock_code / report_date / operating_cash_flow）
- 策略：与 financial_abstract_sync 同款 —— 只补滞后股票 + 逐股增量 upsert，
  复用 _common.load_refresh_targets / latest_expected_report_period
- 幂等：DB 侧 UNIQUE(stock_code, report_date) + ON DUPLICATE KEY UPDATE
- 容错：单只失败记 error 不中断；akshare 缺失返回布尔 False → 清洗为 NULL

⚠️ 源值清洗：净额列是带中文单位的字符串（'2150.12亿' / '378.02亿' / '--'），
   必须用 _parse_cn_value 解析；False/NaN/-- 统一 NULL。
"""
import logging
import time

import pymysql

import akshare as ak

from ..db import get_db_config
from ._common import (with_steps, call_with_timeout,
                      load_refresh_targets, latest_expected_report_period)

logger = logging.getLogger(__name__)

RUN_STEPS = [
    {"no": 1, "name": "构建候选池与滞后集", "params": "候选 = (本地已有股票 ∪ stock_info 在册 A 股) − 退市股；滞后 = MAX(报告期) < max(本地全局 MAX, 披露日历推算最近期)"},
    {"no": 2, "name": "逐股拉现金流量表", "params": "ak.stock_financial_cash_ths(symbol, '按报告期')；取「经营活动产生的现金流量净额」；单只 30s 超时"},
    {"no": 3, "name": "源值清洗", "params": "'2150.12亿' → 数值；False/NaN/'--' → NULL"},
    {"no": 4, "name": "批量 upsert", "params": "ON DUPLICATE KEY UPDATE；每 200 只提交，断点续传"},
]

_OCF_COL = "*经营活动产生的现金流量净额"
_REPORT_COL = "报告期"
_NULL_TOKENS = {"--", "-", "", "nan", "none", "nat", "false", "true"}
_INSERT_COLS = ["stock_code", "report_date", "operating_cash_flow", "update_time", "data_source"]
_UPDATE_COLS = ["operating_cash_flow", "update_time"]

_CN_UNIT = {"亿": 1e8, "万": 1e4, "千": 1e3}


def _parse_cn_value(v) -> float | None:
    """'2150.12亿' → 2150.12e8；'--'/False/NaN → None。"""
    if v is None or isinstance(v, bool):
        return None
    s = str(v).strip()
    if not s or s.lower() in _NULL_TOKENS:
        return None
    neg = s.startswith("-")
    if neg:
        s = s[1:]
    mult = 1.0
    for unit, m in _CN_UNIT.items():
        if s.endswith(unit):
            mult = m
            s = s[:-1]
            break
    try:
        val = float(s) * mult
    except ValueError:
        return None
    return -val if neg else val


class CashFlowSyncCollector:
    """经营性现金流同步（同花顺现金流量表，逐股增量）"""

    def __init__(self, max_stocks: int = 0, timeout_sec: float = 30):
        self.max_stocks = int(max_stocks)
        self.timeout_sec = float(timeout_sec)

    def _fetch(self, symbol: str):
        df = call_with_timeout(
            ak.stock_financial_cash_ths, self.timeout_sec,
            symbol=symbol, indicator="按报告期",
        )
        if df is None or df.empty:
            return None
        if _OCF_COL not in df.columns or _REPORT_COL not in df.columns:
            return None
        return df

    def run(self) -> dict:
        conn = pymysql.connect(**get_db_config().to_dict())
        records_written = 0
        errors: list[str] = []
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT MAX(report_date) FROM stock_cash_flow_ths")
                row = cur.fetchone()
            local_max = row[0] if row and row[0] else None
            expected = latest_expected_report_period()
            floor = max([d for d in (local_max, expected) if d is not None])
            targets, total_candidates = load_refresh_targets(
                conn, "stock_cash_flow_ths", data_col="report_date",
                floor_date=floor, skip_delisted=True,
                exclude_switched_old=True, max_stocks=self.max_stocks,
            )
            total = len(targets)
            logger.info("现金流候选 %d 只（总候选 %d，本地 MAX=%s，应至 %s）",
                        total, total_candidates, local_max, expected)

            processed = 0
            for code_name in targets:
                stock_code = code_name[0] if isinstance(code_name, tuple) else code_name
                processed += 1
                try:
                    df = self._fetch(stock_code)
                    if df is None:
                        continue
                    rows = []
                    for _, r in df.iterrows():
                        rd = str(r.get(_REPORT_COL, "")).strip()[:10]
                        if not rd or rd.lower() in _NULL_TOKENS:
                            continue
                        ocf = _parse_cn_value(r.get(_OCF_COL))
                        if ocf is None:
                            continue
                        rows.append((stock_code, rd, ocf))
                    if not rows:
                        continue
                    with conn.cursor() as cur:
                        cur.executemany(
                            f"INSERT INTO stock_cash_flow_ths ({', '.join(_INSERT_COLS)}) "
                            f"VALUES ({', '.join(['%s'] * len(_INSERT_COLS))}) "
                            "ON DUPLICATE KEY UPDATE "
                            + ", ".join(f"{c}=VALUES({c})" for c in _UPDATE_COLS),
                            [(c, rd, ocf, None, "AKSHARE") for c, rd, ocf in rows],
                        )
                    conn.commit()
                    records_written += len(rows)
                    if processed % 200 == 0:
                        logger.info("现金流进度 %d/%d，累计写 %d 行", processed, total, records_written)
                except Exception as e:  # noqa: BLE001
                    errors.append(f"{stock_code}: {type(e).__name__} {str(e)[:80]}")
                    continue
                time.sleep(0.1)
        finally:
            conn.close()

        msg = f"现金流采集 {processed}/{total} 只，写 {records_written} 行，错误 {len(errors)}"
        logger.info("✅ %s", msg)
        return with_steps(
            {"records_written": records_written, "error_count": len(errors),
             "errors": errors[:20], "note": msg},
            RUN_STEPS,
            {
                1: f"候选 {total} 只（总候选 {total_candidates}）",
                2: f"逐股拉取 {processed} 只",
                3: "中文单位解析为数值",
                4: f"upsert {records_written} 行",
            },
        )
