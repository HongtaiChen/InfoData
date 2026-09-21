#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""分析模板：年初至今（YTD）涨幅排行

数据源：`stock_market_current.ytd_change_pct`，由 **market_current_sync 本地聚合派生**
（`data_source='daily-agg'`），**不是**东财实时行情字段 —— 原注释「采集自东财实时行情」有误（2026-09-20 勘误）。

⚠️ 口径（2026-09-20）：该列 = `(今收×今因子)/(年初收×年初因子) − 1`，即**含分红再投的真实收益**。
口径改造（主表改存不复权实际价）后若不乘因子，会在年内除权的票上漏掉整段分红、
系统性低估收益（实测 600036 由 +1.03% 被算成 −4.16%，**符号反了**）—— 而本模板正是**按该列排序**的
排行榜，故误差会直接体现为「高分红股被排到榜尾」。
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
