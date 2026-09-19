#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""概念板块 API：概念列表、概念K线、成分股、股票↔概念联动"""
from fastapi import APIRouter, Query

from ..db import query_all

router = APIRouter()


@router.get("/list")
def concept_list(
    keyword: str = Query("", description="按概念代码/名称模糊搜索"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    sort: str = Query("change_pct", description="change_pct / amount"),
    order: str = Query("desc", pattern="^(asc|desc)$"),
):
    """概念板块列表（含最新交易日涨跌幅、成交额）

    ⚠️ 必须锚定**全表最新交易日**，不能锚定「每个概念各自的 MAX(trade_date)」。
    后者看似更贴心（每个概念都有值），实则把**不同交易日**的涨跌幅混进同一张榜排序：
    概念改名/停更会在 ths_concept_market 里留下孤儿 index_code（实测 `WiFi6` 的最后一行
    停在 2025-09-19，而现行序列是 `WiFi 6`），于是当日榜上会同时出现 2026-09-18 的
    +2.00% 与 2025-09-19 的 -0.42% 并直接比大小 —— 排序结果无任何意义，且当天榜上
    前几名会被一年前的旧值占据（2026-09-19 实测：limit=8 里 5 条来自 2025-09-19）。
    这正是盘点报告 §2.5「概念异常收益 +110%」的真实成因：不是源列脏，是跨日/跨名拼接。
    """
    where = ""
    params: list = []
    if keyword:
        where = "WHERE (i.concept_code LIKE %s OR i.concept_name LIKE %s)"
        params = [f"%{keyword}%", f"%{keyword}%"]
    # 用最新交易日行情关联（一个概念一条）。LATEST 子查询不相关 → 全表只取一个日期。
    # change_pct 由源侧**间歇性不返回**（实测 09-15~09-18 空值率 99~100%，见 table_meta），
    # 空值时用 `close / 上一交易日 close - 1` 兜底 —— 与 analysis/concept_rank 的区间涨幅同口径，
    # 否则这一页会退化成「前 3 条有值、其余全是空」的假榜单（2026-09-19 实测）。
    latest = query_all("SELECT MAX(trade_date) AS d FROM ths_concept_market")[0]["d"]
    if latest is None:
        return {"total": 0, "page": page, "page_size": page_size, "items": []}
    prev = query_all(
        "SELECT MAX(trade_date) AS d FROM ths_concept_market WHERE trade_date < %s", [latest]
    )[0]["d"]
    sql = f"""
        SELECT i.index_code, i.concept_code, i.concept_name,
               m.trade_date, m.close, m.amount,
               COALESCE(m.change_pct,
                        ROUND((m.close / NULLIF(p.close, 0) - 1) * 100, 4)) AS change_pct,
               COALESCE(m.change_amount, ROUND(m.close - p.close, 4)) AS change_amount
        FROM ths_concept_info i
        JOIN ths_concept_market m
          ON m.index_code = i.index_code
         AND m.trade_date = %s
        LEFT JOIN ths_concept_market p
          ON p.index_code = m.index_code AND p.trade_date = %s
        {where}
        ORDER BY change_pct {order}
        LIMIT %s OFFSET %s
    """
    # total 必须与 items 同口径（同样锚定最新交易日），否则分页会多出永远取不到的页
    total = query_all(
        f"""SELECT COUNT(*) AS n FROM ths_concept_info i
            JOIN ths_concept_market m
              ON m.index_code = i.index_code
             AND m.trade_date = %s
            {where}""",
        [latest] + params,
    )[0]["n"]
    rows = query_all(sql, [latest, prev] + params + [page_size, (page - 1) * page_size])
    return {"total": total, "page": page, "page_size": page_size, "items": rows}


@router.get("/kline")
def concept_kline(
    code: str = Query(..., description="概念指数代码，如 885336"),
    start: str = Query(""),
    end: str = Query(""),
    limit: int = Query(250, ge=10, le=2000),
):
    """概念指数K线"""
    params: list = [code]
    where = "WHERE index_code = %s"
    if start:
        where += " AND trade_date >= %s"
        params.append(start)
    if end:
        where += " AND trade_date <= %s"
        params.append(end)
    rows = query_all(
        f"""
        SELECT trade_date, open, close, high, low, volume, amount, change_pct, change_amount
        FROM ths_concept_market
        {where}
        ORDER BY trade_date DESC LIMIT %s
        """,
        params + [limit],
    )
    rows.reverse()
    return {"code": code, "total": len(rows), "items": rows}


@router.get("/constituents")
def constituents(
    code: str = Query(..., description="概念指数代码或概念代码"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
):
    """概念成分股（关联股票最新行情）"""
    rows = query_all(
        """
        SELECT sc.stock_code, sc.short_name, sc.reason, sc.index_code, sc.concept_name
        FROM ths_stock_concepts sc
        WHERE sc.index_code = %s OR sc.concept_name = %s
        """,
        [code, code],
    )
    if not rows:
        return {"total": 0, "items": []}
    codes = [r["stock_code"] for r in rows]
    marks = ",".join(["%s"] * len(codes))
    mkt = query_all(
        f"""
        SELECT stock_code, stock_name, new, change_pct, ytd_change_pct
        FROM stock_market_current
        WHERE stock_code IN ({marks})
        """,
        codes,
    )
    mkt_map = {m["stock_code"]: m for m in mkt}
    for r in rows:
        m = mkt_map.get(r["stock_code"], {})
        r["new"] = m.get("new")
        r["change_pct"] = m.get("change_pct")
        r["ytd_change_pct"] = m.get("ytd_change_pct")
        r["stock_name"] = m.get("stock_name") or r["short_name"]
    # 按涨跌幅降序
    rows.sort(key=lambda x: x.get("change_pct") or -999, reverse=True)
    total = len(rows)
    items = rows[(page - 1) * page_size : page * page_size]
    return {"total": total, "page": page, "page_size": page_size, "items": items}


@router.get("/stock-concepts")
def stock_concepts(stock_code: str = Query(..., description="股票代码")):
    """股票所属全部概念（股票 → 概念联动，分析研究核心）"""
    rows = query_all(
        """
        SELECT index_code, concept_name, source, reason
        FROM ths_stock_concepts
        WHERE stock_code = %s
        ORDER BY concept_name
        """,
        [stock_code],
    )
    return {"stock_code": stock_code, "total": len(rows), "items": rows}
