#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""分析研究 API：3 个核心分析模板（股息率 / 概念排名 / YTD）"""
from fastapi import APIRouter, Query

from ..analysis import dividend as dividend_mod
from ..analysis import concept_rank as concept_rank_mod
from ..analysis import ytd as ytd_mod

router = APIRouter()


@router.get("/dividend-rank")
def dividend_rank(
    report_period: str | None = Query(None, description="报告期 YYYYMMDD，缺省取最新"),
    limit: int = Query(50, ge=1, le=200),
):
    """股息率排行（按最新报告期税前股息率降序）"""
    items = dividend_mod.dividend_rank(report_period, limit)
    return {"report_period": report_period, "total": len(items), "items": items}


@router.get("/concept-rank")
def concept_rank(
    period: int = Query(1, ge=1, le=120, description="1=单日涨幅，5/10/20=N日区间涨幅"),
    date: str | None = Query(None, description="截止交易日 YYYYMMDD，缺省取最新"),
    limit: int = Query(50, ge=1, le=200),
):
    """概念板块排名（单日 / N 日区间涨幅）"""
    items = concept_rank_mod.concept_rank(period, date, limit)
    return {"period": period, "total": len(items), "items": items}


@router.get("/ytd-rank")
def ytd_rank(
    limit: int = Query(50, ge=1, le=200),
    order: str = Query("desc", pattern="^(asc|desc)$", description="desc=涨幅最高在前，asc=跌幅最深在前"),
):
    """年初至今涨幅排行"""
    items = ytd_mod.ytd_rank(limit, order)
    return {"total": len(items), "items": items}
