#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""分析模板：年初至今（YTD）涨幅排行
数据源：stock_market_current.ytd_change_pct（现成字段，采集自东财实时行情）
"""
from ..db import query_all


def ytd_rank(limit: int = 50, order: str = "desc") -> list[dict]:
    rows = query_all(
        f"""
        SELECT stock_code, stock_name, new, ytd_change_pct,
               change_pct, amount, dynamic_pe, pb
        FROM stock_market_current
        WHERE ytd_change_pct IS NOT NULL
        ORDER BY ytd_change_pct {order}
        LIMIT %s
        """,
        [limit],
    )
    return rows
