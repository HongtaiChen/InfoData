#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""分析模块共用的 KPI / 统计口径助手（2026-09-19 新增，为 Batch C 三个新模块抽公共件）

**为什么要有这个文件**
`market_wind.py` 与 `sector_rotation.py` 各自内联了一份 `_f/_r/_median/_std/_pctile/
_is_extreme/_scale`。两个模块时是"可接受的重复"，本轮要一次新增三个模块，若继续各自
内联就变成 5 份复制粘贴 —— 而 `_scale` 承担的是**卡片墙契约第 ⑤ 条**（`label` 窗口口径必须
随响应下发），口径一旦分叉就是实打实的错误，不是文案问题。

⚠️ **本次刻意不改 market_wind / sector_rotation**：它们是已上线并通过截图验收的模块，
   为了消重去动它们的取数与装配逻辑，收益远小于回归风险。新模块统一用本文件；
   老模块待后续有其它改动需求时再顺路迁移。

**唯一真源约定**：`scale()` 的 `label`（窗口口径文案）只能由调用方以后端已知的实际窗口传入，
绝不允许前端写死"近一年"—— ERP 的分位窗口是"近 250 个月末"，把月频分位说成日频就是口径错误。
"""
from __future__ import annotations


def f(v) -> float | None:
    """Decimal / None / 数值 → float | None（取数层的 Decimal 统一在这里过一遍）"""
    return None if v is None else float(v)


def r(v, n: int = 2):
    """四舍五入到 n 位，None 透传"""
    return None if v is None else round(float(v), n)


def median(vals: list[float]) -> float | None:
    v = sorted(vals)
    return None if not v else v[len(v) // 2]


def std(vals: list[float]) -> float | None:
    if len(vals) < 2:
        return None
    m = sum(vals) / len(vals)
    return (sum((x - m) ** 2 for x in vals) / len(vals)) ** 0.5


def corr(xs: list[float], ys: list[float]) -> float | None:
    """皮尔逊相关系数（长度为 0/1 或任一侧无方差时返回 None）"""
    n = len(xs)
    if n < 2 or n != len(ys):
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / n
    sx = (sum((x - mx) ** 2 for x in xs) / n) ** 0.5
    sy = (sum((y - my) ** 2 for y in ys) / n) ** 0.5
    if sx < 1e-12 or sy < 1e-12:
        return None
    return cov / (sx * sy)


def pctile(vals: list[float], cur: float | None, ndigits: int = 1) -> float | None:
    """cur 在 vals 中的分位（0~100，用 `<=` 计秩，并列取偏高分位）

    `vals` 应已包含 cur 本身（与 `market_wind._pctile` 的口径一致）；
    窗口由调用方决定并负责把窗口文案传给 `scale()`。
    """
    if cur is None or not vals:
        return None
    le = sum(1 for v in vals if v <= cur)
    return round(le / len(vals) * 100, ndigits)


def is_extreme(pct: float | None) -> bool:
    """分位进入极值区（<=10 或 >=90）→ 前端金色高亮。亮得少才算信号。"""
    return pct is not None and (pct <= 10 or pct >= 90)


def scale(pct: float | None, label: str) -> dict | None:
    """KPI 数字的分位刻度（轨道 + 圆点）

    ⚠️ `label` 必须是后端掌握的**实际窗口口径**（如 "近一年" / "近 36 个月" /
       "近 250 个月末"），由后端随分位一起下发，前端只透传渲染、绝不写死。
    ⚠️ 没有历史序列的指标**宁可不下发 scale**（前端优雅降级不渲染刻度条），
       也不要拿其它量纲硬凑一根刻度。
    """
    if pct is None:
        return None
    return {"pct": pct, "label": label}
