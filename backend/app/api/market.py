#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""行情看板 API：最新行情、K线、股票搜索"""
from fastapi import APIRouter, Query

from ..db import query_all
from ..index_meta import DERIVED_INDEXES, NON_EQUITY_INDEXES

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
    # 市值 = 现价 × 总股本。
    # ⚠️ 2026-09-19 前 total_captital 恒 NULL → 本行 `ORDER BY c.new * c.total_captital`
    #    **静默失效**（ORDER BY NULL 等于没排序，却仍返回 200，前端「按市值」按钮点了没反应）。
    #    market_current_sync 已改为用 stock_shares 本地派生填充 total_captital（覆盖率 100%），
    #    排序现已真正生效；同时把市值一并返回，避免「排对了但看不到值」。
    market_cap_expr = "c.new * c.total_captital"
    sort_col = {
        "change_pct": "c.change_pct",
        "amount": "c.amount",
        "ytd_change_pct": "c.ytd_change_pct",
        "market_cap": market_cap_expr,
    }.get(sort, "c.change_pct")
    sql = f"""
        SELECT c.stock_code, c.stock_name, c.new, c.change_pct, c.change_amount,
               c.open, c.high, c.low, c.pre_close, c.volume, c.amount,
               c.turnover_ratio, c.volume_ratio, c.dynamic_pe, c.pb,
               c.ytd_change_pct, c.update_time,
               c.total_captital, c.float_captital,
               ROUND({market_cap_expr} / 100000000, 2) AS market_cap_yi
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
                 ORDER BY x.trade_date DESC LIMIT 1) AS prev_year_close,
               p.description, p.base_date, p.base_point
        FROM dc_index_market i
        JOIN (
            SELECT index_code, MAX(trade_date) AS md
            FROM dc_index_market GROUP BY index_code
        ) t ON t.index_code = i.index_code AND t.md = i.trade_date
        LEFT JOIN index_profile p ON p.index_code = i.index_code
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


@router.get("/index-detail")
def index_detail(code: str = Query(..., description="指数代码")):
    """指数详情：释义档案 + 成分股列表（join 东财行业） + 行业分布聚合

    - 000001 上证指数为全市场指数：成分按「沪市全部上市股」实时派生
    - 行业分布：成分股有权重时按权重加权占比，否则按等权只数占比
    """
    # 1) 指数档案（释义/基准）
    prof = query_all(
        "SELECT index_code, index_name, description, base_date, base_point, source "
        "FROM index_profile WHERE index_code=%s",
        [code],
    )
    profile = prof[0] if prof else None
    if profile is None:
        name_rows = query_all(
            "SELECT DISTINCT index_name FROM dc_index_market WHERE index_code=%s", [code]
        )
        profile = {
            "index_code": code,
            "index_name": name_rows[0]["index_name"] if name_rows else code,
            "description": None,
            "base_date": None,
            "base_point": None,
            "source": None,
        }

    # 2) 成分股（join 行业）
    # ① 全市场派生指数（000001）：不入快照表，按沪市全体上市股实时派生
    # ② 其余指数必须取**最新快照日**的成分：快照表按日留档（uk_index_stock_date），
    #    不过滤快照日的话历史快照会与新快照叠加、同一只股票被算两次
    #    —— 改造前实测 000300 已有两天快照，接口会把 300 只返回成 600 行。
    # ③ 债券等非股票指数：成分口径不适用，cons_note 给说明（而非笼统的「暂缺」）
    derived_note = DERIVED_INDEXES.get(code)
    cons_note = NON_EQUITY_INDEXES.get(code)
    if code in DERIVED_INDEXES:
        cons = query_all(
            """
            SELECT stock_code, short_name AS stock_name, NULL AS weight,
                   NULL AS trade_date, 'derived_sh' AS source, industry
            FROM stock_info
            WHERE exchange = 'SH' AND list_status = '上市'
            ORDER BY stock_code
            """
        )
    else:
        # 先取最新快照日、再用常量比较 —— 不能图省事写成相关子查询
        # `AND c.trade_date = (SELECT MAX(...) WHERE x.index_code = c.index_code)`：
        # EXPLAIN 为 DEPENDENT SUBQUERY，5121 行逐行求值，实测 5.98s；
        # 改成两步后同一份数据 0.04s（150×）。快照按日留档后这类写法极易踩坑。
        snap_rows = query_all(
            "SELECT MAX(trade_date) AS md FROM index_constituents WHERE index_code=%s",
            [code],
        )
        snap = snap_rows[0]["md"] if snap_rows else None
        cons = query_all(
            """
            SELECT c.stock_code, c.stock_name, c.weight, c.trade_date, c.source,
                   s.industry
            FROM index_constituents c
            LEFT JOIN stock_info s ON s.stock_code = c.stock_code
            WHERE c.index_code = %s AND c.trade_date = %s
            ORDER BY c.weight DESC, c.stock_code
            """,
            [code, snap],
        ) if snap else []

    has_weight = any(r["weight"] is not None for r in cons)

    # 2b) 行业覆盖度：成分在本地行业库（stock_info.industry）里的匹配率。
    #     低于一半时「行业分布」不再有意义，硬算只会得到「其他 100%」的假饼图
    #     —— 假饼图比不展示更误导。
    #     历史成因：北交所 2025-10-09 切换 920 代码段后本库名册失同步且行业整段为空，
    #     北证50 曾 36/50 只成分连主表都没有；该问题已于 2026-09-19 由 bj_stock_sync
    #     采集器修复（名册 344 只 + 行业 100% 覆盖），此分支现为兜底保护而非日常路径。
    #     仍保留的原因：新纳入指数的成分若采集排期未跑到，覆盖度会瞬时掉下来，
    #     此时宁可给说明也不要画一张「其他 100%」的假图。
    matched = sum(1 for r in cons if r["industry"])
    ind_cover = round(100.0 * matched / len(cons), 1) if cons else 0.0
    industry_note = None
    if cons and ind_cover < 50:
        industry_note = (
            f"该指数 {len(cons)} 只成分中仅 {matched} 只在本地行业库中有分类，"
            f"覆盖不足五成——画出来只会是「其他」主导的假饼图，故暂不展示行业分布"
        )

    # 3) 行业分布聚合
    dist: dict[str, dict] = {}
    for r in cons:
        ind = r["industry"] or "其他"
        d = dist.setdefault(ind, {"industry": ind, "count": 0, "weight": 0.0})
        d["count"] += 1
        if r["weight"] is not None:
            d["weight"] += float(r["weight"])
    total_w = sum(d["weight"] for d in dist.values())
    industry_items = []
    for d in dist.values():
        if has_weight and total_w > 0:
            d["weight_pct"] = round(100.0 * d["weight"] / total_w, 2)
        else:
            d["weight_pct"] = round(100.0 * d["count"] / len(cons), 2) if cons else 0.0
        industry_items.append(d)
    industry_items.sort(key=lambda x: -x["weight_pct"])

    return {
        "profile": profile,
        "derived_note": derived_note,
        # 口径不适用说明（债券指数等）：前端据此展示说明，而不是「行业数据暂缺」
        "cons_note": cons_note,
        "constituents": {
            "total": len(cons),
            "has_weight": has_weight,
            "items": cons,
        },
        "industry_dist": industry_items,
        # 行业覆盖度与不足时的说明（前端据此不画假饼图）
        "industry_coverage": ind_cover,
        "industry_note": industry_note,
    }
