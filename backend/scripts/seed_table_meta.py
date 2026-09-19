#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
InvestBuddy table_meta 元数据播种脚本（数据中心「数据流卡」数据源）

背景（2026-09-06 用户需求：数据中心按表联动展示 数据源/清洗口径/维护任务调度/质量）：
- table_meta 把散落在「数据体系设计规范 v1.5」与各采集器 docstring 的表级数据流元数据结构化入库；
- DataView 右侧「数据流」折叠卡 = table_meta(源/口径/writers) + task_config/cron + task_runs(最近运行) + dq/table-status；
- 内容基线同步自设计文档 §2 表清单 / §3.2 任务清单 / §6 健康结论（2026-09-05 实测快照）。

字段：
- category    域分类（行情/指数/概念/日历/资讯/资料/基金/系统/质量/静态…）
- source_desc 数据源构成（分号分隔，前端拆标签展示）
- flow_desc   数据流与清洗口径说明（写表方式/频率/降级/护栏）
- writers     维护该表的采集任务名数组（对应 task_config.task_name；无任务=[]）
- writer_cols 列级血缘：{任务名: {source, cols[], derived[], note?, col_notes{}}} —— 该任务主要负责写哪些列
- note        备注（停更/备份/缺口状态等）

幂等：CREATE TABLE IF NOT EXISTS + 按主键 UPSERT，可安全重复执行。
"""
import logging
import sys

sys.path.insert(0, __file__.rsplit("scripts", 1)[0])  # backend 根

import pymysql

from app.db import get_db_config

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

_DDL = """
CREATE TABLE IF NOT EXISTS table_meta (
  table_name varchar(64) NOT NULL COMMENT '目标表名',
  category varchar(20) NULL COMMENT '域分类（行情/指数/概念/日历/资讯/资料/基金/系统/质量等）',
  source_desc varchar(255) NULL COMMENT '数据源构成（分号分隔，前端拆标签）',
  flow_desc varchar(1000) NULL COMMENT '数据流与清洗口径说明',
  writers json NULL COMMENT '维护该表的采集任务名数组（对应 task_config.task_name，无任务为空）',
  writer_cols json NULL COMMENT '列级血缘：{任务名:{source,cols,derived,note,col_notes}} 该任务主要负责写哪些列',
  note varchar(255) NULL COMMENT '备注（停更/备份/缺口）',
  update_time timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '元数据维护时间',
  PRIMARY KEY (table_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='数据中心表级元数据：数据源/清洗口径/维护任务（数据流卡数据源）'
"""

# (table_name, category, source_desc, flow_desc, writers, note)
_META: list[tuple[str, str, str, str, list[str], str]] = [
    # ---- 健康自更新 12 张业务表 + dq_report ----
    ("stock_market_daily", "行情",
     "东财;腾讯;新浪;Tushare",
     "股票日线历史（1990-12-19~）。stock_daily_incr 按每只股票本地最新日起增量补齐（前复权），四级降级：东财→腾讯→新浪→Tushare；退市/停牌股失败不阻断全量；每工作日 19:00。",
     ["stock_daily_incr"], ""),
    ("stock_market_current", "行情",
     "本地聚合（无外部源）",
     "每日行情快照：由 stock_market_daily 最新交易日聚合出全市场当日行情（TRUNCATE+全量重建 ~5,400 行，双重护栏拒写：①<1,000 行 ②不足上一交易日的 90%）；每工作日 20:15（**必须晚于 stock_daily_incr 跑完**——日线常态耗时 15~50 分钟，原 19:30 会读到半量数据，2026-09-16 因此写出 2820/5119 行残快照并毒害下游 stock_info_sync 名单）。",
     ["market_current_sync"], ""),
    ("dc_index_market", "指数",
     "中证官网;国证+腾讯;东财",
     "指数日线（21 个主流指数：市场基准 5 / 市值风格 5 / 科技成长 5 / 情绪温度 1 / 股息防守 4 / 政策周期 1，按指数实际体现的观察内容分组）：中证官网主源含全字段，国证+腾讯合并链（OHLCV 腾讯、成交额国证），东财降级；每工作日 18:30 增量。",
     ["index_market_sync"], ""),
    # 2026-09-14 补：分析研究·市场风向模块的物化表（此前漏登 table_meta）
    ("market_style_daily", "分析",
     "本地聚合（dc_index_market 派生 + stock_market_daily 个股聚合，无外部源）",
     "市场风格日频物化表（5,253 行 / 2005-02-01~）：market_style_sync 每工作日 20:05（**晚于个股日线跑完**，"
     "并带「日线充分性护栏」——当日行数不足上一交易日 90% 时上界退回上一交易日；原 18:45 必然早于日线完成）"
     "按 dc_index_market 的 index_group 六分类等权合成收益（20/60 日）、大小盘剪刀差、风险偏好分数、"
     "情绪温度、政策超额、250 日分位；2026-09-14 起增设**市场宽度** 12 列（个股涨跌家数/涨停跌停/"
     "站上 MA20·MA60 占比/60 日新高新低/腾落线 ADL）+ **量能** 2 列，因为原 19 列全是指数间收益差、测不到"
     "「上涨是否普遍」；2026-09-15 起增设**风险调整** 2 列（剪刀差/风偏 ÷ 其自身滚动σ，修正两条腿"
     "波动率不对称）与**换手率中位数** 1 列（交叉印证的「微观结构」项所需，源列单位漂移已归一）；"
     "纯库内计算，指数列全量重建 + 个股派生列 pandas 增量重算；供「分析研究·市场风向」消费。",
     ["market_style_sync"], ""),
    ("bond_profit_daily", "债券",
     "中债;美债(akshare bond_zh_us_rate)",
     "中美国债 2/5/10/30y 收益率 + spread 日线；每工作日 19:15。"
     "⚠️ **两腿独立水位线**（2026-09-19 修复）：中债腿与美债腿各自算最后非空日，"
     "起点取两者**较早**者（不再一律 MAX(trade_date)）——原先只看全表最大日，"
     "晚间 19:15 跑到时美债当日尚未发布、写入 NULL 行后水位即被推高，"
     "美债列因此自 2026-09-07 起全 NULL 断供 10 天且无规则报警。"
     "同时 INSERT 改 Upsert（uk_trade_date），已回填的历史非空值不再被 NULL 冲掉。",
     ["bond_profit_sync"], ""),
    ("index_constituents", "指数",
     "中证指数官网;国证;csindex members",
     "指数成分股快照（11 指数 ~2,819 行/快照日，**按日留档**）：index_cons_sync 每月 15 日 21:30 "
     "全量刷新（覆盖月度调样）；csindex 主源 → cni/members 降级；stock_name 缺失时以 stock_info 兜底回填。"
     "⚠️ 2026-09-19 §7-⑧ 改造：唯一键由 (index_code, stock_code) 改为 "
     "**uk_index_stock_date(index_code, stock_code, trade_date)**，DELETE 只删**本快照日**的行 → "
     "历史快照保留，可回溯「某只股票何时进出某指数」；trade_date=快照日（本轮采集日），"
     "源侧样本日期另存 sample_date（国证源无则 NULL）。",
     ["index_cons_sync"], ""),
    ("index_profile", "指数",
     "中证指数官网",
     "指数档案（13 只跟踪指数）：名称/代码/简介，随 index_cons_sync 刷新；uk_index_code 幂等；"
     "供前端指数列表与释义展示。",
     ["index_cons_sync"], ""),
    ("ths_concept_info", "概念",
     "同花顺",
     "概念板块清单：concept_market_sync 增量中发现新概念自动注册（309xxx）；与概念指数同轮（每工作日 20:00）。",
     ["concept_market_sync"], ""),
    ("ths_concept_market", "概念",
     "同花顺",
     "概念指数日线（2018-04-12~，398 概念）：每工作日 20:00 增量；失败单概念不阻断整轮。",
     ["concept_market_sync"], ""),
    ("ths_stock_concepts", "概念",
     "同花顺;新浪",
     "股票↔概念关系表：逐概念重建（DELETE+INSERT 小事务）；同花顺反爬/失败自动切新浪同名概念，双失败保留旧数据；每周一 21:00。",
     ["concept_sync"], "生产同花顺通道首跑后转健康"),
    ("news", "资讯",
     "东财快讯;财联社",
     "财经资讯：每 30 分钟双源拉取，财联社失败仅告警；INSERT IGNORE 兜底；保留窗口有限（观察滚动清理）。",
     ["news_fetch"], ""),
    ("trade_calendar", "日历",
     "新浪交易日历(akshare)",
     "A 股交易日历：补齐当年+次年；每周日 09:00。",
     ["trade_calendar_sync"], ""),
    ("finance_calendar", "日历",
     "东财 RPT_CPH_FECALENDAR;JY(历史)",
     "财经日历事件：每日拉未来 60 天窗口，窗口内 DELETE+重插幂等；EM-CAL 与历史 JY 源并存（data_source 区分）；每日 17:45。",
     ["finance_calendar_sync"], ""),
    ("fund_info", "基金",
     "东财基金列表(akshare)",
     "基金基本信息：每月 1 日全量重建 ~27,800 行；<20,000 行护栏拒绝覆盖。",
     ["fund_info_sync"], ""),
    ("stock_info", "资料",
     "东财全A名单;Baostock;巨潮资讯",
     "证券主表+公司档案宽表（34 列）：东财周更名单（短名/exchange，list_date 本地 MIN 推断）；Baostock 周更上市/退市状态+退市日（ipoDate 仅补空）；巨潮日更档案 24 列（列级 UPDATE）。data_source=EM;BAOSTOCK;CNINFO。",
     ["stock_info_sync", "stock_status_sync", "stock_company_sync"], "三源构成见列注释"),
    ("stock_info_ex", "资料",
     "东财全A名单",
     "股票信息扩展：随 stock_info 周更同步全市场名单；人工 is_gxlstock（高股息）标记保留。",
     ["stock_info_sync"], ""),
    ("finance_concept_analysis", "AI",
     "豆包方舟(ARK)",
     "AI 概念分析：分析投资日历事件→关联概念（评分 1-10）；无 ARK_API_KEY 时降级写占位结果；手动触发（当前 enabled=0）。",
     ["ai_concept_analysis"], "当前为占位降级态"),
    ("dq_report", "质量",
     "自产(data_quality_check)",
     "数据质量体检结果快照：每日 21:50（主触发=链式）读 dq_rules（104 条规则）逐条执行写入；"
     "大表限定最新时间切片秒级完成；同轮运行中保护。",
     ["data_quality_check"], ""),
    # ---- 无采集任务（历史导入/静态/系统） ----
    ("stock_market_daily_ex", "行情",
     "历史导入",
     "日K线**不复权原始价**（1,689 万行 / 5,779 只 / 1990-12-19~2025-09-19）："
     "2026-09-13 全表逐字段比对推翻「冗余副本」结论——公共键 1,689 万行中 85% 六字段不同，"
     "实为复权口径差异（daily=前复权 qfq，有 58.7 万行负价退化区；ex=不复权原始价）；"
     "另含 109 个 daily 缺失的键（深 B 200020/200429/200726、北交所 430556 老三板等）。"
     "库内唯一原始价基准，可作为复权因子重建依据，**不可归档**，维持冻结监护。",
     [], "不复权原始价 · 不可归档"),
    ("stock_market_daily_bak_20250802", "行情",
     "备份",
     "2025-08-02 日线数据备份（一次性），只读归档勿写入。",
     [], "备份表待归档"),
    ("futures_spot_price", "商品",
     "100ppi(akshare futures_spot_price_daily)",
     "期货现货价格与基差（54 品种，2012-01~）：futures_sync 分块拉取（chunk_days≤30，"
     "源单块 60~165s）+ 每块独立提交 + 单块 180s 超时；幂等按本地 MAX(trade_date)+1 续跑"
     "（本表同日多快照且值不同，**物理上不可建唯一索引**）；单位统一元/吨。"
     "派生列 main_basis_high/low/avg_180d 为日历 180 天滚动窗口（本地历史 basis ∪ 本轮新值）。"
     "每工作日 22:20。",
     ["futures_sync"], ""),
    ("stock_capital_flow", "资金",
     "历史导入;东财(akshare stock_capital_flow, 未启用)",
     "日度资金流向（748k 行，停更于 2025-09-19）：capital_flow_sync 已实现"
     "（逐股滚动 + uk_stock_date 幂等）但**默认禁用**——东财域名在本机沙箱不可达，"
     "且「主力净流入=超大单+大单」仅 90~93.8% 成立、源口径未与本地表完全对齐，待复核后启用。",
     ["capital_flow_sync"], "采集器已实现未启用（enabled=0）"),
    ("securities_margin", "资金",
     "上交所;深交所;北交所(akshare)",
     "沪深北三市两融合计：rzye=融资余额、rqye=融券余额、rzrqye=rzye+rqye、rzrqyecz=rzye-rqye（本地派生）。margin_sync 每日 20:00 从本地 MAX(trade_date) 次日增量补齐；单位归一——上交所为元、深交所为亿元(×1e8)、北交所取明细求和(元)。",
     ["margin_sync"], ""),
    ("stock_financial_abstract_ths", "财务",
     "同花顺(akshare stock_financial_abstract_ths)",
     "财务关键指标 8 列（34.4 万行 / 5,854 只）：financial_abstract_sync 每日 23:00 "
     "只补「MAX(报告期) < max(本地全局 MAX, 披露日历推算最近期)」的滞后股票；"
     "候选池排除退市股（其报告期恒滞后，会永久占满 max_stocks 名额）；"
     "缺失值清洗——akshare 对缺失返回布尔 False（非 NaN）→ 统一 NULL，'--' 同处理。"
     "uk_stock_report 幂等 upsert。",
     ["financial_abstract_sync"], ""),
    ("ths_stock_dividend", "财务",
     "同花顺(akshare stock_fhps_detail_ths)",
     "分红送配明细（11 业务列与源列一一对应）：ths_dividend_sync 每月 1/15 日 03:00 逐股增量补齐，仅插入源有而本地缺的 report_period，不重写既有行；被 analysis/dividend 分红率分析消费（前端「股息率排行」）。",
     ["ths_dividend_sync"], "含历史重复行约 9.5 万，粒度待专项确认"),
    ("stock_shares", "基本面",
     "巨潮资讯(akshare stock_share_change_cninfo)",
     "股本变动明细（事件型表，源单位万股→×10000 存股数）：stock_shares_sync 每日 22:40 "
     "逐股滚动刷新——事件型表 change_date 常年不变，故按 MAX(update_time) 判「久未刷新」"
     "（refresh_days=30）轮转；退市股源侧无记录（akshare 抛 KeyError 公告日期）归一为"
     "「无数据」，候选池亦排除；upsert 覆盖（巨潮会回溯修订）。字段口径实证："
     "list_a_shares←人民币普通股（A 股流通股，非「已流通股份」）、limit_shares←流通受限股份。",
     ["stock_shares_sync"], ""),
    ("stock_industry_sw", "行业",
     "申万宏源(akshare index_component_sw)",
     "申万一二级成分快照（10,428 行 / 5,214 只 / 162 行业 = 31 一级 + 131 二级）："
     "sw_industry_sync 每月 1 日 03:30 整体重建（DELETE+INSERT 小事务，调样即全量刷新）；"
     "目录来自 sw_index_first/second_info 遍历，覆盖率 <85% 判源异常回滚。"
     "uk_stock_level（stock_code+industry_type）幂等。",
     ["sw_industry_sync"], ""),
    ("stock_jgdy_detail", "调研",
     "东财(akshare stock_jgdy_tj_em)",
     "机构调研明细：jgdy_sync 每日 22:00 从本地 MAX(announcement_date) 回溯 30 天单次拉取增量（该接口 date 参数是「公告日期起点」而非接待日期，一次调用返回区间内全部记录）；按四元组判重跳过。",
     ["jgdy_sync"], ""),
    ("stock_hold_by_fund", "基金",
     "历史导入",
     "基金重仓持股（113k 行）；2025-08-16 后无任务。",
     [], "停更"),
    # ---- 系统表 ----
    ("task_config", "系统",
     "手动维护;作业监控前端",
     "采集任务配置（35 任务：enabled/cron/params，列名就是 `cron`）；作业监控栏目可热改，调度器实时同步。⚠️ cron 的 day_of_week 为 APScheduler 语义（0=周一、6=周日，与 Unix crontab 相反），界面与调度日志会同时显示 cron_human 中文语义。⚠️ 主触发=链式：19:20 日线 success 后自动接力 market_style → market_current → data_quality_check，表内时刻（21:30/21:40/21:50）仅兜底。",
     [], ""),
    ("task_runs", "系统",
     "自产(TaskRecorder)",
     "作业运行记录：每次执行 running→success/failed/**blocked**（blocked=数据未就绪，非失败、不计入失败率）+ 写入条数/错误信息；保留最近 ~100 条。",
     [], ""),
    ("dq_rules", "质量",
     "手动维护;seed_dq_rules",
     "数据质量规则配置（104 条 / 15 类检查器，daily 99 + weekly 5；2026-09-19 新增 column_watermark 检查器专治「有列无值」）；数据质量栏目可维护。",
     [], ""),
    ("stock_company_profile_bak_20260905", "资料",
     "备份(巨潮)",
     "v1.4 并入 stock_info 前的旧公司档案宽表（回滚点）；确认稳定后可 DROP。",
     [], "备份表待归档"),
    # ---- L1/L2 明细表（2026-09-10 新增） ----
    ("dq_gap_detail", "质量",
     "自产(data_quality_check_weekly)",
     "全史疑似缺口明细（L1 gap_scan 产出 → L3 消费）：按 (stock_code, prev_date, next_date) 唯一；status: open/fixed/ignored；30 天保留（与 dq_report 对齐）。",
     ["data_quality_check_weekly"], ""),
    ("dq_recon_detail", "质量",
     "自产(daily_recon)",
     "外部对账差异明细（L2 recon 产出）：仅记差异，零差异轮次明细为空；30 天保留。",
     ["daily_recon_window", "daily_recon_sample"], ""),
    # ---- 系统与配置表 ----
    ("table_meta", "系统",
     "手动维护;seed_table_meta",
     "表级元数据：category/source_desc/flow_desc/writers/writer_cols；数据中心右侧「数据流」折叠卡与本表双向引用；当前本表登记 43 张表（库内共 44 张：29 业务 + 新增 6 + 系统/质量）。",
     [], "元数据自身"),
    ("user_prefs", "系统",
     "手动维护;前端 SortPrefsModal",
     "用户偏好：数据中心表格默认排序（按表持久化）；DataView 「默认排序」弹窗可改。",
     [], ""),
    # ---- 2026-09-19 市场风向蓝图 P2/P3 落地（6 张新表）----
    ("index_valuation_daily", "估值",
     "中证指数官网;乐咕乐股;全A等权(akshare)",
     "指数估值日频三源（1,281 行）：**source 列区分三套口径、绝不可跨源比较**——"
     "csindex=`stock_zh_index_value_csindex`（中证官网，滚动 PE 整体法）；"
     "legu=`stock_index_pe_lg`（乐咕，上证50/沪深300/中证500/中证1000，**含近 250 个月末历史**，"
     "ERP 分位窗口的唯一来源，故 lookback_days=0 不截断）；all_a=`stock_a_ttm_lyr`（全A等权）。"
     "⚠️ 实测同日中证滚动 PE 系统性高于乐咕（16.92 vs 12.68）→ 只可同源纵向比。"
     "uk_index_date_source 幂等 upsert；每工作日 19:25。消费方：ERP（100/滚动PE − 10Y 国债）。",
     ["index_valuation_sync"], ""),
    ("interbank_rate_daily", "利率",
     "上海银行同业拆借市场(Shibor);全国银行间同业拆借中心(LPR)",
     "银行间拆借利率 + LPR（4,984 行）：Shibor 隔夜/1周/1月/3月 走 "
     "`rate_interbank(market='上海银行同业拆借市场')`；LPR 1Y/5Y 走 `macro_china_lpr`。"
     "⚠️ LPR 是**月度报价**（每月 20 日公布），采集器按「公布后生效至下次公布」"
     "**顺延填充到每个自然日**，否则按日 join 会大面积缺值。"
     "PRIMARY KEY(trade_date) 幂等 upsert；每工作日 19:35。回答蓝图A「钱贵不贵」。",
     ["interbank_rate_sync"], ""),
    ("overseas_index_daily", "海外",
     "新浪财经(akshare stock_hk_index_daily_sina / index_us_stock_sina)",
     "海外与港股指数日线（20,367 行）：HSI 恒生（`stock_hk_index_daily_sina`）"
     "+ DJI/SPX/IXIC 道指·标普500·纳指（`index_us_stock_sina`）。"
     "uk_code_date 幂等增量；每自然日 19:50（美股为 T-1 收盘，源侧天然滞后一天，别当缺数）。"
     "回答蓝图E「外围环境」。",
     ["overseas_index_sync"], ""),
    ("currency_boc_daily", "汇率",
     "新浪财经-中行人民币牌价(akshare currency_boc_sina)",
     "人民币外汇牌价与中间价（5,615 行 / 4 币种 USD·EUR·JPY·HKD）："
     "mid_price=央行中间价（政策意图）、spot_buy/sell=中行汇买/汇卖（市场实现），"
     "两者背离本身即信息（中间价稳而即期弱 = 贬值压力靠逆周期因子硬压）。"
     "⚠️ 源按**每 100 外币**报价，采集器已 ÷100 归一为**元/1 外币**（下游无需再除）。"
     "⚠️ 源 symbol 必须逐字对齐（'港币' 而非 '港元'，拼错在映射表里抛 KeyError）。"
     "uk_currency_date 幂等；每自然日 20:05。回答蓝图E「汇率破位=外资流出压力」。",
     ["currency_boc_sync"], ""),
    ("fund_new_issue", "基金",
     "东财数据中心(akshare fund_new_found_em)",
     "新基金发行（6,848 只）：含认购期/募集份额/成立日/经理/申购状态，"
     "回答蓝图E「新增资金供给」。全量 upsert（PRIMARY KEY fund_code，整表仅数千行，不做增量）；"
     "每自然日 20:20。⚠️ establish_date 为 NULL = 仍在发行未成立，属正常态而非缺数。",
     ["fund_new_issue_sync"], ""),
    ("stock_repurchase", "回购",
     "东财数据中心(akshare stock_repurchase_em)",
     "股票回购明细（5,513 条 / 12 页）：预案价上下限、计划金额与占比、已完成金额/股数/均价、进度。"
     "回答蓝图D「产业资本态度」。全量 upsert（uk_code_start(stock_code, start_date)，"
     "源会回溯修订进度，故必须覆盖写而非 INSERT IGNORE）；每自然日 20:35。",
     ["stock_repurchase_sync"], ""),
]

# 列级血缘：table_name -> { 任务名: {source, cols[], derived[], note?, col_notes{}} }
# - source    该任务上游数据源标签（前端血缘图左侧节点）
# - cols      该任务主要负责写入/维护的列（前端血缘图右侧列 chips）
# - derived   其中「本地加工/推断口径」列（前端虚线 + * 标注 + col_notes tooltip）
# - note      任务级补充（如"仅补空"），无则省略
# 约定：同列多任务共写时归主任务（如 list_date 归 stock_info_sync），其余任务以 note 说明。
_WRITER_COLS: dict[str, dict[str, dict]] = {
    "stock_info": {
        "stock_info_sync": {
            "source": "东财全A名单",
            "cols": ["stock_code", "short_name", "exchange", "list_date"],
            "derived": ["exchange", "list_date"],
            "note": "名单内 UPDATE / 新上市 INSERT（stock_code）；list_date 取本地 MIN 推断为主",
            "col_notes": {
                "exchange": "本地加工口径：无现成源字段，按代码前缀推导 60/68→SH、00/30→SZ、43/8x/92→BJ",
                "list_date": "本地推断口径 MIN(stock_market_daily.trade_date) 为主；Baostock 官方 ipoDate 仅补空；巨潮整体上市口径弃用",
            },
        },
        "stock_status_sync": {
            "source": "Baostock 官方",
            "cols": ["list_status", "delist_date"],
            "note": "另以官方 ipoDate 仅补空 list_date 为空的行（不覆盖推断值）",
        },
        "stock_company_sync": {
            "source": "巨潮资讯官方",
            "cols": [
                "company_name", "en_name", "prev_names", "a_short", "b_code", "b_short", "h_code", "h_short",
                "index_members", "market", "industry", "legal_rep", "reg_capital", "establish_date",
                "website", "email", "phone", "fax", "reg_address", "office_address", "postcode",
                "main_business", "business_scope", "org_intro", "profile_updated_at",
            ],
            "note": "列级 UPDATE 档案列自身 + profile_updated_at=NOW()，不触碰证券列",
        },
    },
    "stock_info_ex": {
        "stock_info_sync": {
            "source": "东财全A名单",
            "cols": ["stock_code", "short_name", "exchange", "list_date"],
            "note": "is_gxlstock（高股息人工标记）采集器不覆盖",
            "derived": ["exchange", "list_date"],
            "col_notes": {
                "exchange": "本地加工口径：按代码前缀推导 60/68→SH、00/30→SZ、43/8x/92→BJ",
                "list_date": "本地推断口径 MIN(stock_market_daily.trade_date) 为主；Baostock ipoDate 仅补空",
            },
        },
    },
    "stock_market_daily": {
        "stock_daily_incr": {
            "source": "东财行情(四级降级)",
            "cols": ["stock_code", "trade_date", "open", "high", "low", "close", "pre_close",
                     "change_amount", "change_pct", "volume", "amount", "turnover_ratio"],
            "derived": ["pre_close", "change_amount", "change_pct"],
            "note": "前复权；增量按本地最新日补齐，INSERT IGNORE 去重；昨收/涨跌额/涨跌幅源缺失时按清洗口径本地推算",
            "col_notes": {
                "pre_close": "昨收：源提供则直采；整列缺失时按日期升序用收盘价 shift(1) 推算（首行昨收为空）",
                "change_amount": "涨跌额：源缺时以收盘-昨收推算 round 3",
                "change_pct": "涨跌幅：源缺时以 (收盘-昨收)/昨收×100 推算 round 4",
            },
        },
    },
    "stock_market_current": {
        "market_current_sync": {
            "source": "本地聚合(daily)",
            "cols": ["stock_code", "stock_name", "new", "change_pct", "change_amount", "open", "high", "low",
                     "pre_close", "volume", "amount", "turnover_ratio", "amplitude", "ytd_change_pct",
                     "dynamic_pe", "pb", "volume_ratio", "rise_speed", "5m_change_pct", "60d_change_pct",
                     "total_captital", "float_captital"],
            "note": "TRUNCATE+全量重建（双重护栏拒写：①<1,000 行 ②不足上一交易日 90%）",
        },
    },
    "dc_index_market": {
        "index_market_sync": {
            "source": "中证官网;国证+腾讯;东财",
            "cols": ["index_code", "index_name", "trade_date", "open", "high", "low", "close",
                     "volume", "amount", "change_amount", "change_pct", "turnover_ratio",
                     "index_group", "group_desc"],
            "note": "21 个指数（基准5/市值5/成长5/情绪1/防守4/政策1，INDEX_META 随行写 index_group/group_desc）；"
                    "中证官网主源全字段（2005 前历史为源侧回溯测算值，注意假日锚点行需清理），国证+腾讯合并链，东财降级仅 OHLCV",
        },
    },
    "market_style_daily": {
        "market_style_sync": {
            "source": "本地聚合(dc_index_market 派生 + stock_market_daily 个股聚合)",
            "cols": ["trade_date", "ret_bench_20", "ret_bench_60", "ret_size_20", "ret_size_60",
                     "ret_tech_20", "ret_tech_60", "ret_sent_20", "ret_sent_60",
                     "ret_div_20", "ret_div_60", "ret_pol_20", "ret_pol_60",
                     "scissors_20", "scissors_60", "risk_appetite_20", "sentiment_20",
                     "policy_excess_20", "bench_pos_pct",
                     # 风险调整（2026-09-15 新增，来源同指数列）
                     "scissors_adj20", "risk_appetite_adj20",
                     # 市场宽度（2026-09-14 新增，来源 stock_market_daily 全市场个股）
                     "breadth_total", "breadth_up", "breadth_down", "breadth_up_ratio",
                     "breadth_adl", "limit_up", "limit_down",
                     "above_ma20_pct", "above_ma60_pct", "new_high60", "new_low60", "hl_diff60",
                     # 量能（2026-09-14 批次 3）与换手率结构（2026-09-15 P1 交叉印证）
                     "market_amount", "amount_ratio_20", "turnover_med"],
            "derived": ["ret_bench_20", "ret_bench_60", "ret_size_20", "ret_size_60",
                        "ret_tech_20", "ret_tech_60", "ret_sent_20", "ret_sent_60",
                        "ret_div_20", "ret_div_60", "ret_pol_20", "ret_pol_60",
                        "scissors_20", "scissors_60", "risk_appetite_20", "sentiment_20",
                        "policy_excess_20", "bench_pos_pct",
                        "scissors_adj20", "risk_appetite_adj20",
                        "breadth_total", "breadth_up", "breadth_down", "breadth_up_ratio",
                        "breadth_adl", "limit_up", "limit_down",
                        "above_ma20_pct", "above_ma60_pct", "new_high60", "new_low60", "hl_diff60",
                        "market_amount", "amount_ratio_20", "turnover_med"],
            "note": "全列为本地派生：六分类等权合成 20/60 日区间收益、大小盘剪刀差、风险偏好、情绪温度、"
                    "政策超额、250 日分位；风险调整 2 列 = 差值 ÷ 其自身近 250 日滚动σ"
                    "（0 = 两腿同收益，刻意不减均值以免与「250 日分位」重复）；"
                    "宽度 12 列 + 量能 2 列 + 换手率 1 列为 stock_market_daily 全市场个股聚合"
                    "（pandas 增量重算，按 stock_code 顺序取数）；"
                    "换手率必须物化 —— 源列不在索引内，按 trade_date 现查要随机回表（单日 15 秒）；"
                    "⚠️ 源列 turnover_ratio 单位曾于 2025-09 中旬由百分数切换为小数（差 100 倍），"
                    "已按日归一为百分数；指数列全量重建 + 个股派生列增量合并",
            "col_notes": {
                "scissors_20": "大小盘剪刀差 = 小盘收益 − 大盘收益（20 日）",
                "risk_appetite_20": "风险偏好分数（成长+情绪 相对 防守+政策）",
                "risk_appetite_adj20": "风偏风险调整 = risk_appetite_20 ÷ 其自身近250日滚动σ；σ 恒正故与原值同号，0 = 两腿同收益",
                "scissors_adj20": "剪刀差风险调整 = scissors_20 ÷ 其自身近250日滚动σ；消除「小盘波动大」造成的尺度漂移",
                "bench_pos_pct": "市场基准 250 日分位（0~100，绝不对涨跌染色）",
                "breadth_up_ratio": "上涨家数占比%；与站上均线占比、ADL 共同回答「上涨是否普遍」",
                "breadth_adl": "腾落线 = Σ(上涨家数 − 下跌家数) 的全史累计，需拿全序列计算才连续",
                "limit_up": "涨停家数，近似口径：主板 ≥9.8%、创业板/科创板 ≥19.8%（未细分 ST 5%）",
                "above_ma60_pct": "站上 MA60 占比%，分母为当日已有 60 日历史的个股（不把次新股算成跌破）",
                "hl_diff60": "创 60 日新高 − 新低家数差，情绪拐点先行信号",
                "market_amount": "全市场成交额（亿元），即「两市成交额」",
                "amount_ratio_20": "成交额 / 近 20 日均值 ×100%，>100 放量 / <100 缩量",
                "turnover_med": "全市场个股换手率**中位数**%，已归一为百分数；用中位数而非均值（源列有极端异常值，均值被污染近 10 倍）",
            },
        },
    },
    "index_constituents": {
        "index_cons_sync": {
            "source": "中证指数官网;国证;csindex members",
            "cols": ["index_code", "stock_code", "stock_name", "weight", "trade_date", "source"],
            "note": "csindex 主源 → cni/members 降级；stock_name 缺失以 stock_info 兜底回填；uk_index_stock 幂等",
        },
    },
    "index_profile": {
        "index_cons_sync": {
            "source": "中证指数官网",
            "cols": ["index_code", "index_name", "description", "base_date", "base_point", "source"],
            "note": "13 只跟踪指数的档案，随成分快照同轮刷新；uk_index_code 幂等",
        },
    },
    "bond_profit_daily": {
        "bond_profit_sync": {
            "source": "akshare 中美国债收益率",
            "cols": ["trade_date", "cn_bond_2y", "cn_bond_5y", "cn_bond_10y", "cn_bond_30y",
                     "cn_bond_10y_2y_spread", "us_bond_2y", "us_bond_5y", "us_bond_10y", "us_bond_30y",
                     "us_bond_10y_2y_spread"],
            "note": "spread 由源接口直接提供",
        },
    },
    "ths_concept_info": {
        "concept_market_sync": {
            "source": "同花顺概念",
            "cols": ["index_code", "concept_code", "concept_name"],
            "note": "增量中发现新概念自动注册（309xxx）",
        },
    },
    "ths_concept_market": {
        "concept_market_sync": {
            "source": "同花顺概念指数",
            "cols": ["index_code", "concept_code", "concept_name", "trade_date", "open", "close",
                     "high", "low", "volume", "amount", "change_amount", "change_pct"],
            "note": "INSERT IGNORE（唯一键 index_code+trade_date）",
        },
    },
    "ths_stock_concepts": {
        "concept_sync": {
            "source": "同花顺;新浪",
            "cols": ["stock_code", "short_name", "index_code", "concept_name", "reason"],
            "note": "逐概念 DELETE+INSERT；同花顺失败自动切新浪同名概念",
        },
    },
    "news": {
        "news_fetch": {
            "source": "东财快讯;财联社",
            "cols": ["title", "source", "published_at", "content", "url"],
            "note": "INSERT IGNORE（唯一键 source+url）",
        },
    },
    "trade_calendar": {
        "trade_calendar_sync": {
            "source": "新浪交易日历",
            "cols": ["trade_date", "is_trading_day", "year", "month", "day", "weekday"],
            "note": "INSERT IGNORE（唯一键 trade_date）",
        },
    },
    "finance_calendar": {
        "finance_calendar_sync": {
            "source": "东财 RPT_CPH_FECALENDAR",
            "cols": ["event_date", "title", "content"],
            "note": "未来 60 天窗口 DELETE+重插幂等；EM-CAL 与历史 JY 源并存",
        },
    },
    "fund_info": {
        "fund_info_sync": {
            "source": "东财基金列表",
            "cols": ["fund_code", "fund_name", "fund_type"],
            "note": "每月全量重建（<20,000 行护栏拒绝覆盖）",
        },
    },
    "finance_concept_analysis": {
        "ai_concept_analysis": {
            "source": "豆包方舟 ARK",
            "cols": ["event_date", "title", "concept_code", "concept_name", "relation_type",
                     "relation_degree", "analysis"],
            "derived": ["analysis"],
            "note": "AI 生成列（analysis 由豆包生成，无 ARK_KEY 时写占位降级）",
        },
    },
    "dq_report": {
        "data_quality_check": {
            "source": "本地自产(data_quality_check)",
            "cols": ["run_at", "run_date", "rule_id", "rule_name", "table_name", "check_type",
                     "severity", "status", "metric_value", "message"],
            "note": "读 dq_rules 规则逐条执行后整轮快照写入",
        },
    },
    "ths_stock_dividend": {
        "ths_dividend_sync": {
            "source": "同花顺 stock_fhps_detail_ths",
            "cols": ["stock_code", "short_name", "report_period", "board_date",
                     "shareholders_meeting_date", "implementation_date", "dividend_plan_desc",
                     "ashare_record_date", "ashare_ex_date", "dividend_amount_total",
                     "plan_progress", "dividend_payout_ratio", "pre_tax_dividend_ratio"],
            "note": "仅插入源有而本地缺的 (stock_code, report_period)，不重写既有报告期"
                    "（方案进度由预案转实施的演进不回写，如需可另开开关）",
            "col_notes": {
                "short_name": "取自 stock_info.short_name（源接口不返回简称）",
                "pre_tax_dividend_ratio": "源列「税前分红率」，值为形如 3.19% 的文本；未实施期为 '--' 已清洗为 NULL",
            },
        },
    },
    "securities_margin": {
        "margin_sync": {
            "source": "上交所;深交所;北交所",
            "cols": ["trade_date", "rzye", "rqye", "rzrqye", "rzrqyecz"],
            "derived": ["rzrqye", "rzrqyecz"],
            "note": "三市求和：rzye/rqye 为沪+深+北合计；深交所接口单位为亿元需 ×1e8",
            "col_notes": {
                "rzrqye": "本地派生：rzye + rqye",
                "rzrqyecz": "本地派生：rzye - rqye（2026-09-13 用本地 3 个交易日反解确认，差值均为 0）",
            },
        },
    },
    "stock_jgdy_detail": {
        "jgdy_sync": {
            "source": "东财 stock_jgdy_tj_em",
            "cols": ["stock_code", "stock_name", "new", "change_pct", "received_institution_count",
                     "received_method", "receptionist_name", "receptionist_place",
                     "receptionist_date", "announcement_date"],
            "note": "接口 date 参数为「公告日期起点」，单次调用返回区间内全部记录；"
                    "按 (stock_code, receptionist_date, received_method, received_institution_count) 判重",
        },
    },
    # ---- 2026-09-13 死表恢复采集 第 3/4 批（P2 + P3）----
    "futures_spot_price": {
        "futures_sync": {
            "source": "akshare futures_spot_price_daily(100ppi)",
            "cols": ["trade_date", "good_name", "spot_price", "main_contract_code",
                     "main_contract_price", "main_contract_basis", "main_contract_change_pct",
                     "main_basis_high_180d", "main_basis_low_180d", "main_basis_avg_180d"],
            "derived": ["main_contract_basis", "main_contract_change_pct",
                        "main_basis_high_180d", "main_basis_low_180d", "main_basis_avg_180d"],
            "note": "分块拉取（chunk_days≤30）+ 每块独立提交 + 单块 180s 超时；"
                    "幂等按 MAX(trade_date)+1 续跑（同日多快照不可建唯一索引）；单位统一元/吨",
            "col_notes": {
                "main_contract_basis": "现货价 − 主力合约价（源基差列口径自相矛盾，统一本地重算）",
                "main_contract_change_pct": "基差率 = (现货 − 主力价) / 现货 × 100",
                "main_basis_high_180d": "日历 180 天窗口内基差最大值（本地历史 basis ∪ 本轮新值）",
                "main_basis_low_180d": "日历 180 天窗口内基差最小值",
                "main_basis_avg_180d": "日历 180 天窗口内基差算术均值",
            },
        },
    },
    "stock_industry_sw": {
        "sw_industry_sync": {
            "source": "akshare index_component_sw(申万)",
            "cols": ["stock_code", "sw_code", "industry_name", "industry_type", "source"],
            "note": "31 一级 + 131 二级逐行业遍历成分；DELETE+INSERT 小事务整体重建"
                    "（分类会调样，全量刷新最稳）；覆盖率 <85% 判源异常回滚",
            "col_notes": {
                "industry_type": "行业级别标识（一/二级）",
            },
        },
    },
    "stock_financial_abstract_ths": {
        "financial_abstract_sync": {
            "source": "akshare stock_financial_abstract_ths(同花顺)",
            "cols": ["stock_code", "stock_name", "report_date", "net_profit", "net_profit_yoy_gr",
                     "total_operating_revenue", "total_operating_yoy_gr",
                     "basic_eps", "net_asset_ps", "roe"],
            "note": "只补滞后股票（MAX(报告期) < max(本地全局 MAX, 披露日历推算最近期)）；"
                    "候选池排除退市股；uk_stock_report 幂等 upsert（财务数据会追溯调整）",
            "col_notes": {
                "net_profit": "源缺失值清洗：akshare 返回布尔 False（非 NaN）→ NULL，'--' 同处理",
                "basic_eps": "DECIMAL(8,2)，'--'/False 清洗为 NULL",
                "net_asset_ps": "DECIMAL(8,2)，'--'/False 清洗为 NULL",
            },
        },
    },
    "stock_shares": {
        "stock_shares_sync": {
            "source": "akshare stock_share_change_cninfo(巨潮)",
            "cols": ["stock_code", "change_date", "total_shares", "limit_shares",
                     "list_a_shares", "change_reason"],
            "note": "逐股滚动刷新（事件型表按 MAX(update_time) 判久未刷新，refresh_days=30）；"
                    "源单位万股 ×10000 存股数；upsert 覆盖（巨潮会回溯修订）；"
                    "退市股源无记录（akshare 抛 KeyError 公告日期）归一为「无数据」",
            "col_notes": {
                "total_shares": "← 源「总股本」（万股 ×10000，4 位小数）",
                "limit_shares": "← 源「流通受限股份」（空按 0）",
                "list_a_shares": "← 源「人民币普通股」= A 股流通股（不是「已流通股份」；"
                                 "000002 逐位实证，B 股并存时 ≠ 总股本 − 限售）",
            },
        },
    },
    "stock_capital_flow": {
        "capital_flow_sync": {
            "source": "akshare stock_capital_flow(东财)",
            "cols": ["stock_code", "short_name", "trade_date", "main_net_inflow", "max_net_inflow",
                     "lg_net_inflow", "mid_net_inflow", "sm_net_inflow"],
            "note": "已实现但默认禁用（enabled=0）：东财域沙箱不可达；"
                    "「主力净流入=超大单+大单」仅 90~93.8% 成立，源口径待复核后启用",
        },
    },
    # ---- 2026-09-19 市场风向蓝图 P2/P3 落地 ----
    "index_valuation_daily": {
        "index_valuation_sync": {
            "source": "中证官网;乐咕;全A等权",
            "cols": ["index_code", "index_name", "trade_date", "source", "pe_lyr", "pe_ttm",
                     "pe_ttm_median", "pe_lyr_median", "dividend_yield", "dividend_yield2",
                     "pe_ttm_pct10y", "close_point"],
            "note": "三源同表以 source 区分（csindex/legu/all_a）；乐咕源保留全历史"
                    "（ERP 分位窗口依赖近 250 个月末），故 lookback_days=0 不截断",
            "col_notes": {
                "pe_ttm": "滚动市盈率 TTM（整体法）：中证「市盈率2」/ 乐咕「滚动市盈率」；"
                          "⚠️ 三源口径不可比，只可同源纵向比",
                "pe_lyr": "静态市盈率：中证「市盈率1」/ 乐咕「静态市盈率」",
                "dividend_yield": "中证源股息率1（近12个月）",
                "dividend_yield2": "中证源股息率2（近12个月，分母口径不同）",
                "pe_ttm_pct10y": "近10年 PE 分位（仅乐咕源提供）",
                "close_point": "指数点位（乐咕/全A 源提供，供与 dc_index_market 勾稽）",
            },
        },
    },
    "interbank_rate_daily": {
        "interbank_rate_sync": {
            "source": "上海银行同业拆借市场;全国银行间同业拆借中心",
            "cols": ["trade_date", "shibor_on", "shibor_1w", "shibor_1m", "shibor_3m",
                     "lpr_1y", "lpr_5y"],
            "note": "Shibor 为日频（rate_interbank）、LPR 为月度报价（macro_china_lpr）；"
                    "PRIMARY KEY(trade_date) 幂等 upsert",
            "col_notes": {
                "lpr_1y": "月度报价（每月20日公布）→ 采集器按「公布后生效至下次公布」"
                          "顺延填充到每个自然日，否则按日 join 大面积缺值",
                "lpr_5y": "同 lpr_1y，5年期报价（房贷利率锚）",
            },
        },
    },
    "overseas_index_daily": {
        "overseas_index_sync": {
            "source": "akshare 新浪外盘指数",
            "cols": ["index_code", "index_name", "trade_date", "open", "high", "low", "close",
                     "volume", "amount"],
            "note": "HSI 走 stock_hk_index_daily_sina；DJI/SPX/IXIC 走 index_us_stock_sina；"
                    "uk_code_date 幂等增量",
            "col_notes": {
                "trade_date": "当地交易日；美股为 T-1 收盘，源侧天然滞后一天（非缺数）",
            },
        },
    },
    "currency_boc_daily": {
        "currency_boc_sync": {
            "source": "akshare currency_boc_sina(新浪-中行牌价)",
            "cols": ["currency", "currency_name", "trade_date", "mid_price", "spot_buy",
                     "spot_sell", "ref_price"],
            "derived": ["mid_price", "spot_buy", "spot_sell", "ref_price"],
            "note": "uk_currency_date(currency, trade_date) 幂等 upsert；4 币种 USD/EUR/JPY/HKD",
            "col_notes": {
                "mid_price": "央行中间价，**元/1 外币**——源按每 100 外币报价，已 ÷100 归一",
                "spot_buy": "中行汇买价（元/1 外币，已 ÷100）",
                "spot_sell": "中行钞卖价/汇卖价（元/1 外币，已 ÷100）",
                "ref_price": "中行折算价（元/1 外币，已 ÷100）",
                "currency_name": "⚠️ 源 symbol 必须逐字对齐 akshare 合法取值：'港币' 而非 '港元'，"
                                 "拼错在 _currency_boc_sina_map 抛 KeyError（不报「不支持」）",
            },
        },
    },
    "fund_new_issue": {
        "fund_new_issue_sync": {
            "source": "akshare fund_new_found_em(东财)",
            "cols": ["fund_code", "fund_name", "company", "fund_type", "subs_period",
                     "raise_share", "establish_date", "manager", "purchase_status"],
            "note": "全量 upsert（PRIMARY KEY fund_code，整表仅数千行）",
            "col_notes": {
                "establish_date": "NULL = 仍在发行未成立，属正常态而非缺数",
            },
        },
    },
    "stock_repurchase": {
        "stock_repurchase_sync": {
            "source": "akshare stock_repurchase_em(东财)",
            "cols": ["stock_code", "stock_name", "start_date", "announce_date", "progress",
                     "plan_price_low", "plan_price_high", "plan_amount_low", "plan_amount_high",
                     "plan_pct_low", "plan_pct_high", "done_amount", "done_shares",
                     "done_price_low", "done_price_high"],
            "note": "全量 upsert（uk_code_start(stock_code, start_date)）；"
                    "源会回溯修订回购进度，故必须覆盖写而非 INSERT IGNORE",
        },
    },
}


def _ensure_writer_cols_column(conn) -> None:
    """老库 table_meta 表可能缺 writer_cols 列，补 ALTER（幂等）。"""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM information_schema.columns "
            "WHERE table_schema=DATABASE() AND table_name='table_meta' AND column_name='writer_cols'"
        )
        if cur.fetchone()[0] == 0:
            cur.execute(
                "ALTER TABLE table_meta ADD COLUMN writer_cols json NULL "
                "COMMENT '列级血缘：{任务名:{source,cols,derived,note,col_notes}} 该任务主要负责写哪些列' AFTER writers"
            )
            conn.commit()
            logger.info("table_meta 已补 writer_cols 列")


def main() -> None:
    conn = pymysql.connect(**get_db_config().to_dict())
    try:
        with conn.cursor() as cur:
            cur.execute(_DDL)
            logger.info("table_meta 表就绪")
            _ensure_writer_cols_column(conn)
            import json as _json

            cur.execute("SELECT table_name FROM table_meta")
            exist = {r[0] for r in cur.fetchall()}
            upserted = 0
            for name, cat, src, flow, writers, note in _META:
                wc = _WRITER_COLS.get(name)
                cur.execute(
                    """INSERT INTO table_meta (table_name, category, source_desc, flow_desc, writers, writer_cols, note, update_time)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
                       ON DUPLICATE KEY UPDATE category=VALUES(category), source_desc=VALUES(source_desc),
                         flow_desc=VALUES(flow_desc), writers=VALUES(writers), writer_cols=VALUES(writer_cols),
                         note=VALUES(note), update_time=NOW()""",
                    (name, cat, src, flow, _json.dumps(writers, ensure_ascii=False),
                     _json.dumps(wc, ensure_ascii=False) if wc else None, note),
                )
                upserted += cur.rowcount
            conn.commit()
            cur.execute("SELECT COUNT(*) FROM table_meta")
            total = cur.fetchone()[0]
            print(f"播种完成：upsert {upserted} 行（先有 {len(exist)} → 现有 {total} 行）")
            cur.execute("SELECT table_name, category FROM table_meta ORDER BY table_name")
            print("== table_meta 清单 ==")
            for r in cur.fetchall():
                print(f"  {r[0]:<36} [{r[1] or ''}]")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
