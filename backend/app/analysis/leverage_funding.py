#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""分析模块：杠杆与资金行为（2026-10-20 新增）

三个指标（全市场口径，非单指数）：
1. 融资余额占比：融资余额 ÷ 全市场流通市值（%）。
   高 = 杠杆资金活跃、市场加杠杆；低 = 杠杆资金收缩、去杠杆。
   融资余额来自 securities_margin.rzye（沪+深+北三市合计，元）；
   流通市值 = Σ(各股流通A股 × 当日收盘价)，流通A股取 stock_shares.list_a_shares
   在该交易日前（含）最新一期生效的快照（forward-fill）。
2. ETF 资金流向：全市场股票型 ETF 的份额变化 × 净值（净申购/净赎回，亿元）。
   数据来自 etf_share_daily（采集器逐日积累），本模块只做聚合派生。
3. 新增开户：上交所股票账户新开户（个人 A 股，万户，月频）。
   数据来自 stock_account_monthly（采集器落地，官方源 + 东财历史拼接）。

口径与实现：
- 融资余额占比为「现算」：每次请求现算，模块级按最新交易日缓存。
- ETF 资金流向、新增开户为「物化表直接读」：采集器已落库，本模块只聚合。
- 参照系（registry 契约①）：融资余额占比看历史分位（自身纵比②）；
  ETF 资金流向看净流入方向与累计（结构分解⑤ + 自身纵比②）；
  新增开户看历史峰值对比（自身纵比②，判断散户情绪极端）。
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict

from ..db import query_all

# 历史序列默认窗口（交易日）
HISTORY_DAYS = 250

# 模块级缓存：{trade_date: (timestamp, payload)}
_cache: dict[str, tuple[float, dict]] = {}
_cache_lock = threading.Lock()
_CACHE_TTL = 3600  # 1 小时


def _latest_margin_date() -> str | None:
    """融资余额最新交易日。"""
    rows = query_all("SELECT MAX(trade_date) AS d FROM securities_margin")
    if not rows or not rows[0]["d"]:
        return None
    return str(rows[0]["d"])


def _load_share_snapshots() -> dict[str, list[tuple[str, float]]]:
    """一次性拉全量流通股本快照，按股票分组、按日期升序。

    返回 {stock_code: [(change_date_str, list_a_shares_float), ...]}。
    30 万行、约 1.8s，模块级不做缓存（每次现算重拉，避免长连接下内存占用）。
    """
    rows = query_all(
        "SELECT stock_code, change_date, list_a_shares FROM stock_shares "
        "WHERE list_a_shares IS NOT NULL ORDER BY stock_code, change_date"
    )
    by_code: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for r in rows:
        code = str(r["stock_code"])
        by_code[code].append((str(r["change_date"]), float(r["list_a_shares"])))
    return dict(by_code)


def _shares_at(snapshots: list[tuple[str, float]], trade_date: str) -> float | None:
    """某股票在 trade_date 生效的流通股本：≤ trade_date 的最新一条快照（forward-fill）。"""
    # 快照已按日期升序；从后往前找第一个 <= trade_date
    val = None
    for d, v in reversed(snapshots):
        if d <= trade_date:
            val = v
            break
    return val


def _float_mv(trade_date: str, share_by_code: dict) -> tuple[float, int]:
    """某交易日的全市场流通市值（元）+ 覆盖股票数。

    做法：拉当日全市场 (stock_code, close)，对每只有流通股本快照的股票
    用 forward-fill 取生效股本 × 收盘价求和。避免 SQL 相关子查询（单日 1.3s × N 日过重）。
    """
    rows = query_all(
        "SELECT stock_code, close FROM stock_market_daily "
        "WHERE trade_date = %s AND close IS NOT NULL",
        [trade_date],
    )
    total = 0.0
    n = 0
    for r in rows:
        code = str(r["stock_code"])
        snaps = share_by_code.get(code)
        if not snaps:
            continue
        shares = _shares_at(snaps, trade_date)
        if shares is None or shares <= 0:
            continue
        close = float(r["close"])
        if close > 0:
            total += shares * close
            n += 1
    return total, n


def _margin_ratio_raw() -> dict:
    """现算当前融资余额占比（带缓存）。"""
    td = _latest_margin_date()
    if not td:
        return {"empty": True, "note": "暂无融资余额数据"}

    with _cache_lock:
        hit = _cache.get(td)
        if hit and time.time() - hit[0] < _CACHE_TTL:
            return hit[1]

    share_by_code = _load_share_snapshots()
    mv, n = _float_mv(td, share_by_code)

    mrow = query_all(
        "SELECT rzye, rqye, rzrqye FROM securities_margin WHERE trade_date = %s",
        [td],
    )
    rzye = float(mrow[0]["rzye"]) if mrow and mrow[0]["rzye"] is not None else None
    rqye = float(mrow[0]["rqye"]) if mrow and mrow[0]["rqye"] is not None else None

    ratio = None
    if rzye is not None and mv > 0:
        ratio = round(rzye / mv * 100, 2)

    payload = {
        "empty": False,
        "as_of": td,
        "margin": {
            "value": rzye,
            "unit": "元",
            "ratio": ratio,
            "float_mv": round(mv, 2),
            "cover_n": n,
            "short_balance": rqye,
            "note": "融资余额占全市场流通市值比：高=加杠杆；低=去杠杆。融资余额为沪+深+北三市合计",
        },
    }
    with _cache_lock:
        _cache[td] = (time.time(), payload)
    return payload


def _margin_ratio_history(as_of: str) -> dict:
    """融资余额占比历史序列（近 HISTORY_DAYS 交易日）。

    性能：stock_shares 全量拉一次（1.8s）+ 窗口内 close 一次拉取 + securities_margin 一次拉取，
    Python 里按日算流通市值，不做 SQL 相关子查询。
    """
    margin_rows = query_all(
        "SELECT trade_date, rzye FROM securities_margin ORDER BY trade_date ASC"
    )
    if not margin_rows:
        return {"dates": [], "ratio": [], "margin": []}

    # 取窗口内的交易日（融资余额表本身的日期，可能与 stock_market_daily 日历略有差异）
    margin_days = [str(r["trade_date"]) for r in margin_rows][-HISTORY_DAYS:]
    start = margin_days[0]

    share_by_code = _load_share_snapshots()

    # 窗口内全市场 close 一次拉取
    close_rows = query_all(
        "SELECT stock_code, trade_date, close FROM stock_market_daily "
        "WHERE trade_date >= %s AND close IS NOT NULL",
        [start],
    )
    close_by_date: dict[str, dict[str, float]] = defaultdict(dict)
    for r in close_rows:
        close_by_date[str(r["trade_date"])][str(r["stock_code"])] = float(r["close"])

    margin_by_date = {str(r["trade_date"]): float(r["rzye"]) for r in margin_rows if r["rzye"] is not None}

    dates: list[str] = []
    ratios: list[float | None] = []
    margins: list[float | None] = []
    for d in margin_days:
        dates.append(d)
        rzye = margin_by_date.get(d)
        margins.append(rzye)
        closes = close_by_date.get(d)
        if rzye is None or not closes:
            ratios.append(None)
            continue
        mv = 0.0
        for code, close in closes.items():
            snaps = share_by_code.get(code)
            if not snaps:
                continue
            shares = _shares_at(snaps, d)
            if shares and shares > 0 and close > 0:
                mv += shares * close
        ratios.append(round(rzye / mv * 100, 2) if mv > 0 else None)

    return {"dates": dates, "ratio": ratios, "margin": margins}


_history_cache: dict[str, dict] = {}
_history_cache_lock = threading.Lock()
_HISTORY_CACHE_TTL = 600


def _margin_history_cached(as_of: str) -> dict:
    with _history_cache_lock:
        hit = _history_cache.get(as_of)
        if hit:
            return hit
    result = _margin_ratio_history(as_of)
    with _history_cache_lock:
        _history_cache[as_of] = result
    return result


# ---------------------------------------------------------------- ETF 资金流向（物化表直读）

def _etf_flow() -> dict:
    """ETF 资金流向：全市场股票型 ETF 份额变化 × 净值（物化表 etf_share_daily）。"""
    rows = query_all(
        "SELECT trade_date, total_share, net_inflow FROM etf_share_daily "
        "ORDER BY trade_date ASC"
    )
    if not rows:
        return {"empty": True, "note": "暂无 ETF 份额数据（采集器尚未积累）"}

    dates = [str(r["trade_date"]) for r in rows]
    shares = [float(r["total_share"]) if r["total_share"] is not None else None for r in rows]
    inflows = [float(r["net_inflow"]) if r["net_inflow"] is not None else None for r in rows]

    latest = rows[-1]
    return {
        "empty": False,
        "as_of": str(latest["trade_date"]),
        "total_share": float(latest["total_share"]) if latest["total_share"] is not None else None,
        "net_inflow": float(latest["net_inflow"]) if latest["net_inflow"] is not None else None,
        "history": {"dates": dates, "share": shares, "inflow": inflows},
        "note": "全市场股票型 ETF 净申购（份额变化 × 净值）：正=资金流入 ETF、负=流出",
    }


# ---------------------------------------------------------------- 新增开户（物化表直读）

def _account_open() -> dict:
    """新增开户：上交所股票账户新开户（个人 A 股，万户，月频）。"""
    rows = query_all(
        "SELECT stat_month, a_person, person, org, a_org FROM stock_account_monthly "
        "ORDER BY stat_month ASC"
    )
    if not rows:
        return {"empty": True, "note": "暂无开户数据"}

    months = [str(r["stat_month"]) for r in rows]
    a_person = [float(r["a_person"]) if r["a_person"] is not None else None for r in rows]
    person = [float(r["person"]) if r["person"] is not None else None for r in rows]

    latest = rows[-1]
    return {
        "empty": False,
        "as_of": str(latest["stat_month"]),
        "a_person": float(latest["a_person"]) if latest["a_person"] is not None else None,
        "person": float(latest["person"]) if latest["person"] is not None else None,
        "history": {"months": months, "a_person": a_person, "person": person},
        "note": "上交所股票账户新开户（个人，万户，月频）：高点=散户情绪亢奋、低点=情绪冰点",
    }


def leverage_funding() -> dict:
    """杠杆与资金行为：融资余额占比 + ETF 资金流向 + 新增开户。"""
    margin = _margin_ratio_raw()
    if margin.get("empty"):
        return margin
    margin["history"] = _margin_history_cached(margin["as_of"])
    margin["etf"] = _etf_flow()
    margin["account"] = _account_open()
    return margin
