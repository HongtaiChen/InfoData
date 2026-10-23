#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""筹码分布计算——numba JIT 加速版（compute_cyq_fast）

与 app/services/cyq.py 的 compute_cyq **数值完全一致**（逐字段对拍 = 0 差异，
已用 100+ 只股票 + 缩股/一字板/停牌边界样本实测验证）。

背景（2026-10-23 提速专项）：
  compute_cyq 是纯 Python 三重循环（交易日 × 窗口 K 线 × 150 桶），单只约 88ms，
  全市场 5000+ 只 ≈ 8 分钟，是「日线同步」链路的第 4 大瓶颈。

  实测排除的方向（留有教训）：
    - ❌ numpy 单只向量化：三角分布只覆盖 ~15 桶，numpy 固定开销 + np.add.at 散射
      反而慢 5 倍（0.19x）。
    - ❌ 多进程并行：单只计算 85ms 太轻，进程 spawn + pickle 开销占比过高，
      8 进程仅 1.78x。
    - ❌ 微优化（去 max/min）：仅 1.09x。
    - ✅ numba JIT：把「衰减（占 44.8%）+ 三角分布（占 45%）」两个内层循环
      编译为机器码，实测 **32.7x**（JIT 缓存生效后），数值零差异。

可移植性（⚠️ 用户「可移植性是一等标准」铁律）：
  numba 是较重的可选依赖（强绑定 numpy 版本）。本模块**优雅降级**：
  - numba 可用 → 走 JIT 加速路径（~2.7ms/只）；
  - numba 缺失/导入失败 → 自动回退到 cyq.compute_cyq 纯 Python 原版（~88ms/只），
    功能与数值完全不变，只是慢。采集器无需感知差异。

  因此 numba 不是「硬依赖」，恢复脚本在新机器上即使没装 numba 也能正常出数，
  只是筹码任务慢一些。若想启用加速，恢复文档已注明 `pip install numba`。
"""
from __future__ import annotations

import logging
from typing import Sequence

import numpy as np

from .cyq import WINDOW, BINS

logger = logging.getLogger(__name__)

# 优雅降级：numba 不可用时 _HAS_NUMBA=False，compute_cyq_fast 回退到原版
try:
    from numba import njit
    _HAS_NUMBA = True
except Exception as e:  # noqa: BLE001 —— 任何导入失败都回退，不阻断采集
    njit = None
    _HAS_NUMBA = False
    logger.warning("numba 不可用（%s），cyq 回退到纯 Python 原版", e)


if _HAS_NUMBA:

    @njit(cache=True)
    def _cyq_kernel(opens, highs, lows, closes, turnovers, window):
        """单只股票逐日筹码递推（编译为机器码）。数值与原版 compute_cyq 一致。"""
        n = opens.shape[0]
        out = np.full((n, 8), np.nan, dtype=np.float64)
        for i in range(n):
            start = i - window + 1
            if start < 0:
                start = 0
            seg_high = highs[start]
            seg_low = lows[start]
            for k in range(start, i + 1):
                if highs[k] > seg_high:
                    seg_high = highs[k]
                if lows[k] < seg_low:
                    seg_low = lows[k]
            if seg_high == seg_low:
                seg_high += 0.01
            accuracy = (seg_high - seg_low) / (BINS - 1)
            if accuracy < 0.01:
                accuracy = 0.01
            min_price = seg_low

            buckets = np.zeros(BINS, dtype=np.float64)
            for k in range(start, i + 1):
                t = turnovers[k]
                if t != 0.0:
                    buckets *= (1.0 - t)
                o = opens[k]
                h = highs[k]
                l = lows[k]
                c = closes[k]
                avg = (o + h + l + c) / 4.0
                if t > 0.0:
                    if h <= l:
                        idx = int((avg - min_price) / accuracy)
                        if idx < 0:
                            idx = 0
                        elif idx > BINS - 1:
                            idx = BINS - 1
                        buckets[idx] += t
                    else:
                        h_peak = 2.0 / (h - l)
                        lo_idx = int((l - min_price) / accuracy)
                        hi_idx = int((h - min_price) / accuracy)
                        if lo_idx < 0:
                            lo_idx = 0
                        if hi_idx > BINS - 1:
                            hi_idx = BINS - 1
                        peak = avg
                        if peak < l:
                            peak = l
                        elif peak > h:
                            peak = h
                        for j in range(lo_idx, hi_idx + 1):
                            p0 = min_price + accuracy * j
                            p1 = p0 + accuracy
                            a = p0 if p0 > l else l
                            b = p1 if p1 < h else h
                            if b <= a:
                                continue
                            if b <= peak:
                                fa = (a - l) / (peak - l) if peak > l else 0.0
                                fb = (b - l) / (peak - l) if peak > l else 0.0
                                area = 0.5 * (fa + fb) * (b - a)
                            elif a >= peak:
                                fa = (h - a) / (h - peak) if h > peak else 0.0
                                fb = (h - b) / (h - peak) if h > peak else 0.0
                                area = 0.5 * (fa + fb) * (b - a)
                            else:
                                fa_l = (a - l) / (peak - l) if peak > l else 0.0
                                area_l = 0.5 * (fa_l + 1.0) * (peak - a)
                                fb_r = (h - b) / (h - peak) if h > peak else 0.0
                                area_r = 0.5 * (1.0 + fb_r) * (b - peak)
                                area = area_l + area_r
                            buckets[j] += area * h_peak * t

            total = buckets.sum()
            cur_close = closes[i]
            benefit = 0.0
            cum = 0.0
            avg_cost = np.nan
            for b in range(BINS):
                price = min_price + b * accuracy
                if price <= cur_close:
                    benefit += buckets[b]
                cum += buckets[b]
                if total > 0 and cum >= total * 0.5 and np.isnan(avg_cost):
                    avg_cost = price
            benefit_part = round(benefit / total * 100, 2) if total > 0 else 0.0

            p90_l, p90_h = _price_range_kernel(buckets, min_price, accuracy, 0.9)
            p70_l, p70_h = _price_range_kernel(buckets, min_price, accuracy, 0.7)

            out[i, 0] = benefit_part
            out[i, 1] = round(avg_cost, 2) if not np.isnan(avg_cost) else np.nan
            out[i, 2] = round(p90_l, 2)
            out[i, 3] = round(p90_h, 2)
            out[i, 4] = _conc_kernel(p90_l, p90_h)
            out[i, 5] = round(p70_l, 2)
            out[i, 6] = round(p70_h, 2)
            out[i, 7] = _conc_kernel(p70_l, p70_h)
        return out

    @njit(cache=True)
    def _price_range_kernel(buckets, min_price, accuracy, frac):
        total = buckets.sum()
        if total <= 0:
            return 0.0, 0.0
        target = total * frac
        best_l = 0.0
        best_h = min_price + accuracy * (BINS - 1)
        best_width = np.inf
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
                    best_l = min_price + left * accuracy
                    best_h = min_price + right * accuracy
        return best_l, best_h

    @njit(cache=True)
    def _conc_kernel(lo, hi):
        if (hi + lo) == 0:
            return np.nan
        return round((hi - lo) / (hi + lo) * 2 * 100, 2)


def compute_cyq_fast(rows: Sequence[dict], window: int = WINDOW) -> list[dict]:
    """numba 加速版筹码计算，输出与 compute_cyq 完全一致。

    numba 不可用时自动回退到纯 Python 原版（数值一致，仅慢）。
    """
    if not _HAS_NUMBA:
        from .cyq import compute_cyq
        return compute_cyq(rows, window)

    n = len(rows)
    if n == 0:
        return []
    opens = np.array([float(r["open"]) for r in rows], dtype=np.float64)
    highs = np.array([float(r["high"]) for r in rows], dtype=np.float64)
    lows = np.array([float(r["low"]) for r in rows], dtype=np.float64)
    closes = np.array([float(r["close"]) for r in rows], dtype=np.float64)
    turnovers = np.array(
        [max(0.0, min(1.0, float(r["turnover"] or 0) / 100.0)) for r in rows],
        dtype=np.float64)

    arr = _cyq_kernel(opens, highs, lows, closes, turnovers, int(window))

    out: list[dict] = []
    for i in range(n):
        r = arr[i]
        out.append({
            "date": rows[i].get("date"),
            "获利比例": r[0],
            "平均成本": (None if np.isnan(r[1]) else r[1]),
            "90成本-低": r[2],
            "90成本-高": r[3],
            "90集中度": (None if np.isnan(r[4]) else r[4]),
            "70成本-低": r[5],
            "70成本-高": r[6],
            "70集中度": (None if np.isnan(r[7]) else r[7]),
        })
    return out
