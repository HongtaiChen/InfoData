#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""投资日历 API：财经事件日历（日历表格展示）+ 交易日历"""
from fastapi import APIRouter, Query

from ..db import query_all

router = APIRouter()


@router.get("/events")
def calendar_events(
    year: int = Query(2026, ge=2000, le=2100),
    month: int | None = Query(None, ge=1, le=12, description="缺省查全年"),
):
    """财经事件列表（按日期），前端按日聚合成日历表格"""
    params: list = [year]
    month_where = ""
    if month:
        month_where = " AND MONTH(event_date) = %s"
        params.append(month)
    rows = query_all(
        f"""
        SELECT id, event_date, title, content, data_source
        FROM finance_calendar
        WHERE YEAR(event_date) = %s {month_where}
        ORDER BY event_date, id
        """,
        params,
    )
    return {"year": year, "month": month, "total": len(rows), "items": rows}


@router.get("/trade-days")
def trade_days(
    year: int = Query(2026, ge=2000, le=2100),
    month: int | None = Query(None, ge=1, le=12),
):
    """交易日历（区分交易日/休市日）"""
    params: list = [year]
    month_where = ""
    if month:
        month_where = " AND month = %s"
        params.append(month)
    rows = query_all(
        f"""
        SELECT trade_date, is_trading_day, year, month, day, weekday
        FROM trade_calendar
        WHERE year = %s {month_where}
        ORDER BY trade_date
        """,
        params,
    )
    return {"year": year, "month": month, "total": len(rows), "items": rows}


@router.get("/recent")
def recent_events(days: int = Query(30, ge=1, le=365)):
    """未来 N 天事件预告（首页/看板用）"""
    rows = query_all(
        """
        SELECT id, event_date, title, content, data_source
        FROM finance_calendar
        WHERE event_date >= CURDATE()
        ORDER BY event_date
        LIMIT %s
        """,
        [days],
    )
    return {"total": len(rows), "items": rows}
