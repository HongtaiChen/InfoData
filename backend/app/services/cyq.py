#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""筹码分布计算（复刻东财 CYQ 三角分布 + 换手率衰减模型）

背景（2026-10-20）：
  用户要「筹码分布（B 方案）」。探查结论：网上**没有可直接拉的筹码数据**——
  东方财富 / akshare 的 `stock_cyq_em` 拉的是普通日 K 线（含换手率 hsl），
  筹码分布是 akshare 用东财同款 JS 算法（CYQCalculator）**在本地算出来的**。
  该算法是业界标准的「换手率衰减三角分布」模型，等价于东财页面显示的筹码。

  而本地 `stock_market_daily` 已具备 OHLCV + turnover_ratio（换手率 %），
  故本模块**纯库内离线计算**，不联网、不依赖东财 push2his 接口（该接口在当前
  环境被代理拦截）。同时绕开了「指数自身量 vs 全市场量」的口径坑——筹码用个股
  自身换手率，天然正确。

算法（与东财 stock_cyq_em 的 CYQCalculator 对齐）：
  1. 取最近 N 根（默认 120）日 K 线（不复权 close/high/low + 换手率）。
  2. 价格域：窗口内 [min_low, max_high] 均分成 BINS(150) 个桶。
  3. 逐日递推：
     a. 当日所有桶的筹码先按 (1 - 换手率) 衰减（老筹码被当日成交「换手」掉）。
     b. 再按当日 OHLC 的三角形分布，把「当日换手的那部分筹码」堆到 [low, high]
        区间内（顶点在均价 avg=(o+h+l+c)/4 处，一字板按矩形）。
  4. 逐日算指标：
     - 获利比例 = 现价(close)以下的筹码占比
     - 平均成本 = 累计筹码 50% 分位所在价位
     - 90%/70% 成本区间 = 覆盖 90%/70% 筹码的最窄价格区间
     - 90%/70% 集中度 = 区间宽度 / 区间中点（东财口径：(高-低)/(高+低)*2*100）

输出：每个交易日一行，字段同 stock_cyq_em 的列名（获利比例/平均成本/90成本-低/
90成本-高/90集中度/70成本-低/70成本-高/70集中度）。

幂等性：纯函数，输入确定输出确定；采集器负责落地（近 90 交易日全量重建）。
"""
from __future__ import annotations

from typing import Sequence

# 计算窗口（东财 range=120 根 K 线参与筹码分布）
WINDOW = 120
# 价格桶数（东财 factor=150）
BINS = 150


def _triangle_distribute(buckets: list[float], low: float, high: float, avg: float,
                         min_price: float, accuracy: float, turnover: float) -> None:
    """把「当日换手的那部分筹码」按三角形分布堆到 [low, high] 区间。

    buckets：累计筹码数组（长度 BINS，索引对应价格桶）。
    三角分布：low→avg 线性上升，avg→high 线性下降，面积精确归一化到 turnover。

    ⚠️ 实现教训（2026-10-20 实测踩坑）：原「顶点高度 × 分段斜率」写法
    `(cur_price-low)/(avg-low)*norm*turnover` 在 avg 靠近 high/low、或离散桶与
    continuous 边界错位时，会出现 (high-cur_price) 为负 → 筹码变负 → total<0 →
    获利比例爆出 ±几万%。改用「每个桶做三角形线性函数的精确积分」，数学上保证
    面积恒 = turnover 且逐桶非负。
    """
    if turnover <= 0:
        return
    if high <= low:  # 一字板：全部筹码堆在均价所在桶
        idx = int((avg - min_price) / accuracy)
        idx = max(0, min(BINS - 1, idx))
        buckets[idx] += turnover
        return
    # 三角形顶点高度（面积归一化：底 (high-low)、高 h_peak ⇒ 面积 = (high-low)*h_peak/2 = 1）
    h_peak = 2.0 / (high - low)
    lo_idx = int((low - min_price) / accuracy)
    hi_idx = int((high - min_price) / accuracy)
    lo_idx = max(0, lo_idx)
    hi_idx = min(BINS - 1, hi_idx)
    # avg 可能在 [low, high] 之外（极端 open/close 时），clamp 进区间防符号翻转
    peak = min(max(avg, low), high)
    for j in range(lo_idx, hi_idx + 1):
        # 该桶覆盖的价格区间 [p0, p1]
        p0 = min_price + accuracy * j
        p1 = p0 + accuracy
        # 三角形函数 f(p)：p<=peak 上升段 (p-low)/(peak-low)；p>peak 下降段 (high-p)/(high-peak)
        # 对该桶区间 [max(p0,low), min(p1,high)] 求 f(p) 的精确积分
        a = max(p0, low)
        b = min(p1, high)
        if b <= a:
            continue
        if b <= peak:
            # 完全在上升段：∫ (p-low)/(peak-low) dp
            fa = (a - low) / (peak - low) if peak > low else 0.0
            fb = (b - low) / (peak - low) if peak > low else 0.0
            area = 0.5 * (fa + fb) * (b - a)
        elif a >= peak:
            # 完全在下降段：∫ (high-p)/(high-peak) dp
            fa = (high - a) / (high - peak) if high > peak else 0.0
            fb = (high - b) / (high - peak) if high > peak else 0.0
            area = 0.5 * (fa + fb) * (b - a)
        else:
            # 跨越顶点：分两段
            fa_l = (a - low) / (peak - low) if peak > low else 0.0
            area_l = 0.5 * (fa_l + 1.0) * (peak - a)
            fb_r = (high - b) / (high - peak) if high > peak else 0.0
            area_r = 0.5 * (1.0 + fb_r) * (b - peak)
            area = area_l + area_r
        buckets[j] += area * h_peak * turnover


def _price_range(buckets: Sequence[float], min_price: float, accuracy: float,
                 frac: float) -> tuple[float, float]:
    """覆盖 frac（如 0.9/0.7）筹码的最窄价格区间 [low, high]。"""
    total = sum(buckets)
    if total <= 0:
        return (0.0, 0.0)
    target = total * frac
    best = (min_price, min_price + accuracy * (BINS - 1))
    best_width = float("inf")
    # 双指针滑窗找覆盖 target 筹码的最窄连续区间
    left = 0
    window_sum = 0.0
    for right in range(BINS):
        window_sum += buckets[right]
        while left <= right and window_sum - buckets[left] >= target:
            window_sum -= buckets[left]
            left += 1
        if window_sum >= target:
            width = (right - left) * accuracy
            if width < best_width:
                best_width = width
                best = (min_price + left * accuracy, min_price + right * accuracy)
    return best


def _concentration(lo: float, hi: float) -> float | None:
    """集中度 = 区间宽度 / 区间中点 × 100（东财口径）。"""
    if lo is None or hi is None or (hi + lo) == 0:
        return None
    return round((hi - lo) / (hi + lo) * 2 * 100, 2)


def compute_cyq(rows: Sequence[dict], window: int = WINDOW) -> list[dict]:
    """对一段日 K 线（升序）逐日计算筹码指标。

    rows: list[dict]，每项需含 open/high/low/close/turnover（换手率 %，如 2.5 表示 2.5%）。
    返回：list[dict]，与输入等长，字段 date/获利比例/平均成本/90成本-低/90成本-高/
    90集中度/70成本-低/70成本-高/70集中度。
    """
    n = len(rows)
    if n == 0:
        return []
    # 预提取数值
    opens = [float(r["open"]) for r in rows]
    highs = [float(r["high"]) for r in rows]
    lows = [float(r["low"]) for r in rows]
    closes = [float(r["close"]) for r in rows]
    turnovers = [max(0.0, min(1.0, (float(r["turnover"] or 0) / 100.0))) for r in rows]

    out: list[dict] = []
    for i in range(n):
        start = max(0, i - window + 1)
        # 当前窗口的价格域
        seg_high = max(highs[start:i + 1])
        seg_low = min(lows[start:i + 1])
        if seg_high == seg_low:
            seg_high += 0.01  # 一字板全程的退化保护
        accuracy = max(0.01, (seg_high - seg_low) / (BINS - 1))
        min_price = seg_low

        buckets = [0.0] * BINS
        # 逐日递推（窗口内）
        for k in range(start, i + 1):
            # ① 老筹码按换手率衰减
            for b in range(BINS):
                buckets[b] *= (1.0 - turnovers[k])
            # ② 当日三角分布叠加
            o, h, l, c = opens[k], highs[k], lows[k], closes[k]
            avg = (o + h + l + c) / 4.0
            _triangle_distribute(buckets, l, h, avg, min_price, accuracy, turnovers[k])

        # 当日指标
        total = sum(buckets)
        cur_close = closes[i]
        benefit = 0.0
        cum = 0.0
        avg_cost = None
        for b in range(BINS):
            price = min_price + b * accuracy
            if price <= cur_close:
                benefit += buckets[b]
            cum += buckets[b]
            if avg_cost is None and total > 0 and cum >= total * 0.5:
                avg_cost = price
        benefit_part = round(benefit / total * 100, 2) if total > 0 else 0.0

        p90_low, p90_high = _price_range(buckets, min_price, accuracy, 0.9)
        p70_low, p70_high = _price_range(buckets, min_price, accuracy, 0.7)

        out.append({
            "date": rows[i].get("date"),
            "获利比例": benefit_part,
            "平均成本": round(avg_cost, 2) if avg_cost is not None else None,
            "90成本-低": round(p90_low, 2),
            "90成本-高": round(p90_high, 2),
            "90集中度": _concentration(p90_low, p90_high),
            "70成本-低": round(p70_low, 2),
            "70成本-高": round(p70_high, 2),
            "70集中度": _concentration(p70_low, p70_high),
        })
    return out
