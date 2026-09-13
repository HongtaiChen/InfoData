#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""分析研究 API：模块注册表 + 分析模块取数
（旧 3 个排行模板已下线保留代码，见《分析研究模块交互设计规范》§5）
"""
from fastapi import APIRouter, Query

from ..analysis import market_wind as market_wind_mod
from ..analysis.registry import REGISTRY

router = APIRouter()


@router.get("/registry")
def analysis_registry():
    """分析模块注册表（总览页卡片墙 + 目录；配置文件版，见 app/analysis/registry.py）"""
    return {"total": len(REGISTRY), "items": REGISTRY}


@router.get("/market-wind")
def market_wind(
    trend_days: int = Query(250, ge=60, le=1000, description="轮动时序窗口（交易日）"),
):
    """市场风向：六组收益热力 + 大小盘剪刀差 + 风偏分数 + 轮动时序"""
    return market_wind_mod.market_wind(trend_days)
