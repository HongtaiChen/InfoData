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
    if not date:
        date = _latest_trade_date()
    if not date:
        return []
    rows = query_all(
        """
        SELECT i.index_code, i.concept_code, i.concept_name,
               m.trade_date, m.close, m.change_pct, m.change_amount, m.amount, m.volume
        FROM ths_concept_market m
        JOIN ths_concept_info i ON i.index_code = m.index_code
        WHERE m.trade_date = %s
        ORDER BY m.change_pct DESC
        LIMIT %s
        """,
        [date, limit],
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
