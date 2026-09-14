#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""分析模块：市场风向（首发跟踪型模块，数据源 market_style_daily + dc_index_market）

KPI 结论规则：
- 风偏分数：与 5 个交易日前对比 → 走扩/收敛；正=偏进攻，负=偏防守
- 剪刀差：>0 小盘占优 / <0 大盘占优（叠加走扩/收敛）
- 情绪温度：证券公司 − 中证全指 20 日超额；正=情绪偏暖
- 政策敏感：中证全指房地产 − 中证全指 20 日超额；正=政策板块占优
- 大势位置（250 日分位）：>=80 高位 / >=60 偏高 / 40~60 中位 / 20~40 偏低 / <=20 低位

分位与极值（2026-09-14 新增）：
- 每个 KPI 附 `pct` = 当前值在近 250 个交易日中的分位（0=区间最低，100=最高）。
  绝对 pp 跨期不可比（2005 年的 5pp 与现在的 5pp 意义不同），只有分位才能判断「是否极端」。
- `highlight` = 分位进入极值区（<=10 或 >=90），前端用金色渲染（「蓝骨金魂」体系里金色专供亮点信号）。
- `bench_pos_pct` 自身即 250 日分位，不再二次求分位（否则是重复信息）。

滞后检测：
- `stale_sessions` = 本表 as_of 之后还走出了几个交易日（用 stock_market_daily 当日历）。
  0 = 最新；>0 说明风格表落后于行情，前端把「数据截至」标成琥珀色（设计规范 §2.2）。
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
    """21 只指数最新快照 + 20 日收益

    2026-09-14：由「逐行相关子查询 + OFFSET 19」改为窗口函数。原写法每行都要重跑一次
    子查询取「当日之前第 20 个交易日收盘」，SQL 又脆又难读；LAG(close, 20) 与之等价
    （当前行往前数 20 行 = 当日之前第 20 个交易日），MySQL 8.2 支持。
    """
    return query_all(
        """
        SELECT t.index_code, t.index_name, t.index_group, t.group_desc,
               t.trade_date, t.close, t.change_pct,
               CASE WHEN t.c20 > 0 THEN (t.close / t.c20 - 1) * 100 END AS ret_20
        FROM (
            SELECT i.index_code, i.index_name, i.index_group, i.group_desc,
                   i.trade_date, i.close, i.change_pct,
                   LAG(i.close, 20) OVER (PARTITION BY i.index_code ORDER BY i.trade_date) AS c20,
                   ROW_NUMBER() OVER (PARTITION BY i.index_code ORDER BY i.trade_date DESC) AS rn
            FROM dc_index_market i
        ) t
        WHERE t.rn = 1
        ORDER BY t.index_group, t.index_code
        """
    )


def _pctile(col: str, window: int = 250) -> float | None:
    """当前值在近 window 个交易日中的分位（0~100）

    ⚠️ col 由本模块内部以字面量传入（白名单列名），不接受外部输入，故可用 f-string 拼接。
    """
    rows = query_all(
        f"""
        SELECT
          (SELECT COUNT(*) FROM (
             SELECT {col} AS v FROM market_style_daily
             WHERE {col} IS NOT NULL ORDER BY trade_date DESC LIMIT %s
           ) a WHERE a.v <= (SELECT {col} FROM market_style_daily
                             WHERE {col} IS NOT NULL ORDER BY trade_date DESC LIMIT 1)) AS le,
          (SELECT COUNT(*) FROM (
             SELECT {col} AS v FROM market_style_daily
             WHERE {col} IS NOT NULL ORDER BY trade_date DESC LIMIT %s
           ) b) AS n
        """,
        [window, window],
    )
    if not rows:
        return None
    le, n = rows[0]["le"], rows[0]["n"]
    if not n:
        return None
    return round(float(le) / float(n) * 100, 1)


def _is_extreme(pct: float | None) -> bool:
    """分位进入极值区（<=10 或 >=90）→ 前端用金色高亮"""
    return pct is not None and (pct <= 10 or pct >= 90)


def _stale_sessions(as_of) -> int:
    """as_of 之后还走出了几个交易日（0 = 最新）。用日线表当日历，避免周末误判。"""
    rows = query_all(
        "SELECT COUNT(DISTINCT trade_date) AS n FROM stock_market_daily WHERE trade_date > %s",
        [as_of],
    )
    return int(rows[0]["n"]) if rows else 0


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


def _sent_status(cur: float | None, prev: float | None) -> str:
    """情绪温度（证券公司 − 中证全指 20 日超额）：正 = 市场情绪偏暖"""
    if cur is None:
        return "--"
    if prev is None:
        return "情绪偏暖" if cur > 0 else "情绪偏冷"
    rising = cur - prev > 0
    if cur > 0:
        return "情绪升温" if rising else "热度回落"
    return "情绪修复" if rising else "情绪转冷"


def _policy_status(cur: float | None, prev: float | None) -> str:
    """政策敏感（中证全指房地产 − 中证全指 20 日超额）：正 = 政策敏感板块占优"""
    if cur is None:
        return "--"
    if prev is None:
        return "政策占优" if cur > 0 else "政策拖累"
    rising = cur - prev > 0
    if cur > 0:
        return "政策走强" if rising else "政策降温"
    return "政策企稳" if rising else "政策拖累"


def market_wind(trend_days: int = 250) -> dict:
    """市场风向模块数据装配（/api/analysis/market-wind）"""
    cur_row = _latest_style_row()
    prev_row = _offset_style_row(5)
    if not cur_row:
        return {"as_of": None, "note": "market_style_daily 尚无数据，等待 market_style_sync 首跑"}

    as_of = str(cur_row["trade_date"])
    prev5 = {k: _num(prev_row.get(k)) if prev_row else None
             for k in ("risk_appetite_20", "scissors_20", "sentiment_20", "policy_excess_20")}

    # KPI 结论区
    # 2026-09-14：3 → 5 项。补入 sentiment_20（情绪温度）与 policy_excess_20（政策敏感）——
    # 这两列 market_style_sync 每个交易日都在算（见该文件 120-123 行）与设计规范 §4.1 的口径表，
    # 但视图层一直没接入，属「白算」。同时每项附近 250 日分位与极值标记。
    ra, sc = _num(cur_row.get("risk_appetite_20")), _num(cur_row.get("scissors_20"))
    se, po = _num(cur_row.get("sentiment_20")), _num(cur_row.get("policy_excess_20"))
    bp = _num(cur_row.get("bench_pos_pct"))
    pct_ra, pct_sc = _pctile("risk_appetite_20"), _pctile("scissors_20")
    pct_se, pct_po = _pctile("sentiment_20"), _pctile("policy_excess_20")

    kpis = [
        {"key": "risk_appetite", "label": "风偏分数（20日）", "value": ra, "unit": "pp", "tone": "updown",
         "status": _risk_status(ra, prev5.get("risk_appetite_20")),
         "pct": pct_ra, "highlight": _is_extreme(pct_ra), "anchor": "mw-trend",
         "hint": "科技成长组 − 股息防守组 等权20日收益差；正=偏进攻，负=偏防守"},
        {"key": "scissors", "label": "大小盘剪刀差（20日）", "value": sc, "unit": "pp", "tone": "updown",
         "status": _scissors_status(sc, prev5.get("scissors_20")),
         "pct": pct_sc, "highlight": _is_extreme(pct_sc), "anchor": "mw-gradient",
         "hint": "(中证1000+中证2000) − (上证50+沪深300) 等权20日收益差；正=小盘占优"},
        {"key": "sentiment", "label": "情绪温度（20日超额）", "value": se, "unit": "pp", "tone": "updown",
         "status": _sent_status(se, prev5.get("sentiment_20")),
         "pct": pct_se, "highlight": _is_extreme(pct_se), "anchor": "mw-heat",
         "hint": "证券公司 − 中证全指 20 日超额；正=券商跑赢，视为市场情绪偏暖"},
        {"key": "policy", "label": "政策敏感（20日超额）", "value": po, "unit": "pp", "tone": "updown",
         "status": _policy_status(po, prev5.get("policy_excess_20")),
         "pct": pct_po, "highlight": _is_extreme(pct_po), "anchor": "mw-heat",
         "hint": "中证全指房地产 − 中证全指 20 日超额；正=政策敏感板块占优"},
        {"key": "bench_pos", "label": "大势位置（250日分位）", "value": bp, "unit": "%", "tone": "neutral",
         "status": _pos_status(bp), "pct": None, "highlight": False, "anchor": "mw-detail",
         "hint": "中证全指在近 250 日高低区间的分位，80+ 高位 / 20- 低位（本身即分位，不再二次求分位）"},
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

    return {"as_of": as_of, "stale_sessions": _stale_sessions(as_of),
            "kpis": kpis, "groups": groups,
            "size_gradient": size_gradient, "trend": trend, "detail": detail}
