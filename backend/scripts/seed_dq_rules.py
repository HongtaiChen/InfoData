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
- BLUEPRINT_RULES ：2026-09-19 蓝图 P2/P3 落地（估值/美债等 6 表 + 美债断供补盲点）。
- CONSUMPTION_RULES：2026-09-19 Batch C 消费端守护 —— 5 张「只进不出」的表接上
                   分析研究消费端（钱贵不贵 / 跨市场对照 / 资金温度）后，
                   补「某一条腿、某一列还在更新吗」类规则（既有规则只守整表更新）。
                   配套新增 `date_floor_where` 检查器（见 data_quality_check.py）。详细理由见该列表头部注释。
- REF_RULES        ：2026-09-24 引用完整性 + 名册停更守护 —— 名册曾漏收 89 只 A 股
                   （含沪深300 成分 001280 中国铀业）**潜伏数月无人发现**，直到前端
                   「行业分布」出现「其他(74)」被追问。配套新增 `ref_missing` 检查器
                   （跨表判据的唯一形态：where 白名单不允许子查询）。见 REF_RULES 头部注释。
- LIQUIDITY_RULES   ：2026-09-25 货币流动性批次 2 —— 3 张新表（中国货币供应月度 /
                   中国央行资产负债表月度 / 多国央行政策利率）+ 1 张自算派生表
                   （美元指数自算 + DINIW 快照标定）。**两条刻意 enabled=0**（已知上游停更：
                   社融无源可采、macro_bank_* 全族停更），理由逐条写在规则 description 里。
                   含一条「标价法单位错」通用网 `dxy_vs_snapshot`，来自本轮实测事故。见该列表头部注释。
- RESERVE_RULES    ：2026-09-26 批次 C2 —— 中国官方外汇储备 / 黄金储备（`cn_reserve_monthly`，
                   419 行 1978-12 起）。配套新增 `date_ceiling` 检查器（**日期不得落到未来**）
                   —— 源月份列是字符串 `'YYYY.M'`，解析写错会静默写出未来月份，而那种行会让
                   freshness / column_watermark 全部通过（唯一能穿透新鲜度守护的形态）；
                   且该判据**无法用 where_count 表达**（白名单不放行 CURDATE/NOW）。
                   两条量级阈值按实测**重定**（方案原「2000 年后」会误报 2010-2014 段）。见该列表头部注释。

⚠️ 维护纪律（2026-09-13 踩坑）：**本脚本是 dq_rules 的唯一事实来源**。
   任何绕过脚本的直改 DB（如事故应急调阈值）必须同步回本文件，
   否则下次跑 seed 会把手工调整静默覆盖回去（已发生：concept_market 两条规则
   09-12 手工调过阈值，09-13 跑 seed 被还原成旧值导致体检变红）。

规则分组（dq_rules.rule_group）：
- daily ：每日盘后 **22:45** 跑（最新切片类，秒级；主触发=链式延迟，见下）
- weekly：每周一 21:30 独立任务跑（全史窗口扫描类）

⚠️ daily 组的执行时点（2026-09-22 重排，**这是理解多条 freshness 规则为何无 grace 的前提**）：
  实际执行时刻 = `scheduler.CHAIN_NOT_BEFORE["data_quality_check"] = 22:45`，
  由链式在 market_current_sync 成功后挂一次性 job 到点触发；`task_config` 里的
  `0 23 * * *` 只是**兜底**（仅在当日链式没跑成时才真正执行）。
  为什么必须这么晚：本组的 freshness 规则守护的表由 22:00~22:30 的采集写入，
  体检若早于它们（原为链式紧跟上游的 20:02）就会数到 T-1 → 每个工作日恒 warning。
  故 2026-09-22 起：**freshness 类规则一律不加 grace_days**，靠时点正确而非靠放宽来 pass。

weekly 组设计说明（2026-09-10 立，2026-09-20 随 daily 口径改造刷新）：
- daily_gap_scan        全史疑似缺口（LAG 窗口，18M 行 ~196s）→ 明细入 dq_gap_detail
- daily_ohlc_consistent 全史 OHLC 自洽（实测 0 违反，守护型）
- daily_change_link     涨跌幅与前收衔接（恒等式，构造上必成立）。
  ⚠️2026-09-20 **二次修订**：原判据用 **pp 口径 0.02pp** —— 它与价格量级无关，而低价股的
  3 位小数舍入 ≈0.05/pre_close pp 会直接顶穿阈值 ⇒ 误报 72,614 行（99.97% 在 <3 元，
  而全表 <3 元仅占 4.13%）。改为「元口径 + 存储精度上界」后实测 **0 行**。
  adj_factor 过滤保留，但理由改写：**不是**旧口径行会失败，而是 AUSAHRE 段历史行的
  pct 只存 2 位小数（22 行，由 legacy_scale_rows 上账）。
- daily_coverage_recent 近 250 日每票行数下限（实测 0 异常）
- daily_source_handoff  跨源衔接偏差（实测 0 行，改造后按「前收盘 vs 上一笔 close×因子比」比）
- daily_factor_link     🆕2026-09-20：全史因子自洽，整个复权体系的**最强单一判据**。
  ⚠️窗口必须在**未过滤**全序列上做 LAG —— 旧版把 WHERE adj_factor IS NOT NULL 写在内层
  再 LAG 会造「跳跃相邻」，把「口径未知的空因子区」误报成「因子写错」（实测 1,000 行）。
  ❌2026-09-21 旧定性「低价股 + 2 位小数源舍入退化」**只解释主体、解释不了尾部**；
  ❌2026-09-22 早前「缩股/重整导致价格跳变而因子未跟随」**已被推翻**（因子未变恰恰证明无除权，
  此时 pre_close 必须等于上一笔 close，124.83 vs 2.09 任何因子解释都推不出）。
  ✅2026-09-22 v3 **已查清真因**：`pre_close`/`change_pct` **不是源字段**，而是
  `stock_daily_core.py` L311–326 用**后复权(hfq)序列的逐日比值 `ret`** 反算
  （`pct=ret×100`、`pre=close/(1+ret)`、`ret≤-1 时 pre 强制 NULL`）⇒ **ret 失真则两列同错**。
  决定性指纹：全库 `change_pct ≤ -100%` 共 307 行，其中 **307 行 pre_close IS NULL、0 反例**，
  与代码护栏完全一致。`close`/OHLC **不受影响**（daily_ohlc_consistent pass）。
  ⚠️ 故本条的「偏差 >0.5%」实为**同一批 pre_close 失真行**，`max_count=1000` 是**上账不是豁免**；
  是否重算该列属口径取舍（取证报告 §八，可不回源重算），**未擅自动手**。
  详见 docs/昨收与涨跌幅列失真取证_2026-09-22.md
  （原决策项 ⑧ 已撤回，详见 docs/2026-09-20_日线口径重建收尾报告.md §9）。
  ⚠️2026-09-22 补（本次）：既然这 1,000 行是**已解释、已接受**的技术债，而检查器原实现是
  `n != 0 即 warning`，该规则自建立起**从未 pass 过**（09-20/09-21 三次执行全 warning）
  —— 与 margin_freshness / pct_limit 同类「恒定假信号」。故按本项目既有**上账**策略
  （见 legacy_scale_rows：设固定上限、平时静止、增长即新故障）为它加 `max_count=1000`。
  检查器侧新增 `max_count` 参数支持（默认 0，向后兼容）。
  ⚠️实测成本：全史窗口查询 **432s**（7.2 分钟），是周组最重的一条。
- daily_pct_limit_rows  🆕2026-09-21：涨跌幅**板块上限**违规数 = 0（主板±10/创业科创±20/
  北交所±30，留 1pp 容差）。这是决策项 ⑧ 核查时发现的**真缺口**：旧判据用
  `ABS(change_pct)>11` 隐含「全市场涨跌停=10%」的错误前提，把 73,285 行**合法涨停**
  判成异常（77% 是创业板 ±20% 被误杀）。按板块重判后 16,491 行，其中 72% 在 2013 年前、
  78% 带因子（属本体系内）→ 早期数据质量 + 低价舍入，性质同 legacy_scale_rows，列决策项 ⑨。
  本检查器的价值：**与复权口径无关**的物理约束，能同时守护「换源价格单位错」
  「价格写错」「复权断阶」三类故障。
  ⚠️2026-09-22 v3 勘误：把 16,491 行笼统写成「低价舍入」**不准确**。分域实测（详见
  docs/昨收与涨跌幅列失真取证_2026-09-22.md）：全史 |pct|>31% 共 4,721 行，其中
  718 行在 1996-12-16（涨跌停制度实施日）之前、3,160 行在 4/8/92 段（新三板协议转让
  **本无涨跌停**）→ **这两类的大波动可能合法，不能判脏**；其余 843 行中「因子未变
  （无除权）却仍跳变」的 **92 行**才是可确证错（❌原写 202 已作废 —— 该数实为 constrained
  域 `same_factor=1` 的总数、含正常的 `pre_close==prev_close` 行；同口径实测 92，勘误见取证报告 §五/§十-6），
  真因是 `pre_close`/`change_pct` 列由
  **后复权序列比值 `ret` 反算**、`ret` 在个别日期被源侧 hfq 阶跃打歪（见 factor_link 条）。
  故本条的 `max_count` 上账基线**混入了合法行**，属粗粒度护栏，细粒度判据见 factor_link。
- legacy_scale_rows     🆕2026-09-20：旧口径残留**显式上账**（上限 90,000，实测 75,173）。
  平时静止，一旦增长即说明又有数据悄悄落进旧口径。**是上账不是豁免**。
- 已废弃：daily_amount_cross（量额勾稽）—— 2026-09-10 废弃理由是「volume/amount 为真实值
  而 OHLC 为前复权值，两者不同口径，勾稽数学退化」。**2026-09-20 该理由随口径改造消失**
  （OHLC 已同为真实成交价），其职责由 daily 组的 `daily_price_vwap`（VWAP ∈ [low,high]）
  完整承接，故仍不单列规则，但「不可实现」的定性已作废。

⚠️ 周组实测耗时（2026-09-20）：合计 **~39 分钟**（原注「4~7 分钟」已过期）。
  成本几乎全在 daily_factor_link 的全史窗口函数（18.5M 行 LAG）。


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
    # ⚠️2026-09-22 口径标注（取证时发现描述与实现不符，曾致误信「已覆盖全史」）：
    #   `violation_count` 检查器带切片谓词 `trade_date = (SELECT MAX(trade_date) FROM 表)`，
    #   **只判最新一个交易日**。故这条描述里的「|涨跌幅|>31%」**不覆盖 1,850 万行历史**。
    #   全史同类判据在 weekly 组：`daily_pct_limit_rows`（按板块上限）与 `daily_factor_link`。
    #   实测教训：`daily_value_bounds` 一直 pass，而全史其实有 4,721 行 |pct|>31%
    #   （占 0.026%，其中「无除权却跳变」202~333 行可确证错）。
    #   详见 docs/昨收与涨跌幅列失真取证_2026-09-22.md
    ("daily_value_bounds", "stock_market_daily", "violation_count",
     {"date_col": "trade_date", "where": "close<=0 OR volume<0 OR ABS(change_pct)>31", "max_count": 0},
     "warning", 1,
     "脏值拦截（**仅最新交易日切片**，非全史）：close≤0 / 量为负 / |涨跌幅|>31%（北交所 30cm 容差）。"
     "全史同类判据见 daily_pct_limit_rows / daily_factor_link"),
    # 2026-09-20 随「实际价 + adj_factor」口径改造新增两条守护
    ("daily_adj_factor_null", "stock_market_daily", "null_rate_slice",
     {"date_col": "trade_date", "col": "adj_factor", "max_pct": 2.0}, "warning", 1,
     "最新日 adj_factor 空值率 ≤2%（空=该日源未返回、口径未知；大面积空=重建/增量漏写因子，"
     "会让前复权/后复权/收益率三个派生口径一并失真）"),
    ("daily_price_vwap", "stock_market_daily", "violation_count",
     {"date_col": "trade_date",
      "where": "volume>500000 AND amount>0 AND close>0 "
               "AND (amount/volume < low*0.85 OR amount/volume > high*1.15)",
      "max_count": 0}, "critical", 1,
     "**实际价判据**：当日 VWAP=amount/volume 必须落在 [low,high] 内（±15% 容差）。"
     "复权价会与 amount/volume 脱钩（库内旧值曾出现茅台 2016 low=-35.78 这种减法前复权负价、"
     "以及偏离实际价 5% 的 1602.65），故这条是「存的是不是真实成交价」的直接体检"),
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
    # 2026-09-22 处置：**时点错位而非数据异常**。本表由 xcheck_sync 在 22:30 写入，
    # 而 DQ 实际在链式里紧跟 market_current_sync（约 20:02）跑，故 behind 恒为 1 →
    # 每个工作日必 warning。当日的处置分两步：
    #   ① 【治本·同日生效】把 DQ 推到所有采集之后（scheduler.CHAIN_NOT_BEFORE 22:45）——
    #      22:45 时 xcheck 22:30 已落库，behind=0 天然 pass；
    #   ② 【治标·已撤回】当日曾先加 grace_days=1 压制，时点改对后**撤回**：留着它会把
    #      「当日数据根本没落库」也一起放过，等于把这条监控废掉。现在 behind=1 即 warning，
    #      这才是它该有的敏感度（xcheck 是秒级任务，正常不应迟到）。
    ("xcheck_freshness", "market_xcheck_daily", "freshness_daily",
     {"date_col": "trade_date", "warn_days": 2}, "warning", 1,
     "背离数对齐交易日历（每工作日 22:30 xcheck_sync 写入，须晚于概念补班 22:00）。"
     "无 grace：DQ 已改到 22:45，当日数据未落库就该报警"),
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
    # 2026-09-19 补盲点：本表「行数达标即 pass」曾掩盖**整表双写**。
    # 实测 09-19 该表 10,242 行 / 仅 5,121 只股票 = 每只恰好重复 2 次
    # （同一 update_time、同一 data_source=daily-agg，仅 id 不同），
    # 而 current_rows(≥4500) 与 current_code 两条规则全都 pass —— 因为 10,242 也 ≥4500。
    # 真因：market_current_sync 的写入是「TRUNCATE + 全量重建」，本身幂等，
    # 但**并发两份同时跑**会让两边交错写入 → 双份。触发条件是调度器 _execute 的
    # TOCTOU 竞态（链式线程 vs 启动补跑线程），已于同日修复。
    # 本条是**结构性防线 + 回归探测器**：唯一键一旦缺失/被删，立刻变红。
    # 2026-09-19 新增：total_captital / float_captital 由「恒 NULL」改为本地派生填充
    # （market_current_sync 取 stock_shares 每只股票 MAX(change_date) 的最新股本）。
    # 这两条是这个派生的守护：① 派生断供（列又空了）② 量纲/口径搞反（流通 > 总股本）。
    # 背景：这两个列此前恒空，导致 api/market.py 的「按市值排序」静默失效（ORDER BY NULL）。
    ("current_cap_notnull", "stock_market_current", "where_count",
     {"where": "total_captital IS NULL OR total_captital <= 0", "max_count": 0}, "warning", 1,
     "总股本（total_captital）非空且为正：本列由 stock_shares 本地派生（实测名单覆盖率 100%），"
     "它同时是 api/market.py 「按市值排序」的依据——一旦回空，市值排序会静默失效"),
    ("current_cap_order", "stock_market_current", "where_count",
     {"where": "float_captital IS NOT NULL AND total_captital IS NOT NULL "
               "AND float_captital > total_captital", "max_count": 0}, "warning", 1,
     "股本科级自洽：A 股流通股（float_captital）不得大于总股本（total_captital），"
     "越界说明 stock_shares 两列取错或量纲不一致"),
    ("current_uniq_code", "stock_market_current", "unique_index",
     {"cols": ["stock_code"], "expect": "exists"}, "critical", 1,
     "快照必须存在 stock_code 唯一索引：本表 TRUNCATE+全量重建，"
     "无唯一键时并发派发会把整表写成每只股票 2 份（2026-09-19 实测 10,242/5,121=2.00x，"
     "且行数类规则全部无法察觉）。同时它也是 UI 侧 /api/market/list 总数翻倍的根因"),
    # ---------- 概念 ----------
    # 2026-09-22 处置（同 xcheck_freshness，两处一并改正）：
    #   ❌ 推翻当日早前的写法：「task_config 里登记的 `50 21` 经实测全史从未触发」——错。
    #      该班次**照常触发**，只是 `_run_scheduled` 见当日链式已 success（约 20:02）便走
    #      「⏭ 兜底班次跳过」分支直接 return，**不写 task_runs**，故按 started_at 查不到
    #      21 时 50 分的记录。现象是真的，归因错了（见 scheduler.py CHAIN_FALLBACK_TASKS）。
    #   ✅ 真因：DQ 实际执行在 20:02，早于 concept_market_sync 的 21:00/22:00 两批，
    #      behind 恒为 1（T-1）。实测 09-18/09-21/09-22 三连 warning，09-19/09-20 因周末才 pass。
    #   ① 治本：DQ 改到 22:45（CHAIN_NOT_BEFORE）→ 概念补班 22:00 已落库，behind=0 天然 pass；
    #   ② 治标撤回：原先加的 grace_days=1 已去掉，恢复「当日概念没落库即报警」的敏感度。
    ("concept_market_freshness", "ths_concept_market", "freshness_daily",
     {"date_col": "trade_date", "warn_days": 5}, "warning", 1,
     "概念指数日线对齐交易日历（同花顺周级波动容忍，2026-09-12 由 2 放宽至 5）。"
     "无 grace：DQ 已改到 22:45（晚于 22:00 补班），当日概念未落库就该报警"),
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
    # ---------- 北交所口径守护（2026-09-20 立） ----------
    # 起因：北交所 2025-10-09 全面切换 920 独立代码段，本库没跟上 ——
    #   ① 277 条主表记录全是失效旧码 → 所有按 stock_code 的 JOIN（成分/行情/财务）北交所段全断；
    #   ② 切换后新上市的 69 只从未入库；③ 行业字段整段为空（北证50 行业分布画不出来）。
    # 三条规则把「这一次修好了」变成「不会再漂回去」。
    ("stock_info_bj_code_920", "stock_info", "where_count",
     {"where": "exchange='BJ' AND IFNULL(list_status,'')<>'退市' AND stock_code NOT LIKE '92%'",
      "max_count": 0}, "critical", 1,
     "北交所代码口径：**在市**标的必须已是 920 段（旧 43/83/87 段自 2025-10-09 起作废；"
     "已退市标的保留原码，故显式排除，否则规则会常红掩盖真问题）"),
    ("stock_info_bj_industry", "stock_info", "where_count",
     {"where": "exchange='BJ' AND IFNULL(list_status,'')<>'退市' AND (industry IS NULL OR industry='')",
      "max_count": 0}, "warning", 1,
     "北交所行业覆盖：在市标的 industry 必须非空 —— 该列对北交所只有一个来源"
     "（bj_stock_sync 从北交所官网名册取，巨潮不提供北交所档案），列空了必是该任务没跑成功。"
     "下游直接消费方：指数详情抽屉的「行业分布」"),
    ("stock_code_mapping_rows", "stock_code_mapping", "row_count_total",
     {"min_rows": 242}, "critical", 1,
     "代码对照台账行数下限 = 官方公告的存量切换只数 242（240 switched + 2 retired）；"
     "低于此值说明映射逻辑退化（bj_stock_sync 内部有同款不变式巡检，此处是落库侧的独立复核）"),
    ("stock_code_mapping_uniq", "stock_code_mapping", "unique_index",
     {"cols": ["old_code"], "expect": "exists"}, "info", 1,
     "幂等保障：uk_old_code（一旧码只对应一条新码）"),
    ("stock_code_mapping_fields", "stock_code_mapping", "where_count",
     {"where": "status NOT IN ('switched','retired') OR evidence IS NULL OR evidence=''",
      "max_count": 0}, "warning", 1,
     "台账完整性：status 取值合法 + evidence 必填（人工核证项若没写证据，这条映射就是猜测，"
     "而它决定了主表代码会被改成什么）"),
    # 2026-09-20 补：扩展表影子名册的旧码守护。
    # 起因：stock_info_ex 的写方 stock_info_sync 取自东财 spot 名单，而**东财不含北交所**，
    #   故该表北交所段永远不会被刷新 —— 主表 920 迁移后它整段留在旧码（实测 242 条，
    #   其中 2 条还挂着人工「高股息」标记）。bj_stock_sync 现已按台账同步迁移。
    # ⚠️ 该表**无 list_status 列**，主表那套「排除退市」用不了，只能用台账口径排除：
    #   2 只退市标的（835305/839680）本就无 920 对应码，硬排除它们会变成常红假信号。
    ("stock_info_ex_bj_code", "stock_info_ex", "where_count",
     {"where": "exchange='BJ' AND stock_code NOT LIKE '92%' "
               "AND stock_code NOT IN ('835305','839680')",
      "max_count": 0}, "warning", 1,
     "扩展表影子名册代码口径：北交所标的必须已是 920 段（排除台账 status='retired' 的无新码标的）；"
     "残留说明 bj_stock_sync 的扩展表迁移没跑到"),
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
     "全史 OHLC 自洽：high≥max(o,c) 且 low≤min(o,c) 且 volume≥0"
     "（>0 前置条件保留为防御性写法；口径改造后主表已是实际价、不再有负价区）"),
    ("daily_change_link", "stock_market_daily", "where_count",
     {"where": "adj_factor IS NOT NULL AND pre_close>0 AND close>0 AND change_pct IS NOT NULL "
               "AND ABS(close - pre_close*(1+change_pct/100)) "
               "    > 0.001 + 0.0005*ABS(change_pct)/100 + pre_close*0.000001",
      "max_count": 0}, "warning", 1,
     "涨跌幅与「收盘/前收」恒等式：|close−pre×(1+pct/100)| ≤ 存储精度上界"
     "（0.001+0.0005|pct|/100+pre×1e-6）。构造上恒成立（pre、pct 同源于 ret），非零只能是舍入。"
     "⚠️原判据用 pp 口径 0.02pp（与价格量级无关）⇒ 低价股舍入顶穿，误报 72,614 行"
     "（99.97% 在 <3 元）；改元口径后 0 行。adj_factor 过滤理由已改写：AUSAHRE 段 pct 只存 2 位小数。"
     "详见本文件头"),
    ("daily_factor_link", "stock_market_daily", "factor_link",
     {"date_col": "trade_date", "max_pct": 0.5, "max_count": 1000}, "warning", 1,
     "【上账·非豁免】全史因子自洽：pre_close(t) ≈ close(t-1)×adj_factor(t-1)/adj_factor(t)，"
     "偏差 >0.5% 的行数上限 1,000（复权体系最强单一判据）。⚠️这 1,000 行 2026-09-21 已查明"
     "非因子写错（95 票各 1 行、847 行涨跌幅正常，系低价股+2位小数源舍入退化）；原实现 n!=0 "
     "即 warning → 自建立起从未 pass，2026-09-22 改固定基线：平时静止、增长即新故障"),
    ("daily_pct_limit_rows", "stock_market_daily", "pct_limit",
     {"date_col": "trade_date", "tol_pp": 1.0,
      "limit_main": 10, "limit_star": 20, "limit_bj": 30,
      "max_count": 20000}, "warning", 1,
     "【上账·非豁免】涨跌幅超「板块上限+1pp」行数上限 20,000（主板±10/创业科创±20/"
     "北交所±30），实测 16,491 行（72% 在 2013 前、78% 带因子，成因为早期数据质量+"
     "低价舍入退化，非因子写错）。⚠️旧判据 ABS(change_pct)>11 隐含「全市场涨跌停=10%」"
     "的错误前提、误报 73,285 行（77% 系创业板 ±20% 被冤杀）。"
     "本判据与复权口径无关，守护换源单位错/价格写错/复权断阶（2026-09-21 立，见报告 §9）"),
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
    ("legacy_scale_rows", "stock_market_daily", "where_count",
     {"where": "data_source IN ('AKSHARE','AUSHARE','AUSAHRE') AND adj_factor IS NULL",
      "max_count": 90000}, "warning", 1,
     "【旧口径残留·显式上账】AKSHARE/AUSHARE/AUSAHRE 源且无 adj_factor 的行数上限，实测 75,173 行。"
     "这些日期新浪/腾讯均不返回（五源探查无覆盖），无法重建 → 接受现状、保留原值。本条不是豁免而是"
     "上账：数值平时静止，一旦增长即有新数据落入旧口径（2026-09-20 立）"),
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
     "成分股快照总行数下限（2026-09-20 实测 19 指数单份 10,228 行、全表 15,866 行）。"
     "2026-09-19 起快照按日留档、总行数会随快照份数累积，故该下限只升不降。"
     "（原文「北证50 待 akshare 修复后补」已过期：2026-09-20 实测 csindex 支持北交所指数，"
     "北证50 已走 csindex 正常采到 50 只）"),
    ("index_cons_uniq", "index_constituents", "unique_index",
     {"cols": ["index_code", "stock_code", "trade_date"], "expect": "exists"}, "info", 1,
     "幂等保障：uk_index_stock_date（指数 × 成分 × 快照日）。**2026-09-19 §7-⑧ 改造**："
     "快照按日留档后，同一成分会在不同快照日重复出现，故唯一键必须含快照日；"
     "原 uk_index_stock(index_code, stock_code) 会让每轮采集覆盖历史、永远无法回溯归因"),
    ("index_cons_stock_code", "index_constituents", "regex_count",
     {"col": "stock_code", "pattern": "^[0-9]{6}$"}, "warning", 1,
     "成分股代码格式校验"),
    # 2026-09-22 修正谓词 `weight <= 0` → `weight < 0`：0.0000 是**合法**取值，不是越界。
    # 实证：中证全指（000985）5,121 只成分股，权重保留 4 位小数；微盘股权重 <0.00005% 时
    #   四舍五入即得 0.0000。实测全 index 仅 4 行为 0.0000（920718/920802/920926/920957），
    #   而同属 920 段的其它 20 只均为 0.0010、非 92 段 4,880 行**无一为 0**
    #   → 说明源侧对 920 段是适配的，0 只是「最小的那一档被舍入」，并非源未适配。
    # 时间线：09-13~09-20 08:02 恒 pass(0)，09-20 16:06 起 fail(4)，与 920 段迁移同刻。
    ("index_cons_weight_bounds", "index_constituents", "where_count",
     {"where": "weight IS NOT NULL AND (weight < 0 OR weight > 100)", "max_count": 0},
     "warning", 1, "权重越界拦截：权重应落在 [0,100]。0.0000 合法（微盘股权重 <0.00005% 的舍入结果）；"
     "2026-09-22 由 `<= 0` 放宽为 `< 0`，避免把合法舍入判成越界"),
    ("index_cons_name_cover", "index_constituents", "where_count",
     {"where": "stock_name IS NULL OR stock_name = ''", "max_count": 0}, "warning", 1,
     "成分股名称覆盖（曾因国证列名读错致 750 行为空；采集器已加 stock_info 兜底回填）"),
    ("index_profile_rows", "index_profile", "row_count_total",
     {"min_rows": 21}, "warning", 1,
     "指数档案行数下限（21 只上市场页的指数；2026-09-19 由 13 扩至 21 后此处曾漏改）"),
    ("index_profile_uniq", "index_profile", "unique_index",
     {"cols": ["index_code"], "expect": "exists"}, "info", 1,
     "幂等保障：uk_index_code"),
    ("index_profile_desc", "index_profile", "where_count",
     {"where": "description IS NULL OR description = ''", "max_count": 0}, "info", 1,
     "释义覆盖：21 只指数均应有简介"),
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
# ⚠️⚠️ 2026-09-20 二次勘误（daily 口径改造后）：上述「复权口径差异」理由**已失效** ——
#   daily 现已同为「不复权实际成交价」，两表在重叠键上理论应一致。故本表的定位从
#   「唯一原始价基准」转为**跨源独立验证基准**：daily 现以新浪为基准源，ex 由 AKSHARE
#   采集且止于 2025-09-19，血缘不同 → 逐日 close 比对是对「重建后存的确实是真实成交价」
#   的独立外部证据（实测见 .workbuddy/tmp/out_xcheck.txt）。ex 仍有 109 个 daily 缺失的键，
#   故**仍不可归档**，维持冻结监护。
FROZEN_RULES = [
    ("frozen_daily_ex_rows", "stock_market_daily_ex", "row_count_slice",
     {"date_col": "trade_date", "min_rows": 4000}, "warning", 1,
     "冻结监护·除权日线：最新日切片行数下限（**不复权原始价**）。⚠️2026-09-20 勘误：原「与 daily 前复权口径不同」失效——daily 已同口径改造，本表价值转为跨源独立验证基准（AKSHARE vs 新浪），且仍有 109 个 daily 缺失键，仍不可归档"),
    ("frozen_capital_flow_rows", "stock_capital_flow", "row_count_slice",
     {"date_col": "trade_date", "min_rows": 4000}, "warning", 1,
     "冻结监护·资金流向：最新日切片行数下限（停更于 2025-09，防误清空；东财域受限待复测）"),
    # ⚠️ 2026-09-20 移除 4 条**死代码**：frozen_fin_abstract_rows / frozen_shares_rows /
    # frozen_sw_industry_rows / frozen_futures_rows 曾同时出现在本列表与 RETIRED_RULES 里
    # —— 每轮 seed 先 INSERT 再 DELETE，净效果虽等于「不存在」，但属「同文件自相矛盾」，
    # 且每次运行都白写四行、并让 created_at 被反复重置；若脚本在两步之间中断，它们还会短暂留存。
    # 实测其职责已被完整接管且在册：fin_abstract_*（3）/ shares_*（4）/ sw_*（3）/ futures_*（3）。
    # 清理后本列表只剩 3 条**真·冻结**（采集器已下线、仅防误清空）。
    ("frozen_hold_by_fund_rows", "stock_hold_by_fund", "row_count_total",
     {"min_rows": 100000}, "warning", 1,
     "冻结监护·基金重仓：总行数下限（防误清空）"),
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
    # 2026-09-22 处置：本表由 futures_sync 在 22:20 写入，而 DQ 实际在 20:02 跑 →
    # behind 恒为 1，排期日必 warning（唯一一次 pass 是 09-18 手动提前跑过）。
    # 治本同 xcheck/concept：DQ 推到 22:45 后 futures 22:20 已落库；原先压制的
    # grace_days=1 已撤回，恢复「当日期货数据未落库即报警」。
    ("futures_freshness", "futures_spot_price", "freshness_daily",
     {"date_col": "trade_date", "warn_days": 4}, "warning", 1,
     "期现价格对齐交易日历（源 100ppi 日频；含周末/假期缓冲，容错 4 日）。"
     "无 grace：DQ 已改到 22:45（晚于 22:20 采集），当日未落库就该报警"),
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

# ============================================================================
# 消费端守护规则（2026-09-19 Batch C）
#
# 起因：《未落地优化项盘点_2026-09-19》第三节列出 5 张「只进不出」的表
#   （interbank_rate_daily / overseas_index_daily / currency_boc_daily
#    + fund_new_issue + stock_repurchase）。本轮把它们接上了消费端
#   （分析研究新增「钱贵不贵」「跨市场对照」「资金温度」三个模块）。
#
# ⚠️ 为什么这 5 张表**已有规则却仍要补**：
#   既有规则都在守护「整表还在更新吗」（freshness_daily / date_floor / row_count_*），
#   而新模块依赖的是**其中某一条腿、某一列**。以下三个失效模式
#   在既有规则下会**静默通过**（这是本批的真实价值，不是凑数）：
#     ① `overseas_index_daily` 的恒生腿整条停更 → 当日仍有 3 行（3 个美股），
#        `overseas_rows_latest` 的 min_rows 只能设 3（港股与美股日历本就不同）→ 通过；
#        （⚠️ 2026-09-25 更新：该规则本身已**退休** —— 海外指数扩到 10 条腿跨三个时区后，
#          「全表最新日行数」变成时点耦合量，盘中跑会因只有 N225 的未完成 bar 而假 fail。
#          现由 spx/dax/n225/kospi 四条逐腿 date_floor_where 替代，见下方该段注释）
#     ② `stock_repurchase` 的 **start_date 腿**停更 → `repurchase_fresh` 看的是
#        announce_date，而它会被后续公告覆盖、永远新鲜 → 通过；
#        但新模块只按 start_date 统计，腿死了模块会静默退化成 0；
#     ③ `currency_boc_daily` 的 mid_price 整列变空 → `currency_usd_mid_range`
#        把 `mid_price IS NOT NULL` 写成前置，违反数恒为 0 → **规则反而更绿**。
#   ① ② 用新加的 `date_floor_where` 检查器；③ 用 `date_floor_where`；
#   另补 `shibor_3m` 的水位列线（新模块最核心的 KPI 列）。
# ============================================================================
CONSUMPTION_RULES = [
    # -- 「钱贵不贵」：3M 是新模块的核心 KPI 列，3M 断供会让分位静默失真 --
    ("interbank_shibor3m_notnull", "interbank_rate_daily", "column_watermark",
     {"date_col": "trade_date", "value_col": "shibor_3m", "max_gap_rows": 1}, "warning", 1,
     "Shibor 3M 列水位线（新模块「钱贵不贵」的核心 KPI 列）。既有 interbank_shibor_notnull "
     "守的是 shibor_on，3M 断了它不会发现；而 Shibor 3M 断供时模块仍会算出"
     "「近一年分位」—— 只是分位基于一段陈旧的窗口，不会报错、只会静默失真。"
     "容忍 1 行 = 当日 Shibor 11:00 才发布、19:35 采集时的固有边界"),

    # -- 「跨市场对照」：恒生腿是 4 个指数里唯一与 A股 时段部分重叠的 --
    ("overseas_hsi_fresh", "overseas_index_daily", "date_floor_where",
     {"date_col": "trade_date", "where": "index_code = 'HSI'", "days_back": 15}, "warning", 1,
     "恒生腿新鲜度：子集内 MAX(trade_date) 不得早于今天-15 天。"
     "**既有 overseas_rows_latest 看不见这条** —— 它数的是最新日的行数，"
     "恒生停更当日仍有 3 个美股 → 计数 3、min_rows 3 → 照常通过。"
     "15 天容忍：港股与 A股 假期不同步，且春节/圣诞前后各有长假"),

    # -- 2026-09-25 货币流动性批次 2C：海外指数由 4 个扩到 10 个（新增 DAX/CAC/UKX/SX5E/N225/KOSPI）--
    # ⚠️ 这次扩展**打掉了 overseas_rows_latest 的前提**（已退休，见 RETIRED_RULES）：
    #    该规则数「全表 MAX(trade_date) 那一日的行数」，在 4 条腿同一时区时够用；
    #    扩到 10 条腿横跨亚/欧/美三个时区后，它的结果开始**取决于体检时刻**——
    #    2026-09-25 10:04 手工跑实测：新浪 `gi` 接口会返回当日**盘中未完成的 bar**，
    #    当时只有 N225 有当日行 ⇒ 「最新日 1 行 < 3」直接判 fail，
    #    而同一张表在 08:11 跑是 4 行 pass。这是**时点耦合**造成的假信号，不是真故障。
    #    ⇒ 改用「逐腿 date_floor_where」：一条腿的 MAX(trade_date) 只会向前走，
    #      与体检时刻无关，且能直接答出「哪一国的腿停了」。
    ("overseas_spx_fresh", "overseas_index_daily", "date_floor_where",
     {"date_col": "trade_date", "where": "index_code = 'SPX'", "days_back": 15}, "warning", 1,
     "美股腿（标普500）新鲜度：子集内 MAX(trade_date) 不得早于今天-15 天。"
     "SPX 是「隔夜传导」唯一使用的海外指数（恒生与 A股 时段重叠，混算会把两个机制平均掉），"
     "它停更 = 跨市场对照的传导腿直接失效"),
    ("overseas_dax_fresh", "overseas_index_daily", "date_floor_where",
     {"date_col": "trade_date", "where": "index_code = 'DAX'", "days_back": 15}, "warning", 1,
     "欧洲腿（德国DAX）新鲜度：以 DAX 代表欧洲四指数（DAX/CAC/UKX/SX5E 同一时区、同一接口，"
     "同时停更的概率极高，逐条配规则只会产生 4 倍联动告警）。"
     "15 天容忍：欧洲假期与 A股 不同步"),
    ("overseas_n225_fresh", "overseas_index_daily", "date_floor_where",
     {"date_col": "trade_date", "where": "index_code = 'N225'", "days_back": 15}, "warning", 1,
     "日本腿（日经225）新鲜度。**这条最容易被时点假信号误导**：新浪 `gi` 接口在日股盘中"
     "就返回当日未完成的 bar，故「当日行数」在盘中看只有 1 行（其余市场还没开盘）—— "
     "而 MAX(trade_date) 永远是单调向前，不受体检时刻影响，这正是改用 date_floor_where 的原因"),
    ("overseas_kospi_fresh", "overseas_index_daily", "date_floor_where",
     {"date_col": "trade_date", "where": "index_code = 'KOSPI'", "days_back": 20}, "warning", 1,
     "韩国腿（KOSPI）新鲜度。20 天容忍（比其它腿多 5 天）：韩国中秋（추석，公历 9~10 月浮动）"
     "连休可达 5 天，叠加周末与接口本身 1 天的发布滞后。"
     "⚠️ 韩国是设计文档 §2.4 A4 明确记录的缺口层：akshare 无韩国央行利率接口，"
     "该层只能做「结果观测」（KOSPI + 韩元汇率），因此这条腿断了就没有替代读数"),

    # -- 「资金温度」：汇率腿 --
    ("currency_usd_mid_fresh", "currency_boc_daily", "date_floor_where",
     {"date_col": "trade_date",
      "where": "currency = 'USD' AND mid_price IS NOT NULL", "days_back": 10}, "warning", 1,
     "美元中间价腿新鲜度：**不能靠 currency_usd_mid_range** —— 那条规则把 "
     "`mid_price IS NOT NULL` 写成前置条件，整列变空时违反数恒为 0、规则反而更绿。"
     "本规则限定在 mid_price 非空的子集上取 MAX(trade_date)，直接问「这条腿还活着吗」。"
     "10 天容忍：mid_price 有空值且最新一日常为空（当日中间价发布晚于采集时刻，"
     "实测 2026-09-19 为 NULL 而 ref_price 有值），叠加周末"),

    # -- 「资金温度」：产业资本腿。模块按 start_date 统计，必须证明 start_date 腿活着 --
    ("repurchase_start_fresh", "stock_repurchase", "date_floor_where",
     {"date_col": "start_date", "where": "start_date IS NOT NULL", "days_back": 45}, "warning", 1,
     "回购**起始日腿**新鲜度（不是公告日）：子集内 MAX(start_date) 不得早于今天-45 天。"
     "⚠️ 既有 repurchase_fresh 看的是 announce_date —— 而 announce_date 会被后续公告"
     "**覆盖成最新日期、永远新鲜**，所以它无法证明 start_date 腿还活着。"
     "而新模块「资金温度」只按 start_date 统计（announce_date 口径实测分位恒为 100%、零区分度）。"
     "45 天容忍：实测近 40 个月单月最低 15 个计划、从无空月"),

    # -- 把「announce_date 口径不可用」这个实测结论钉死，防止后人改回 --
    ("repurchase_announce_not_only", "stock_repurchase", "where_count",
     {"where": "announce_date IS NULL", "max_count": 0}, "warning", 1,
     "回购公告日非空（announce_date 是「最新公告日」、会被覆盖，不能用于时间序列统计；"
     "本规则只保证它作为「这条计划有过公告」的标记是完整的。"
     "按时间序列统计请用 start_date —— 见 table_meta 的 flow_desc 与资金温度模块文件头）"),
]

# ============================================================================
# 已知技术债监控（2026-09-19 批次 D3 立）
#
# D3 的原始定性是「源列脏（turnover_ratio>1、概念异常收益 +110%）→ 只记录不修改」。
# 逐条实测后**只留了第一半**，第二半定性被推翻（实证见下），所以这里加的两条都是
# **监控**而非修复——报警即真实信号，别当成规则误报去放宽阈值。
# ============================================================================
DEBT_RULES = [
    # 【2026-09-22 定性推翻】turnover_ratio > 1 不是「源侧脏值」，是**量纲在 09-20 变了**。
    # 原注释「本表是比率量纲（0~1 正常），只有 stock_market_daily 是百分比」成立于 09-20 之前
    # —— 当时快照源是东财实时快照（本就是比率量纲）。09-20 快照改为「从 stock_market_daily
    # 聚合」（data_source=daily-agg）后，该列变成**直拷日线同名百分比列**：
    #   实测 2026-09-22：快照 5,465 行 vs 日线同交易日，逐行相等 5,465 / 不等 0 —— 同一列。
    # 于是旧判据 >1 比真实量纲小 100 倍，把正常换手率全判成越界。
    # 时间线（dq_report 实录）：09-19 与 09-20 08:02 均 pass(25) → 09-20 16:06 起
    #   fail(3945) → 09-21 fail(4230) → 09-22 fail(4177)，翻转点与快照切源同一时刻。
    # 现判据改为**物理不可能的下限**：单日换手 >100%（成交量超过全部流通股）。
    # 实测该日分布：>1 共 4177、>10 共 284、>20 共 48、>30 共 7、>50 共 0、>100 共 0
    #   —— 阈值 100 平时静止，一旦出现即说明价格/成交量单位或复权出问题（同 pct_limit 思路）。
    ("current_turnover_dirty", "stock_market_current", "where_count",
     {"where": "turnover_ratio IS NOT NULL AND turnover_ratio > 100", "max_count": 0},
     "warning", 1,
     "换手率越界行数（>100%）上限 —— 单日换手不可能超过 100%（成交量 > 全部流通股），"
     "超限即源侧单位/复权故障。⚠️ 本表与 stock_market_daily 同列为**百分比**量纲"
     "（2026-09-20 快照改从日线聚合后一致，实测逐行相等）；「0~1 比率」是 09-20 前的旧口径，"
     "已于 2026-09-22 更正"),

    # 【推翻报告定性】概念「异常收益」不是源列脏，是**跨日/跨名拼接**。
    # 实测（2026-09-19）：
    #   ① ths_concept_market.change_pct 全史范围只有 -17.05% ~ +22.30%，ABS>60 的行数 = 0
    #      —— 日频源列里根本不存在 >60% 的脏值；
    #   ② 真因是概念改名留下的**孤儿 index_code**：`WiFi6` 的最后一行停在 2025-09-19，
    #      现行序列是 `WiFi 6`（带空格）；任何「取每个概念自己的最新行」的关联都会把
    #      一年前的涨跌幅拉进当日榜 —— 报告里的「WiFi6 20 日 +110.89%」就是这么拼出来的。
    #      该缺陷已在 api/concept.py::concept_list 修掉（锚定全表最新交易日）；
    #   ③ 另发现一个报告没抓到的活缺口：change_pct 自 09-03 起**间歇性大面积空**
    #      （09-01/02 为 0%，09-15~09-18 达 99~100%），采集器 INSERT 是带该列的 → 源侧行为。
    # 这条规则盯的是 ③：一旦空值率逼近 100%，任何按该列排序的榜单都会静默失真。
    ("concept_change_pct_gap", "ths_concept_market", "null_rate_slice",
     {"date_col": "trade_date", "col": "change_pct", "max_pct": 30},
     "warning", 1,
     "概念最新切片 change_pct 空值率上限（%）—— 源侧间歇性不返回涨跌幅，"
     "空值率高时按该列排序会退化为任意序（分析层应以 close 自算收益）。"
     "阈值 30% 是按实测如实设定，**刻意没有放宽到 100% 去换一个假 pass**："
     "亮灯即代表源侧确有缺口，属真实信号"),
]

# ============================================================================
# REF_RULES：2026-09-24 引用完整性 + 名册停更守护（本次事故的直接产物）
# ----------------------------------------------------------------------------
# 背景：「其他(74)」故障的根因 —— `stock_info_sync` 的名单源曾是东财**实时行情快照**
#   `stock_zh_a_spot_em()`，它只返回「当时有报价」的代码 ⇒ **漏收 89 只 A 股**
#   （含 001280 中国铀业，沪深300 + 深证成指成分，2025-12-03 上市）。而采集器是
#   upsert-only、逐源容错、无报错、行数只增不减 ⇒ 既有守护**全部 pass**：
#     - `stock_info_rows`（row_count_total，min_rows=5856）：故障时 5927 行仍 > 下限；
#     - `freshness_interval`：任务照常跑、`update_time` 照常前进；
#     - `per_key_coverage`：**INNER JOIN** 取参照表，缺的 key 被 JOIN 直接丢弃、不进统计
#       —— 本次故障里 stock_market_daily 同时缺那批代码，属**双重致盲**，历史上必然全绿；
#     - `where_count` 类：`_validate` 的 where 白名单**不允许子查询**（`SELECT`/`FROM`
#       都算非白名单标识符）⇒ 反向连接根本写不进去。
#   ⇒ 故新增 `ref_missing` 检查器。这是「名册缺行」唯一能被 DQ 自动发现的形态。
#   断链的代价是**级联**：stock_info 是 stock_daily_incr（FROM stock_info）与
#   market_current_sync 的**上游股票池** ⇒ 名册缺 1 只 = 日线缺、快照缺、行业分布缺。
#
# ⚠️ 为什么只挂 index_constituents，不铺开到所有含 stock_code 的表：
#   2026-09-24 全库实测：13 张表存在「stock_code 不在名册」的非零缺口，但其中 ~240 只
#   是**北交所旧码段历史残留**（430 / 831~839 / 870~873；2025-10-09 起统一迁 920 段，
#   老码→新码在 `stock_code_mapping`，status='switched'）——**属预期、不是缺陷**。
#   `index_constituents` 实测为 0（成分快照只含现行代码、无旧码污染），是唯一可以
#   `max_count=0` 严格守护的目标；给旧码表配这条规则会**恒红掩盖真问题**。
#
#   ✅ 2026-09-24 补充：**加时间窗口可以把两类问题分开** —— `stock_market_daily` 同样有旧码
#   残留，但那些残留的最新交易日均 ≤2023-06-30；用 `where trade_date >= '2025-01-01'`
#   限定「近期仍在交易」后，旧码被窗口挡掉，而「活跃股缺行」可被严格守护 ⇒ 增挂
#   `daily_roster_link`。注意窗口必须是**静态起始日**：where 白名单（_WHERE_FUNCS）
#   只放行 ABS/ROUND/COALESCE/IFNULL/NULL/NOT/AND/OR/IN/IS/LIKE/LEAST/GREATEST，
#   DATE_SUB / CURDATE / INTERVAL 都不在，滚动区间写不进去。
# ============================================================================
REF_RULES = [
    ("index_cons_roster_link", "index_constituents", "ref_missing",
     {"key_col": "stock_code", "ref_table": "stock_info", "ref_key": "stock_code",
      "max_count": 0},
     "critical", 1,
     "引用完整性：指数成分代码必须存在于本地名册 stock_info（2026-09-24 立，直接对应「其他(74)」故障）。"
     "⚠️ 这是唯一能发现「跑成功但漏收」的规则——行数下限 / freshness / per_key_coverage "
     "对该类故障全部无感（后者 INNER JOIN 丢键致盲）"),
    ("daily_roster_link", "stock_market_daily", "ref_missing",
     {"key_col": "stock_code", "ref_table": "stock_info", "ref_key": "stock_code",
      "where": "trade_date >= '2025-01-01'",
      "max_count": 0},
     "warning", 1,
     "引用完整性·活跃个股：2025 年起仍有行情的股票必须在名册内（守「名册缺行」类静默故障）。"
     "实测 2026-09-24 孤行 6 只（含北交所老码），最新日均 ≤2023-06-30 被窗口排除 ⇒ PASS。"
     "窗口取静态起始日：where 白名单不放行 DATE_SUB/CURDATE"),
    ("stock_info_fresh", "stock_info", "freshness_interval",
     {"time_col": "update_time", "pass_hours": 30, "fail_hours": 48},
     "warning", 1,
     "名册停更守护：stock_info.update_time 距今 ≤30h pass / ≤48h warning / 超则 fail。"
     "2026-09-24 立——该表原有 10 条规则全是值域·口径类，无一能发现「名册停更」，"
     "而采集器 upsert-only，整源静默失败既不报错、行数也不减"),
]

# ============================================================================
# 2026-09-25 货币流动性批次 2（LIQUIDITY_RULES）
# 《货币流动性观测体系设计_2026-09-25.md》§6 批次 2：新增 3 张表
# （cn_liquidity_monthly / cn_cb_balance_monthly / cb_policy_rate）
# + 1 张美元指数表（global_usd_index_daily，D2 后已改为「官方日线主口径 + 自算校验」）。
#
# 每张表按「四件套」配：unique_index（幂等）/ 新鲜度 / column_watermark（"这一列还在更新吗"）
# / 值域 where_count（口径守护）。两条刻意 enabled=0 的规则在下面逐条说明理由。
#
# ★ 本批最有价值的一组是 `dxy_sina_vs_calc_latest` + `dxy_sina_vs_calc` ——
#   **「标价法单位错」的通用网**（D2 后从「自算 vs 快照」升级为「官方日线 vs 自算」，
#   样本从 1 行变成 2,380 行，网从"几乎没样本"变成"天天有样本"）：
#     直接来自 2026-09-25 的一起实测事故（本轮最大发现）：
#     `currency_boc_safe` 宽表**混用两种标价法**：
#       直接标价 = 人民币 / 100 外币（美元 674.89 → 6.7489、日元 4.259 → 0.04259）
#       间接标价 = 外币 / 100 人民币（瑞典克朗 147.31 → 0.67884）
#     采集器初版把间接标价的 SEK 当直标处理 ⇒ USDSEK 算成 4.58（真值 9.94）
#     ⇒ 自算 DXY 偏低 3.3%，且这个偏差**恰好伪装成**「人民币中间价与市场价的固有偏离」，
#     设计文档初稿据此写下「±3% 系统性偏离」的错误结论（已勘误）。
#   教训：单币种量级错不会报错、行数不变、成分列也「看起来正常」，
#   只有跟独立来源做整值对账才能一眼看出来。
#
# ⚠️ D2 落地时又补出一条重要分寸：**两条口径的偏差不是常数**。
#   实测 2,380 个共有日：中位 0.28%、p90 0.74%、p99 1.43%、最大 2.29%
#   （带符号均值只有 +0.074%，因正负相抵而**严重低估**离散度，不可当作"精度"引用）。
#   成因是机制差：中间价每日 9:15 定盘、基于前一交易日篮子 ⇒ 趋势日系统性滞后约 1 天。
#   ⇒ 阈值必须取在**实测极值之上**（本组取 2.5% / 1.5%），才既不误报、又能抓住 3.3% 那类系统性错误。
# ============================================================================
LIQUIDITY_RULES = [
    # ---------- cn_liquidity_monthly（中国「数量维度」：M0/M1/M2 + 信贷 + 社融 + 准备金率） ----------
    ("cnliq_uniq", "cn_liquidity_monthly", "unique_index",
     {"cols": ["stat_month"], "expect": "exists"},
     "info", 1, "幂等保障：stat_month 主键"),
    ("cnliq_fresh", "cn_liquidity_monthly", "date_floor",
     {"date_col": "stat_month", "days_back": 75},
     "warning", 1,
     "月度新鲜度：stat_month 不得早于今天-75 天。金融统计数据通常次月 10~15 日发布（央行），"
     "75 天容忍覆盖「发布偏晚 + 机器离线数天」的极端情形；再晚就是采集断档"),
    ("cnliq_m1m2_wm", "cn_liquidity_monthly", "column_watermark",
     {"date_col": "stat_month", "value_col": "m1_yoy", "max_gap_rows": 1},
     "warning", 1,
     "M1 同比列水位线（本表核心列，M1−M2 剪刀差的分子）。⚠️ 容忍 1 行而非 0：本表 stat_month "
     "取自 4 个源的**并集**，准备金率按「下一次生效月」落行 —— 若央行提前公告下月降准，"
     "会先出现一行只有 rrr 的行，此时 m1_yoy 为空但并非断供（次月 10~15 日补上）。gap > 1 才是真落后"),
    ("cnliq_credit_wm", "cn_liquidity_monthly", "column_watermark",
     {"date_col": "stat_month", "value_col": "credit_month", "max_gap_rows": 1},
     "warning", 1,
     "新增人民币信贷列水位线（第二条腿）。与 m1_yoy 分开守：money_supply 与 new_financial_credit "
     "是两个独立接口，一条腿停更不该把另一条也判红（否则真实故障被淹没在联动告警里）"),
    ("cnliq_yoy_range", "cn_liquidity_monthly", "where_count",
     {"where": "m1_yoy IS NOT NULL AND (m1_yoy < -20 OR m1_yoy > 60)", "max_count": 0},
     "warning", 1,
     "M1 同比取值域 (-20, 60]%。实测历史区间约 -10~40%，宽松上限只为挡量纲错（源改成小数、"
     "或误把余额绝对值写进同比列）"),
    ("cnliq_m0_scale", "cn_liquidity_monthly", "where_count",
     {"where": "m0 IS NOT NULL AND (m0 < 1000 OR m0 > 1000000)", "max_count": 0},
     "warning", 1,
     "M0 余额量级守护（单位亿元：实测 2026-08 = 148,312 亿）。挡「把同比写进余额列」这类串列"),
    ("cnliq_shrzgm_wm", "cn_liquidity_monthly", "column_watermark",
     {"date_col": "stat_month", "value_col": "shrzgm", "max_gap_rows": 6},
     "warning", 0,
     "社融列水位线 —— **已知无源可采，刻意关闭（enabled=0）**。2026-09-25 实测："
     "`macro_china_shrzgm` 最后数据 2026-04；东财 datacenter 无对应报告名；"
     "`macro_china_bank_financing` 实为「银行理财发行数量」（名字骗人）。"
     "⇒ 社融为无源辅助指标，主口径以 M1/M2 + 信贷为准。钩子规则：找到替代源后改 enabled=1"),
    # ---------- cn_cb_balance_monthly（央行资产负债表：中国式 QE 的直接观测） ----------
    ("cncb_uniq", "cn_cb_balance_monthly", "unique_index",
     {"cols": ["stat_month"], "expect": "exists"},
     "info", 1, "幂等保障：stat_month 主键"),
    ("cncb_fresh", "cn_cb_balance_monthly", "date_floor",
     {"date_col": "stat_month", "days_back": 75},
     "warning", 1, "月度新鲜度：央行资产负债表通常次月中下旬发布，75 天容忍同上"),
    ("cncb_total_wm", "cn_cb_balance_monthly", "column_watermark",
     {"date_col": "stat_month", "value_col": "total_assets", "max_gap_rows": 0},
     "warning", 1, "总资产列水位线（本表存在的意义就是这一列：扩张 = 放水、收缩 = 收水）"),
    ("cncb_claims_wm", "cn_cb_balance_monthly", "column_watermark",
     {"date_col": "stat_month", "value_col": "claims_other_dep_banks", "max_gap_rows": 0},
     "warning", 1,
     "「对其他存款性公司债权」列水位线 —— 本表**最有信息量的科目**（2026-08 实测 21.34 万亿）："
     "它是央行通过 MLF/逆回购/PSL 投给银行的资金总量。中国没有官方 QE 公告，"
     "只能从这一列倒推「主动投放 vs 外汇占款」的结构切换"),
    ("cncb_identity", "cn_cb_balance_monthly", "where_count",
     {"where": "total_assets IS NOT NULL AND total_liab IS NOT NULL "
               "AND ABS(total_assets - total_liab) > 1",
      "max_count": 0},
     "warning", 1,
     "资产负债表恒等式：总资产 = 总负债（单位亿元，容差 1 亿）。这是**物理约束**、与口径无关 —— "
     "任一侧列被错映射（源列改名后错位、COL_MAP 漏改）都会立刻打破它"),
    ("cncb_scale", "cn_cb_balance_monthly", "where_count",
     {"where": "total_assets IS NOT NULL AND (total_assets < 10000 OR total_assets > 1000000)",
      "max_count": 0},
     "warning", 1,
     "总资产量级守护（单位亿元：实测 2026-08 = 498,568 亿 ≈ 49.9 万亿）。"
     "挡单位切换（亿元↔万元↔元）与「取到同名但含义不同的科目」"),
    # ---------- cb_policy_rate（多国央行政策利率决议：美/欧/日/英） ----------
    ("cbpr_uniq", "cb_policy_rate", "unique_index",
     {"cols": ["country_code", "event_date"], "expect": "exists"},
     "info", 1, "幂等保障：uk_country_date(country_code, event_date)"),
    ("cbpr_rate_notnull", "cb_policy_rate", "where_count",
     {"where": "rate IS NULL", "max_count": 0},
     "warning", 1,
     "利率非空：采集器**只写「今值」非空的有效决议行**（源表更晚的日期行今值为空 —— 那是"
     "「尚未发布」而不是「利率为 0」）。本规则守住这个约定：否则表内会混入空决议，"
     "下游「最新一次决议」的判断会被推到根本没有利率的那天"),
    ("cbpr_country_whitelist", "cb_policy_rate", "where_count",
     {"where": "country_code NOT IN ('US', 'EU', 'JP', 'UK')", "max_count": 0},
     "warning", 1,
     "国家白名单：只有美/欧/日/英四个央行（韩国央行 akshare 无接口 —— 属已知缺口，见设计文档 §2.4 A4，"
     "该层降级为「结果观测」：只用 KOSPI + 韩元汇率）"),
    ("cbpr_rate_range", "cb_policy_rate", "where_count",
     {"where": "rate IS NOT NULL AND (rate < -2 OR rate > 30)", "max_count": 0},
     "warning", 1,
     "政策利率取值域 [-2, 30]%。负值合法（欧元区 2014-2019 曾 -0.5%），上限 30% 覆盖全部历史高利率期，"
     "只挡量纲错（百分数↔小数会差 100 倍）"),
    ("cbpr_rows", "cb_policy_rate", "row_count_total",
     {"min_rows": 1300}, "warning", 1,
     "决议总行数下限（实测 1,395 条；防误清空）"),
    ("cbpr_fresh", "cb_policy_rate", "date_floor",
     {"date_col": "event_date", "days_back": 400},
     "warning", 0,
     "央行决议新鲜度 —— **上游整体停更，刻意关闭（enabled=0）**。2026-09-25 实测："
     "`macro_bank_*_interest_rate` 全族最后有效「今值」停在 2025-07~08（距今 14 个月），"
     "穷举 11 个 macro_bank_* 皆然。⇒ 本表只能读历史方向，不能当当前政策利率用（UI 已标注）。"
     "钩子规则：上游恢复后改 enabled=1"),
    # ---------- global_usd_index_daily（★官方日线主口径 + 自算交叉校验 + DINIW 快照标定） ----------
    ("dxy_uniq", "global_usd_index_daily", "unique_index",
     {"cols": ["trade_date"], "expect": "exists"},
     "info", 1, "幂等保障：trade_date 主键"),
    ("dxy_fresh", "global_usd_index_daily", "freshness_daily",
     {"date_col": "trade_date", "warn_days": 5, "grace_days": 2},
     "warning", 1,
     "自算美元指数新鲜度（依赖人民币中间价宽表 `currency_boc_safe`，随银行间工作日更新；"
     "grace 2 天覆盖周末与假期）"),
    ("dxy_calc_wm", "global_usd_index_daily", "column_watermark",
     {"date_col": "trade_date", "value_col": "dxy_calc", "max_gap_rows": 2},
     "warning", 1,
     "自算 DXY 列水位线（本表核心列；容忍 2 行 = 中间价与快照之间固有的 1~2 日错位）"),
    ("dxy_vs_snapshot", "global_usd_index_daily", "where_count",
     {"where": "dxy_calc IS NOT NULL AND dxy_snapshot IS NOT NULL "
               "AND ABS(dxy_calc - dxy_snapshot) > 1.5",
      "max_count": 0},
     "warning", 1,
     "★ 标价法单位错的通用网：自算 DXY 与 ICE 官方快照（新浪 DINIW）的整值偏差不得超过 1.5 点（≈1.5%）。"
     "2026-09-25 事故：`currency_boc_safe` 混用两种标价法，采集器把间接标价的瑞典克朗当直标 ⇒ "
     "USDSEK 算成 4.58（真值 9.94）、自算 DXY 偏低 3.3%，还伪装成「固有口径偏离」骗过了人工复核；"
     "修正后偏差 +0.085 点。成分列自己不会报错、行数也不变，只有整值对账能一眼看出来"),
    ("dxy_component_range", "global_usd_index_daily", "where_count",
     {"where": "(eur_usd IS NOT NULL AND (eur_usd < 0.8 OR eur_usd > 1.7)) "
               "OR (usd_jpy IS NOT NULL AND (usd_jpy < 70 OR usd_jpy > 220)) "
               "OR (gbp_usd IS NOT NULL AND (gbp_usd < 0.9 OR gbp_usd > 2.0)) "
               "OR (usd_cad IS NOT NULL AND (usd_cad < 0.85 OR usd_cad > 2.0)) "
               "OR (usd_sek IS NOT NULL AND (usd_sek < 5.5 OR usd_sek > 15)) "
               "OR (usd_chf IS NOT NULL AND (usd_chf < 0.55 OR usd_chf > 1.5))",
      "max_count": 0},
     "warning", 1,
     "六个成分交叉汇率的量级守护（区间取 2015 以来实测极值再放约 15% 余量）。"
     "采集器内已有同款断言（`_CROSS_RANGE`，越界即 raise），本条是**第二道网**："
     "断言只管本次写入，规则管**已在库里的历史行**（含手工修补、历史回填、源口径变更后的存量）"),
    ("dxy_snapshot_wm", "global_usd_index_daily", "column_watermark",
     {"date_col": "trade_date", "value_col": "dxy_snapshot", "max_gap_rows": 5},
     "warning", 1,
     "快照列水位线（容忍 5 行：DINIW 是实时报价、非交易日无新值，且快照自 2026-09-24 起才逐日累积）。"
     "该列是 `dxy_vs_snapshot` 的标定基准 —— 它一旦断供，整值对账会**静默退化**成「无从校验」，"
     "所以必须单独守它"),
    # ---------- 2026-09-25 D2：新增官方日线主口径 dxy_sina 的四条守护 ----------
    # 背景：用户要求「再找找网上有无现成可以拉取历史的数据源」，实测找到新浪外汇 jsonp
    #   `NewForexService.getDayKLine?symbol=DINIW`（ICE 口径，1985-11-08 起 10,573 行）⇒ 写入 dxy_sina 并升为主口径。
    #   自算 dxy_calc 降为交叉校验列 ⇒ 两条独立口径互比成为**最有价值的一道网**（见下两行）。
    ("dxy_sina_fresh", "global_usd_index_daily", "date_floor_where",
     {"date_col": "trade_date", "where": "dxy_sina IS NOT NULL", "days_back": 6},
     "warning", 1,
     "★主口径新鲜度：官方日线（dxy_sina）子集内 MAX(trade_date) 不得早于今天-6 天。"
     "它现在顶在最前面出数，一旦停更 = 整个「美元指数」KPI 直接失效（自算虽能兜，但那是校验口径）；"
     "6 天容差覆盖全球汇市周末休市 + 圣诞/新年长假"),
    ("dxy_sina_wm", "global_usd_index_daily", "column_watermark",
     {"date_col": "trade_date", "value_col": "dxy_sina", "max_gap_rows": 2},
     "warning", 1,
     "官方日线列水位线（容忍 2 行：与人民币中间价的交易日历有 1~2 日天然错位）。"
     "`dxy_sina_fresh` 管「停更多久」，本条管「中间断档几行」—— 两者互补，缺一不可"),
    ("dxy_sina_range", "global_usd_index_daily", "where_count",
     {"where": "dxy_sina IS NOT NULL AND (dxy_sina < 40 OR dxy_sina > 250)",
      "max_count": 0},
     "warning", 1,
     "官方日线整值域（ICE DXY 历史区间约 70~165，放宽到 40~250 只为拦量级错，"
     "实测全史 10,573 行 0 行越界）。解析层的 OHLC 不变量自检也拦一层（`bad_ohlc`），本条管**库内存量**"),
    ("dxy_sina_vs_calc_latest", "global_usd_index_daily", "violation_count",
     {"where": "dxy_sina IS NOT NULL AND dxy_calc IS NOT NULL "
               "AND ABS(dxy_sina - dxy_calc) / dxy_calc > 0.025",
      "max_count": 0},
     "warning", 1,
     "★★ 两条口径互比的**当日切片**快网（阈值 2.5%）：官方日线 vs 自算的整值偏差必须 ≤2.5%。"
     "为什么不取 1.5%：实测 2,380 个共有日 |偏差|>1.5% 有 16 天、最大 2.29%，根源是**机制差**"
     "（中间价每日 9:15 定盘、基于前一日篮子 ⇒ 趋势日滞后约 1 天，"
     "如 2022-09-23 英国迷你预算日官方跳升 1.6% 而自算不动）。"
     "⇒ 2.5% 之上全史零发生，超过它的只可能是**系统性单位错**（如瑞典克朗标价法事故 = 3.3%），"
     "故零假阳性且当日即触发"),
    ("dxy_sina_vs_calc", "global_usd_index_daily", "where_count",
     {"where": "dxy_sina IS NOT NULL AND dxy_calc IS NOT NULL "
               "AND ABS(dxy_sina - dxy_calc) / dxy_calc > 0.015",
      "max_count": 30},
     "warning", 1,
     "两条口径互比的**全史上账**网（阈值 1.5%，上账 30 行 = 实测 16 行 + 约 1 倍余量）。"
     "与上一条的关系：上一条管「最新那天有没有出大事」（快，需最新日有 dxy_calc），"
     "本条管「全史累计有没有悄悄变多」（慢，但永远有样本）。"
     "为什么要全史：dxy_calc 是**独立第二口径**，价值全在「两条互比」——本项目正是靠它发现了"
     "「瑞典克朗标价法算错 ⇒ 自算 DXY 偏低 3.3%」那个伪装成「固有口径偏离」的 bug。"
     "上账式设固定上限：平时静止、一旦增长即说明有新故障落入"),
    # ==========================================================================
    # 2026-09-25 货币流动性补源 v2.0（无 FRED 密钥版）批次 A/B/C/D：6 张新表
    # 《货币流动性补源方案_无FRED密钥版_v2.0_2026-09-25.md》§7。
    # 每张表按「四件套」配：unique_index / 新鲜度 / column_watermark / 值域 where_count。
    # ★ 决策 E5′ 落地：DBnomics 的 -999999 是「缺失哨兵」（合法数字、非 NULL、SQL 过滤不掉、
    #   会污染同比）。采集器解析层已一律剔除并计数，但**必须有一条库内守护**确认「哨兵没漏进来」——
    #   这是对「解析层防漏」的第二道网（第一道是采集器剔除，第二道是这里查库内存量）。
    #   注意：不能写 `col = -999999` 精确匹配（浮点 -999999.0 存 DECIMAL 会变成 -999999.00，
    #   但未来源若改为 -888888 之类会漏），故用「< -900000」的量级网兜住所有哨兵形态。
    # --------------------------------------------------------------------------
    # ---------- us_fed_balance_weekly（美联储资产负债表，周度） ----------
    ("usfb_uniq", "us_fed_balance_weekly", "unique_index",
     {"cols": ["trade_date"], "expect": "exists"},
     "info", 1, "幂等保障：trade_date 主键"),
    ("usfb_fresh", "us_fed_balance_weekly", "date_floor",
     {"date_col": "trade_date", "days_back": 14},
     "warning", 1,
     "周度新鲜度：H.4.1 每周四发布，14 天容忍覆盖「节假日顺延 + 机器离线数天」；再晚即采集断档"),
    ("usfb_total_wm", "us_fed_balance_weekly", "column_watermark",
     {"date_col": "trade_date", "value_col": "total_assets", "max_gap_rows": 1},
     "warning", 1,
     "总资产列水位线（本表存在的意义就是这一列：扩表=放水、缩表=收水）。容忍 1 行=发布日固有滞后"),
    ("usfb_total_scale", "us_fed_balance_weekly", "where_count",
     {"where": "total_assets IS NOT NULL AND (total_assets < 100000 OR total_assets > 20000000)",
      "max_count": 0},
     "warning", 1,
     "总资产量级守护（单位百万美元：实测 6,747,704）。挡单位切换（百万↔十亿↔元）与「取到同名但含义不同的序列」"),
    ("usfb_sentinel", "us_fed_balance_weekly", "where_count",
     {"where": "total_assets < -900000 OR securities_total < -900000 OR reserve_balances < -900000 "
               "OR currency_in_circ < -900000",
      "max_count": 0},
     "warning", 1,
     "★ E5′ 决策落地：DBnomics -999999 缺失哨兵**绝不允许出现在库内**（解析层已剔除，这里是第二道网）。"
     "量级网 < -900000 兜住所有哨兵形态（-999999/-888888…），不必精确匹配"),
    ("usfb_cross_check", "us_fed_balance_weekly", "where_count",
     {"where": "total_assets IS NOT NULL AND official_check IS NOT NULL "
               "AND ABS(total_assets - official_check) / total_assets > 0.3",
      "max_count": 0},
     "warning", 1,
     "双轨互校：DBnomics 主口径 vs 官网 H.4.1 校验口径的偏差不得 >30%（官网只回填最近几期，"
     "故只在最新一期比对；30% 是「两通道抓到的根本不是同一个数」的判据，正常偏差 0.000000%）"),
    # ---------- us_money_supply_monthly（美国货币供应量，月度） ----------
    ("usms_uniq", "us_money_supply_monthly", "unique_index",
     {"cols": ["stat_month"], "expect": "exists"},
     "info", 1, "幂等保障：stat_month 主键"),
    ("usms_fresh", "us_money_supply_monthly", "date_floor",
     {"date_col": "stat_month", "days_back": 90},
     "warning", 1,
     "月度新鲜度：H.6 在次月下旬发布（8 月数据约 9 月下旬出），且 stat_month 取「月初 1 日」"
     "天然比今天早约一个月 ⇒ 90 天 = 30（月初） + 30（发布滞后） + 30（缓冲）；再晚即断档"),
    ("usms_m2_wm", "us_money_supply_monthly", "column_watermark",
     {"date_col": "stat_month", "value_col": "m2", "max_gap_rows": 1},
     "warning", 1, "M2 列水位线（本表核心列）"),
    ("usms_m2_scale", "us_money_supply_monthly", "where_count",
     {"where": "m2 IS NOT NULL AND (m2 < 100 OR m2 > 100000)", "max_count": 0},
     "warning", 1,
     "M2 量级守护（单位十亿美元：实测 23,342.8）。挡单位切换（十亿↔百万↔亿）"),
    ("usms_base_identity", "us_money_supply_monthly", "where_count",
     {"where": "reserve_balances IS NOT NULL AND currency_in_circ IS NOT NULL "
               "AND monetary_base IS NOT NULL "
               "AND ABS((reserve_balances + currency_in_circ) - monetary_base) > 0.25",
      "max_count": 0},
     "warning", 1,
     "基础货币恒等式：准备金余额 + 流通中货币 = 基础货币（十亿美元，容差 0.25）。"
     "这是**物理约束**，任一侧序列被错映射（源序列代码改后错位）都会立刻打破它"),
    ("usms_sentinel", "us_money_supply_monthly", "where_count",
     {"where": "m2 < -900000 OR m1 < -900000 OR monetary_base < -900000", "max_count": 0},
     "warning", 1,
     "★ E5′ 决策落地：DBnomics -999999 缺失哨兵库内守护（第二道网）"),
    ("usms_cross_check", "us_money_supply_monthly", "where_count",
     {"where": "m2 IS NOT NULL AND official_m2 IS NOT NULL "
               "AND ABS(m2 - official_m2) / m2 > 0.05",
      "max_count": 0},
     "warning", 1,
     "双轨互校：DBnomics M2 vs 官网 H.6 M2 的偏差不得 >5%（正常偏差 0.0000%）。"
     "5% 阈值：官网当期页若停留在旧版（如 h6.htm 停在 2013 年那种坑）会立刻暴露"),
    # ---------- us_money_market_daily（美国货币市场，日度） ----------
    ("usmm_uniq", "us_money_market_daily", "unique_index",
     {"cols": ["trade_date"], "expect": "exists"},
     "info", 1, "幂等保障：trade_date 主键"),
    ("usmm_fresh", "us_money_market_daily", "date_floor",
     {"date_col": "trade_date", "days_back": 6},
     "warning", 1,
     "日度新鲜度：三个源（SOFR/EFFR/ON RRP/TGA）都随美国工作日更新，6 天容忍覆盖周末 + 美节假日"),
    ("usmm_sofr_wm", "us_money_market_daily", "column_watermark",
     {"date_col": "trade_date", "value_col": "sofr", "max_gap_rows": 3},
     "warning", 1,
     "SOFR 列水位线（容忍 3 行：SOFR 只在回购市场开市日有值，与整表日期并集有天然错位）"),
    ("usmm_tga_wm", "us_money_market_daily", "column_watermark",
     {"date_col": "trade_date", "value_col": "tga", "max_gap_rows": 3},
     "warning", 1,
     "TGA 列水位线（容忍 3 行：财政部工作日与回购市场工作日不同，日期是并集）"),
    ("usmm_sofr_range", "us_money_market_daily", "where_count",
     {"where": "sofr IS NOT NULL AND (sofr < -1 OR sofr > 15)", "max_count": 0},
     "warning", 1,
     "SOFR 利率取值域 [-1, 15]%。挡量纲错（百分数↔小数差 100 倍）"),
    ("usmm_effr_range", "us_money_market_daily", "where_count",
     {"where": "effr IS NOT NULL AND (effr < -1 OR effr > 20)", "max_count": 0},
     "warning", 1,
     "EFFR 利率取值域 [-1, 20]%（覆盖 1980 年代高利率期，只挡量纲错）"),
    ("usmm_tga_scale", "us_money_market_daily", "where_count",
     {"where": "tga IS NOT NULL AND (tga < 1000 OR tga > 100000000)", "max_count": 0},
     "warning", 1,
     "TGA 量级守护（单位百万美元：实测 957,409）。挡单位切换（源是美元，采集器已 /1e6）"),
    # ---------- eu_money_supply_monthly（欧元区货币供应量，月度） ----------
    ("eums_uniq", "eu_money_supply_monthly", "unique_index",
     {"cols": ["stat_month"], "expect": "exists"},
     "info", 1, "幂等保障：stat_month 主键"),
    ("eums_fresh", "eu_money_supply_monthly", "date_floor",
     {"date_col": "stat_month", "days_back": 120},
     "warning", 1,
     "月度新鲜度：ECB 货币数据滞后约 2 个月（7 月数据 8 月底发布、8 月数据 9 月底发布），"
     "且 stat_month 取「月初 1 日」天然比今天早约一个月 ⇒ 120 天 = 30（月初） + 60（发布滞后） + 30（缓冲）。"
     "⚠️ 本表源单一（ECB 直连全阻断、只能走 DBnomics 镜像）⇒ 这条 freshness 是「失效即告警」的唯一防线"),
    ("eums_m3_wm", "eu_money_supply_monthly", "column_watermark",
     {"date_col": "stat_month", "value_col": "m3", "max_gap_rows": 1},
     "warning", 1, "M3 列水位线（本表核心列）"),
    ("eums_m3_scale", "eu_money_supply_monthly", "where_count",
     {"where": "m3 IS NOT NULL AND (m3 < 100000 OR m3 > 100000000)", "max_count": 0},
     "warning", 1,
     "M3 量级守护（单位百万欧元：实测 17,613,983）。挡单位切换与错映射"),
    ("eums_sentinel", "eu_money_supply_monthly", "where_count",
     {"where": "m3 < -900000 OR m2 < -900000 OR m1 < -900000", "max_count": 0},
     "warning", 1,
     "★ E5′ 决策落地：DBnomics -999999 缺失哨兵库内守护（第二道网）"),
    # ---------- global_liquidity_bis（BIS 全球流动性，季度） ----------
    ("glb_uniq", "global_liquidity_bis", "unique_index",
     {"cols": ["time_period", "curr_denom", "borrowers_cty", "borrowers_sector",
              "lenders_sector", "l_pos_type", "l_instr", "unit_measure"], "expect": "exists"},
     "info", 1, "幂等保障：8 列复合唯一键 uk_gliquidity"),
    ("glb_fresh", "global_liquidity_bis", "date_floor",
     {"date_col": "time_period", "days_back": 200},
     "warning", 1,
     "季度新鲜度：BIS GLI 是 T+1 季发布（如 2026-Q1 数据约 2026-07 才出），"
     "200 天容忍覆盖「发布滞后 + 机器离线」；再晚即断档"),
    ("glb_rows", "global_liquidity_bis", "row_count_total",
     {"min_rows": 6000}, "warning", 1,
     "总行数下限（实测 6,720 行；防误清空 —— BIS 维度是「国×部门×工具×头寸」笛卡尔积，"
     "一旦维度解析崩了行数会骤降）"),
    ("glb_obs_scale", "global_liquidity_bis", "where_count",
     {"where": "unit_measure = 'USD' AND obs_value IS NOT NULL AND (obs_value < 0 OR obs_value > 100000000)",
      "max_count": 0},
     "warning", 1,
     "美元读数量级守护（百万美元，单条上限 100 万亿）。挡单位切换与负值哨兵。"
     "⚠️ BIS 单行最大的是对美借款人（US/P/B ≈ 8,060 万百万），上限不可压到 3 千万"),
    # ⚠️ 以下 3 条为 2026-09-26 复核新增：守护**取数口径**本身（原 4 条只守护新鲜度/行数/量级）
    ("glb_denom_usd", "global_liquidity_bis", "where_count",
     {"where": "curr_denom <> 'USD'", "max_count": 0},
     "warning", 1,
     "币种唯一性：本表只含美元计价（采集器只拉 BIS key Q.USD）。出现非 USD 行说明采集范围变了，"
     "分析层「全球美元」口径会被稀释；若将来扩欧/日元须同步改本白名单"),
    ("glb_unit_set", "global_liquidity_bis", "where_count",
     {"where": "unit_measure NOT IN ('USD', '771')", "max_count": 0},
     "warning", 1,
     "单位白名单：只有 USD=百万美元存量 与 771=同比增速% 两种。出现第三种即上游改了维度编码，"
     "分析层按 unit_measure 筛的逻辑会静默漏数或把同比%当金额混算"),
    ("glb_offshore_nonnull", "global_liquidity_bis", "where_count",
     {"where": "borrowers_cty = '3P' AND borrowers_sector = 'N' AND lenders_sector = 'A' "
               "AND l_pos_type = 'I' AND l_instr = 'B' AND unit_measure = 'USD' "
               "AND obs_value IS NULL",
      "max_count": 0},
     "warning", 1,
     "★离岸美元核心读数不可为空。正确口径 3P×N×A×I×B×USD = 14,747,700 百万 @2026-Q1。"
     "⚠️不可用 borrowers_cty<>'US' 全量 SUM（B 已含 D/G + 3P 与明细国重复 + 771 是同比%，"
     "三重重复、实测虚高 3.12 倍）"),
    # ---------- cn_omo_daily（中国央行公开市场操作，日度） ----------
    ("cno_uniq", "cn_omo_daily", "unique_index",
     {"cols": ["section", "notice_year", "notice_no"], "expect": "exists"},
     "info", 1, "幂等保障：主键 (section, notice_year, notice_no) —— 各栏目各自独立编号"),
    ("cno_fresh", "cn_omo_daily", "date_floor",
     {"date_col": "trade_date", "days_back": 8},
     "warning", 1,
     "日度新鲜度：OMO 每个工作日都有公告（含零操作日），8 天容忍覆盖周末 + 节假日 + 机器离线"),
    ("cno_win_notnull", "cn_omo_daily", "where_count",
     {"where": "op_type = 'reverse_repo' AND win_amount IS NULL", "max_count": 0},
     "warning", 1,
     "逆回购中标量非空：零操作日 win_amount=0（不是 NULL），NULL 才表示源侧没解析到。"
     "本规则守住「逆回购必须给出操作量」的约定"),
    ("cno_rate_range", "cn_omo_daily", "where_count",
     {"where": "op_rate IS NOT NULL AND (op_rate < 0 OR op_rate > 10)", "max_count": 0},
     "warning", 1,
     "操作利率取值域 [0, 10]%。挡量纲错（百分数↔小数）"),
    ("cno_win_scale", "cn_omo_daily", "where_count",
     {"where": "win_amount IS NOT NULL AND (win_amount < 0 OR win_amount > 100000)", "max_count": 0},
     "warning", 1,
     "中标量量级守护（单位亿元：单日逆回购常态 0~7000 亿，历史上限远低于 10 万亿）。"
     "挡单位切换与「把百分比写进量列」"),
    ("cno_section_whitelist", "cn_omo_daily", "where_count",
     {"where": "section NOT IN ('omo_trade', 'outright_repo')", "max_count": 0},
     "warning", 1,
     "栏目白名单：当前只采交易公告 + 买断式两个栏目。未来扩展国债买卖/国库现金等栏目时，"
     "先加采集器再加白名单，防「采集器写出未知栏目却无人知晓」"),
]

# ============================================================================
# 2026-09-26 批次 C2（RESERVE_RULES）：中国官方外汇储备 / 黄金储备
# 出处：《货币流动性补源方案 v2.1》§11.3.6（原拟 5 条，其中 2 条阈值按实测**重定**，见下）
#
# 表 `cn_reserve_monthly`（419 行，1978-12 ~ 2026-08）承载中国层「央行对外资产」的**国际可比口径**：
# fx_reserve_usd（亿美元·官方）与 gold_reserve_oz（万盎司·实物量）。
#
# ⚠️ 两条阈值的**实测重定**（方案原值会稳定误报，勿回退）：
#   ① `cnrv_scale_fx` 方案原写「**2000 年后**外储 ∈ [25,000, 45,000]」——
#      实测 2000-2009 段 min = **1,561**（2000 年才 1,656 亿美元）、2010-2019 段 min = **24,152**
#      ⇒ 按 2000 年起算会连报 2010-2014 的行。实测 **2015+ min = 29,982 / max = 38,134**
#      ⇒ 窗口改为 **2015 年起**，区间 [25,000, 45,000] 才成立。
#   ② `cnrv_scale_gold` 用区间 [1,000, 12,000] 万盎司：实测**全史** min = 1,267 / max = 7,673
#      ⇒ 全史窗口即可，无需分段（黄金储备从 1981 年起就没低于 1,267）。
#
# ⚠️ **`BETWEEN` 不在 `_WHERE_FUNCS` 白名单里**（白名单只有 ABS/ROUND/COALESCE/IFNULL/NULL/
#    NOT/AND/OR/IN/IS/LIKE/LEAST/GREATEST）⇒ 区间只能用 `x < lo OR x > hi` 表达，
#    写成 `BETWEEN` 会被 `_validate` 判成「where 表达式含非白名单标识符」而整条记 error。
#
# ⚠️ 缺 `cnrv_no_future` 就**没有防线**：月份列是**字符串** `'YYYY.M'`，解析写错不会报错、
#    只会静默写出未来月份的行，而那种行会让 fresh/watermark 全部"通过"（表看起来永远最新）
#    ⇒ 它是唯一能穿透新鲜度守护的形态，必须由 `date_ceiling` 独立守住。
# ============================================================================
RESERVE_RULES = [
    ("cnrv_uniq", "cn_reserve_monthly", "unique_index",
     {"cols": ["stat_month"], "expect": "exists"},
     "info", 1, "幂等保障：stat_month 主键（全量 upsert 的前提）"),
    ("cnrv_fresh", "cn_reserve_monthly", "date_floor",
     {"date_col": "stat_month", "days_back": 75},
     "warning", 1,
     "月度新鲜度：官方外储约**次月 7 日**发布（黄金同月同行），75 天容忍覆盖「发布偏晚 + 机器离线数天」；再晚就是采集断档"),
    ("cnrv_no_future", "cn_reserve_monthly", "date_ceiling",
     {"date_col": "stat_month", "grace_days": 0},
     "warning", 1,
     "🔴 月份不得落到未来。源 `统计时间` 是**字符串** 'YYYY.M'（字符串序 ≠ 时间序）⇒ 解析写错不报错、"
     "只静默写出未来月份，而那种行会让新鲜度/水位线全部通过（表看起来永远最新）—— "
     "这是唯一能穿透新鲜度守护的错误形态。⚠️ 无法用 where_count 表达：白名单不放行 CURDATE/NOW"),
    ("cnrv_scale_fx", "cn_reserve_monthly", "where_count",
     {"where": "stat_month >= '2015-01-01' AND fx_reserve_usd IS NOT NULL "
               "AND (fx_reserve_usd < 25000 OR fx_reserve_usd > 45000)",
      "max_count": 0},
     "warning", 1,
     "外储量级守护（亿美元，**2015 年起**）：实测 2015+ 区间 29,982~38,134 ⇒ 取 [25,000, 45,000]。"
     "⚠️ 方案原写「2000 年后」，实测 2000-2009 min=1,561、2010-2019 min=24,152，按 2000 年起算会连报误警"),
    ("cnrv_scale_gold", "cn_reserve_monthly", "where_count",
     {"where": "gold_reserve_oz IS NOT NULL "
               "AND (gold_reserve_oz < 1000 OR gold_reserve_oz > 12000)",
      "max_count": 0},
     "warning", 1,
     "黄金储备量级守护（万盎司，**全史**）：实测 min=1,267 / max=7,673 ⇒ 区间 [1,000, 12,000]。"
     "挡「吨↔万盎司」这类单位错（1 吨 ≈ 3.215 万盎司，混用会产生 3 倍级偏差）"),
]


# 已废弃规则：每次 seed 时显式删除（避免升级后旧冻结规则与新规则并存产生噪音）
RETIRED_RULES = [
    # 2026-09-25 货币流动性批次 2C：海外指数扩到 10 条腿（跨亚/欧/美三个时区）后，
    # 本规则「全表 MAX(trade_date) 那一日的行数 ≥ 3」的结果开始取决于体检时刻
    # （新浪 gi 会返回当日盘中 bar，盘中跑时只有 N225 一行 → 假 fail），
    # 已由 overseas_spx_fresh / overseas_dax_fresh / overseas_n225_fresh / overseas_kospi_fresh
    # 四条逐腿 date_floor_where 替代。详见 CONSUMPTION_RULES 中该段的注释。
    "overseas_rows_latest",
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
            + [(r, "daily") for r in CONSUMPTION_RULES]    # 2026-09-19 Batch C 消费端守护（5 表 3 模块）
            + [(r, "daily") for r in DEBT_RULES]           # 2026-09-19 Batch D3 已知技术债监控
            + [(r, "daily") for r in REF_RULES]             # 2026-09-24 引用完整性 + 名册停更守护
            + [(r, "daily") for r in LIQUIDITY_RULES]       # 2026-09-25 货币流动性批次 2（3 新表 + 自算 DXY）
            + [(r, "daily") for r in RESERVE_RULES]         # 2026-09-26 批次 C2（中国官方外储/黄金）
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
