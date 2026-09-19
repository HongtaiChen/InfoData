#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
InvestBuddy 数据质量规则种子（幂等，可重复执行）
用法：python scripts/seed_dq_rules.py
- 不存在则插入，存在则更新 params/severity/description/enabled/rule_group
- 规则阈值基于 2026-09-05 全库盘点 + 2026-09-10 日线全史实测校准
  + 2026-09-13 全库覆盖率排查（COVERAGE_RULES / FROZEN_RULES 两批，见下）
  （见 docs/design/数据体系设计规范.md §5 与 docs/design/日线质量体检与对账体系设计规范.md）

规则批次（2026-09-13 起按来源分四段）：
- RULES          ：首批 31 条（行情/资料/概念/日历/资讯/系统核心表）
- WEEKLY_RULES   ：周频全史扫描 5 条
- COVERAGE_RULES ：2026-09-13 活跃表补全 —— index_constituents / index_profile /
                   finance_concept_analysis（3 张有采集器但此前无规则的表）
- FROZEN_RULES   ：2026-09-13 历史导入表冻结监护 —— 10 张无采集任务、水位停于
                   2025-08/09 的表，只配防清空规则，刻意不加 freshness（加了必红）
- RECOVERED_RULES：2026-09-13 死表恢复采集批次 —— 3 张已完成采集器落地并全量补齐
                   的表（分红送配 / 融资融券 / 机构调研），从 FROZEN 升级为完整规则
                   （含 freshness），并由 RETIRED_RULES 清理其旧冻结规则
- RECOVERED_RULES_B34：2026-09-13 死表恢复采集 第 3/4 批 —— 4 张表
                   （期货现货 / 申万行业 / 财务摘要 / 股本变动）从 FROZEN 升级为完整规则。
                   资金流向（东财域不可达）仍留 FROZEN。
                   注：futures_spot_price 无唯一索引（同日多快照），刻意不配 unique_index。
- 市场宽度规则     ：2026-09-14 市场风向模块新增宽度维度（涨跌家数/均线参与度/新高新低）
                   配套 3 条规则（非空 / 占比越界 / 派生列自洽），见 RULES 中 market_style_daily 段。
- 交叉印证规则     ：2026-09-15 P1「交叉印证」落地 —— market_style_daily 新增换手率中位数
                   turnover_med，配套 2 条规则（非空 / 取值域）。其中 range 一条兼作
                   **单位漂移守护**：源列 turnover_ratio 的单位曾于 2025-09 中旬切换
                   （百分数 → 小数，差 100 倍），源侧若再改口径而采集器未跟上，该规则立刻变红。

⚠️ 维护纪律（2026-09-13 踩坑）：**本脚本是 dq_rules 的唯一事实来源**。
   任何绕过脚本的直改 DB（如事故应急调阈值）必须同步回本文件，
   否则下次跑 seed 会把手工调整静默覆盖回去（已发生：concept_market 两条规则
   09-12 手工调过阈值，09-13 跑 seed 被还原成旧值导致体检变红）。

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

🗓️ 调度语义提醒（2026-09-14 事故）：task_config 的 cron 由 APScheduler
   `CronTrigger.from_crontab` 解析，**day_of_week 为 0=周一、6=周日**，
   与 Unix crontab 相反。写 `* * 1-5` 不是「工作日」而是「周二~周六」。
   `futures_sync` 曾因此周一漏跑、周六空跑。凡涉及星期的 cron 请用
   `scheduler.cron_human()` 复核中文语义后再落库（作业监控页与调度日志均会显示）。
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
     {"date_col": "trade_date", "min_rows": 20}, "warning", 1, "最新日指数条数（当前跟踪 21 只）"),
    # ---------- 市场风格物化（分析研究·市场风向模块数据源，2026-09-13） ----------
    ("style_freshness", "market_style_daily", "freshness_daily",
     {"date_col": "trade_date", "warn_days": 2}, "warning", 1, "风格物化表对齐交易日历（20:05 挂个股日线之后）"),
    ("style_rows", "market_style_daily", "row_count_total",
     {"min_rows": 1000}, "warning", 1, "风格物化表行数下限（全史约 5,300 行，防清空）"),
    # 市场宽度（2026-09-14 新增，来源 stock_market_daily 全市场个股）
    ("style_breadth_notnull", "market_style_daily", "null_rate_slice",
     {"col": "breadth_up_ratio", "max_pct": 0}, "warning", 1,
     "最新日宽度非空（宽度列缺失 = 宽度面板静默降级为「--」，须报警）"),
    ("style_breadth_range", "market_style_daily", "violation_count",
     {"where": "breadth_up_ratio > 100 OR above_ma20_pct > 100 OR above_ma60_pct > 100 "
               "OR (breadth_up + breadth_down) > breadth_total"},
     "critical", 1, "宽度占比越界（占比 >100% 或 涨+跌家数 > 总家数 = 计算口径错）"),
    ("style_breadth_hl", "market_style_daily", "where_count",
     {"where": "breadth_up_ratio IS NOT NULL AND hl_diff60 <> new_high60 - new_low60"},
     "warning", 1, "新高新低差派生列自洽（hl_diff60 必须等于 new_high60 − new_low60）"),
    # 2026-09-15 新增：风险调整列（risk_appetite_adj20 / scissors_adj20 = 收益差 ÷ 其自身滚动σ）
    # 符号是数学必然（σ 恒正 → 除完必与原差值同号），异号只可能来自计算错或写入错位，故设 critical
    ("style_adj_sign", "market_style_daily", "where_count",
     {"where": "risk_appetite_adj20 IS NOT NULL AND risk_appetite_20 IS NOT NULL "
               "AND risk_appetite_adj20 * risk_appetite_20 < 0"},
     "critical", 1, "风险调整列符号自洽（adj=差值÷σ，σ 恒正 → 必须与原差值同号，异号=计算错）"),
    # 参考：全史 risk_appetite_adj20 max 6.026 / scissors_adj20 max 3.276，取 10 作宽松上限
    ("style_adj_range", "market_style_daily", "where_count",
     {"where": "ABS(risk_appetite_adj20) > 10 OR ABS(scissors_adj20) > 10"},
     "warning", 1, "风险调整值取值域（归一化量，全史实测 max 6.03；|adj|>10 说明 σ 被算得过小）"),
    # 2026-09-15 新增：换手率结构列（交叉印证「微观结构」项所需）
    # ⚠️ 这条 range 规则不只是数值越界检查，更是**单位漂移的守护**：源列
    #    stock_market_daily.turnover_ratio 的单位在 2025-09 中旬从「百分数（2.0=2%）」
    #    切换为「小数（0.02=2%）」，首次物化时被统一 ×100，导致历史段虚高 100 倍
    #    （修正前该规则会命中 4,847 行、max 859%）。归一后全史落在 0.4%~10% 区间。
    #    若日后源侧再改口径而采集器未跟上，这条会立刻变红——这就是它存在的意义。
    ("style_turnover_notnull", "market_style_daily", "null_rate_slice",
     {"col": "turnover_med", "max_pct": 0}, "warning", 1,
     "最新日换手率中位数非空（缺失 = 交叉印证「微观结构」项静默降级为数据缺失）"),
    ("style_turnover_range", "market_style_daily", "where_count",
     {"where": "turnover_med IS NOT NULL AND (turnover_med <= 0 OR turnover_med > 30)"},
     "critical", 1,
     "换手率中位数取值域（应为 0~10% 量级；>30 几乎必然是「源列单位漂移」——"
     "源列 2025-09 中旬由百分数改为小数，差 100 倍，见 market_style_sync 文件头）"),
    # ---------- 交叉印证背离数物化（2026-09-19，market_wind 卡片分位的数据源） ----------
    # ⚠️ 为什么必须显式报警：本表若停更，market_wind 的 `_xcheck_series` 会拿到空/短序列，
    #    `_diverge_stats` 直接返回 None，判读条**静默退回纯计数**（「7 项中 4 项背离」）——
    #    页面看起来完全正常，只是那个「99.6% 分位」悄悄没了。这是「优雅降级」的阴暗面：
    #    降级太安静就没人会发现。故三条规则覆盖 停更 / 行数不足 / 数值越界。
    ("xcheck_freshness", "market_xcheck_daily", "freshness_daily",
     {"date_col": "trade_date", "warn_days": 2}, "warning", 1,
     "背离数对齐交易日历（每工作日 22:30 xcheck_sync 写入；须晚于概念补班 22:00）"),
    ("xcheck_rows", "market_xcheck_daily", "row_count_total",
     {"min_rows": 60}, "warning", 1,
     "背离数物化行数下限（一次性回填 250 行；<60 行则分位样本不足，_diverge_stats 不判）"),
    ("xcheck_range", "market_xcheck_daily", "violation_count",
     {"where": "diverge_n > items_n OR diverge_n < 0 OR items_n < 5"},
     "critical", 1,
     "背离数越界（背离数不可能超过项数；项数 <5 说明交叉印证大面积降级）"),
    # ---------- 行情快照 ----------
    ("current_rows", "stock_market_current", "row_count_total",
     {"min_rows": 4500}, "critical", 1, "快照总行数（防日线缺口连带清空快照）"),
    # 2026-09-14 补盲点：本表**没有 trade_date 列**，此前只有行数 + 代码正则两条规则，
    # 行数达标即判 pass —— 实测 update_time 曾停在 09-12 09:33（缺 09-14 全天）
    # 而 5121 行依然 pass，是「体检说没事、实际已停更」的唯一盲点。
    # freshness_daily 检查器取 MAX(date_col) 后只截前 10 位比较，可直接吃时间戳列。
    ("current_fresh", "stock_market_current", "freshness_daily",
     {"date_col": "update_time", "warn_days": 1}, "warning", 1,
     "快照新鲜度（表无 trade_date，改以 update_time 判定）：快照更新日应对齐最近交易日，容忍 1 个交易日（2026-09-14 补盲点）"),
    ("current_code", "stock_market_current", "regex_count",
     {"col": "stock_code", "pattern": "^[0-9]{6}$"}, "warning", 1, "快照股票代码格式校验"),
    # ---------- 概念 ----------
    ("concept_market_freshness", "ths_concept_market", "freshness_daily",
     {"date_col": "trade_date", "warn_days": 5}, "warning", 1,
     "概念指数日线对齐交易日历（同花顺周级波动容忍，2026-09-12 由 2 放宽至 5）"),
    ("concept_market_rows", "ths_concept_market", "row_count_slice",
     {"date_col": "trade_date", "min_rows": 150}, "critical", 1,
     "概念指数最新日条数（正常 375；源侧对部分新概念**延迟/分批发布**，20:30 体检时实测可能只有 ~204，"
     "已由 20:00 主班次 + 22:00 补班次同日晚间补齐。2026-09-14 由 240 下调至 150：240 会把「分批发布」"
     "误判为 fail，150 仍能挡住「只写几行」的假成功）"),
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
    # ---------- 调度/运维健康（2026-09-14 新增） ----------
    # 关注对象不是业务数据，而是「采集任务本身有没有被卡住」。
    # 起因：uvicorn 重启中断任务 → 留下永久 running 记录 → 调度器「同任务 2h running 保护」
    # 令该任务 2h 内无法重跑（daily_recon_window 因此连续两个 20:45 班次被静默跳过）。
    # 兜底层：SchedulerManager.start() 的启动自愈；本规则负责进程未重启时的长挂兜底。
    ("task_stale_running", "task_runs", "stale_running",
     {"hours": 3}, "warning", 1,
     "僵尸 running 记录：任务记录停在 running 且已超 3 小时（重启中断的残留，会阻塞该任务后续触发）"),
    # 2026-09-19 补：与 task_stale_running 互补的另一半 ——「跑完了，但跑了很久」。
    # 原盘点报告称「单次 >3h 无任何告警覆盖」并不准确（stale_running 一直在 pass），
    # 真实缺口是它只覆盖**未收尾**的 running，覆盖不到**已完成但超长**：
    #   daily_recon_window     09-17 21:15 → 09-18 18:14  success 1259 分钟
    #   financial_abstract_sync 09-16 06:52 → 09-16 18:43 success  711 分钟
    # ⚠️ 判读：此类超长绝大多数是「机器待机冻结进程」所致（家用电脑合盖/睡眠），
    # 跨度里绝大部分是冻结时长而非执行时长 → 本规则是**物理离线信号**，不是性能告警。
    # ⚠️ 只数 status='success'：failed 记录里那批「9741 分钟」是运维脚本收尾跨度，非真实耗时。
    ("task_long_finished", "task_runs", "long_finished_run",
     {"minutes": 180, "lookback_days": 7}, "warning", 1,
     "已完成但耗时超长（success 且 >180 分钟）：stale_running 只管「没跑完」，本规则管「跑完了但很久」。"
     "⚠️ 此类超长多为机器待机冻结进程所致，是**物理离线的信号**，不是任务本身变慢——"
     "请结合开机/睡眠时段判读，勿据此优化任务性能"),
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

# ============================================================================
# 2026-09-13 批次：全库覆盖率排查补充（36 表盘点，纳入体检的表 13 → 26）
# 排查方法：用 DataQualityCheckCollector 的内部检查器对候选规则逐条 dry-run，
#          确认「当前状态全部 pass」后才写入，避免上线即红。
# 两条硬约束（本次排查得出的教训）：
#   1) severity 不参与 worst 计算（quality.py dq_table_status 只看 status）
#      → 缺唯一索引这类结构缺失不能配 expect=exists，否则该表永久标红；
#        须先补 DDL 再加规则。本批次因此只给「已有唯一索引」的表配结构规则。
#   2) 大表禁用 row_count_total（COUNT(*) 全扫）→ 用 row_count_slice 走 MAX 索引；
#      但事件型稀疏表（最新日仅数行）只能用 row_count_total。
# ============================================================================

# ---------- A. 活跃表补全（3 张）----------
COVERAGE_RULES = [
    ("index_cons_fresh", "index_constituents", "date_floor",
     {"date_col": "trade_date", "days_back": 45}, "critical", 1,
     "成分快照新鲜度：月度任务（每月15日 21:30），快照日期不得早于 45 天前（防连续漏跑）"),
    ("index_cons_rows", "index_constituents", "row_count_total",
     {"min_rows": 2600}, "critical", 1,
     "成分股快照总行数下限（11 指数单份实测 2819；北证50 待 akshare 修复后补。"
     "2026-09-19 起快照按日留档、总行数会随快照份数累积，故该下限只升不降）"),
    ("index_cons_uniq", "index_constituents", "unique_index",
     {"cols": ["index_code", "stock_code", "trade_date"], "expect": "exists"}, "info", 1,
     "幂等保障：uk_index_stock_date（指数 × 成分 × 快照日）。**2026-09-19 §7-⑧ 改造**："
     "快照按日留档后，同一成分会在不同快照日重复出现，故唯一键必须含快照日；"
     "原 uk_index_stock(index_code, stock_code) 会让每轮采集覆盖历史、永远无法回溯归因"),
    ("index_cons_stock_code", "index_constituents", "regex_count",
     {"col": "stock_code", "pattern": "^[0-9]{6}$"}, "warning", 1,
     "成分股代码格式校验"),
    ("index_cons_weight_bounds", "index_constituents", "where_count",
     {"where": "weight IS NOT NULL AND (weight <= 0 OR weight > 100)", "max_count": 0},
     "warning", 1, "权重越界拦截：权重应落在 (0,100]"),
    ("index_cons_name_cover", "index_constituents", "where_count",
     {"where": "stock_name IS NULL OR stock_name = ''", "max_count": 0}, "warning", 1,
     "成分股名称覆盖（曾因国证列名读错致 750 行为空；采集器已加 stock_info 兜底回填）"),
    ("index_profile_rows", "index_profile", "row_count_total",
     {"min_rows": 13}, "warning", 1,
     "指数档案行数下限（13 只跟踪指数）"),
    ("index_profile_uniq", "index_profile", "unique_index",
     {"cols": ["index_code"], "expect": "exists"}, "info", 1,
     "幂等保障：uk_index_code"),
    ("index_profile_desc", "index_profile", "where_count",
     {"where": "description IS NULL OR description = ''", "max_count": 0}, "info", 1,
     "释义覆盖：13 只指数均应有简介"),
    ("ai_concept_rows", "finance_concept_analysis", "row_count_total",
     {"min_rows": 600}, "warning", 1,
     "AI 概念分析结果行数下限（防误清空；实测 699）"),
    # 2026-09-14 启用：22 行 legacy 负值（-6~-1）已归一到绝对值——早期版本用「符号表方向」
    # （负=利空），与现行「1~10 表强度、relation_type 表方向」口径冲突；已在
    # concept_ai._clamp_degree 加写入末关（入库前强制夹紧 1~10），三处写入路径全部覆盖。
    ("ai_concept_dim_range", "finance_concept_analysis", "where_count",
     {"where": "relation_degree < 1 OR relation_degree > 10", "max_count": 0},
     "warning", 1,
     "关联程度应落在 1~10（2026-09-14 清理 22 行 legacy 负值后启用；写入侧已加夹紧）"),
]

# ---------- B. 历史导入表冻结监护（原 10 张 → 现存 3 张）----------
# 设计要点：这些表**不加 freshness/date_floor**——期望日期永远对不上，加了必红。
# 真正风险是「被误删/误清」，故只配防清空类规则（全部走索引，秒级，可进 daily 组）。
# 2026-09-13 更新：ths_stock_dividend / securities_margin / stock_jgdy_detail 三张
#   已恢复采集 → 迁出本组，见下方 RECOVERED_RULES；futures/sw/fin/shares 四张
#   已恢复采集 → 迁出本组，见下方 RECOVERED_RULES_B34。
# ⚠️ stock_market_daily_ex 归档评估结论（2026-09-13 全表逐字段比对，**推翻此前抽样结论**）：
#   公共键 16,891,204 行中 14,360,378 行（85%）六字段不同，253 万行全等；
#   抽样验证 daily.close 为前复权（2007 年万科 0.17~15 元、存在 58.7 万行负价退化区），
#   ex.close 为不复权原始价（同期 21~39 元）——**两表是复权口径差异，非冗余副本**，
#   且 ex 另有 109 个 daily 缺失的键（B 股 200020/200429/200726、北交所 430556 等）。
#   **不能归档**，维持冻结监护；它是库内唯一保留原始价的表，可作为后续复权因子重建的基准。
FROZEN_RULES = [
    ("frozen_daily_ex_rows", "stock_market_daily_ex", "row_count_slice",
     {"date_col": "trade_date", "min_rows": 4000}, "warning", 1,
     "冻结监护·除权日线：最新日切片行数下限（**不复权原始价**，与 daily 前复权口径不同、非冗余副本，2026-09-13 全表比对推翻冗余结论；库内唯一原始价基准，不可归档）"),
    ("frozen_capital_flow_rows", "stock_capital_flow", "row_count_slice",
     {"date_col": "trade_date", "min_rows": 4000}, "warning", 1,
     "冻结监护·资金流向：最新日切片行数下限（停更于 2025-09，防误清空；东财域受限待复测）"),
    ("frozen_fin_abstract_rows", "stock_financial_abstract_ths", "row_count_slice",
     {"date_col": "report_date", "min_rows": 4000}, "warning", 1,
     "冻结监护·财务关键指标：最新报告期切片行数下限（停更于 2025-09，防误清空）"),
    ("frozen_shares_rows", "stock_shares", "row_count_total",
     {"min_rows": 140000}, "warning", 1,
     "冻结监护·股本事件表（稀疏，最新日仅数行→不适用切片）：总行数下限（防误清空）"),
    ("frozen_hold_by_fund_rows", "stock_hold_by_fund", "row_count_total",
     {"min_rows": 100000}, "warning", 1,
     "冻结监护·基金重仓：总行数下限（防误清空）"),
    ("frozen_sw_industry_rows", "stock_industry_sw", "row_count_total",
     {"min_rows": 6000}, "warning", 1,
     "冻结监护·申万行业：总行数下限（防误清空）"),
    ("frozen_futures_rows", "futures_spot_price", "row_count_total",
     {"min_rows": 130000}, "warning", 1,
     "冻结监护·期现价格：总行数下限（防误清空）"),
]

# ---------- C. 死表恢复采集（2026-09-13 新增，3 张）----------
# 这 3 张原属 FROZEN 组，2026-09-13 完成采集器落地（ths_dividend_sync /
# margin_sync / jgdy_sync）并全量补齐后，升级为**完整规则**——即加上 freshness。
# 阈值均为 dry-run 实测校准，确认「上线即 pass」。
RECOVERED_RULES = [
    ("dividend_fresh", "ths_stock_dividend", "date_floor",
     {"date_col": "board_date", "days_back": 200}, "warning", 1,
     "分红送配新鲜度：最新董事会日期不得早于 200 天前（分红披露季节性——年报3-4月/中报8月/三季报10月，最长空档约 5 个月）"),
    ("dividend_rows", "ths_stock_dividend", "row_count_total",
     {"min_rows": 130000}, "warning", 1,
     "分红送配总行数下限（当前 14.9 万；该表被 analysis/dividend 分红率分析消费）"),
    ("margin_freshness", "securities_margin", "freshness_daily",
     {"date_col": "trade_date", "warn_days": 3, "grace_days": 1}, "warning", 1,
     "融资融券对齐交易日历（沪深所 T+1 发布）：grace_days=1 表示交易日盘后只能拿到 T-1，属固有滞后不算异常；连停 2 日 → warning，>3 日 → fail（2026-09-14 加 grace，此前每个交易日盘后恒 warning，是固定假信号）"),
    ("margin_rows", "securities_margin", "row_count_total",
     {"min_rows": 3500}, "warning", 1,
     "融资融券总行数下限（三市合计口径：沪+深+北）"),
    ("jgdy_fresh", "stock_jgdy_detail", "date_floor",
     {"date_col": "announcement_date", "days_back": 30}, "warning", 1,
     "机构调研新鲜度：最新公告日期不得早于 30 天前（调研公告日频但存在假期空档）"),
    ("jgdy_rows", "stock_jgdy_detail", "row_count_total",
     {"min_rows": 20000}, "warning", 1,
     "机构调研明细总行数下限（恢复采集后 4.5 万+）"),
]

# ---------- D. 死表恢复采集 · 第 3/4 批（2026-09-13 新增，4 张）----------
# 这 4 张原属 FROZEN 组，完成采集器落地（futures_sync / sw_industry_sync /
# financial_abstract_sync / stock_shares_sync）并全量补齐后升级为完整规则。
# 阈值全部为 dry-run/实测校准（2026-09-13），确认「上线即 pass」：
#   futures_spot_price            149,514 行 / 2012-01~2026-09-11 / 56 品种 / 每日恒定 54 行
#   stock_industry_sw              10,428 行 / 5,214 只 / 162 行业（31 一级 + 131 二级）
#   stock_financial_abstract_ths  344,049 行 / 5,854 只 / MAX 报告期 2026-06-30
#   stock_shares                   179,396 行 / 5,744 只 / MAX 变动日 2026-09-11
# 未恢复：stock_capital_flow（东财域不可达 + 口径待复核）继续留在 FROZEN 组。
#
# ⚠️ futures_spot_price **无唯一索引**（同日多快照且值不同，物理上不可建），
#    故本组刻意不给它配 unique_index 规则（配了必红）。
RECOVERED_RULES_B34 = [
    # -- 期货现货价格与基差 --
    ("futures_freshness", "futures_spot_price", "freshness_daily",
     {"date_col": "trade_date", "warn_days": 4}, "warning", 1,
     "期现价格对齐交易日历（源 100ppi 日频；含周末/假期缓冲，容错 4 日）"),
    ("futures_rows", "futures_spot_price", "row_count_total",
     {"min_rows": 130000}, "warning", 1,
     "期现价格总行数下限（防误清空；实测 14.95 万，日增 ~54）"),
    ("futures_rows_latest", "futures_spot_price", "row_count_slice",
     {"date_col": "trade_date", "min_rows": 50}, "critical", 1,
     "最新日期品种数下限（近 250 日实测恒定 54/日；防‘假成功’只写几行）"),
    # -- 申万行业分类 --
    ("sw_rows", "stock_industry_sw", "row_count_total",
     {"min_rows": 6000}, "warning", 1,
     "申万行业分类总行数下限（整体重建；实测 10,428 = 5,214 只 × 两级行业）"),
    ("sw_stock_code", "stock_industry_sw", "regex_count",
     {"col": "stock_code", "pattern": "^[0-9]{6}$"}, "warning", 1,
     "成分股票代码格式校验"),
    ("sw_uniq", "stock_industry_sw", "unique_index",
     {"cols": ["stock_code", "industry_type"], "expect": "exists"}, "info", 1,
     "幂等保障：uk_stock_level（同一股票同一级别行业唯一）"),
    # -- 财务关键指标 --
    ("fin_abstract_rows", "stock_financial_abstract_ths", "row_count_total",
     {"min_rows": 300000}, "warning", 1,
     "财务摘要总行数下限（稀疏事件表按报告期计，实测 34.4 万；不适用日切片规则）"),
    ("fin_abstract_floor", "stock_financial_abstract_ths", "date_floor",
     {"date_col": "report_date", "days_back": 230}, "warning", 1,
     "财务报告期下限：最新报告期不得早于 230 天前（季度披露 + 年报/一季报 4-30 截止的极端空档；防整体停更）"),
    ("fin_abstract_uniq", "stock_financial_abstract_ths", "unique_index",
     {"cols": ["stock_code", "report_date"], "expect": "exists"}, "info", 1,
     "幂等保障：uk_stock_report"),
    # -- 股本变动 --
    ("shares_rows", "stock_shares", "row_count_total",
     {"min_rows": 140000}, "warning", 1,
     "股本变动总行数下限（防误清空；实测 17.9 万并在补齐中）"),
    ("shares_floor", "stock_shares", "date_floor",
     {"date_col": "change_date", "days_back": 45}, "warning", 1,
     "股本变动日期下限：最新变动日不得早于 45 天前（事件型稀疏表——月度事件量 13~1804 不等，留足空档）"),
    ("shares_total_positive", "stock_shares", "where_count",
     {"where": "total_shares <= 0", "max_count": 0}, "warning", 1,
     "总股本必须为正（采集器已挡 total_shares IS NULL/<=0，此处为入库后复核）"),
    ("shares_uniq", "stock_shares", "unique_index",
     {"cols": ["stock_code", "change_date"], "expect": "exists"}, "info", 1,
     "幂等保障：uk_stock_date"),
]

# ============================================================================
# E. 市场风向蓝图 P2/P3 落地（2026-09-19）
# 依据 docs/市场风向数据蓝图落地审计_2026-09-19.md §5。本批做两件事：
#   (1) 补上 §7-⑤「美债断供 10 天零报警」的盲点 —— 新增 check_type `column_watermark`；
#   (2) 为 6 张新采集表配完整规则（非空/新鲜度/取值域/唯一索引）。
# 阈值全部按 2026-09-19 实际入库状态校准，确认「上线即 pass」。
#
# ⚠️ 为什么需要新检查器 column_watermark：既有 14 类检查器**结构上**看不见「有列无值」——
#    null_rate_slice 只看最新切片（而空缺往往在最新日之前的一整段，空值率恒 0）；
#    freshness_daily / date_floor 取的是 date_col 的 MAX，与 value_col 无关
#    （「表在正常更新、某列已死」它们不可能发现）。美债断供 10 天就是这么漏掉的。
# ============================================================================
BLUEPRINT_RULES = [
    # -- §7-⑤ 美债断供补盲点（本批最重要的一条：此前断供 10 天无任何规则覆盖） --
    ("bond_us_notnull", "bond_profit_daily", "column_watermark",
     {"date_col": "trade_date", "value_col": "us_bond_10y", "max_gap_rows": 2}, "critical", 1,
     "美债 10Y 列水位线：最后非空日之后堆积的行数不得超过 2。容忍 2 的理由——1 = 当日美债尚未发布"
     "（美债在北京时间次日凌晨才出，每晚 19:15 拉取时当日必为 NULL，属固有滞后）；"
     "2 = 再叠加一个美国假期（感恩节/圣诞）。实测 2026-09-07~09-18 连续 10 个交易日全 NULL 时"
     "**没有任何规则报警**，纯靠人工复测才发现，本规则即为堵住该盲点"),

    # -- P2 估值（index_valuation_daily，蓝图 B 的估值分母） --
    ("valuation_fresh", "index_valuation_daily", "freshness_daily",
     {"date_col": "trade_date", "warn_days": 5, "grace_days": 3}, "warning", 1,
     "指数估值新鲜度：乐咕/全A 为「月末 + 最新」序列，非月末日靠最新一个点覆盖，故容忍 5 日"
     "（grace 3 日覆盖周末 + 假期）"),
    ("valuation_rows", "index_valuation_daily", "row_count_total",
     {"min_rows": 1000}, "warning", 1,
     "估值总行数下限（中证官网 6 指数×20 日 + 乐咕 4 指数全史 ~900 + 全A ~260 ≈ 1,280；防误清空）"),
    ("valuation_pe_range", "index_valuation_daily", "where_count",
     {"where": "pe_ttm IS NOT NULL AND (pe_ttm <= 0 OR pe_ttm > 300)", "max_count": 0},
     "warning", 1, "滚动市盈率取值域 (0, 300] —— 上限放到 300 是因全A 中位口径与科创50 会到三位数"),
    ("valuation_anchor_notnull", "index_valuation_daily", "column_watermark",
     {"date_col": "trade_date", "value_col": "pe_ttm", "max_gap_rows": 5}, "warning", 1,
     "估值腿水位线：滚动 PE 最后非空日之后不得堆积超过 5 行（三源任一更新即算，容忍假期与月末节奏）"),
    ("valuation_uniq", "index_valuation_daily", "unique_index",
     {"cols": ["index_code", "trade_date", "source"], "expect": "exists"}, "info", 1,
     "幂等保障：uk_index_date_source（三源并存，故源必须进唯一键）"),

    # -- 蓝图 A 拆借利率（interbank_rate_daily，「钱贵不贵」） --
    ("interbank_fresh", "interbank_rate_daily", "freshness_daily",
     {"date_col": "trade_date", "warn_days": 5, "grace_days": 2}, "warning", 1,
     "Shibor 新鲜度（Shibor 每工作日 11:00 发布；grace 覆盖周末与假期）"),
    ("interbank_shibor_notnull", "interbank_rate_daily", "column_watermark",
     {"date_col": "trade_date", "value_col": "shibor_on", "max_gap_rows": 5}, "warning", 1,
     "Shibor 隔夜水位线：防「LPR 顺延填充把日期撑起来、Shibor 腿其实已死」这种假活"),
    ("interbank_lpr_range", "interbank_rate_daily", "where_count",
     {"where": "lpr_1y IS NOT NULL AND (lpr_1y <= 0 OR lpr_1y > 10)", "max_count": 0},
     "warning", 1, "LPR 1Y 取值域 (0, 10]%（同样是「顺延填充」的守护：不出现 0 或异常值）"),

    # -- 蓝图 E 跨市场（overseas_index_daily） --
    ("overseas_fresh", "overseas_index_daily", "freshness_daily",
     {"date_col": "trade_date", "warn_days": 5, "grace_days": 2}, "warning", 1,
     "海外指数新鲜度（恒生与美股交易日历不同，取二者较新者，grace 覆盖各自假期）"),
    ("overseas_rows_latest", "overseas_index_daily", "row_count_slice",
     {"date_col": "trade_date", "min_rows": 3}, "warning", 1,
     "最新日海外指数条数下限（共 4 个指数；港股与美股日历不同，同日通常 3~4 个）"),
    ("overseas_close_range", "overseas_index_daily", "where_count",
     {"where": "close IS NOT NULL AND close <= 0", "max_count": 0}, "warning", 1,
     "收盘价必须为正"),
    ("overseas_uniq", "overseas_index_daily", "unique_index",
     {"cols": ["index_code", "trade_date"], "expect": "exists"}, "info", 1,
     "幂等保障：uk_code_date"),

    # -- 蓝图 E 汇率（currency_boc_daily） --
    ("currency_fresh", "currency_boc_daily", "freshness_daily",
     {"date_col": "trade_date", "warn_days": 5, "grace_days": 2}, "warning", 1,
     "外汇牌价新鲜度（中行牌价按工作日发布）"),
    ("currency_usd_mid_range", "currency_boc_daily", "where_count",
     {"where": "currency = 'USD' AND mid_price IS NOT NULL AND (mid_price < 5 OR mid_price > 10)",
      "max_count": 0}, "warning", 1,
     "美元中间价取值域 5~10 元 —— 同时是**单位守护**：源按「每 100 外币」报价（675.80），"
     "采集器已 ÷100 归一为 6.7580；哪一轮忘了归一，这条立刻变红"),
    ("currency_uniq", "currency_boc_daily", "unique_index",
     {"cols": ["currency", "trade_date"], "expect": "exists"}, "info", 1,
     "幂等保障：uk_currency_date"),

    # -- 蓝图 E 新基金发行（fund_new_issue，发行冰点=反向底部信号） --
    ("fund_new_issue_rows", "fund_new_issue", "row_count_total",
     {"min_rows": 5000}, "warning", 1, "新基金发行总量下限（实测 6,848 只；防误清空）"),
    ("fund_new_issue_floor", "fund_new_issue", "date_floor",
     {"date_col": "establish_date", "days_back": 60}, "warning", 1,
     "最近成立基金日期下限（发行节奏连续，60 天无新成立基金说明采集断档）"),
    ("fund_new_issue_share_range", "fund_new_issue", "where_count",
     {"where": "raise_share IS NOT NULL AND (raise_share < 0 OR raise_share > 5000)", "max_count": 0},
     "warning", 1, "募集份额取值域（单位亿份，单只理论上限取 5000 的宽松值）"),

    # -- 蓝图 D 股票回购（stock_repurchase，产业资本态度） --
    ("repurchase_rows", "stock_repurchase", "row_count_total",
     {"min_rows": 4000}, "warning", 1, "回购记录总量下限（实测 5,516 单；防误清空）"),
    ("repurchase_fresh", "stock_repurchase", "date_floor",
     {"date_col": "announce_date", "days_back": 60}, "warning", 1,
     "回购公告新鲜度（回购公告日频，60 天无新公告说明采集断档）"),
    ("repurchase_amount_order", "stock_repurchase", "where_count",
     {"where": "plan_amount_low IS NOT NULL AND plan_amount_high IS NOT NULL "
               "AND plan_amount_low > plan_amount_high", "max_count": 0}, "warning", 1,
     "金额区间自洽：计划金额下限不得大于上限"),
    ("repurchase_uniq", "stock_repurchase", "unique_index",
     {"cols": ["stock_code", "start_date"], "expect": "exists"}, "info", 1,
     "幂等保障：uk_code_start"),

    # -- §7-② 两融口径：把复核结论钉死（原报告疑为「列名与值不符」，实测不成立） --
    ("margin_cz_identity", "securities_margin", "where_count",
     {"where": "rzrqyecz IS NOT NULL AND rzrqye IS NOT NULL AND rzye IS NOT NULL AND rqye IS NOT NULL "
               "AND ABS(rzrqyecz - (rzye - rqye)) > 1", "max_count": 0}, "warning", 1,
     "两融差值恒等式守护：rzrqyecz 必须 ≡ rzye − rqye。2026-09-19 全表 3,980 行复核差值恒为 0，"
     "**确认列名与值一致、原「口径错配」疑虑不成立**（融券腿仅占 1% 量级，故该列信息量低）。"
     "本规则把结论钉死：口径若日后漂移立刻变红"),
]

# 已废弃规则：每次 seed 时显式删除（避免升级后旧冻结规则与新规则并存产生噪音）
RETIRED_RULES = [
    "frozen_dividend_rows",       # → dividend_fresh + dividend_rows
    "frozen_margin_rows",         # → margin_freshness + margin_rows
    "frozen_jgdy_rows",           # → jgdy_fresh + jgdy_rows
    # 2026-09-13 第 3/4 批恢复 → 升级为 RECOVERED_RULES_B34 完整规则
    "frozen_futures_rows",        # → futures_freshness + futures_rows + futures_rows_latest
    "frozen_sw_industry_rows",    # → sw_rows + sw_stock_code + sw_uniq
    "frozen_fin_abstract_rows",   # → fin_abstract_rows + fin_abstract_floor + fin_abstract_uniq
    "frozen_shares_rows",         # → shares_rows + shares_floor + shares_total_positive + shares_uniq
    # 保留：frozen_capital_flow_rows（东财域不可达，采集器已实现但默认禁用）
]


def main():
    conn = pymysql.connect(**get_db_config().to_dict())
    try:
        all_rules = (
            [(r, "daily") for r in RULES]
            + [(r, "weekly") for r in WEEKLY_RULES]
            + [(r, "daily") for r in COVERAGE_RULES]      # 2026-09-13 活跃表补全
            + [(r, "daily") for r in FROZEN_RULES]        # 2026-09-13 历史表冻结监护
            + [(r, "daily") for r in RECOVERED_RULES]     # 2026-09-13 死表恢复（第 1 批）
            + [(r, "daily") for r in RECOVERED_RULES_B34]  # 2026-09-13 死表恢复（第 3/4 批）
            + [(r, "daily") for r in BLUEPRINT_RULES]      # 2026-09-19 蓝图 P2/P3 落地（6 表 + 美债补盲点）
        )
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
            # 废弃规则清理（升级后避免新旧并存产生重复告警）
            retired = 0
            if RETIRED_RULES:
                ph = ", ".join(["%s"] * len(RETIRED_RULES))
                cur.execute(f"SELECT COUNT(*) FROM dq_rules WHERE rule_name IN ({ph})", tuple(RETIRED_RULES))
                retired = cur.fetchone()[0]
                cur.execute(f"DELETE FROM dq_rules WHERE rule_name IN ({ph})", tuple(RETIRED_RULES))
        conn.commit()
        with conn.cursor() as cur:
            cur.execute("SELECT rule_group, COUNT(*) FROM dq_rules GROUP BY rule_group")
            dist = cur.fetchall()
            cur.execute("SELECT COUNT(*) FROM dq_rules")
            total = cur.fetchone()[0]
        print(f"seed 完成，dq_rules 共 {total} 条规则，分组分布: {dist}")
        if retired:
            print(f"已清理废弃规则 {retired} 条（配置清单 {len(RETIRED_RULES)} 条，其余此前已删除）: {RETIRED_RULES}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
