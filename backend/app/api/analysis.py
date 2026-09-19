#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""分析研究 API：模块注册表 + 分析模块取数
（旧 3 个排行模板已下线保留代码，见《分析研究模块交互设计规范》§5）
"""
from fastapi import APIRouter, Query

from ..analysis import cross_market as cross_market_mod
from ..analysis import funding_temperature as funding_temperature_mod
from ..analysis import market_wind as market_wind_mod
from ..analysis import money_cost as money_cost_mod
from ..analysis import sector_rotation as sector_rotation_mod
from ..analysis.registry import REGISTRY

router = APIRouter()


@router.get("/registry")
def analysis_registry():
    """分析模块注册表（总览页卡片墙 + 目录；配置文件版，见 app/analysis/registry.py）"""
    return {"total": len(REGISTRY), "items": REGISTRY}


@router.get("/market-wind")
def market_wind(
    trend_days: int = Query(250, ge=60, le=1000, description="轮动时序窗口（交易日）"),
    as_of: str | None = Query(None, description="历史回放锚点 YYYY-MM-DD；空则取最新"),
):
    """市场风向：六组收益热力 + 大小盘剪刀差 + 风偏分数 + 市场宽度/量能 + 轮动时序 + 交叉印证

    as_of 非空时按该日回放（复盘用），全部查询只取该日及之前。
    """
    return market_wind_mod.market_wind(trend_days, as_of)


@router.get("/sector-rotation")
def sector_rotation(
    as_of: str | None = Query(None, description="历史回放锚点 YYYY-MM-DD；空则取最新"),
    level: str = Query("一级", description="申万行业层级：一级（31 个）/ 二级（131 个）"),
):
    """板块轮动：申万行业 20 日收益排行（含相对基准超额）+ 概念口径排行 + 双侧口径互证

    ⚠️ 行业排行需扫个股日线快照（约 8 秒），模块内已套 10 分钟 TTL 缓存（见 analysis/_cache.py）。
    """
    return sector_rotation_mod.sector_rotation(as_of, level)


@router.get("/money-cost")
def money_cost(
    as_of: str | None = Query(None, description="历史回放锚点 YYYY-MM-DD；空则取最新"),
    trend_days: int = Query(500, ge=60, le=2000, description="时序图回看的交易日数"),
):
    """钱贵不贵：Shibor 期限结构 + 3M 近一年分位 + LPR 政策姿态 + 政策/市场背离 + 资金×权益位置
    """
    return money_cost_mod.money_cost(as_of, trend_days)


@router.get("/cross-market")
def cross_market(
    as_of: str | None = Query(None, description="历史回放锚点 YYYY-MM-DD；空则取最新"),
    trend_days: int = Query(500, ge=60, le=1200, description="归一化走势回看的交易日数"),
):
    """跨市场对照：美股/中国香港/A股 20 日收益与基准超额 + 隔夜传导同向率（含近一年分位）
    """
    return cross_market_mod.cross_market(as_of, trend_days)


@router.get("/funding-temperature")
def funding_temperature(
    as_of: str | None = Query(None, description="历史回放锚点 YYYY-MM-DD；空则取最新"),
):
    """资金温度：人民币汇率 × 新发基金 × 上市公司回购，三条独立线索的交叉印证
    """
    return funding_temperature_mod.funding_temperature(as_of)
