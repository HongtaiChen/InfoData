#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""投资环境 API：国内指数页（数据 + 指标 + 历史曲线，结论由观看者判断）

本期只做「国内指数」（E1~E6 定稿，见 docs/design/投资环境模块_设计方案_v0.2_2026-10-18.md）。
不提供 AI 解读（E3）。
"""
from fastapi import APIRouter, Query

from ..services import env_service

router = APIRouter()


@router.get("/domestic/indices")
def domestic_indices():
    """国内指数列表（21 个）+ 当日涨跌幅 + 阈值。"""
    return env_service.domestic_indices()


@router.get("/domestic/overview")
def domestic_overview(
    window: str = Query("", description="区间窗口：3m/6m/1y/3y（可选，给定则每行带 range_stats）"),
    start: str = Query("", description="自定义起始日期 YYYYMMDD"),
    end: str = Query("", description="自定义结束日期 YYYYMMDD"),
):
    """21 指数关键指标横向对比表（总览页）。"""
    return env_service.domestic_overview(window=window, start=start, end=end)


@router.get("/domestic/compare")
def domestic_compare(
    codes: str = Query(..., description="逗号分隔的指数代码，如 000001,000300,399006"),
    window: str = Query("1y", description="回看窗口：3m/6m/1y/3y（start/end 给定时忽略）"),
    start: str = Query("", description="自定义起始日期 YYYYMMDD"),
    end: str = Query("", description="自定义结束日期 YYYYMMDD"),
):
    """多指数收盘序列 → 原始收盘价叠加（不归一化）。"""
    code_list = [c.strip() for c in codes.split(",") if c.strip()]
    # 去重且保持顺序
    seen, ordered = set(), []
    for c in code_list:
        if c not in seen:
            seen.add(c)
            ordered.append(c)
    return env_service.domestic_compare(ordered, window, start, end)


@router.get("/domestic/indices/{code}/tech")
def domestic_tech(
    code: str,
    limit: int = Query(500, ge=60, le=2000, description="历史序列回看交易日数"),
):
    """该指数全部技术面指标（A 组趋势 + B 组波动下行风险）。"""
    return env_service.tech_indicators(code, limit)


@router.get("/domestic/indices/{code}/fundamental")
def domestic_fundamental(code: str):
    """该指数基本面指标（估值分位序列 / 盈利成长 / 宏观）。"""
    return env_service.fundamental_indicators(code)


@router.get("/domestic/correlation")
def domestic_correlation():
    """21 指数间 120 日相关性热力矩阵。"""
    return env_service.correlation_matrix()


@router.get("/macro")
def macro():
    """宏观组（GDP/PMI/CPI/PPI）读数 + 历史曲线。"""
    return env_service.macro_series()


@router.get("/domestic/policy")
def policy_views():
    """政策与外部环境判读（货币政策/财政政策/外部环境，人工两态判读）。"""
    return env_service.policy_views()


@router.post("/domestic/policy")
def set_policy_view(topic: str = Query(...), state: str = Query(...)):
    """切换某主题的判读状态（落库持久化）。"""
    return env_service.set_policy_view(topic, state)


@router.get("/scope-note")
def scope_note():
    """指数范围说明（样本指数 / 阈值口径 / 指标口径）。"""
    return env_service.scope_note()


# ---------------------------------------------------------------------------
# 国际指数（GlobalIndexView）—— 技术面 4 块 + 基本面
# ---------------------------------------------------------------------------

@router.get("/global/indices")
def global_indices():
    """国际指数列表（18 个，按 6 组）+ 最新技术面读数。"""
    return env_service.global_indices()


@router.get("/global/compare")
def global_compare(
    codes: str = Query(..., description="逗号分隔的海外指数代码，如 SPX,NDX,HSI"),
    window: str = Query("1y", description="回看窗口：3m/6m/1y/3y"),
    start: str = Query("", description="自定义起始 YYYYMMDD"),
    end: str = Query("", description="自定义结束 YYYYMMDD"),
):
    """多海外指数收盘序列叠加。"""
    code_list = [c.strip() for c in codes.split(",") if c.strip()]
    seen, ordered = set(), []
    for c in code_list:
        if c not in seen:
            seen.add(c)
            ordered.append(c)
    return env_service.global_compare(ordered, window, start, end)


@router.get("/global/indices/{code}/tech")
def global_tech(code: str, limit: int = Query(500, ge=60, le=2000)):
    """该海外指数技术面指标（趋势与动量 + 波动率与风险 + 相对强弱）。"""
    return env_service.global_tech(code, limit)


@router.get("/global/correlation")
def global_correlation():
    """海外指数间 60 日相关性热力矩阵。"""
    return env_service.global_correlation()


@router.get("/global/vix")
def global_vix():
    """VIX 恐慌指数序列。"""
    return env_service.global_vix()


@router.get("/global/cftc")
def global_cftc():
    """CFTC 货币期货净仓位。"""
    return env_service.global_cftc()


@router.get("/global/valuation")
def global_valuation():
    """国际指数估值快照。"""
    return env_service.global_valuation()


@router.get("/global/fx-bond")
def global_fx_bond():
    """美元指数 + 美债利差。"""
    return env_service.global_fx_bond()


@router.get("/global/fundamental")
def global_fundamental():
    """国际指数基本面分析（估值/盈利/宏观/地缘 4 大块）。"""
    return env_service.global_fundamental()


@router.get("/sentiment")
def market_mood():
    """市场氛围（技术面 5 块 + 基本面 1 块）。"""
    return env_service.market_mood()


@router.get("/global-env")
def global_environment():
    """国际环境（技术面 5 块 + 基本面 5 块）。"""
    return env_service.global_environment()


@router.get("/global-money")
def global_money():
    """全球货币（技术面 3 块 + 基本面 4 块）。"""
    return env_service.global_money()


@router.get("/domestic-money")
def domestic_money():
    """国内货币（技术面 3 块 + 基本面 4 块）。"""
    return env_service.domestic_money()
