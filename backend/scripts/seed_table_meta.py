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
     "新浪;腾讯;东财;Tushare",
     "股票日线历史（1990-12-19~）。**口径 = 不复权实际成交价 + 后复权因子列 adj_factor（2026-09-20 全库改造）**："
     "实际价/后复权/前复权三种口径全部由公式派生、永不过期。stock_daily_incr 按每只股票本地最新日起增量补齐，"
     "四级降级：新浪→腾讯→东财→Tushare（raw 与 hfq 必须同源）；退市/停牌股失败不阻断全量；每工作日 19:20。"
     "存量 1,815 万行的一次性口径重写见 stock_daily_rebuild（带腾讯兜底，退市股亦覆盖）；"
     "⚠️ 改造前本表存的是「按各自最后写入日做基准的前复权价」——基准因股而异且写入后不再更新，"
     "实测指纹：末端价=实际价、历史被逐段压低（退市股 000005 1991 年被压低 8.39 倍）、早期出现负价。"
     "**2026-09-20 全量重建收尾实测**：5,787 只 / 18,495,727 行，因子空 80,438 行（0.43%），"
     "全部来自旧口径残留与退市股兜底（AKSHARE 73,768 + AUSAHRE 1,448 + AUSHARE 178 + TENCENT 5,044）；"
     "「真·未重建」（因子空 >90%）仅 8 只 / 16,241 行 —— 新浪与腾讯均无数据的退市股与北交所老码"
     "（五源探查确认无覆盖）。残留段处置 = **接受现状 + DQ 显式上账**（规则 legacy_scale_rows），"
     "不删行、不伪造：这些日期已无任何可得源。",
     ["stock_daily_incr"],
     "勘误(2026-09-20)：① data_source 存在历史拼写错误 AUSAHRE(600018,1448行)/AUSHARE(920段,178行)，"
     "未回改标签以免掩盖血缘、仅在 DQ 并集处理；② 689009 CDR 腾讯 volume 曾为真实股数×100(1432行)，"
     "已 ÷100 并在 canon() 加第三单位假设；③ 新浪 1990~92 volume 混乱，已用腾讯覆盖 55 只/10,464 行。"),
    ("stock_market_current", "行情",
     "本地聚合（无外部源）",
     "每日行情快照：由 stock_market_daily 最新交易日聚合出全市场当日行情（TRUNCATE+全量重建 ~5,121 行，双重护栏拒写：①<1,000 行 ②不足上一交易日的 90%）；每"
     "工作日 21:40（**必须晚于 stock_daily_incr 跑完**——日线常态 15~50 分钟，原 19:30 会读到半量数据：2026-09-16 写出 2820/5119 行残快照并毒"
     "害下游 stock_info_sync 名单）。⚠️ **8 个「东财实时专属列」的处置（2026-09-19）**：本表是「日线聚合」口径（data_source=daily-agg），源里本没有这"
     " 8 列，按「能否本地精确派生」分两类：① **已补齐**——total_captital / float_captital 改由 stock_shares 每只 MAX(change_date) 的"
     "最新股本本地派生（920 段曾缺，2026-09-22 已修），并顺带修好 api/market.py 里**静默失效**的「按市值排序」（原因列恒 NULL 等于没排序）；② **仍为 NULL**"
     "——dynamic_pe / pb / volume_ratio / rise_speed / 5m_change_pct。**涉及 PE/PB 的判断不要读这几列**。不接实时源的原因：东财 pus"
     "h2 子域对本机是**间歇性 RST 风控**（首连可通、连续请求即被拒），不适合作稳定依赖。⚠️ **uk_stock_code 唯一索引是幂等护栏**：并发双跑会交错写入致整表双写（实测 10,2"
     "42/5,121=2.00x，行数类 DQ 规则察觉不到），唯一键让第二次 INSERT 直接报错而非静默双份。⚠️ `turnover_ratio` 与 `stock_market_daily` 同"
     "名列**同量纲（百分比）**——本表该列自 2026-09-20 从日线聚合后即为直拷（实测 5,465/5,465 行逐行相等）。旧说法「本表是比率量纲、两表不可同比」成立于 09-20 前（当时源"
     "为东财实时快照），已失效，2026-09-22 更正；DQ 规则 current_turnover_dirty 的判据同步由 `>1` 改为 `>100%`（单日换手不可能超 100%）。",
     ["market_current_sync"], "股本 2 列已本地派生补齐、余 6 列为东财专属仍 NULL；uk_stock_code 为幂等护栏"),
    ("dc_index_market", "指数",
     "中证官网;国证+腾讯;东财",
     "指数日线（21 个主流指数：市场基准 5 / 市值风格 5 / 科技成长 5 / 情绪温度 1 / 股息防守 4 / 政策周期 1，按指数实际体现的观察内容分组）：中证官网主源含全字段，国证+腾讯合并"
     "链（OHLCV 腾讯、成交额国证），东财降级；每工作日 19:05 增量（index_market_sync）。",
     ["index_market_sync"], ""),
    # 2026-09-14 补：分析研究·市场风向模块的物化表（此前漏登 table_meta）
    ("market_style_daily", "分析",
     "本地聚合（dc_index_market 派生 + stock_market_daily 个股聚合，无外部源）",
     "市场风格日频物化表（5,253 行 / 2005-02-01~）：market_style_sync 每工作日 21:30（**晚于个股日线跑完**，并带「日线充分性护栏」——当日行数不足上一交易日 "
     "90% 时上界退回上一交易日；原 18:45 必然早于日线完成）按 dc_index_market 的 index_group 六分类等权合成收益（20/60 日）、大小盘剪刀差、风险偏好分数、情绪温"
     "度、政策超额、250 日分位；2026-09-14 起增设**市场宽度** 12 列（个股涨跌家数/涨停跌停/站上 MA20·MA60 占比/60 日新高新低/腾落线 ADL）+ **量能** 2 列"
     "，因为原 19 列全是指数间收益差、测不到「上涨是否普遍」；2026-09-15 起增设**风险调整** 2 列（剪刀差/风偏 ÷ 其自身滚动σ，修正两条腿波动率不对称）与**换手率中位数** 1 列"
     "（交叉印证的「微观结构」项所需，源列单位漂移已归一）；纯库内计算，指数列全量重建 + 个股派生列 pandas 增量重算；供「分析研究·市场风向」消费。",
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
     "源侧样本日期另存 sample_date（国证源无则 NULL）。"
     "📌 快照日数量 = **实际成功执行次数**，不是 cron 频次的推导值（D2 结论，2026-09-19）："
     "改造前唯一键不含 trade_date，同 (index_code, stock_code) 的后一次运行会**覆盖**前一次，"
     "所以 09-13/09-15 两次成功运行没留下快照日、只剩最后一次的 09-17；"
     "改造后按日留档，当前 09-17 / 09-19 两日属**正常起点**。"
     "决策：**不回补历史快照** —— csindex 只提供当前成分，历史成分无法同源回取，"
     "回补需付费第三方源，成本远超收益；往后按月度自然积累即可。",
     ["index_cons_sync"], ""),
    ("index_profile", "指数",
     "中证指数官网",
     "指数档案（13 只跟踪指数）：名称/代码/简介，随 index_cons_sync 刷新；uk_index_code 幂等；"
     "供前端指数列表与释义展示。",
     ["index_cons_sync"], ""),
    ("stock_code_mapping", "资料",
     "北交所官网（经 akshare stock_info_bj_name_code）;北交所《新旧代码对照表》",
     "证券代码变更对照台账（**北交所 920 代码切换**，2026-09-20 立）："
     "北交所自 2025-10-09 起将全部 277 只股票统一为 920 段（旧 43/83/87 段为新三板遗留），"
     "规则＝旧码前三位改 920、后三位不变，撞车时按上市时间递进第四位（837023→920123、831305→920405）。"
     "bj_stock_sync 每日 19:06 重建：本地旧码按「简称归一」匹配北交所名册，"
     "归一仍对不上的 6 条走人工核证（evidence 列随行落库），无法映射即认定已退市。"
     "**行数不变式 = 242**（官方公告的存量切换只数：240 switched + 2 retired），"
     "采集器巡检据此拦截映射退化。"
     "⚠️ list_date 列是**北交所上市日期**（名册口径），与 stock_info.list_date"
     "（推断口径：MIN(daily) + Baostock ipoDate）**不是同一套口径**，勿互相覆盖。",
     ["bj_stock_sync"], "旧→新映射的唯一权威留档；迁移可逆（按 new_code 反查回滚）"),
    ("ths_concept_info", "概念",
     "同花顺",
     "概念板块清单：concept_market_sync 增量中发现新概念自动注册（309xxx）；与概念指数同轮（每工作日 20:00）。",
     ["concept_market_sync"], ""),
    ("ths_concept_market", "概念",
     "同花顺",
     "概念指数日线（2018-04-12~，398 概念）：每工作日 20:00 增量；失败单概念不阻断整轮。"
     "⚠️ 消费方两条坑（D3，2026-09-19）："
     "① **改名会留孤儿 index_code**——同花顺改名后在新名重建序列、旧名停更（实测 `WiFi6` 末行停在 "
     "2025-09-19，现行是 `WiFi 6`）。故**禁止**按「每个概念各自的 MAX(trade_date)」关联当日行情："
     "会把一年前的涨跌幅拉进当日榜一起比大小（api/concept.py::concept_list 已修为锚定全表最新交易日）。"
     "报告 §2.5 的「WiFi6 20 日 +110.89%」实为此因，**不是源列脏**。"
     "② **change_pct 由源侧间歇性不返回**——2026-09-01/02 空值率 0%，09-15~09-18 达 99~100%"
     "（采集器 INSERT 是带该列的，属源侧行为）。按它排序会退化为任意序 → 需长期序列请用 `close` "
     "自算收益（concept_rank 的区间涨幅已是 close 口径），勿直接依赖 change_pct。"
     "监控见 DQ 规则 concept_change_pct_gap。",
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
     "财经日历事件：每日拉未来 60 天窗口，窗口内 DELETE+重插幂等；EM-CAL 与历史 JY 源并存（data_source 区分）；每日 19:10。",
     ["finance_calendar_sync"], ""),
    ("fund_info", "基金",
     "东财基金列表(akshare)",
     "基金基本信息：每月 1 日全量重建 ~27,800 行；<20,000 行护栏拒绝覆盖。",
     ["fund_info_sync"], ""),
    ("stock_info", "资料",
     "东财全A名单;Baostock;巨潮资讯;北交所官网",
     "证券主表+公司档案宽表（34 列）：**交易所官方名单**日更短名/exchange —— 2026-09-24 v2.9 换源：沪主板A + 沪科创 + 深A + 北交所四路官方名单并集 → 东财代"
     "码名称表兜底补漏；官方名单**自带上市日期**（仅缺值才回填 MIN(日线)）；Baostock 周更上市/退市状态+退市日（ipoDate 仅补空）；巨潮日更档案 24 列（列级 UPDATE）。d"
     "ata_source=EM;BAOSTOCK;CNINFO。⚠️ v2.9 前主源是东财实时快照 stock_zh_a_spot_em —— 它是行情源不是名单源，实测漏收 89 只 A 股（含沪深3"
     "00 成分 001280 中国铀业），并在除权日把 XD 前缀写进 short_name，已弃用。⚠️ **北交所（exchange='BJ'）**：industry 由 bj_stock_sync "
     "专属维护 —— 巨潮不提供北交所档案，北交所官网名册（344 只）的「所属行业」是该字段唯一来源（2026-09-20 前 277 条全空，导致北证50 行业分布画不出来）；bj_stock_sync"
     " 同时负责 2025-10-09 代码切换（→920 段）的旧码迁移。沪深标的行业由巨潮档案提供。",
     ["stock_info_sync", "stock_status_sync", "stock_company_sync", "bj_stock_sync"],
     "三源构成见列注释；北交所名册与行业由 bj_stock_sync 单独维护"),
    ("stock_info_ex", "资料",
     "东财全A名单;北交所官网",
     "股票信息扩展：随 stock_info_sync 日更同步全市场名单（2026-09-24 v2.9 起源为交易所官方名单 ∪ 东财代码名表，**已含北交所**）；人工 is_gxlstock（高股息"
     "）标记保留。北交所段代码由 bj_stock_sync 按对照台账同步迁移；v2.9 前该段因「写方取自东财 spot、而东财不含北交所」而永远不刷新，该边界已随换源消失（实测 920 段 347 行"
     "已全量刷新）。",
     ["stock_info_sync", "bj_stock_sync"], ""),
    ("finance_concept_analysis", "AI",
     "豆包方舟(ARK)",
     "AI 概念分析：分析投资日历事件→关联概念（评分 1-10）；无 ARK_API_KEY 时降级写占位结果；手动触发（当前 enabled=0）。",
     ["ai_concept_analysis"], "当前为占位降级态"),
    ("dq_report", "质量",
     "自产(data_quality_check)",
     "数据质量体检结果快照：每日 22:45（主触发=链式延迟；兜底 23:00）读 dq_rules 逐条执行写入；"
     "大表限定最新时间切片秒级完成；同轮运行中保护。",
     ["data_quality_check"], ""),
    # ---- 无采集任务（历史导入/静态/系统） ----
    ("stock_market_daily_ex", "行情",
     "历史导入",
     "日K线**不复权原始价**（1,689 万行 / 5,779 只 / 1990-12-19~2025-09-19）："
     "2026-09-13 全表逐字段比对推翻「冗余副本」结论——公共键 1,689 万行中 85% 六字段不同，"
     "当时差异源于复权口径（daily=前复权 qfq 且有 58.7 万行负价退化区；ex=不复权原始价）。"
     "**2026-09-20 勘误**：daily 已完成口径改造、不再存前复权价，「口径差异」这一理由随之失效；"
     "但 ex 仍有 109 个 daily 缺失的键（深 B 200020/200429/200726、北交所 430556 老三板等），"
     "故维持冻结监护、不并入 daily（2026-09-20 复核结论：ex 表不动，只做冻结监护）。",
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
     "日度资金流向（748,019 行，停更于 2025-09-19）：capital_flow_sync 已实现"
     "（逐股滚动 + uk_stock_date 幂等）但 **2026-09-19 已明确废弃**（enabled=0 + cron=「手动」）。"
     "三条理由：① 需逐股打东财 push2his（400 只/轮），该子域对本机是**间歇性 RST 风控**"
     "（实测首次直连可通、连续请求即被拒，5 次跨 4 分钟重试全败；同域 datacenter-web 却稳定 200），"
     "批量调用必触发风控；② 「主力净流入=超大单+大单」仅 90~93.8% 成立、源口径未与本地表对齐；"
     "③ 原 cron `15 22 * * *` 与 financial_abstract_sync 完全撞车。"
     "本表与采集器代码**保留为冻结态**（不 drop，历史 748k 行仍可读），仅去掉排期。",
     ["capital_flow_sync"], "已明确废弃（2026-09-19）：冻结保留，不排期"),
    ("securities_margin", "资金",
     "上交所;深交所;北交所(akshare)",
     "沪深北三市两融合计：rzye=融资余额、rqye=融券余额、rzrqye=rzye+rqye（余额合计）、"
     "rzrqyecz=rzye-rqye（余额**净额**，本地派生）。margin_sync 每日 20:00 从本地 "
     "MAX(trade_date) 次日增量补齐；单位归一——上交所为元、深交所为亿元(×1e8)、北交所取明细求和(元)。"
     "⚠️ 口径已定论（2026-09-19 全表 3,980 行复核）：两条恒等式 rzrqyecz≡rzye−rqye、"
     "rzrqye≡rzye+rqye **违反行数均为 0**，列名与值完全一致。"
     "常有疑问「rzrqyecz/rzrqye 恒≈0.9778，像另一套口径」——这是**数学必然**："
     "该比值 = (1−r)/(1+r)，其中 r=rqye/rzye≈1.12%（融券腿仅占融资余额约 1.1%），"
     "实测比值与理论式吻合到 2e-6。**故 rzrqyecz 不是「余额」而是「净额」，"
     "与 rzrqye 不可直接当同一量纲比较**；也因融券腿极小，该列相对 rzye 信息量有限。",
     ["margin_sync"], "口径已定论：rzrqyecz=净额(融资−融券)"),
    ("stock_financial_abstract_ths", "财务",
     "同花顺(akshare stock_financial_abstract_ths)",
     "财务关键指标 8 列（34.4 万行 / 5,854 只）：financial_abstract_sync 每日 22:15 只补「MAX(报告期) < max(本地全局 MAX, 披露日历推算最近期"
     ")」的滞后股票；候选池排除退市股（其报告期恒滞后，会永久占满 max_stocks 名额）；缺失值清洗——akshare 对缺失返回布尔 False（非 NaN）→ 统一 NULL，'--' 同处理。"
     "uk_stock_report 幂等 upsert。",
     ["financial_abstract_sync"], ""),
    ("ths_stock_dividend", "财务",
     "同花顺(akshare stock_fhps_detail_ths)",
     "分红送配明细（11 业务列与源列一一对应）：ths_dividend_sync 每月 1/15 日 21:30 逐股增量补齐，仅插入源有而本地缺的 report_period，不重写既有行；被 ana"
     "lysis/dividend 分红率分析消费（前端「股息率排行」）。",
     ["ths_dividend_sync"], "含历史重复行约 9.5 万，粒度待专项确认"),
    ("stock_shares", "基本面",
     "巨潮资讯(akshare stock_share_change_cninfo)",
     "股本变动明细（事件型表，源单位万股→×10000 存股数）：stock_shares_sync 每日 21:45 逐股滚动刷新——事件型表 change_date 常年不变，故按 MAX(update"
     "_time) 判「久未刷新」（refresh_days=30）轮转；退市股源侧无记录（akshare 抛 KeyError 公告日期）归一为「无数据」，候选池亦排除（2026-09-22 起一并剔除 "
     "920 切换前旧码，见 stock_code_mapping.status='switched'）；upsert 覆盖（巨潮会回溯修订）。字段口径实证：list_a_shares←人民币普通股（A 股"
     "流通股，非「已流通股份」）、limit_shares←流通受限股份。",
     ["stock_shares_sync"], ""),
    ("stock_industry_sw", "行业",
     "申万宏源(akshare index_component_sw)",
     "申万一二级成分快照（10,428 行 / 5,214 只 / 162 行业 = 31 一级 + 131 二级）：sw_industry_sync 每月 1 日 21:00 整体重建（DELETE+IN"
     "SERT 小事务，调样即全量刷新）；目录来自 sw_index_first/second_info 遍历，覆盖率 <85% 判源异常回滚。uk_stock_level（stock_code+indus"
     "try_type）幂等。",
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
     "采集任务配置（35 任务：enabled/cron/params，列名就是 `cron`）；作业监控栏目可热改，调度器实时同步。⚠️ cron 的 day_of_week 为 APScheduler 语义（0=周一、6=周日，与 Unix crontab 相反），界面与调度日志会同时显示 cron_human 中文语义。⚠️ 主触发=链式：19:20 日线 success 后自动接力 market_style → market_current → data_quality_check（后者受 CHAIN_NOT_BEFORE 约束，延迟到 22:45 执行）。⚠️ 链式跑成后，表内登记的兜底时刻**会照常触发但被跳过**（不写 task_runs），故「表内时刻查不到运行记录」不等于班次没触发。",
     [], ""),
    ("task_runs", "系统",
     "自产(TaskRecorder)",
     "作业运行记录：每次执行 running→success/failed/**blocked**（blocked=数据未就绪，非失败、不计入失败率）+ 写入条数/错误信息；保留最近 ~100 条。",
     [], ""),
    ("dq_rules", "质量",
     "手动维护;seed_dq_rules",
     "数据质量规则配置（132 条 / 20 类检查器，daily 124 + weekly 8；2026-09-19 新增 column_watermark "
     "检查器专治「有列无值」；2026-09-24 新增 ref_missing 引用完整性 —— 名册曾漏收 89 只 A 股"
     "潜伏数月无人发现，该检查器是唯一能发现「跑成功但漏收」的形态）；数据质量栏目可维护。",
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
     "表级元数据：category/source_desc/flow_desc/writers/writer_cols；数据中心右侧「数据流」折叠卡与本表双向引用；当前本表登记 44 张表（库内共 45 张：29 业务 + 新增 7 + 系统/质量；2026-09-19 补登漏网的 market_xcheck_daily）。",
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
     "PRIMARY KEY(trade_date) 幂等 upsert；每工作日 19:35。回答蓝图A「钱贵不贵」。"
     "消费方：分析研究「钱贵不贵」模块（期限结构 ON→1W→1M→3M + 3M 近一年分位 + LPR 政策姿态）。"
     "⚠️ 该模块的分位窗口必须用**近一年**而非全历史 —— 全历史含 2008/2013 两次钱荒，"
     "会把当下永远压在极低分位（实测 3M 全历史 <10%、近一年 34.6%），"
     "那不是「钱便宜」而是「没经历过钱荒」，属窗口选错导致的伪信号。",
     ["interbank_rate_sync"], ""),
    ("overseas_index_daily", "海外",
     "新浪财经(akshare stock_hk_index_daily_sina / index_us_stock_sina / 直连 gi 接口)",
     "海外与港股指数日线（**10 个指数**，跨亚/欧/美三个时区）：HSI 恒生"
     "（`stock_hk_index_daily_sina`）+ DJI/SPX/IXIC 道指·标普500·纳指（`index_us_stock_sina`）"
     "+ **2026-09-25 新增** DAX/CAC/UKX/SX5E/N225/KOSPI —— 这 6 个**直连**新浪 "
     "`gi.finance.sina.com.cn/hq/daily`，不走 akshare 函数（实测 `index_global_hist_sina` "
     "符号映射表 key 对不上、`index_global_hist_em` 因东财 kline 域名被拦全数失败）。"
     "uk_code_date 幂等增量；每自然日 19:50（美股为 T-1 收盘，源侧天然滞后一天，别当缺数）。"
     "⚠️ 新增 6 个指数**只有 1,000 行（约 4 年，2022-08 起）**——`num=10000` 实测只返 1000 行，"
     "够算近一年分位与 20/60 日变化，**不够做长周期历史类比**。"
     "⚠️ 新浪 gi 在亚洲盘中会返回**当日未完成的 bar**（2026-09-25 实测 10:04 时 N225 已有当日行、"
     "而其余 9 条腿都停在 T-1）⇒「最新日行数」是**时点耦合量**、不可作判据：既有规则 "
     "`overseas_rows_latest`（最新日行数 ≥3）已因此退休，改为 spx/dax/n225/kospi 四条逐腿 "
     "`date_floor_where`。"
     "消费方：分析研究「跨市场对照」模块 —— 它仍只用 SPX/IXIC/DJI/HSI 四个"
     "（`INDEXES` 是显式白名单，扩表不影响它）。⚠️ 与 A股 交易日不同步（时差 + 各自休市），"
     "该模块按**日期并集 + 前值填充**对齐；按下标对齐会把相差近一个月的日期画成同一时刻，"
     "产生的错位恰好会被读成「美股领先 A股」。隔夜传导只用『标普500 × 中证全指』这一对 —— "
     "恒生与 A股 交易时段部分重叠，混算会把两个机制平均掉。",
     ["overseas_index_sync"], ""),
    ("currency_boc_daily", "汇率",
     "新浪财经-中行人民币牌价(akshare currency_boc_sina)",
     "人民币外汇牌价与中间价（**17 币种 = 源侧合法全集**；2026-09-25 由 4 个扩到 17）。"
     "扩集两个理由：① 覆盖 **ICE DXY 的 6 个成分货币**（欧元/日元/英镑/加元/瑞典克朗/瑞士法郎），"
     "使「美元指数自算」多一条独立口径可交叉复核；② 外部约束维度要看**一篮子货币的相对强弱**，"
     "只盯美元会漏掉日元/英镑的独立行情。"
     "⚠️ 口径纠正：设计文档初稿写的「扩到 26 币种」是把 `currency_boc_safe`（SAFE 宽表）"
     "的币种数误套到了本表上 —— `currency_boc_sina`（中行牌价）**只有 17 个**。"
     "mid_price=央行中间价（政策意图）、spot_buy/sell=中行汇买/汇卖（市场实现），"
     "两者背离本身即信息（中间价稳而即期弱 = 贬值压力靠逆周期因子硬压）。"
     "⚠️ 源按**每 100 外币**报价，采集器已 ÷100 归一为**元/1 外币**（下游无需再除）。"
     "⚠️ 源 symbol 必须逐字对齐（'港币' 而非 '港元'、'韩国元' 而非 '韩元'、'澳门元' 而非 '澳门币'，"
     "拼错在映射表里抛 KeyError 而非报「不支持」）。"
     "uk_currency_date 幂等；每自然日 20:05。"
     "⚠️ mid_price 有空值，且**最新一日常为空** —— 当日中间价发布晚于采集时刻"
     "（实测 2026-09-19 与 2026-09-25 的 mid_price 为 NULL 而 ref_price 有值）。消费端一律 "
     "`mid_price IS NOT NULL`，**不要用 ref_price 兜底**：两者口径不同"
     "（央行中间价 vs 中行折算价），混用会让序列出现台阶。"
     "双消费方：①「资金温度」模块用美元中间价看外部资金环境；"
     "②「货币流动性」模块只把它当作 **DXY 自算的成分来源**（真正参与 DXY 计算的是 "
     "`currency_boc_safe`，本表是**对照口径**）。",
     ["currency_boc_sync"], ""),
    ("fund_new_issue", "基金",
     "东财数据中心(akshare fund_new_found_em)",
     "新基金发行（6,848 只）：含认购期/募集份额/成立日/经理/申购状态，"
     "回答蓝图E「新增资金供给」。全量 upsert（PRIMARY KEY fund_code，整表仅数千行，不做增量）；"
     "每自然日 20:20。⚠️ establish_date 为 NULL = 仍在发行未成立，属正常态而非缺数；"
     "实测 6,848 只中 0 只在「未成立」态，即该情况当前不存在。"
     "消费方：分析研究「资金温度」模块（增量资金线索，按成立日 + 募集份额统计）。"
     "⚠️ 最新月通常只覆盖到月中（实测 2026-09 只到 09-15），消费端必须**剔除残缺月**"
     "（判定：最新月最后一条记录距该月月末 > 5 天则整月丢弃），否则近 3 月合计断崖式低估。",
     ["fund_new_issue_sync"], ""),
    ("stock_repurchase", "回购",
     "东财数据中心(akshare stock_repurchase_em)",
     "股票回购明细（5,513 条 / 12 页）：预案价上下限、计划金额与占比、已完成金额/股数/均价、进度。"
     "回答蓝图D「产业资本态度」。全量 upsert（uk_code_start(stock_code, start_date)，"
     "源会回溯修订进度，故必须覆盖写而非 INSERT IGNORE）；每自然日 20:35。"
     "消费方：分析研究「资金温度」模块（产业资本线索）。"
     "⚠️ 做时间序列统计**必须用 start_date（回购起始时间，不可变）而非 announce_date** ——"
     "uk_code_start 决定同一计划只有一行，announce_date 会被后续公告**覆盖成最新日期**："
     "用它统计时历史月份的记录被不断抽走、近期被顶高。实测滚动 90 天口径下，"
     "announce_date 的当前分位恒为 **100.0%**（零区分度，是个假信号），"
     "start_date 为 **84.2%**（区间 55~872，且能正确识别 2024-02 回购潮与 2025-09 低谷）。",
     ["stock_repurchase_sync"], ""),
    # 2026-09-19 补：7 张新表中唯一漏登 table_meta 的一张（数据中心看不到说明）
    ("market_xcheck_daily", "质量",
     "本地派生（market_style_daily + dc_index_market，无外部源）",
     "市场风向「交叉印证」日频结果（250 行 / 2025-09-09~）：xcheck_sync 每日 22:30 写入，"
     "把市场风向的多项判定两两交叉比对，产出 参与项数 items_n / 一致 agree_n / "
     "**背离 diverge_n（核心产出）** / 中性 neutral_n（两维都在死区内）/ 缺数 nodata_n，"
     "diverge_keys 列出具体背离项。⚠️ 必须**晚于 market_style_sync 与 index_market_sync** 跑，"
     "否则读到的是半量风向数据。PRIMARY KEY(trade_date) 幂等 upsert。",
     ["xcheck_sync"], ""),

    # ---- 2026-09-25 货币流动性批次 2（3 张新表 + 1 张自算派生表）----
    # 出处：《货币流动性观测体系设计_2026-09-25.md》§6 批次 2。
    # 一句话背景：原「钱贵不贵」领域只有**银行间利率**一条腿（价格维度），
    # 补上「数量维度（货币供应/央行资产表）+ 央行行为（多国利率）+ 外部约束（美元指数）」。
    ("cn_liquidity_monthly", "宏观",
     "央行-金融统计数据(akshare macro_china_money_supply / _new_financial_credit / "
     "_reserve_requirement_ratio / _shrzgm)",
     "中国货币数量维度月表（224 期，2015-01 起）：M0/M1/M2 的余额 + 同比 + 环比（`m*_mom` "
     "为本地派生，由余额按日期间隔归一算得，故列宽 DECIMAL(8,4)）、`m1_m2_gap` = M1 同比 − M2 同比"
     "（**资金活化度**：剪刀差收窄 = 钱从定期转向活期 = 实体意愿回升，比单看 M2 更有信息量）、"
     "新增人民币信贷（月增/累计/同比）、社融 8 列、准备金率（大行/中小行 + 生效日，按「下一生效日顺延」"
     "填充到月份，不做前向填充）。stat_month 取四源并集 + 主键幂等 upsert。"
     "⚠️ **上游时效各不相同（逐列水位由采集器 run_steps 分别暴露）**：M1/M2/M0 与信贷至 2026-08；"
     "准备金率最后生效 2025-05-15；**社融源已停更于 2026-04 且无替代源** —— `macro_china_shrzgm`"
     "（商务数据中心，无参 POST）之后不再更新，东财 datacenter 无对应报告名，"
     "`macro_china_bank_financing` 实为「银行理财产品发行数量」（名字骗人）。"
     "⇒ 社融只是**辅助参考列**，主口径以 M1/M2 + 信贷为准；对应的 DQ 规则 `cnliq_shrzgm_wm` "
     "已 enabled=0 留作钩子（找到替代源再开）。"
     "⚠️ 建表三纪律：显式 COLLATE utf8mb4_0900_ai_ci（库默认是 unicode_ci，不写死 JOIN 会报 1267）、"
     "CREATE TABLE IF NOT EXISTS、口径写进列注释。"
     "消费方：分析研究「货币流动性」模块的「数量维度」（暂未接入前端，属批次 3 视图重构范围）。",
     ["cn_liquidity_sync"], ""),
    ("cn_cb_balance_monthly", "宏观",
     "央行-货币当局资产负债表(akshare macro_china_central_bank_balance)",
     "中国央行资产负债表月表（356 期，1993-03 起，28 科目 + stat_month）："
     "**纯源值镜像，不存任何派生列**（派生逻辑留给消费端，避免口径分叉在两层各写一遍）。"
     "最具信息量的科目是 `claims_other_dep_banks`（对其他存款性公司债权）—— 它就是央行通过 "
     "MLF/逆回购/PSL 投给银行的资金：**扩张 = 放水、收缩 = 收水**。中国没有官方「QE 规模」公告，"
     "只能从这一列倒推；用它可以区分「主动投放」与「外汇占款被动投放」两个时代"
     "（2014 年前靠外汇占款、之后转为主动投放，这是中国货币投放机制的分水岭）。"
     "⚠️ `_period()` 解析源里的 `'2026.8'` 格式（年.月，**月不补零**，`'2026.10'` 才会两位数）。"
     "⚠️ `COL_MAP` 必须逐字对齐 27 个源列名（含 `其中:中央政府` 的**半角冒号**）；"
     "源列名缺失即 raise —— 防「拼错后静默写 NULL」这个本项目已踩过的坑。"
     "DQ 侧配了资产负债表恒等式（总资产 = 总负债，容差 1 亿）作物理约束。"
     "消费方：分析研究「货币流动性」模块的「央行行为/数量维度」（批次 3 视图重构范围）。",
     ["cn_cb_balance_sync"], ""),
    ("cb_policy_rate", "宏观",
     "各国央行决议(akshare macro_bank_usa/euro/japan/english_interest_rate)",
     "多国央行政策利率决议（1,395 条，美/欧/日/英四国）：一国一表结构，"
     "`uk_country_date(country_code, event_date)` 幂等。**只写入「今值」非空的有效决议行**"
     "（源表里更晚的日期行「今值」为空 —— 那是「尚未发布」，不是「利率为 0」）。"
     "`prev_rate` / `change_bp` **本地自算**（按同国 event_date 的相邻有效决议），"
     "**不取源里的「前值」列**（源该列口径不稳）。"
     "⚠️ **上游整体停更（2026-09-25 实测）**：11 个 `macro_bank_*` 接口的最后一条有效「今值」"
     "统一停在 2025-07~08（距今 14 个月），换接口无用。"
     "⇒ 本表只能读**历史方向**（各国加息/降息周期的相对位置），**不能当当前政策利率用**；"
     "前端「货币流动性」的「外部约束」分区已强制标注「源已停更 N 个月」。"
     "对应的 DQ 规则 `cbpr_fresh` 已 enabled=0 留作钩子。"
     "⚠️ 缺口登记：韩国央行利率 akshare **无接口**（实测 AttributeError），"
     "韩国层只能做「结果观测」（KOSPI + 韩元汇率），这是设计文档 §2.4 A4 明确接受的缺口。"
     "消费方：分析研究「货币流动性」模块的「全球央行方向」表。",
     ["cb_policy_rate_sync"], ""),
    ("global_usd_index_daily", "汇率",
     "自算(人民币中间价 currency_boc_safe 6 成分货币) + 新浪 DINIW 实时快照",
     "美元指数**自算**日线（2,380 行，2016-12-12 起）：按 ICE 标准公式给 6 个成分货币的"
     "**人民币中间价交叉汇率**加权 —— `50.14348112 × EURUSD^-0.576 × USDJPY^0.136 × "
     "GBPUSD^-0.119 × USDCAD^0.091 × USDSEK^0.042 × USDCHF^0.036`；"
     "另存新浪 `hq.sinajs.cn/list=DINIW` 的实时快照列做**标定基准**（逐日累积）。"
     "⚠️ **口径 = 人民币中间价交叉汇率，不是 ICE 官方 DXY**（UI 已标注「自算」）："
     "用于看趋势与相对位置，**不作绝对值引用**。实测与快照偏差 **+0.085%**（2026-09-24 单点标定）。"
     "⚠️ 官方历史源全部实测不可用：东财 `push2his` 域名不通（kline 路径被拦）、"
     "新浪 hq/daily 不支持 UDI/DINIW、`futures_foreign_hist('DX')` 只返 13 行（2019 年）"
     "⇒ 自算是唯一可行路径。"
     "⚠️ **标价法混用是本表最大的坑**（2026-09-25 实测事故）：`currency_boc_safe` 宽表里"
     "**直接标价 = 人民币/100 外币**（美元 674.89 → 6.7489、日元 4.259 → 0.04259）而"
     "**间接标价 = 外币/100 人民币**（瑞典克朗 147.31 → 0.67884），**只有这两种、不是「按 1 单位」**；"
     "采集器初版把 SEK 当直标 ⇒ USDSEK 算成 4.58（真值 9.94）⇒ DXY 偏低 3.3%，"
     "还伪装成「中间价与市场价的固有偏离」。现由 `_CROSS_RANGE` 逐币量级断言 + "
     "`dxy_vs_snapshot` DQ 规则（整值偏差 ≤1.5 点）双重守护。"
     "⚠️ 快照落位用 `_stitch_snapshot()`：按**快照自带报价日**对齐（exact → latest 回落到表内最新行），"
     "不能只挂 `d == today` —— 中间价宽表末行是 T 或 T−1，只靠 INSERT 会让快照永远写不进去。"
     "消费方：分析研究「货币流动性」模块的「外部约束」分区（G1 全球美元总闸门）。",
     ["usd_index_sync"], ""),
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
        "bj_stock_sync": {
            "source": "北交所《新旧代码对照表》",
            "cols": ["stock_code"],
            "note": "北交所 920 代码切换：按 stock_code_mapping 台账把旧码 UPDATE 为新码，"
                    "**只改代码**（is_gxlstock / list_date / data_source 均不动）；"
                    "**不补录**名册新增标的一一该表以人工标注为主，批量灌机器行属口径取舍（待决策）",
            "derived": [],
            "col_notes": {
                "stock_code": "北交所段现为 920 段；2 只退市标的（835305/839680）无新码，保持旧码",
            },
        },
    },
    "stock_market_daily": {
        "stock_daily_incr": {
            "source": "新浪;腾讯;东财;Tushare(四级降级)",
            "cols": ["stock_code", "trade_date", "open", "high", "low", "close", "pre_close",
                     "change_amount", "change_pct", "volume", "amount", "turnover_ratio",
                     "adj_factor"],
            "derived": ["pre_close", "change_amount", "change_pct", "adj_factor"],
            "note": "**实际价（不复权）+ 后复权因子列**（2026-09-20 口径改造）。三种口径全部由公式派生且都不过期："
                    "实际价=close；后复权价=close×adj_factor；前复权价=close×adj_factor÷latest(adj_factor)。"
                    "增量为 INSERT IGNORE 去重、只补不改；volume 统一为「股」、turnover_ratio 统一为百分数 %。"
                    "存量全量重写见 stock_daily_rebuild（一次性工具）",
            "col_notes": {
                "pre_close": "昨收：按 hfq 比值反推，即 close(t-1)×adj_factor(t-1)÷adj_factor(t)。"
                             "**除权日它等于交易所公布的除权参考价**（不是上一日原始收盘价），"
                             "故 (close-pre_close)/pre_close 恒等于含分红再投的真实收益率，与 change_pct 自洽",
                "change_amount": "涨跌额 = close - pre_close（round 3）",
                "change_pct": "涨跌幅：由 hfq 比值算真实收益再 ×100（round 4），除权日不会被记成假跌",
                "adj_factor": "后复权累计因子 = hfq_close ÷ close（round 8）。基准取该票首个交易日，"
                              "**历史值永不改变**（新增数据与分红都不会回改历史），这是选后复权因子而非前复权的原因。"
                              "按「相对变化 >0.3%」阶梯化以吃掉源 2 位小数的舍入噪声；"
                              "⚠️ 非新浪源命中时不写因子（置 NULL）以避免两个基准拼接成断阶 —— "
                              "例外是退市股整只走腾讯重建时（单源全史重写，基准内部自洽，必须写因子）",
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
    "stock_code_mapping": {
        "bj_stock_sync": {
            "source": "北交所官网（akshare stock_info_bj_name_code）",
            "cols": ["old_code", "new_code", "short_name", "list_date", "change_date",
                     "status", "match_rule", "evidence", "source"],
            "derived": ["new_code", "change_date", "status", "match_rule", "evidence"],
            "note": "new_code 来自名册匹配；change_date 取切换生效日/摘牌日常量；"
                    "match_rule 与 evidence 为映射依据留档（人工核证与退市项必填）",
            "col_notes": {
                "old_code": "变更前代码（北交所新三板时期 43/83/87 等段）；uk_old_code 唯一键",
                "new_code": "920 段新码；**已退市标的为 NULL**（status='retired'）",
                "list_date": "北交所上市日期（名册口径）——**≠ stock_info.list_date**",
                "change_date": "switched 取切换生效日 2025-10-09；retired 取摘牌日",
                "status": "switched=已切换 920 段 / retired=已退市无新码",
                "match_rule": "简称归一 / 人工核证 / 官方通报",
                "evidence": "映射证据，人工核证与退市项必填（否则本表就是猜测）",
            },
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
            "source": "akshare 新浪外盘指数(HSI/美股) + 直连新浪 gi(日韩欧)",
            "cols": ["index_code", "index_name", "trade_date", "open", "high", "low", "close",
                     "volume", "amount"],
            "note": "**两套取数路径**：HSI 走 `stock_hk_index_daily_sina`；DJI/SPX/IXIC 走 "
                    "`index_us_stock_sina`；DAX/CAC/UKX/SX5E/N225/KOSPI（2026-09-25 新增）"
                    "**直连** `gi.finance.sina.com.cn/hq/daily`（akshare 的 "
                    "`index_global_hist_sina` 符号表对不上、`index_global_hist_em` 因东财 "
                    "kline 域名被拦全数失败）。uk_code_date 幂等增量",
            "col_notes": {
                "trade_date": "当地交易日；美股为 T-1 收盘，源侧天然滞后一天（非缺数）。"
                              "⚠️ 新浪 gi 在亚洲盘中会返回当日**未完成的 bar** ⇒「最新日行数」"
                              "是时点耦合量，别拿它做判据（参见 table_meta.flow_desc）",
                "volume": "新浪 gi 返回的 v<=0 一律转 NULL（该接口对部分指数、部分日期不提供成交量；"
                          "实测空值率 CAC 216/1000、SX5E 404/1000、N225 91/1000、KOSPI 6/1000）",
                "amount": "**新增的 6 个指数（DAX/CAC/UKX/SX5E/N225/KOSPI）整列为 NULL** —— "
                          "新浪 gi 接口不返回成交额（实测 1000/1000 空）；"
                          "做「量能」类指标时只能用 volume、且必须先处理空值",
            },
        },
    },
    "currency_boc_daily": {
        "currency_boc_sync": {
            "source": "akshare currency_boc_sina(新浪-中行牌价)",
            "cols": ["currency", "currency_name", "trade_date", "mid_price", "spot_buy",
                     "spot_sell", "ref_price"],
            "derived": ["mid_price", "spot_buy", "spot_sell", "ref_price"],
            "note": "uk_currency_date(currency, trade_date) 幂等 upsert；"
                    "**17 币种 = 源侧合法全集**（2026-09-25 由 4 个扩到 17，含 ICE DXY 的 6 个成分货币）",
            "col_notes": {
                "mid_price": "央行中间价，**元/1 外币**——源按每 100 外币报价，已 ÷100 归一。"
                             "⚠️ 有空值且最新一日常为空（当日中间价发布晚于采集时刻）",
                "spot_buy": "中行汇买价（元/1 外币，已 ÷100）",
                "spot_sell": "中行钞卖价/汇卖价（元/1 外币，已 ÷100）",
                "ref_price": "中行折算价（元/1 外币，已 ÷100）。⚠️ **不要用它兜底 mid_price** —— "
                             "两者口径不同，混用会让序列出现台阶",
                "currency_name": "⚠️ 源 symbol 必须逐字对齐 akshare 合法取值：'港币' 而非 '港元'、"
                                 "'韩国元' 而非 '韩元'、'澳门元' 而非 '澳门币'、'澳大利亚元' 而非 '澳元'，"
                                 "拼错在 _currency_boc_sina_map 抛 KeyError（不报「不支持」）",
            },
        },
    },
    "cn_liquidity_monthly": {
        "cn_liquidity_sync": {
            "source": "akshare macro_china_money_supply / _new_financial_credit / "
                      "_reserve_requirement_ratio / _shrzgm",
            "cols": ["stat_month", "m0", "m0_yoy", "m0_mom", "m1", "m1_yoy", "m1_mom",
                     "m2", "m2_yoy", "m2_mom", "m1_m2_gap", "credit_month", "credit_cum",
                     "credit_yoy", "shrzgm", "shrzgm_rmb_loan", "shrzgm_fx_loan",
                     "shrzgm_entrust", "shrzgm_trust", "shrzgm_undiscounted",
                     "shrzgm_ent_bond", "shrzgm_equity", "rrr_large", "rrr_small",
                     "rrr_effective_date"],
            "derived": ["m0_mom", "m1_mom", "m2_mom", "m1_m2_gap", "rrr_large", "rrr_small",
                        "rrr_effective_date"],
            "note": "四接口合一：`*_mom` = 余额环比的**按日期间隔归一**（不是简单差分）；"
                    "`m1_m2_gap` = M1 同比 − M2 同比；准备金率按「下一生效日」顺延填充到月份"
                    "（该月早于首个事件则留 NULL，刻意不做前向填充 —— 那会造出假历史）",
            "col_notes": {
                "stat_month": "统计月份归一为月初 1 日；取四源月份**并集**，主键幂等 upsert",
                "m1_m2_gap": "**资金活化度**：剪刀差收窄 = 钱从定期转向活期 = 实体意愿回升。"
                             "比单看 M2 更有信息量（本表最有判断力的派生列）",
                "shrzgm": "⚠️ **源已停更于 2026-04 且无替代源**（详见 table_meta.flow_desc）—— "
                          "列仍在、表还在长，但这一列不再有新值。DQ 规则 `cnliq_shrzgm_wm` "
                          "已 enabled=0 留作钩子",
                "rrr_effective_date": "准备金率的**生效日**（不是公告日）。该月若无事件则继承"
                                      "上一次生效值；若该月早于首个事件则整组留 NULL",
            },
        },
    },
    "cn_cb_balance_monthly": {
        "cn_cb_balance_sync": {
            "source": "akshare macro_china_central_bank_balance",
            "cols": ["stat_month", "foreign_assets", "fx_reserve", "monetary_gold",
                     "other_foreign_assets", "claims_gov", "claims_central_gov",
                     "claims_other_dep_banks", "claims_other_fin_cos", "claims_non_monetary",
                     "claims_non_fin_cos", "other_assets", "total_assets", "reserve_money",
                     "currency_issue", "fin_cos_deposit", "other_dep_banks_dep",
                     "other_fin_cos_dep", "fin_liab", "reserve_deposit", "non_fin_cos_dep",
                     "demand_deposit", "bonds", "foreign_liab", "gov_deposit", "own_capital",
                     "other_liab", "total_liab"],
            "note": "**纯源值镜像，不存派生列**（派生留给消费端，避免口径在两层各写一遍）。"
                    "`_period()` 解析源的 `'2026.8'` 格式（年.月，月不补零）；"
                    "`COL_MAP` 逐字对齐 27 个源列名（含 `其中:中央政府` 的**半角冒号**），"
                    "源列名缺失即 raise（防拼错后静默写 NULL）",
            "col_notes": {
                "stat_month": "统计月份归一为月初 1 日；主键幂等 upsert",
                "claims_other_dep_banks": "**本表最有信息量的科目**（对其他存款性公司债权）："
                                          "MLF/逆回购/PSL 的投放总量。扩张 = 放水、收缩 = 收水 —— "
                                          "中国没有官方 QE 公告，只能从这一列倒推",
                "fx_reserve": "外汇占款（被动投放时代的主力）。与 claims_other_dep_banks 对照，"
                              "可看出「被动投放 → 主动投放」的结构切换（分水岭在 2014 年）",
                "total_liab": "与 total_assets 构成恒等式，DQ 规则 `cncb_identity` 守护（容差 1 亿）",
            },
        },
    },
    "cb_policy_rate": {
        "cb_policy_rate_sync": {
            "source": "akshare macro_bank_usa/euro/japan/english_interest_rate",
            "cols": ["country_code", "country_name", "central_bank", "event_date", "rate",
                     "prev_rate", "change_bp"],
            "derived": ["prev_rate", "change_bp"],
            "note": "uk_country_date(country_code, event_date) 幂等 upsert；"
                    "**只写入「今值」非空的有效决议行**（源表更晚的日期行今值为空 = 尚未发布，"
                    "不是利率为 0）；`prev_rate`/`change_bp` 按同国相邻有效决议**本地自算**，"
                    "不取源里的「前值」列",
            "col_notes": {
                "event_date": "决议**公布日**（不是生效日）",
                "change_bp": "本行利率 − 上一条有效决议的利率（bp）。自算而非取源，"
                             "因为源该列口径不稳",
                "country_code": "白名单 US/EU/JP/UK（韩国央行 akshare 无接口，属已接受的缺口）；"
                                "DQ 规则 `cbpr_country_whitelist` 守护",
            },
        },
    },
    "global_usd_index_daily": {
        "usd_index_sync": {
            "source": "自算(akshare currency_boc_safe 中间价) + 新浪 DINIW 实时快照",
            "cols": ["trade_date", "dxy_calc", "dxy_snapshot", "eur_usd", "usd_jpy",
                     "gbp_usd", "usd_cad", "usd_sek", "usd_chf"],
            "derived": ["dxy_calc", "eur_usd", "usd_jpy", "gbp_usd", "usd_cad", "usd_sek",
                        "usd_chf"],
            "note": "ICE 标准公式：`50.14348112 × EURUSD^-0.576 × USDJPY^0.136 × "
                    "GBPUSD^-0.119 × USDCAD^0.091 × USDSEK^0.042 × USDCHF^0.036`；"
                    "`_SAFE_COL` 逐币声明**标价法**（direct 6 个 / inverse 1 个 = 瑞典克朗），"
                    "`_CROSS_RANGE` 逐币量级断言；快照落位用 `_stitch_snapshot()` "
                    "按快照自带报价日对齐（exact → latest 回落）",
            "col_notes": {
                "dxy_calc": "⚠️ **口径 = 人民币中间价交叉汇率，不是 ICE 官方 DXY** —— "
                            "看趋势与相对位置，不作绝对值引用。实测与快照偏差 +0.085%"
                            "（2026-09-24 单点标定）",
                "dxy_snapshot": "新浪 `hq.sinajs.cn/list=DINIW` 的实时值（GB18030 解码，"
                                "取 parts[1] 与 parts[10] 报价日）。**自 2026-09-24 起逐日累积**，"
                                "是 `dxy_vs_snapshot` 规则的标定基准",
                "usd_sek": "⚠️ **唯一走间接标价的成分货币**（源值 = 外币/100 人民币，需 100/v）；"
                           "其余 5 个都走直接标价（源值 = 人民币/100 外币，需 v/100）。"
                           "把 SEK 当直标算会得到 4.58（真值 9.94）并把 DXY 拉低 3.3% —— "
                           "这是 2026-09-25 实测事故的根因",
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
