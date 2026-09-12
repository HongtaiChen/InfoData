#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""行情看板 API：最新行情、K线、股票搜索"""
from fastapi import APIRouter, Query

from ..db import query_all

router = APIRouter()


@router.get("/current")
def market_current(
    keyword: str = Query("", description="按代码或名称模糊搜索"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    sort: str = Query("change_pct", description="排序字段：change_pct / amount / ytd_change_pct / market_cap"),
    order: str = Query("desc", pattern="^(asc|desc)$"),
):
    """最新行情列表（涨跌幅/成交额/换手/市盈率/市净率等）"""
    where = ""
    params: list = []
    if keyword:
        where = "WHERE c.stock_code LIKE %s OR c.stock_name LIKE %s"
        params = [f"%{keyword}%", f"%{keyword}%"]
    # 市值 = 现价 × 总股本，库内无现成市值字段，用动态计算
    sort_col = {
        "change_pct": "c.change_pct",
        "amount": "c.amount",
        "ytd_change_pct": "c.ytd_change_pct",
        "market_cap": "c.new * c.total_captital",
    }.get(sort, "c.change_pct")
    sql = f"""
        SELECT c.stock_code, c.stock_name, c.new, c.change_pct, c.change_amount,
               c.open, c.high, c.low, c.pre_close, c.volume, c.amount,
               c.turnover_ratio, c.volume_ratio, c.dynamic_pe, c.pb,
               c.ytd_change_pct, c.update_time
        FROM stock_market_current c
        {where}
        ORDER BY {sort_col} {order}
        LIMIT %s OFFSET %s
    """
    total_sql = f"SELECT COUNT(*) AS n FROM stock_market_current c {where}"
    total = query_all(total_sql, params)[0]["n"]
    params = params + [page_size, (page - 1) * page_size]
    rows = query_all(sql, params)
    return {"total": total, "page": page, "page_size": page_size, "items": rows}


@router.get("/kline")
def kline(
    code: str = Query(..., description="股票代码，如 000001"),
    start: str = Query("", description="起始日期 YYYYMMDD，缺省取最近 N 条"),
    end: str = Query("", description="结束日期 YYYYMMDD"),
    limit: int = Query(250, ge=10, le=2000),
    is_concept: bool = Query(False, description="True 表示查询概念指数K线"),
    is_index: bool = Query(False, description="True 表示查询大盘指数K线（dc_index_market）"),
):
    """K线数据（前端 KLineChart 直接消费）"""
    if is_index:
        return _kline_from_table("dc_index_market", "index_code", code, start, end, limit)
    if is_concept:
        return _kline_from_table("ths_concept_market", "index_code", code, start, end, limit)
    return _kline_from_table("stock_market_daily", "stock_code", code, start, end, limit)


# 指数行情条的展示顺序（未列出的增补指数排在末尾）
INDEX_ORDER = [
    "000001", "399001", "399006", "000300", "000016",
    "000905", "000852", "000688", "000698", "399330",
    "399673", "899050", "931775",
]


@router.get("/index-list")
def index_list():
    """指数行情条：主流指数最新快照（收盘/涨跌/成交额/年初至今）"""
    sql = """
        SELECT i.index_code, i.index_name, i.trade_date,
               i.open, i.high, i.low, i.close, i.volume, i.amount,
               i.change_amount, i.change_pct, i.turnover_ratio, i.data_source,
               (SELECT x.close FROM dc_index_market x
                 WHERE x.index_code = i.index_code
                   AND x.trade_date < MAKEDATE(YEAR(i.trade_date), 1)
                 ORDER BY x.trade_date DESC LIMIT 1) AS prev_year_close
        FROM dc_index_market i
        JOIN (
            SELECT index_code, MAX(trade_date) AS md
            FROM dc_index_market GROUP BY index_code
        ) t ON t.index_code = i.index_code AND t.md = i.trade_date
    """
    rows = query_all(sql)
    for r in rows:
        base = r.pop("prev_year_close", None)
        close = r.get("close")
        if base not in (None, 0) and close is not None:
            r["ytd_change_pct"] = round(100.0 * (float(close) - float(base)) / float(base), 2)
        else:
            r["ytd_change_pct"] = None
    rank = {c: n for n, c in enumerate(INDEX_ORDER)}
    rows.sort(key=lambda r: (rank.get(r["index_code"], len(INDEX_ORDER)), r["index_code"]))
    return {"total": len(rows), "items": rows}


def _kline_from_table(table: str, code_col: str, code: str, start: str, end: str, limit: int) -> dict:
    params: list = [code]
    where = f"WHERE {code_col} = %s"
    if start:
        where += " AND trade_date >= %s"
        params.append(start)
    if end:
        where += " AND trade_date <= %s"
        params.append(end)
    # 先取日期倒序最近 limit 条，再升序返回（K线按时间正序）
    sql = f"""
        SELECT trade_date, open, close, high, low, volume, amount,
               change_pct, change_amount
        FROM {table}
        {where}
        ORDER BY trade_date DESC
        LIMIT %s
    """
    rows = query_all(sql, params + [limit])
    rows.reverse()
    return {"code": code, "total": len(rows), "items": rows}


@router.get("/stock-search")
def stock_search(keyword: str = Query(..., min_length=1), limit: int = Query(20, ge=1, le=100)):
    """股票搜索（行情看板切换 K 线用）"""
    sql = """
        SELECT stock_code, short_name, exchange
        FROM stock_info
        WHERE stock_code LIKE %s OR short_name LIKE %s
        ORDER BY stock_code
        LIMIT %s
    """
    rows = query_all(sql, [f"%{keyword}%", f"%{keyword}%", limit])
    return {"items": rows}


@router.get("/stock-detail")
def stock_detail(code: str = Query(..., description="股票代码")):
    """单只股票基本信息 + 最新行情（联动跳转用）"""
    info = query_all("SELECT stock_code, short_name, exchange, list_date FROM stock_info WHERE stock_code=%s", [code])
    mkt = query_all(
        "SELECT * FROM stock_market_current WHERE stock_code=%s",
        [code],
    )
    return {"info": info[0] if info else None, "market": mkt[0] if mkt else None}
