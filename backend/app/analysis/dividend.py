#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""分析模板：股息率排行
数据源：ths_stock_dividend（同花顺分红送配），核心字段 pre_tax_dividend_ratio（税前股息率 %）
"""
from ..db import query_all


def dividend_rank(report_period: str | None = None, limit: int = 50) -> list[dict]:
    """按报告期股息率降序排行，关联最新行情补充现价/涨跌幅。
    默认报告期：优先最近年报（A 股年度分红最全），无年报则回退含有效数据的最近报告期"""
    if not report_period:
        r = query_all(
            """
            SELECT report_period FROM ths_stock_dividend
            WHERE pre_tax_dividend_ratio NOT IN ('--', '') AND report_period LIKE '%年报'
            ORDER BY report_period DESC LIMIT 1
            """
        )
        if not r:
            r = query_all(
                """
                SELECT report_period FROM ths_stock_dividend
                WHERE pre_tax_dividend_ratio NOT IN ('--', '')
                ORDER BY report_period DESC LIMIT 1
                """
            )
        report_period = r[0]["report_period"] if r else None
    params: list = []
    if report_period:
        params.append(report_period)
    period_where = "WHERE d.report_period = %s" if report_period else "WHERE 1=1"
    rows = query_all(
        f"""
        SELECT d.stock_code, d.short_name, d.report_period,
               d.pre_tax_dividend_ratio, d.dividend_payout_ratio,
               d.dividend_plan_desc, d.plan_progress, d.implementation_date,
               d.ashare_record_date, d.ashare_ex_date
        FROM ths_stock_dividend d
        {period_where}
          AND d.pre_tax_dividend_ratio NOT IN ('--', '')
        ORDER BY CAST(REPLACE(d.pre_tax_dividend_ratio, '%%', '') AS DECIMAL(10,4)) DESC
        LIMIT %s
        """,
        params + [limit],
    )
    # 同花顺原始值为 '3.05%' 格式，统一转为数值
    for r in rows:
        for f in ("pre_tax_dividend_ratio", "dividend_payout_ratio"):
            v = r.get(f)
            if isinstance(v, str):
                v = v.replace("%", "").strip()
                try:
                    r[f] = float(v)
                except ValueError:
                    r[f] = None
    if not rows:
        return []
    codes = [r["stock_code"] for r in rows]
    marks = ",".join(["%s"] * len(codes))
    mkt = query_all(
        f"""
        SELECT stock_code, stock_name, new, change_pct, ytd_change_pct, dynamic_pe
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
        r["dynamic_pe"] = m.get("dynamic_pe")
        r["stock_name"] = m.get("stock_name") or r["short_name"]
    return rows
