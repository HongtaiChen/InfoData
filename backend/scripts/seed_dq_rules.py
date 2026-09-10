#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
InvestBuddy 数据质量规则种子（幂等，可重复执行）
用法：python scripts/seed_dq_rules.py
- 不存在则插入，存在则更新 params/severity/description/enabled/rule_group
- 规则阈值基于 2026-09-05 全库盘点 + 2026-09-10 日线全史实测校准
  （见 docs/design/数据体系设计规范.md §5 与 docs/design/日线质量体检与对账体系设计规范.md）

规则分组（dq_rules.rule_group）：
- daily ：每日盘后 20:30 跑（最新切片类，秒级）
- weekly：每周一 21:30 独立任务跑（全史窗口扫描类，实测合计 4~7 分钟）

weekly 组设计说明（2026-09-10 实测结论）：
- daily_gap_scan        全史疑似缺口（LAG 窗口，18M 行 ~196s）→ 明细入 dq_gap_detail
- daily_ohlc_consistent 全史 OHLC 自洽（实测 0 违反，守护型）
- daily_change_link     涨跌幅与前收衔接（实测 0 违反；pre_close 当前仅 6.6% 行有值，待回填后全覆盖）
- daily_coverage_recent 近 250 日每票行数下限（实测 0 异常）
- daily_source_handoff  跨源衔接偏差（实测 2,532 行，AKSHARE→TENCENT 基准微差，真实问题）
- 已废弃：daily_amount_cross（量额勾稽）—— 实测 47% 行违反，根因是 volume/amount 为真实值
  而 OHLC 为前复权值，两者不同口径，勾稽数学退化。不可实现，故不写入规则。
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
    # ---------- 股票档案/退市一致性（v1.4 stock_info 宽表合并后） ----------
    ("stock_info_delist_mark", "stock_info", "where_count",
     {"where": "list_status='上市' AND delist_date IS NOT NULL", "max_count": 0}, "warning", 1,
     "退市一致性：在市股不应带退市日期"),
    ("stock_info_delist_date", "stock_info", "where_count",
     {"where": "list_status='退市' AND delist_date IS NULL", "max_count": 0}, "warning", 1,
     "退市一致性：退市股应有退市日期（Baostock 覆盖 331 只）"),
    ("stock_info_profile_cover", "stock_info", "where_count",
     {"where": "list_status='上市' AND (stock_code LIKE '6%' OR stock_code LIKE '0%' OR stock_code LIKE '3%') AND company_name IS NULL",
      "max_count": 0}, "warning", 1,
     "档案覆盖：沪深在市 A 股均应已有巨潮公司档案（company_name 非空）"),
]

# weekly 组：全史窗口扫描类（每周一 21:30 独立任务，见文件头说明）
WEEKLY_RULES = [
    ("daily_gap_scan", "stock_market_daily", "gap_scan",
     {"date_col": "trade_date", "gap_days": 45, "high_days": 365}, "warning", 1,
     "全史疑似缺口：同票相邻记录间隔 >45 自然日（≥365 天高危单独计数）；明细入 dq_gap_detail 供 L3 修复"),
    ("daily_ohlc_consistent", "stock_market_daily", "where_count",
     {"where": "close>0 AND high>0 AND low>0 "
               "AND (high < LEAST(open,close) OR low > GREATEST(open,close) OR volume<0)",
      "max_count": 0}, "warning", 1,
     "全史 OHLC 自洽：high≥max(o,c) 且 low≤min(o,c) 且 volume≥0（前置 >0 规避前复权负价区）"),
    ("daily_change_link", "stock_market_daily", "where_count",
     {"where": "pre_close>0 AND close>0 AND change_pct IS NOT NULL "
               "AND ABS((close-pre_close)/pre_close*100 - change_pct) > 0.02",
      "max_count": 0}, "warning", 1,
     "涨跌幅与收盘/昨收推导值偏差 ≤0.02pp（当前仅覆盖有 pre_close 的行，回填后全覆盖）"),
    ("daily_coverage_recent", "stock_market_daily", "per_key_coverage",
     {"date_col": "trade_date", "key_col": "stock_code",
      "window_days": 250, "min_rows": 100, "min_listed_days": 365,
      "ref_table": "stock_info", "ref_key": "stock_code",
      "ref_status_col": "list_status", "ref_status_val": "上市",
      "ref_date_col": "list_date", "exclude_prefixes": ["4", "8", "920"]},
     "warning", 1,
     "近 250 日每票行数下限（在市且上市满 1 年，排除北交所；抓均匀稀疏型缺失）"),
    ("daily_source_handoff", "stock_market_daily", "source_handoff",
     {"date_col": "trade_date", "source_col": "data_source",
      "since": "2025-09-01", "max_pct": 0.5}, "warning", 1,
     "跨源衔接一致性：相邻行 data_source 变化处 pre_close 与上一笔 close 偏差 >0.5%（复权基准微差）"),
]


def main():
    conn = pymysql.connect(**get_db_config().to_dict())
    try:
        all_rules = [(r, "daily") for r in RULES] + [(r, "weekly") for r in WEEKLY_RULES]
        with conn.cursor() as cur:
            for (name, table, ctype, params, severity, enabled, desc), group in all_rules:
                cur.execute(
                    """INSERT INTO dq_rules
                       (rule_name, table_name, check_type, rule_group, params, severity, enabled, description)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                       ON DUPLICATE KEY UPDATE table_name=VALUES(table_name), check_type=VALUES(check_type),
                       rule_group=VALUES(rule_group), params=VALUES(params), severity=VALUES(severity),
                       enabled=VALUES(enabled), description=VALUES(description)""",
                    (name, table, ctype, group, json.dumps(params, ensure_ascii=False), severity, enabled, desc),
                )
        conn.commit()
        with conn.cursor() as cur:
            cur.execute("SELECT rule_group, COUNT(*) FROM dq_rules GROUP BY rule_group")
            dist = cur.fetchall()
            cur.execute("SELECT COUNT(*) FROM dq_rules")
            total = cur.fetchone()[0]
        print(f"seed 完成，dq_rules 共 {total} 条规则，分组分布: {dist}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
