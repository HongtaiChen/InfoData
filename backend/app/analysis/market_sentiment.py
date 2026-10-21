#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""分析模块：市场情绪与交易结构（2026-10-20 新增，数据源 stock_market_daily + market_style_daily）

三个指标（全市场口径，非单指数）：
1. 成交集中度：前 5% 个股成交额之和 ÷ 全市场成交额之和（%）。
   低 = 资金分散、无主线；接近/超过 45% = 抱团风险（资金高度集中于少数个股）。
2. 股价偏高个股占比：近 60 日涨幅 >20% 的个股占比（%）。
   低 = 行情未过度脱离性价比；超过 15% = 结构性过热（短期涨幅过度集中于部分个股）。
3. 换手率、涨停家数：直接取 market_style_daily 的 turnover_med（全市场个股换手率中位数 %）、
   limit_up（涨停家数）。情绪温和 = 常态区间；异常放大 = 短期过热。

口径与实现：
- 成交集中度 / 偏高占比为「现算」：每次请求按最新完整交易日现算（Python 内排序取分位），
  模块级按 (trade_date) 缓存，当日数据落库后首算即缓存、后续请求命中。
- 换手率/涨停取物化表现成列，无现算成本。
- 分位判定：偏高占比的「前 10%」用个股自身 60 日涨幅横截面分位（>90%），
  与「结构性过热」语义一致；集中度阈值 45%、偏高占比阈值 15% 为用户给定红线。
"""
from __future__ import annotations

import threading
import time

from ..db import query_all

# 阈值（用户给定）
CONCENTRATION_THRESHOLD = 45.0   # 成交集中度 ≥45% → 抱团风险
OVERHEAT_THRESHOLD = 15.0        # 偏高占比 ≥15% → 结构性过热
TOP_PCT = 0.05                   # 前 5% 个股
OVERHEAT_GAIN = 20.0             # 近 60 日涨幅 >20% 算「股价偏高」（绝对阈值，非分位）
RET_WINDOW = 60                  # 60 日涨幅

# 历史序列默认窗口（交易日）。情绪/结构指标看半年足够；窗口过大会让现算序列过重（全市场 amount+close 拉取）
HISTORY_DAYS = 120

# 模块级缓存：{trade_date: (timestamp, payload)}，简单按日缓存（当日不变）
_cache: dict[str, tuple[float, dict]] = {}
_cache_lock = threading.Lock()
_CACHE_TTL = 3600  # 1 小时；跨日由 trade_date 键天然失效


def _latest_complete_trade_date() -> str | None:
    """最新完整交易日：取 stock_market_daily 里「个股数最充足」的最近交易日。

    盘后采集进行中时，MAX(trade_date) 当天个股数可能偏少（未采集完），
    因此取最近 3 个交易日中个股数最多的那天作为「完整交易日」，避免用半成品算指标。
    """
    rows = query_all(
        "SELECT trade_date, COUNT(*) AS n FROM stock_market_daily "
        "GROUP BY trade_date ORDER BY trade_date DESC LIMIT 3"
    )
    if not rows:
        return None
    best = max(rows, key=lambda r: r["n"])
    return str(best["trade_date"])


def _concentration(trade_date: str) -> float | None:
    """成交集中度（%）：前 5% 个股成交额占比。"""
    rows = query_all(
        "SELECT amount FROM stock_market_daily WHERE trade_date = %s AND amount IS NOT NULL",
        [trade_date],
    )
    amounts = [float(r["amount"]) for r in rows if r["amount"]]
    if not amounts:
        return None
    amounts.sort(reverse=True)
    total = sum(amounts)
    if total <= 0:
        return None
    top_n = max(1, int(round(len(amounts) * TOP_PCT)))
    top_sum = sum(amounts[:top_n])
    return round(top_sum / total * 100, 2)


def _overheat_ratio(trade_date: str) -> float | None:
    """股价偏高个股占比（%）：近 60 日涨幅 >90% 分位的个股占当日全市场比例。

    优化：不全表窗口函数（stock_market_daily 5955 只 × 30 年太重）。
    改为「先取目标日之前第 60 个交易日 t0，再取 t0 与当日两天的收盘价做对比」，
    只扫 2 个交易日的行（约 1 万行），代价可忽略。
    """
    # 目标日之前（含）的第 60 个交易日
    base = query_all(
        "SELECT trade_date FROM ("
        "  SELECT DISTINCT trade_date FROM stock_market_daily "
        "  WHERE trade_date <= %s ORDER BY trade_date DESC LIMIT %s"
        ") t ORDER BY trade_date ASC LIMIT 1",
        [trade_date, RET_WINDOW],
    )
    if not base:
        return None
    t0 = str(base[0]["trade_date"])

    # 当日收盘价 + 60 日前收盘价，按股票对齐
    cur_rows = query_all(
        "SELECT stock_code, close FROM stock_market_daily WHERE trade_date = %s",
        [trade_date],
    )
    base_rows = query_all(
        "SELECT stock_code, close FROM stock_market_daily WHERE trade_date = %s",
        [t0],
    )
    base_map = {r["stock_code"]: float(r["close"]) for r in base_rows if r["close"]}
    gains: list[float] = []
    for r in cur_rows:
        c0 = base_map.get(r["stock_code"])
        c = float(r["close"]) if r["close"] is not None else None
        if c0 and c and c0 > 0:
            gains.append((c / c0 - 1) * 100)
    if not gains:
        return None
    # 「偏高」= 近 60 日涨幅超绝对阈值（>20%），占比 = 超阈值个股数 / 全市场
    overheated = sum(1 for g in gains if g > OVERHEAT_GAIN)
    return round(overheated / len(gains) * 100, 2)


def _sentiment_raw() -> dict:
    """现算当前值（带缓存）。"""
    td = _latest_complete_trade_date()
    if not td:
        return {"empty": True, "note": "暂无个股日线数据"}
    with _cache_lock:
        hit = _cache.get(td)
        if hit and time.time() - hit[0] < _CACHE_TTL:
            return hit[1]

    conc = _concentration(td)
    over = _overheat_ratio(td)

    # 换手率/涨停家数（物化表现成列）
    mrow = query_all(
        "SELECT turnover_med, limit_up, limit_down, trade_date "
        "FROM market_style_daily WHERE turnover_med IS NOT NULL OR limit_up IS NOT NULL "
        "ORDER BY trade_date DESC LIMIT 1"
    )
    turnover_med = None
    limit_up = None
    limit_down = None
    style_date = None
    if mrow:
        turnover_med = mrow[0].get("turnover_med")
        limit_up = mrow[0].get("limit_up")
        limit_down = mrow[0].get("limit_down")
        style_date = str(mrow[0]["trade_date"])

    payload = {
        "empty": False,
        "as_of": td,
        "concentration": {
            "value": conc,
            "threshold": CONCENTRATION_THRESHOLD,
            "note": "前 5% 个股成交额占全市场比例：低=资金分散；≥45% 抱团风险（资金高度集中少数个股）",
        },
        "overheat_ratio": {
            "value": over,
            "threshold": OVERHEAT_THRESHOLD,
            "note": "近 60 日涨幅 >20% 的个股占比：低=未脱离性价比；≥15% 结构性过热",
        },
        "turnover": {
            "value": turnover_med,
            "unit": "%",
            "date": style_date,
            "note": "全市场个股换手率中位数：温和=常态；异常放大=短期过热",
        },
        "limit_up": {
            "value": limit_up,
            "limit_down": limit_down,
            "date": style_date,
            "note": "涨停家数（跌停为参考）：温和=常态；异常放大=短期过热",
        },
    }
    with _cache_lock:
        _cache[td] = (time.time(), payload)
    return payload


_history_cache: dict[str, dict] = {}
_history_cache_lock = threading.Lock()
_HISTORY_CACHE_TTL = 600  # 10 分钟；当日历史序列不变，按最新交易日缓存


def _history_cached(as_of: str) -> dict:
    """历史序列带缓存：按最新交易日缓存，当日不变则不重算（接口响应从 ~8s 降到 <100ms）。"""
    with _history_cache_lock:
        hit = _history_cache.get(as_of)
        if hit:
            return hit
    result = _history()
    with _history_cache_lock:
        _history_cache[as_of] = result
    return result


def _history() -> dict:
    """近 HISTORY_DAYS 日的成交集中度 / 偏高占比 / 换手率 / 涨停 序列（供曲线）。

    性能关键：不用 SQL 窗口函数（stock_market_daily 大表 LAG 排序太慢，实测 51s），
    改为「拉取最近窗口的 amount/close 到内存，Python 按日分组」：
    - 成交集中度：最近 N 日所有 amount，按日分组排序取前 5% 占比。
    - 偏高占比：拉最近 N+60 日的 close，Python 按 (stock_code) 排序后算 60 日涨幅，再按日分组。
    - 换手率/涨停直接读物化表。
    窗口取 HISTORY_DAYS（默认 120），偏高涨幅需回溯 60 日故多拉 RET_WINDOW 日。
    """
    # 换手率/涨停序列（物化表，一次取全）
    style_rows_all = query_all(
        "SELECT trade_date, turnover_med, limit_up FROM market_style_daily "
        "ORDER BY trade_date ASC"
    )
    style_by_date = {str(r["trade_date"]): r for r in style_rows_all}
    days = [str(r["trade_date"]) for r in style_rows_all[-HISTORY_DAYS:]]
    turnover_series = [
        float(style_by_date[d]["turnover_med"]) if d in style_by_date and style_by_date[d].get("turnover_med") is not None else None
        for d in days
    ]
    limit_up_series = [
        int(style_by_date[d]["limit_up"]) if d in style_by_date and style_by_date[d].get("limit_up") is not None else None
        for d in days
    ]
    if not days:
        return {"dates": [], "concentration": [], "overheat_ratio": [], "turnover": [], "limit_up": []}

    # 数据起点：days 首日往前再退 RET_WINDOW 日（保证每只股票都能算到 60 日前基准）
    lookback_start = query_all(
        "SELECT MIN(trade_date) AS d FROM (SELECT DISTINCT trade_date FROM market_style_daily "
        "ORDER BY trade_date DESC LIMIT %s) x",
        [HISTORY_DAYS + RET_WINDOW],
    )
    if not lookback_start or not lookback_start[0]["d"]:
        return {"dates": days, "concentration": [None]*len(days), "overheat_ratio": [None]*len(days), "turnover": turnover_series, "limit_up": limit_up_series}
    start_date = str(lookback_start[0]["d"])

    # —— 一次拉取窗口内 amount + close（供集中度/偏高占比） ——
    rows = query_all(
        "SELECT stock_code, trade_date, amount, close FROM stock_market_daily "
        "WHERE trade_date >= %s AND amount IS NOT NULL",
        [start_date],
    )
    by_date_amounts: dict[str, list[float]] = {}
    by_code_close: dict[str, list[tuple[str, float]]] = {}
    for r in rows:
        d = str(r["trade_date"])
        if r["amount"] is not None:
            by_date_amounts.setdefault(d, []).append(float(r["amount"]))
        if r["close"] is not None:
            by_code_close.setdefault(r["stock_code"], []).append((d, float(r["close"])))

    # 成交集中度按日
    conc_by_date: dict[str, float | None] = {}
    for d, amounts in by_date_amounts.items():
        amounts.sort(reverse=True)
        total = sum(amounts)
        top_n = max(1, int(round(len(amounts) * TOP_PCT)))
        conc_by_date[d] = round(sum(amounts[:top_n]) / total * 100, 2) if total > 0 else None

    # 偏高占比按日：每只股票按日期排序，算 60 日涨幅
    by_date_gains: dict[str, list[float]] = {}
    for code, series in by_code_close.items():
        series.sort(key=lambda x: x[0])  # 按日期升序
        for i in range(RET_WINDOW, len(series)):
            d, c = series[i]
            c0 = series[i - RET_WINDOW][1]
            if c0 > 0:
                by_date_gains.setdefault(d, []).append((c / c0 - 1) * 100)
    over_by_date: dict[str, float | None] = {}
    for d, gains in by_date_gains.items():
        if not gains:
            over_by_date[d] = None
            continue
        overheated = sum(1 for g in gains if g > OVERHEAT_GAIN)
        over_by_date[d] = round(overheated / len(gains) * 100, 2)

    conc_series = [conc_by_date.get(d) for d in days]
    over_series = [over_by_date.get(d) for d in days]

    return {
        "dates": days,
        "concentration": conc_series,
        "overheat_ratio": over_series,
        "turnover": turnover_series,
        "limit_up": limit_up_series,
    }


def market_sentiment() -> dict:
    """市场情绪与交易结构（当前值 + 历史序列）。"""
    cur = _sentiment_raw()
    if cur.get("empty"):
        return cur
    cur["history"] = _history_cached(cur["as_of"])
    return cur
