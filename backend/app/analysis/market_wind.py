#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""分析模块：市场风向（首发跟踪型模块，数据源 market_style_daily + dc_index_market）

KPI 结论规则（首版简单化）：
- 风偏分数：与 5 个交易日前对比 → 走扩/收敛；正=偏进攻，负=偏防守
- 剪刀差：>0 小盘占优 / <0 大盘占优（叠加走扩/收敛）
- 大势位置（250 日分位）：>=80 高位 / >=60 偏高 / 40~60 中位 / 20~40 偏低 / <=20 低位
"""
from __future__ import annotations

from ..db import query_all

# 六组展示顺序与中文名
GROUP_LABELS = [
    ("bench", "市场基准"),
    ("size", "市值风格"),
    ("tech", "科技成长"),
    ("sent", "情绪温度"),
    ("div", "股息防守"),
    ("pol", "政策周期"),
]

SIZE_INDICES = [
    ("000016", "上证50", "超大盘"),
    ("000300", "沪深300", "大盘"),
    ("000905", "中证500", "中盘"),
    ("000852", "中证1000", "小盘"),
    ("932000", "中证2000", "微盘"),
]


def _num(v) -> float | None:
    return None if v is None else round(float(v), 2)


def _latest_style_row() -> dict | None:
    rows = query_all("SELECT * FROM market_style_daily ORDER BY trade_date DESC LIMIT 1")
    return rows[0] if rows else None


def _style_history(days: int) -> list[dict]:
    return query_all(
        "SELECT trade_date, scissors_20, risk_appetite_20 FROM market_style_daily "
        "ORDER BY trade_date DESC LIMIT %s",
        [days],
    )


def _offset_style_row(offset: int) -> dict | None:
    rows = query_all(
        "SELECT * FROM market_style_daily ORDER BY trade_date DESC LIMIT 1 OFFSET %s",
        [offset],
    )
    return rows[0] if rows else None


def _latest_indices() -> list[dict]:
    """21 只指数最新快照 + 20 日收益（第 20 个前一交易日收盘起算）"""
    return query_all(
        """
        SELECT i.index_code, i.index_name, i.index_group, i.group_desc,
               i.trade_date, i.close, i.change_pct,
               (i.close / (SELECT x.close FROM dc_index_market x
                            WHERE x.index_code = i.index_code AND x.trade_date < i.trade_date
                            ORDER BY x.trade_date DESC LIMIT 1 OFFSET 19) - 1) * 100 AS ret_20
        FROM dc_index_market i
        JOIN (SELECT index_code, MAX(trade_date) AS md FROM dc_index_market GROUP BY index_code) t
          ON t.index_code = i.index_code AND t.md = i.trade_date
        ORDER BY i.index_group, i.index_code
        """
    )


def _risk_status(cur: float | None, prev: float | None) -> str:
    if cur is None:
        return "--"
    if prev is None:
        return "偏进攻" if cur > 0 else "偏防守"
    delta = cur - prev
    if cur > 0:
        return "风偏升温" if delta > 0 else "进攻放缓"
    return "防守加深" if delta < 0 else "防守松动"


def _scissors_status(cur: float | None, prev: float | None) -> str:
    if cur is None:
        return "--"
    if prev is None:
        return "小盘占优" if cur > 0 else "大盘占优"
    delta = cur - prev
    side = "小盘占优" if cur > 0 else "大盘占优"
    trend = "走扩" if (delta > 0) == (cur > 0) and delta != 0 else "收敛"
    return f"{side}·{trend}" if cur != 0 else "均衡"


def _pos_status(v: float | None) -> str:
    if v is None:
        return "--"
    if v >= 80:
        return "高位"
    if v >= 60:
        return "偏高"
    if v >= 40:
        return "中位"
    if v >= 20:
        return "偏低"
    return "低位"


def market_wind(trend_days: int = 250) -> dict:
    """市场风向模块数据装配（/api/analysis/market-wind）"""
    cur_row = _latest_style_row()
    prev_row = _offset_style_row(5)
    if not cur_row:
        return {"as_of": None, "note": "market_style_daily 尚无数据，等待 market_style_sync 首跑"}

    as_of = str(cur_row["trade_date"])
    prev5 = {k: _num(prev_row.get(k)) if prev_row else None
             for k in ("risk_appetite_20", "scissors_20")}

    # KPI 结论区
    ra, sc, bp = (_num(cur_row.get("risk_appetite_20")),
                  _num(cur_row.get("scissors_20")),
                  _num(cur_row.get("bench_pos_pct")))
    kpis = [
        {"key": "risk_appetite", "label": "风偏分数（20日）", "value": ra, "unit": "pp",
         "status": _risk_status(ra, prev5.get("risk_appetite_20")),
         "hint": "科技成长组 − 股息防守组 等权20日收益差；正=偏进攻，负=偏防守"},
        {"key": "scissors", "label": "大小盘剪刀差（20日）", "value": sc, "unit": "pp",
         "status": _scissors_status(sc, prev5.get("scissors_20")),
         "hint": "(中证1000+中证2000) − (上证50+沪深300) 等权20日收益差；正=小盘占优"},
        {"key": "bench_pos", "label": "大势位置（250日分位）", "value": bp, "unit": "%",
         "status": _pos_status(bp), "hint": "中证全指在近 250 日高低区间的分位，80+ 高位 / 20- 低位"},
    ]

    # 六组收益热力条
    groups = [
        {"group": label, "ret_20": _num(cur_row.get(f"ret_{g}_20")),
         "ret_60": _num(cur_row.get(f"ret_{g}_60"))}
        for g, label in GROUP_LABELS
    ]

    # 大小盘五档梯度（最新收盘 + 当日涨跌 + 20 日收益）
    idx = _latest_indices()
    by_code = {r["index_code"]: r for r in idx}
    size_gradient = [
        {"code": c, "name": n, "desc": d,
         "ret_20": _num(by_code[c]["ret_20"]) if c in by_code else None,
         "change_pct": _num(by_code[c]["change_pct"]) if c in by_code else None}
        for c, n, d in SIZE_INDICES
    ]

    # 轮动时序（近 trend_days 个交易日）
    hist = _style_history(trend_days)
    hist.reverse()
    trend = {
        "dates": [str(r["trade_date"]) for r in hist],
        "scissors": [_num(r.get("scissors_20")) for r in hist],
        "risk_appetite": [_num(r.get("risk_appetite_20")) for r in hist],
    }

    # 明细：21 只指数
    detail = [
        {
            "index_code": r["index_code"], "index_name": r["index_name"],
            "index_group": r["index_group"], "group_desc": r["group_desc"],
            "close": _num(r["close"]), "change_pct": _num(r["change_pct"]),
            "ret_20": _num(r["ret_20"]),
        }
        for r in idx
    ]

    return {"as_of": as_of, "kpis": kpis, "groups": groups,
            "size_gradient": size_gradient, "trend": trend, "detail": detail}
