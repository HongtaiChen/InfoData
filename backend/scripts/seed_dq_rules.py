#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
InvestBuddy 数据质量规则种子（幂等，可重复执行）
用法：python scripts/seed_dq_rules.py
- 不存在则插入，存在则更新 params/severity/description/enabled
- 规则阈值基于 2026-09-05 全库盘点校准（见 docs/design/数据体系设计规范.md §5）
"""
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.db import get_db_config  # noqa: E402
import pymysql  # noqa: E402

# (rule_name, table, check_type, params, severity, enabled, description)
RULES = [
    # ---------- 行情 ----------
    ("daily_freshness", "stock_market_daily", "freshness_daily",
     {"date_col": "trade_date", "warn_days": 2}, "critical", 1,
     "日线需对齐交易日历（曾发生 09-02~04 断档，行情看板整体停更）"),
    ("daily_rows_latest", "stock_market_daily", "row_count_slice",
     {"date_col": "trade_date", "min_rows": 4500}, "critical", 1,
     "最新交易日全市场行数下限（正常 ~5100；防‘假成功’只写几十行）"),
    ("daily_close_null", "stock_market_daily", "null_rate_slice",
     {"date_col": "trade_date", "col": "close", "max_pct": 1.0}, "warning", 1,
     "最新日 close 空值率 ≤1%"),
    ("daily_value_bounds", "stock_market_daily", "violation_count",
     {"date_col": "trade_date", "where": "close<=0 OR volume<0 OR ABS(change_pct)>31", "max_count": 0},
     "warning", 1, "脏值拦截：close≤0 / 量为负 / |涨跌幅|>31%（北交所 30cm 上限容差）"),
    ("daily_natural_key", "stock_market_daily", "unique_index",
     {"cols": ["stock_code", "trade_date"], "expect": "exists"}, "warning", 1,
     "结构体检：缺 (stock_code,trade_date) 唯一索引，建议 DDL 补充保障幂等"),
    # ---------- 指数 ----------
    ("index_freshness", "dc_index_market", "freshness_daily",
     {"date_col": "trade_date", "warn_days": 2}, "warning", 1, "指数日线对齐交易日历"),
    ("index_rows_latest", "dc_index_market", "row_count_slice",
     {"date_col": "trade_date", "min_rows": 12}, "warning", 1, "最新日指数条数（当前跟踪 13 只，含 931775 待补）"),
    # ---------- 行情快照 ----------
    ("current_rows", "stock_market_current", "row_count_total",
     {"min_rows": 4500}, "critical", 1, "快照总行数（防日线缺口连带清空快照）"),
    ("current_code", "stock_market_current", "regex_count",
     {"col": "stock_code", "pattern": "^[0-9]{6}$"}, "warning", 1, "快照股票代码格式校验"),
    # ---------- 概念 ----------
    ("concept_market_freshness", "ths_concept_market", "freshness_daily",
     {"date_col": "trade_date", "warn_days": 2}, "warning", 1, "概念指数日线对齐交易日历"),
    ("concept_market_rows", "ths_concept_market", "row_count_slice",
     {"date_col": "trade_date", "min_rows": 300}, "critical", 1,
     "概念指数最新日条数（正常 ~375；2026-09-04 曾仅 22 行大面积缺失）"),
    ("concept_info_rows", "ths_concept_info", "row_count_total",
     {"min_rows": 300}, "warning", 1, "概念清单总行数下限（当前 406）"),
    ("stock_concepts_rows", "ths_stock_concepts", "row_count_total",
     {"min_rows": 30000}, "warning", 1, "股票-概念关系总行数（周更重建 ~58k；新浪降级覆盖不足会明显缩水）"),
    ("stock_concepts_code", "ths_stock_concepts", "regex_count",
     {"col": "stock_code", "pattern": "^[0-9]{6}$"}, "warning", 1, "成分股票代码格式校验"),
    # ---------- 资讯 ----------
    ("news_freshness", "news", "freshness_interval",
     {"time_col": "published_at", "pass_hours": 4, "fail_hours": 24}, "warning", 1,
     "高频流：距最新一条资讯超 4h 提示、超 24h 失败"),
    ("news_rows", "news", "row_count_total",
     {"min_rows": 100}, "warning", 1, "资讯总量下限（防误清空；保留策略另议）"),
    # ---------- 财经日历 ----------
    ("finance_calendar_fresh", "finance_calendar", "date_floor",
     {"date_col": "event_date", "days_back": 7}, "critical", 1,
     "事件表窗口需覆盖近 7 天（曾停更 8 个月；东财源 60 天滚动窗口）"),
    ("finance_calendar_rows", "finance_calendar", "row_count_total",
     {"min_rows": 500}, "warning", 1, "事件总量下限（当前 ~2,067）"),
    # ---------- 债券 ----------
    ("bond_freshness", "bond_profit_daily", "freshness_daily",
     {"date_col": "trade_date", "warn_days": 3}, "warning", 1, "国债收益率对齐交易日历（中美债假日略有差异，容错 3 日）"),
    # ---------- 基金 / 资料 ----------
    ("fund_info_rows", "fund_info", "row_count_total",
     {"min_rows": 20000}, "warning", 1, "基金全量重建护栏（当前 27,790）"),
    ("stock_info_rows", "stock_info", "row_count_total",
     {"min_rows": 5000}, "warning", 1, "全 A 股票名单下限（当前 5,856）"),
    ("stock_info_code", "stock_info", "regex_count",
     {"col": "stock_code", "pattern": "^[0-9]{6}$"}, "warning", 1, "股票代码格式校验"),
    ("stock_info_ex_rows", "stock_info_ex", "row_count_total",
     {"min_rows": 5000}, "info", 1, "扩展表名单下限"),
    ("stock_info_ex_code", "stock_info_ex", "regex_count",
     {"col": "stock_code", "pattern": "^[0-9]{6}$"}, "info", 1, "扩展表代码格式校验"),
    ("trade_calendar_rows", "trade_calendar", "row_count_total",
     {"min_rows": 3000}, "info", 1, "交易日历覆盖下限（当前 7,845）"),
    # ---------- 结构性（唯一索引存在性） ----------
    ("ths_concept_market_uniq", "ths_concept_market", "unique_index",
     {"cols": ["index_code", "trade_date"], "expect": "exists"}, "info", 1, "幂等保障：uk_index_date"),
    ("news_uniq", "news", "unique_index",
     {"cols": ["source", "url"], "expect": "exists"}, "info", 1, "幂等保障：uk_source_url"),
    ("trade_calendar_uniq", "trade_calendar", "unique_index",
     {"cols": ["trade_date"], "expect": "exists"}, "info", 1, "幂等保障：uk_trade_date"),
]


def main():
    conn = pymysql.connect(**get_db_config().to_dict())
    try:
        with conn.cursor() as cur:
            for name, table, ctype, params, severity, enabled, desc in RULES:
                cur.execute(
                    """INSERT INTO dq_rules (rule_name, table_name, check_type, params, severity, enabled, description)
                       VALUES (%s,%s,%s,%s,%s,%s,%s)
                       ON DUPLICATE KEY UPDATE table_name=VALUES(table_name), check_type=VALUES(check_type),
                       params=VALUES(params), severity=VALUES(severity), enabled=VALUES(enabled), description=VALUES(description)""",
                    (name, table, ctype, json.dumps(params, ensure_ascii=False), severity, enabled, desc),
                )
        conn.commit()
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM dq_rules")
            print(f"seed 完成，dq_rules 共 {cur.fetchone()[0]} 条规则")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
