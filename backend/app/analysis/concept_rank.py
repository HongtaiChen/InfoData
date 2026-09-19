#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""分析模板：概念板块排名
单日涨幅 + N 日区间涨幅（核心联动分析价值：板块轮动观察）
"""
from ..db import query_all


def _latest_trade_date() -> str | None:
    row = query_all("SELECT MAX(trade_date) AS d FROM ths_concept_market")
    return str(row[0]["d"]) if row and row[0]["d"] else None


def _prev_trade_date(date: str, offset: int) -> str | None:
    """date 往前第 offset 个交易日"""
    row = query_all(
        """
        SELECT trade_date FROM ths_concept_market
        WHERE trade_date < %s
        ORDER BY trade_date DESC
        LIMIT 1 OFFSET %s
        """,
        [date, max(offset - 1, 0)],
    )
    return str(row[0]["trade_date"]) if row else None


def concept_rank(period: int = 1, date: str | None = None, limit: int = 50) -> list[dict]:
    """概念板块排名
    period=1：单日涨跌幅
    period=5/10/20：N 日区间涨幅（最新收盘相对 N 个交易日前收盘）
    """
    if period <= 1:
        return _day_rank(date, limit)
    return _period_rank(period, date, limit)


def _day_rank(date: str | None, limit: int) -> list[dict]:
    """概念当日涨幅榜。

    ⚠️ 不能只 `ORDER BY m.change_pct`：该列**由源侧间歇性不返回**（实测 2026-09-15~09-18
    空值率 99~100%，09-01/02 却是 0%）。全空时 ORDER BY 退化为任意序，榜单静默失真。
    改为在源值为空时用 `close / 上一交易日 close - 1` 兜底 —— 与 _period_rank 同口径，
    源值非空时仍优先用源值（两者口径一致时应相等）。
    与 api/concept.py::concept_list 是同一处坑的两侧，改一处别忘了另一处。
    """
    if not date:
        date = _latest_trade_date()
    if not date:
        return []
    prev = _prev_trade_date(date, 1)
    if not prev:
        return []
    rows = query_all(
        """
        SELECT i.index_code, i.concept_code, i.concept_name,
               m.trade_date, m.close, m.amount, m.volume,
               COALESCE(m.change_pct,
                        ROUND((m.close / NULLIF(p.close, 0) - 1) * 100, 4)) AS change_pct,
               COALESCE(m.change_amount, ROUND(m.close - p.close, 4)) AS change_amount
        FROM ths_concept_market m
        JOIN ths_concept_info i ON i.index_code = m.index_code
        LEFT JOIN ths_concept_market p
          ON p.index_code = m.index_code AND p.trade_date = %s
        WHERE m.trade_date = %s
        ORDER BY change_pct DESC
        LIMIT %s
        """,
        [prev, date, limit],
    )
    return rows


def _period_rank(period: int, date: str | None, limit: int) -> list[dict]:
    if not date:
        date = _latest_trade_date()
    if not date:
        return []
    prev = _prev_trade_date(date, period)
    if not prev:
        return []
    rows = query_all(
        """
        SELECT cur.index_code, i.concept_name,
               cur.trade_date AS end_date, cur.close AS end_close,
               prev.close AS start_close,
               ROUND((cur.close / NULLIF(prev.close, 0) - 1) * 100, 4) AS period_change_pct,
               cur.change_pct AS day_change_pct, cur.amount
        FROM ths_concept_market cur
        JOIN ths_concept_market prev
          ON prev.index_code = cur.index_code AND prev.trade_date = %s
        JOIN ths_concept_info i ON i.index_code = cur.index_code
        WHERE cur.trade_date = %s AND prev.close > 0
        ORDER BY period_change_pct DESC
        LIMIT %s
        """,
        [prev, date, limit],
    )
    for r in rows:
        r["period"] = period
    return rows
