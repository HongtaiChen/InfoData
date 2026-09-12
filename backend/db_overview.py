#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""一次性 DB 健康概览脚本（手工排查用，不入业务路径）。"""
import os
import pymysql

CFG = dict(
    host=os.getenv("INFO_DATA_DB_HOST", "127.0.0.1"),
    port=int(os.getenv("INFO_DATA_DB_PORT", "3306")),
    user=os.getenv("INFO_DATA_DB_USER", "root"),
    password=os.getenv("INFO_DATA_DB_PASSWORD", "root"),
    database=os.getenv("INFO_DATA_DB_NAME", "adata"),
    charset=os.getenv("INFO_DATA_DB_CHARSET", "utf8mb4"),
)

QUERIES = [
    (
        "stock_market_current 列结构",
        "SELECT COLUMN_NAME, COLUMN_TYPE FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='stock_market_current' ORDER BY ORDINAL_POSITION",
    ),
    (
        "ths_concept_market 列结构",
        "SELECT COLUMN_NAME, COLUMN_TYPE FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='ths_concept_market' ORDER BY ORDINAL_POSITION",
    ),
    (
        "stock_market_daily 列结构",
        "SELECT COLUMN_NAME, COLUMN_TYPE FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='stock_market_daily' ORDER BY ORDINAL_POSITION",
    ),
] + [
    ("stock_market_daily 总行数", "SELECT COUNT(*) AS v FROM stock_market_daily"),
    ("stock_market_daily 最新交易日", "SELECT MAX(trade_date) AS v FROM stock_market_daily"),
    (
        "stock_market_daily 最新日行数",
        "SELECT COUNT(*) AS v FROM stock_market_daily "
        "WHERE trade_date = (SELECT MAX(trade_date) FROM stock_market_daily)",
    ),
    (
        "stock_market_daily 09-10 行数",
        "SELECT COUNT(*) AS v FROM stock_market_daily WHERE trade_date = 20260910",
    ),
    (
        "stock_market_daily 09-09 行数",
        "SELECT COUNT(*) AS v FROM stock_market_daily WHERE trade_date = 20260909",
    ),
    (
        "stock_market_daily 最新日各前缀分布",
        "SELECT SUBSTRING(stock_code,1,1) AS p, COUNT(*) AS c "
        "FROM stock_market_daily "
        "WHERE trade_date = (SELECT MAX(trade_date) FROM stock_market_daily) "
        "GROUP BY p ORDER BY c DESC",
    ),
    (
        "stock_market_daily data_source 分布（最新日）",
        "SELECT data_source, COUNT(*) AS c FROM stock_market_daily "
        "WHERE trade_date = (SELECT MAX(trade_date) FROM stock_market_daily) "
        "GROUP BY data_source",
    ),
    ("stock_market_current 总行数", "SELECT COUNT(*) AS v FROM stock_market_current"),
    (
        "stock_market_current stock_code 各前缀分布",
        "SELECT SUBSTRING(stock_code,1,1) AS p, COUNT(*) AS c "
        "FROM stock_market_current GROUP BY p ORDER BY c DESC",
    ),
    (
        "stock_market_current 不在 stock_market_daily 最新日的股票数",
        "SELECT COUNT(*) AS v FROM stock_market_current c "
        "WHERE NOT EXISTS (SELECT 1 FROM stock_market_daily d "
        "  WHERE d.stock_code = c.stock_code "
        "  AND d.trade_date = (SELECT MAX(trade_date) FROM stock_market_daily))",
    ),
    ("ths_concept_market 总行数", "SELECT COUNT(*) AS v FROM ths_concept_market"),
    ("ths_concept_market 最新交易日", "SELECT MAX(trade_date) AS v FROM ths_concept_market"),
    (
        "ths_concept_market 最新日行数",
        "SELECT COUNT(*) AS v FROM ths_concept_market "
        "WHERE trade_date = (SELECT MAX(trade_date) FROM ths_concept_market)",
    ),
    (
        "ths_concept_market 09-10 行数",
        "SELECT COUNT(*) AS v FROM ths_concept_market WHERE trade_date = 20260910",
    ),
    (
        "ths_concept_market 09-11 行数",
        "SELECT COUNT(*) AS v FROM ths_concept_market WHERE trade_date = 20260911",
    ),
    (
        "ths_concept_market 各 trade_date 行数（TOP 10）",
        "SELECT trade_date, COUNT(*) AS c FROM ths_concept_market "
        "GROUP BY trade_date ORDER BY trade_date DESC LIMIT 10",
    ),
    (
        "ths_concept_market 09-10 各 concept_code 样本",
        "SELECT concept_code, concept_name FROM ths_concept_market WHERE trade_date = 20260910 LIMIT 10",
    ),
    (
        "stock_info 在册",
        "SELECT COUNT(*) AS v FROM stock_info WHERE list_status='上市'",
    ),
    (
        "stock_info 总数",
        "SELECT COUNT(*) AS v FROM stock_info",
    ),
    (
        "stock_info 在册的各前缀分布",
        "SELECT SUBSTRING(stock_code,1,1) AS p, COUNT(*) AS c "
        "FROM stock_info WHERE list_status='上市' GROUP BY p ORDER BY c DESC",
    ),
    (
        "trade_calendar 最近交易日",
        "SELECT MAX(trade_date) AS v FROM trade_calendar "
        "WHERE is_trading_day=1 AND trade_date <= CURDATE()",
    ),
    (
        "stock_info_ex 列结构",
        "SELECT COLUMN_NAME, COLUMN_TYPE FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='stock_info_ex' ORDER BY ORDINAL_POSITION",
    ),
    (
        "dq_recon_detail 最近一轮 run_at",
        "dq_recon_detail 最近一轮 run_at",
        "SELECT run_at, COUNT(*) AS c FROM dq_recon_detail GROUP BY run_at ORDER BY run_at DESC LIMIT 3",
    ),
    (
        "dq_rules 表分布统计",
        "SELECT table_name, COUNT(*) AS c, SUM(enabled) AS ec "
        "FROM dq_rules GROUP BY table_name ORDER BY table_name",
    ),
    (
        "stock_market_current update_time 历史（5 条）",
        "SELECT update_time, COUNT(*) AS c FROM stock_market_current GROUP BY update_time ORDER BY update_time DESC LIMIT 5",
    ),
    (
        "stock_market_current 中各 update_time 对应股票数（前缀 6）",
        "SELECT DATE(update_time) AS d, COUNT(*) AS c FROM stock_market_current WHERE stock_code LIKE '6%' "
        "GROUP BY DATE(update_time) ORDER BY d DESC LIMIT 10",
    ),
]


def main() -> None:
    conn = pymysql.connect(**CFG, cursorclass=pymysql.cursors.DictCursor)
    try:
        for entry in QUERIES:
            label = entry[0]
            sql = entry[1]
            print("--- " + label)
            try:
                with conn.cursor() as cur:
                    cur.execute(sql)
                    for row in cur.fetchall():
                        print("  " + " | ".join(f"{k}={v}" for k, v in row.items()))
            except Exception as e:
                print("  !! query failed: " + str(e))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
