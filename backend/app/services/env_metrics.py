#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
投资环境模块 · 纯计算层（env_metrics）—— 无 IO、可单测

职责：把「国内指数」技术面 / 基本面的所有指标计算集中在这里，口径单一定义，
供 env_derive（物化）与 env_service（读取/分位）共同调用，杜绝前后端复算漂移。

设计约束（对应 docs/design/投资环境模块_设计方案_v0.2_2026-10-18.md）：
  - 结论由观看者判断，本层**只产出客观数值**，不产出「低估/高估/偏强/偏弱」等定性词；
  - 分位 = 当前值在窗口内的排位（0=最低，100=最高），是「位置参考」而非结论。

全部函数输入为 pandas Series/DataFrame（调用方负责取数与排序），输出数值或 Series。
依赖 numpy / pandas（后端已有）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# 常量：多窗口涨跌幅的 12 个窗口（交易日数）
#   半月=10 / 一月=21 / 一个半月=32 / 两月=42 / 两个半月=53 / 三月=63 /
#   六月=126 / 一年=252 / 本年(YTD) / 本季(QTD) / 本月(MTD) / 本周(WTD)
# 后四者按自然日切（周/月/季/年），非固定交易日数，故单独处理。
# ---------------------------------------------------------------------------
WINDOW_DAYS = [
    ("win_halfm", 10), ("win_1m", 21), ("win_15m", 32), ("win_2m", 42),
    ("win_25m", 53), ("win_3m", 63), ("win_6m", 126), ("win_1y", 252),
]


def pct_rank(current: float, history: np.ndarray) -> float | None:
    """当前值在历史序列中的分位（0~100）。历史为空或当前为空返回 None。

    用「小于当前值的占比」计算（与 pandas rank(pct=True) 的 average 口径略有差异，
    这里取 (strictly_less + 0.5*equal) / n，等价于 numpy 常见分位定义，稳定可复现）。
    """
    if current is None or history is None or len(history) == 0:
        return None
    arr = np.asarray(history, dtype=float)
    arr = arr[~np.isnan(arr)]
    if len(arr) == 0:
        return None
    less = float((arr < current).sum())
    equal = float((arr == current).sum())
    return round(100.0 * (less + 0.5 * equal) / len(arr), 2)


def ema(s: pd.Series, span: int) -> pd.Series:
    """指数移动平均（span 周期，与国内软件 MACD 常用口径一致）"""
    return s.ewm(span=span, adjust=False).mean()


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    """MACD：返回 (dif, dea, hist)。hist 按国内惯例 = (dif - dea) * 2。"""
    dif = ema(close, fast) - ema(close, slow)
    dea = ema(dif, signal)
    hist = (dif - dea) * 2
    return dif, dea, hist


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """RSI（Wilder 平滑口径）。"""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).where(avg_loss != 0, 100.0)


def boll(close: pd.Series, period: int = 20, k: float = 2.0):
    """布林带：返回 (upper, mid, lower)。"""
    mid = close.rolling(period, min_periods=period).mean()
    std = close.rolling(period, min_periods=period).std(ddof=0)
    return mid + k * std, mid, mid - k * std


def drawdown(close: pd.Series) -> pd.Series:
    """回撤序列（水下曲线）：当前值相对历史最高点的跌幅(%)，非负表示下跌幅度。"""
    cummax = close.cummax()
    return (close / cummax - 1) * 100  # ≤ 0，负值 = 回撤幅度


def max_drawdown(close: pd.Series) -> float | None:
    """区间内最大回撤(%)（返回负值，如 -23.5 表示最大回撤 23.5%）。"""
    if close is None or len(close.dropna()) == 0:
        return None
    dd = drawdown(close)
    m = dd.min()
    return None if pd.isna(m) else round(float(m), 4)


def annualized_vol(close: pd.Series, window: int = 60) -> pd.Series:
    """年化波动率(%)：日收益率滚动标准差 × sqrt(252) × 100。"""
    ret = close.pct_change()
    return ret.rolling(window, min_periods=window).std(ddof=0) * np.sqrt(252) * 100


def downside_vol(close: pd.Series, window: int = 60) -> pd.Series:
    """下行波动率(%)：只取负收益率算滚动标准差 × sqrt(252) × 100。"""
    ret = close.pct_change()
    downside = ret.where(ret < 0, 0.0)
    return downside.rolling(window, min_periods=window).std(ddof=0) * np.sqrt(252) * 100


def rolling_beta(close: pd.Series, bench: pd.Series, window: int = 60) -> pd.Series:
    """滚动 Beta：Cov(ret, bench_ret) / Var(bench_ret)。"""
    r = close.pct_change()
    b = bench.pct_change()
    cov = r.rolling(window, min_periods=window).cov(b)
    var = b.rolling(window, min_periods=window).var(ddof=0)
    return cov / var.replace(0, np.nan)


def rolling_corr(a: pd.Series, b: pd.Series, window: int = 120) -> pd.Series:
    """滚动相关系数。"""
    return a.pct_change().rolling(window, min_periods=window).corr(b.pct_change())


def window_return(close: pd.Series, n: int) -> pd.Series:
    """N 日涨跌幅(%)：close_t / close_{t-n} - 1。"""
    return (close / close.shift(n) - 1) * 100


def period_return(close: pd.Series, mode: str) -> pd.Series:
    """按自然周期（week/month/quarter/year）累计涨跌幅(%)。

    mode ∈ {week, month, quarter, year}。用「期初收盘 → 当前收盘」，
    期初 = 同一周期内第一个交易日的**前一日收盘**（保证含首个交易日自身涨幅）。
    """
    idx = pd.to_datetime(close.index)
    if mode == "week":
        grp = idx.to_period("W")
    elif mode == "month":
        grp = idx.to_period("M")
    elif mode == "quarter":
        grp = idx.to_period("Q")
    else:
        grp = idx.to_period("Y")

    s = pd.Series(close.values, index=grp)
    first = s.groupby(level=0).transform("first")
    # 期初基准：前一周期最后一个交易日收盘
    prev_close = close.shift(1)
    prev = pd.Series(prev_close.values, index=grp)
    base = prev.groupby(level=0).transform("first")
    out = (s / base - 1) * 100
    return pd.Series(out.values, index=close.index)


def quantile_suggested_threshold(close: pd.Series, years: float = 3.0,
                                 q: float = 0.95) -> float | None:
    """「大幅下跌阈值」建议值：近 N 年日跌幅的 q 分位（取绝对值，%）。

    E4 决策：未手定阈值的指数，用「近 3 年日跌幅 95 分位」算出建议阈值，
    口径标注为统计事实而非 AI 判断。日跌幅用 -change_pct 的 q 分位。
    """
    ret = close.pct_change() * 100  # 日涨跌幅(%)
    ret = ret.dropna()
    if len(ret) == 0:
        return None
    cutoff = ret.index[-1] - pd.Timedelta(days=int(years * 365.25))
    recent = ret[ret.index >= cutoff]
    if len(recent) < 30:
        recent = ret
    losses = -recent[recent < 0]
    if len(losses) == 0:
        return None
    return round(float(losses.quantile(q)), 3)


def aggregate_financial(frames: dict[str, pd.DataFrame]) -> dict[str, float | None]:
    """成分股财务聚合（近一报告期截面）。

    输入 frames：{metric: DataFrame}，每张 DataFrame 至少含 report_date 与 value 两列
    （按成分股逐行）。取**最新报告期**截面，聚合口径：
      - roe：市值无关，取中位数（抗极端值，与换手率中位同思路）
      - net_profit_yoy / revenue_yoy：取中位数（同比增速也是比率，中位抗极端）
      - net_profit_sum / revenue_sum：求和
    返回 dict，含 constituent_count。

    由 env_derive 组装 frames（从 stock_financial_abstract_ths + index_constituents 取数），
    本函数只做纯聚合，便于单测。
    """
    out: dict[str, float | None] = {
        "roe": None, "net_profit_yoy": None, "revenue_yoy": None,
        "net_profit_sum": None, "revenue_sum": None, "constituent_count": None,
    }
    latest = None
    for metric, df in frames.items():
        if df is None or len(df) == 0:
            continue
        d = df["report_date"].max()
        latest = d if latest is None or d > latest else latest
    if latest is None:
        return out

    counts: set[int] = set()
    for metric, df in frames.items():
        if df is None or len(df) == 0:
            continue
        sub = df[df["report_date"] == latest]
        counts.add(len(sub))
        vals = sub["value"].astype(float).replace([np.inf, -np.inf], np.nan).dropna()
        if len(vals) == 0:
            continue
        if metric in ("roe", "net_profit_yoy", "revenue_yoy"):
            out[metric] = round(float(vals.median()), 4)
        elif metric in ("net_profit_sum", "revenue_sum"):
            out[metric] = round(float(vals.sum()), 2)
    out["constituent_count"] = max(counts) if counts else None
    return out
