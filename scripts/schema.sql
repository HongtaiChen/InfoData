-- ============================================================================
-- InvestBuddy / InfoData — 数据库结构定义（schema only，不含任何数据）
-- ============================================================================
-- 由 scripts/dump_schema.py 从现网 adata 库导出，导出时间：2026-10-09 22:54:15
-- 源库：adata@127.0.0.1:3306
-- 表数量：56 张（已排除 4 张 *_bak_* / *_backup_* 备份表）
--
-- 用途：全新环境初始化数据库结构。
--
-- 使用方式（二选一）：
--   ① 命令行：  mysql -u root -p < scripts/schema.sql
--   ② MySQL 客户端里： SOURCE /绝对路径/scripts/schema.sql;
--
-- 特性与注意事项：
--   * **幂等**：全部 CREATE TABLE IF NOT EXISTS，重复执行不报错、不覆盖已有表。
--   * **不含数据**：只建结构。业务数据需由采集器回补，见 scripts/data-bootstrap.md。
--   * **不删任何东西**：本文件不含 DROP，可安全执行。
--   * **每张表都显式声明了 CHARACTER SET / COLLATE**，请勿删除这些声明 ——
--     本库默认排序规则与多数表不同，省略会导致 JOIN 报
--     1267 Illegal mix of collations（详见 dump_schema.py 文件头）。
--   * 无外键、无视图、无触发器、无存储过程（已实测），故**导入顺序无关**。
--
-- 建表之后的步骤（**顺序不能颠倒**，否则启动会失败或页面空白）：
--   1) 导入本文件                    → 建出全部空表
--   2) 跑 seed 脚本                  → 灌入任务配置 / DQ 规则 / 表元数据
--   3) 跑采集器回补业务数据           → 见 scripts/data-bootstrap.md
--   4) 启动后端                      → uvicorn app.main:app
--   详细说明见仓库根目录 README.md 的「数据库初始化」章节。
-- ============================================================================

CREATE DATABASE IF NOT EXISTS `adata`
    DEFAULT CHARACTER SET utf8mb4
    DEFAULT COLLATE utf8mb4_0900_ai_ci;

USE `adata`;

-- 让本次导入不受客户端字符集影响（避免中文注释乱码）
SET NAMES utf8mb4;

-- ---------- [1/56] bond_profit_daily ----------
CREATE TABLE IF NOT EXISTS `bond_profit_daily` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `trade_date` date NOT NULL COMMENT '交易日期',
  `cn_bond_2y` decimal(8,2) DEFAULT NULL COMMENT '中国国债收益率2年',
  `cn_bond_5y` decimal(8,2) DEFAULT NULL COMMENT '中国国债收益率5年',
  `cn_bond_10y` decimal(8,2) DEFAULT NULL COMMENT '中国国债收益率10年',
  `cn_bond_30y` decimal(8,2) DEFAULT NULL COMMENT '中国国债收益率30年',
  `cn_bond_10y_2y_spread` decimal(8,2) DEFAULT NULL COMMENT '中国国债收益率10年-2年',
  `us_bond_2y` decimal(8,2) DEFAULT NULL COMMENT '美国国债收益率2年',
  `us_bond_5y` decimal(8,2) DEFAULT NULL COMMENT '美国国债收益率5年',
  `us_bond_10y` decimal(8,2) DEFAULT NULL COMMENT '美国国债收益率10年',
  `us_bond_30y` decimal(8,2) DEFAULT NULL COMMENT '美国国债收益率30年',
  `us_bond_10y_2y_spread` decimal(8,2) DEFAULT NULL COMMENT '美国国债收益率10年-2年',
  `update_time` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  `data_source` varchar(100) DEFAULT NULL COMMENT '数据来源',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_trade_date` (`trade_date`),
  KEY `idx_trade_date` (`trade_date`),
  KEY `idx_update_time` (`update_time`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='国债收益率数据表';

-- ---------- [2/56] cb_policy_rate ----------
CREATE TABLE IF NOT EXISTS `cb_policy_rate` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `country_code` varchar(8) NOT NULL COMMENT '国家/地区代码（US/EU/JP/UK）',
  `country_name` varchar(20) DEFAULT NULL COMMENT '国家/地区中文名',
  `central_bank` varchar(30) DEFAULT NULL COMMENT '央行名（美联储/欧洲央行/日本央行/英国央行）',
  `event_date` date NOT NULL COMMENT '决议日（源侧发布日期）',
  `rate` decimal(8,4) DEFAULT NULL COMMENT '决议后政策利率（%）',
  `prev_rate` decimal(8,4) DEFAULT NULL COMMENT '决议前政策利率（%）',
  `change_bp` decimal(8,2) DEFAULT NULL COMMENT '本次变动（bp，负数=降息）',
  `update_time` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `data_source` varchar(100) DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_country_date` (`country_code`,`event_date`),
  KEY `idx_event_date` (`event_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='多国央行政策利率决议（美/欧/日/英，事件表：只在决议日有行，非日频顺延）。⚠️【源时效风险·实测 2026-09-25】akshare 的 macro_bank_*_interest_rate **整族**接口最后非空「今值」停在 2025-07~08（美 2025-07-31=4.5 / 欧 2025-07-24=2.15 / 日 2025-07-31=0.5 / 英 2025-08-07=4.0），表内最后一行日期在 2025-09~10 但今值为空。这是上游（经济日历源）整体停更，**换接口无用**（全族 11 个 macro_bank_* 同病）。⇒ 本表可用作「历史与方向共振」，**不可当作当前利率**；下游必须用 MAX(event_date) 标出「源更新至」而不是默认它就是最新。若上游恢复更新，本表自动跟上。⚠️ 只存 rate 非空的**有效决议**行（源侧未公布的占位行不入库）。';

-- ---------- [3/56] cn_cb_balance_monthly ----------
CREATE TABLE IF NOT EXISTS `cn_cb_balance_monthly` (
  `stat_month` date NOT NULL COMMENT '统计月份（源为 2026.8 形式，取当月 1 日）',
  `foreign_assets` decimal(20,2) DEFAULT NULL COMMENT '国外资产（亿元）',
  `fx_reserve` decimal(20,2) DEFAULT NULL COMMENT '外汇（亿元）—— 外汇占款，被动投放渠道',
  `monetary_gold` decimal(20,2) DEFAULT NULL COMMENT '货币黄金（亿元）',
  `other_foreign_assets` decimal(20,2) DEFAULT NULL COMMENT '其他国外资产（亿元）',
  `claims_gov` decimal(20,2) DEFAULT NULL COMMENT '对政府债权（亿元）',
  `claims_central_gov` decimal(20,2) DEFAULT NULL COMMENT '其中：中央政府（亿元）',
  `claims_other_dep_banks` decimal(20,2) DEFAULT NULL COMMENT '★对其他存款性公司债权（亿元）—— MLF/逆回购/PSL 投放余额，扩张=央行在主动放水',
  `claims_other_fin_cos` decimal(20,2) DEFAULT NULL COMMENT '对其他金融性公司债权（亿元）',
  `claims_non_monetary` decimal(20,2) DEFAULT NULL COMMENT '对非货币金融机构债权（亿元，1993~2000 期口径）',
  `claims_non_fin_cos` decimal(20,2) DEFAULT NULL COMMENT '对非金融性公司债权（亿元，历史口径）',
  `other_assets` decimal(20,2) DEFAULT NULL COMMENT '其他资产（亿元）',
  `total_assets` decimal(20,2) DEFAULT NULL COMMENT '总资产（亿元）',
  `reserve_money` decimal(20,2) DEFAULT NULL COMMENT '储备货币（亿元）= 基础货币',
  `currency_issue` decimal(20,2) DEFAULT NULL COMMENT '发行货币（亿元）',
  `fin_cos_deposit` decimal(20,2) DEFAULT NULL COMMENT '金融性公司存款（亿元）',
  `other_dep_banks_dep` decimal(20,2) DEFAULT NULL COMMENT '其他存款性公司存款（亿元）',
  `other_fin_cos_dep` decimal(20,2) DEFAULT NULL COMMENT '其他金融性公司存款（亿元，历史口径）',
  `fin_liab` decimal(20,2) DEFAULT NULL COMMENT '对金融机构负债（亿元，历史口径）',
  `reserve_deposit` decimal(20,2) DEFAULT NULL COMMENT '准备金存款（亿元，历史口径）',
  `non_fin_cos_dep` decimal(20,2) DEFAULT NULL COMMENT '非金融性公司存款（亿元，历史口径）',
  `demand_deposit` decimal(20,2) DEFAULT NULL COMMENT '活期存款（亿元，历史口径）',
  `bonds` decimal(20,2) DEFAULT NULL COMMENT '发行债券（亿元）',
  `foreign_liab` decimal(20,2) DEFAULT NULL COMMENT '国外负债（亿元）',
  `gov_deposit` decimal(20,2) DEFAULT NULL COMMENT '政府存款（亿元）—— 财政存款，抽水/放水项',
  `own_capital` decimal(20,2) DEFAULT NULL COMMENT '自有资金（亿元）',
  `other_liab` decimal(20,2) DEFAULT NULL COMMENT '其他负债（亿元）',
  `total_liab` decimal(20,2) DEFAULT NULL COMMENT '总负债（亿元）',
  `update_time` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `data_source` varchar(100) DEFAULT NULL,
  PRIMARY KEY (`stat_month`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='中国人民银行资产负债表月度（28 科目，源 356 期，1993.3 起）。⭐ 本域最有价值的单项新增：中国没有官方「QE 规模」公告，只能从资产负债表倒推——「对其他存款性公司债权」即 MLF/逆回购/PSL 等主动投放工具余额，其扩张/收缩就是中国式扩表/缩表。⚠️ 纯源值镜像表，**不存派生列**（同比/环比在分析层按需算；避免派生值与源值不同步）。⚠️ 部分科目为历史口径（1993~2000 期），近端为 NULL。';

-- ---------- [4/56] cn_liquidity_monthly ----------
CREATE TABLE IF NOT EXISTS `cn_liquidity_monthly` (
  `stat_month` date NOT NULL COMMENT '统计月份（取当月 1 日）',
  `m2` decimal(20,2) DEFAULT NULL COMMENT 'M2 数量（亿元）',
  `m2_yoy` decimal(8,2) DEFAULT NULL COMMENT 'M2 同比增长（%）',
  `m2_mom` decimal(8,4) DEFAULT NULL COMMENT '环比增长（%，源精度 8 位小数，保留 4 位）',
  `m1` decimal(20,2) DEFAULT NULL COMMENT 'M1 数量（亿元）',
  `m1_yoy` decimal(8,2) DEFAULT NULL COMMENT 'M1 同比增长（%）',
  `m1_mom` decimal(8,4) DEFAULT NULL COMMENT '环比增长（%，源精度 8 位小数，保留 4 位）',
  `m0` decimal(20,2) DEFAULT NULL COMMENT 'M0 流通中现金数量（亿元）',
  `m0_yoy` decimal(8,2) DEFAULT NULL COMMENT 'M0 同比增长（%）',
  `m0_mom` decimal(8,4) DEFAULT NULL COMMENT '环比增长（%，源精度 8 位小数，保留 4 位）',
  `m1_m2_gap` decimal(8,2) DEFAULT NULL COMMENT '【派生】M1同比 − M2同比（剪刀差，pp）。负值走阔=资金活化度下降（钱往定期走）',
  `credit_month` decimal(20,2) DEFAULT NULL COMMENT '当月新增人民币贷款（亿元）',
  `credit_cum` decimal(20,2) DEFAULT NULL COMMENT '年内累计新增人民币贷款（亿元）',
  `credit_yoy` decimal(8,2) DEFAULT NULL COMMENT '当月新增人民币贷款同比（%）',
  `shrzgm` decimal(20,2) DEFAULT NULL COMMENT '社会融资规模增量（亿元）',
  `shrzgm_rmb_loan` decimal(20,2) DEFAULT NULL COMMENT '社融分项·人民币贷款（亿元）',
  `shrzgm_fx_loan` decimal(20,2) DEFAULT NULL COMMENT '社融分项·委托贷款外币贷款（亿元）',
  `shrzgm_entrust` decimal(20,2) DEFAULT NULL COMMENT '社融分项·委托贷款（亿元）',
  `shrzgm_trust` decimal(20,2) DEFAULT NULL COMMENT '社融分项·信托贷款（亿元）',
  `shrzgm_undiscounted` decimal(20,2) DEFAULT NULL COMMENT '社融分项·未贴现银行承兑汇票（亿元）',
  `shrzgm_ent_bond` decimal(20,2) DEFAULT NULL COMMENT '社融分项·企业债券（亿元）',
  `shrzgm_equity` decimal(20,2) DEFAULT NULL COMMENT '社融分项·非金融企业境内股票融资（亿元）',
  `rrr_large` decimal(8,2) DEFAULT NULL COMMENT '【派生·顺延】大型金融机构法定存款准备金率（%），取该月末生效值',
  `rrr_small` decimal(8,2) DEFAULT NULL COMMENT '【派生·顺延】中小金融机构法定存款准备金率（%）',
  `rrr_effective_date` date DEFAULT NULL COMMENT '当前生效准备金率的生效日（用于判断"距上次调整多久"）',
  `other_deposit` decimal(20,2) DEFAULT NULL COMMENT '其他存款（亿元）。源 macro_china_supply_of_money。⚠️ 源侧近端只报「其他存款」，活期/定期/储蓄自 2020 年代起多为空 —— 不是采集失败，是源口径调整',
  `other_deposit_yoy` decimal(8,2) DEFAULT NULL COMMENT '其他存款同比（%）。源侧近端多为空，同上市',
  `savings_deposit` decimal(20,2) DEFAULT NULL COMMENT '储蓄存款（亿元）。源 macro_china_supply_of_money。⚠️ 源侧近端只报「其他存款」，活期/定期/储蓄自 2020 年代起多为空 —— 不是采集失败，是源口径调整',
  `savings_deposit_yoy` decimal(8,2) DEFAULT NULL COMMENT '储蓄存款同比（%）。源侧近端多为空，同上市',
  `time_deposit` decimal(20,2) DEFAULT NULL COMMENT '定期存款（亿元）。源 macro_china_supply_of_money。⚠️ 源侧近端只报「其他存款」，活期/定期/储蓄自 2020 年代起多为空 —— 不是采集失败，是源口径调整',
  `time_deposit_yoy` decimal(8,2) DEFAULT NULL COMMENT '定期存款同比（%）。源侧近端多为空，同上市',
  `demand_deposit` decimal(20,2) DEFAULT NULL COMMENT '活期存款（亿元）。源 macro_china_supply_of_money。⚠️ 源侧近端只报「其他存款」，活期/定期/储蓄自 2020 年代起多为空 —— 不是采集失败，是源口径调整',
  `demand_deposit_yoy` decimal(8,2) DEFAULT NULL COMMENT '活期存款同比（%）。源侧近端多为空，同上市',
  `quasi_money` decimal(20,2) DEFAULT NULL COMMENT '货币和准货币（广义货币M2）的准货币部分（亿元）。源 macro_china_supply_of_money。⚠️ 源侧近端只报「其他存款」，活期/定期/储蓄自 2020 年代起多为空 —— 不是采集失败，是源口径调整',
  `quasi_money_yoy` decimal(8,2) DEFAULT NULL COMMENT '货币和准货币（广义货币M2）的准货币部分同比（%）。源侧近端多为空，同上市',
  `update_time` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `data_source` varchar(100) DEFAULT NULL,
  PRIMARY KEY (`stat_month`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='中国货币与信用月度（M0/M1/M2 数量+同比+环比 / 新增信贷 / 社融增量+分项 / 法定准备金率）。⚠️ 两条派生行为：① m1_m2_gap 两列相减；② rrr_* 由「准备金率调整事件表」按月末顺延填充（源是 58 条事件、非月度序列）。其余列均为源值直存。⚠️ 社融 shrzgm* 来自商务数据中心，源侧更新滞后（实测 2026-09-25 时最新仅到 2026-04），故本表以 M1/M2 与新增信贷为主力指标、社融为辅。';

-- ---------- [5/56] cn_omo_daily ----------
CREATE TABLE IF NOT EXISTS `cn_omo_daily` (
  `section` varchar(24) NOT NULL DEFAULT 'omo_trade' COMMENT '来源栏目。⚠️ **各栏目各自独立编号**（同一年「交易公告第1号」与「买断式第1号」并存）⇒ 主键必须含本列。取值：omo_trade=公开市场业务交易公告（7天期逆回购 + 香港央票，日度）/ outright_repo=公开市场买断式逆回购业务公告（月度，2024-10 起）',
  `notice_year` smallint NOT NULL COMMENT '公告年份（人行**按年重新编号**，故「年份+序号」才唯一）。与 section、notice_no 共同构成主键',
  `notice_no` int NOT NULL COMMENT '公告编号 = 标题 [2026]第189号 中的 189。与 section、notice_year 共同构成主键',
  `trade_date` date DEFAULT NULL COMMENT '操作日：正文日期优先，其次中文落款日期，最后取公告 ID 前 8 位。⚠️ **刻意不作主键** —— 人行存在**批量补发**（实测 2025-10-09 这一个发布日下挂了 17 条公告、2025-11-14 挂了 27 条），且买断式公告是**招标预告**（发布日 9-14、操作日 9-15）⇒ 「发布日 ≠ 操作日」是常态',
  `op_type` varchar(24) NOT NULL DEFAULT 'reverse_repo' COMMENT '操作类型：reverse_repo=7天期逆回购 / outright_reverse_repo=买断式逆回购 / cbb=央行票据(香港) / treasury_deposit=国库现金定存 / other（含现券买断等）',
  `tenor_days` int DEFAULT NULL COMMENT '期限（天）。7=7天期逆回购；买断式 91/181；香港央票 91/182。来源：公告表格（按**列名**定位，不可硬编码表索引）或正文「期限为6个月（181天）」',
  `op_rate` decimal(8,4) DEFAULT NULL COMMENT '操作利率 / 中标利率（%）。⚠️ 逆回购的该值**不在正文、只在公告表格里**；买断式是「利率招标、多重价位中标」故**无单一利率**（本列为 NULL）。2024-07 起逆回购为「固定利率、数量招标」',
  `bid_amount` decimal(18,4) DEFAULT NULL COMMENT '投标量（亿元）。「固定利率、数量招标」下与中标量相等（全额满足一级交易商需求）',
  `win_amount` decimal(18,4) DEFAULT NULL COMMENT '★中标量 / 操作量（亿元）。⚠️ 零操作日**也发公告**、本列 = 0（不是 NULL）—— 措辞为「N天期逆回购操作量为零」；NULL 才表示源侧没给',
  `notice_url` varchar(255) DEFAULT NULL COMMENT '公告详情页 URL（溯源用）',
  `raw_text` varchar(500) DEFAULT NULL COMMENT '首个含操作信息的正文句（原文留档，供口径复核）',
  `update_time` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `data_source` varchar(100) DEFAULT NULL,
  PRIMARY KEY (`section`,`notice_year`,`notice_no`),
  KEY `idx_date` (`trade_date`),
  KEY `idx_type_date` (`op_type`,`trade_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='中国·央行公开市场操作（人行「公开市场业务」栏目群）。⭐ 中国层数量维度的**短端抓手**：M2 是月度存量、OMO 是日度流量。⚠️ 现采 2 个栏目：①`omo_trade` 公开市场业务交易公告（7天期逆回购，日度，约 3800 条；含香港央票）②`outright_repo` 公开市场买断式逆回购业务公告（2024-10 起，月度，39 条）—— 两者**各自独立编号**、且**都叫「公告」但业务完全不同**。⚠️ 已知未采的同级栏目（按需再补）：公开市场国债买卖业务公告、中央国库现金管理业务公告、中央银行票据业务公告、央行票据互换(CBS)、互换便利(SFISF)、其他业务公告(SLO)。⚠️ 两种公告 ID 形式并存：19 位时间戳型（2025-10-09 改版后，前 8 位=日期）与 7 位纯序号型（改版前，**不含日期**）。⚠️ 翻页 modulekey **不一定是数字**（交易公告=17081，买断式=b0da893b 这种 UUID）⇒ 必须从页面动态解析。⚠️ 只有事实列、**没有净投放/未到期余额**：净投放需按期限滚动推算到期日，口径选择权属分析层，刻意不在采集层固化。';

-- ---------- [6/56] cn_reserve_monthly ----------
CREATE TABLE IF NOT EXISTS `cn_reserve_monthly` (
  `stat_month` date NOT NULL COMMENT '统计月份（源 统计时间 形如 2026.8 → 2026-08-01）',
  `fx_reserve_usd` decimal(18,2) DEFAULT NULL COMMENT '★国家外汇储备（亿美元，官方口径）。源 akshare macro_china_foreign_exchange_gold。实测 34,383.25 @2026-08。⚠️ 中国层「外储」是三套口径之一，与 cn_cb_balance_monthly.fx_reserve（亿元、历史成本口径）严禁相加、严禁按市价互校',
  `fx_reserve_usd_yoy` decimal(10,2) DEFAULT NULL COMMENT '【派生】外储同比增长（%，同月比，本表自算）。两期都存在且基期 >0 才算；早期量级极小故同比噪声大，UI 不用',
  `gold_reserve_oz` decimal(18,2) DEFAULT NULL COMMENT '★黄金储备（万盎司，实物量）。实测 7,673 @2026-08',
  `gold_reserve_oz_chg` decimal(18,2) DEFAULT NULL COMMENT '【派生】较上月增减（万盎司；**正 = 增持**）。⚠️ 未增持月该值是常数而非 0 变动，故「连续增持 N 月」必须判 chg<>0，不能按月序连续推断',
  `update_time` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `data_source` varchar(100) DEFAULT NULL,
  PRIMARY KEY (`stat_month`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='中国·官方外汇储备与黄金储备（月度，akshare macro_china_foreign_exchange_gold，1978-12 起 419 期）。⭐ 中国层「央行对外资产」的**国际可比口径**：外储环比变动 ≈ 估值效应（汇率/金价/债价）+ 交易变动；黄金实物量支撑「央行储备多元化/去美元化」叙事。⚠️ 中国层储备有**三套口径、三种单位，严禁相加、严禁互校**：① 本表 fx_reserve_usd（亿美元·官方）② 本表 gold_reserve_oz（万盎司·实物量）③ cn_cb_balance_monthly.fx_reserve（亿元·货币当局资产负债表「国外资产」的人民币表内口径）。实测「人民币口径 ÷ 美元口径」的隐含折算率 24 期由 6.886 **单调降至** 6.318（同期市场汇率约 7.0~7.3）⇒ 它是**历史成本/复合折算而非汇率**，按市价互校会**稳定误报** ⇒ 互校只保留区间 [6.0,7.4] + 月度变动 <2%，且**放在采集器内断言**（DQ 检查器是单表+where、无 JOIN，跨表校验写不成规则）。⚠️ 源 `统计时间` 是字符串 `YYYY.M`：**字符串序 ≠ 时间序**（`2025.10 < 2025.2`）⇒ 采集器按 `(年,月)` 元组排序（公共件 `parse_cn_month`），**禁止 `iloc[-1]` 取最新**。⚠️ 早期量级极端（1980-12 外储 −12.96 亿美元）⇒ 量级 DQ 只守 2015 年后。';

-- ---------- [7/56] currency_boc_daily ----------
CREATE TABLE IF NOT EXISTS `currency_boc_daily` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `currency` varchar(10) NOT NULL DEFAULT 'USD' COMMENT '币种代码（USD/EUR/JPY…）',
  `currency_name` varchar(20) DEFAULT NULL COMMENT '币种中文名',
  `trade_date` date NOT NULL COMMENT '交易日期',
  `mid_price` decimal(12,6) DEFAULT NULL COMMENT '央行中间价（**元/1 外币**，源值 100 外币价已除 100 归一）',
  `spot_buy` decimal(12,6) DEFAULT NULL COMMENT '中行汇买价（元/1 外币）',
  `spot_sell` decimal(12,6) DEFAULT NULL COMMENT '中行钞卖价/汇卖价（元/1 外币）',
  `ref_price` decimal(12,6) DEFAULT NULL COMMENT '中行折算价（元/1 外币）',
  `update_time` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `data_source` varchar(100) DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_currency_date` (`currency`,`trade_date`),
  KEY `idx_trade_date` (`trade_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='人民币外汇牌价与中间价（蓝图E，2026-09-19 落地）';

-- ---------- [8/56] dc_index_market ----------
CREATE TABLE IF NOT EXISTS `dc_index_market` (
  `id` int NOT NULL AUTO_INCREMENT,
  `index_code` varchar(20) NOT NULL COMMENT '指数代码',
  `index_name` varchar(100) DEFAULT NULL COMMENT '指数简称',
  `trade_date` date NOT NULL COMMENT '交易日期',
  `open` decimal(10,4) DEFAULT NULL COMMENT '开盘价',
  `high` decimal(10,4) DEFAULT NULL COMMENT '最高价',
  `low` decimal(10,4) DEFAULT NULL COMMENT '最低价',
  `close` decimal(10,4) DEFAULT NULL COMMENT '收盘价',
  `volume` bigint DEFAULT NULL COMMENT '成交量，单位: 手',
  `amount` bigint DEFAULT NULL COMMENT '成交额，单位: 元',
  `change_amount` decimal(10,3) DEFAULT NULL COMMENT '涨跌额',
  `change_pct` decimal(8,4) DEFAULT NULL COMMENT '涨跌幅(%)',
  `turnover_ratio` decimal(8,4) DEFAULT NULL COMMENT '换手率(%)',
  `index_group` varchar(12) DEFAULT NULL COMMENT '指数分组：市场基准/市值风格/科技成长/情绪温度/股息防守/政策周期',
  `group_desc` varchar(32) DEFAULT NULL COMMENT '角色说明（全A基准锚点等）',
  `update_time` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  `data_source` varchar(100) DEFAULT NULL COMMENT '数据来源',
  PRIMARY KEY (`id`),
  KEY `idx_index_code` (`index_code`),
  KEY `idx_index_name` (`index_name`),
  KEY `idx_index_date` (`trade_date`),
  KEY `idx_update_time` (`update_time`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='东财指数行情信息表';

-- ---------- [9/56] dq_close_nonpositive_detail ----------
CREATE TABLE IF NOT EXISTS `dq_close_nonpositive_detail` (
  `run_date` date NOT NULL,
  `stock_code` varchar(10) NOT NULL,
  `trade_date` date NOT NULL,
  `open` decimal(10,3) DEFAULT NULL,
  `high` decimal(10,3) DEFAULT NULL,
  `low` decimal(10,3) DEFAULT NULL,
  `close` decimal(10,3) DEFAULT NULL,
  `pre_close` decimal(10,3) DEFAULT NULL,
  `change_amount` decimal(10,3) DEFAULT NULL,
  `change_pct` decimal(12,4) DEFAULT NULL,
  `volume` bigint DEFAULT NULL,
  `amount` decimal(15,2) DEFAULT NULL,
  `adj_factor` decimal(18,8) DEFAULT NULL,
  `data_source` varchar(100) DEFAULT NULL,
  `defect_type` varchar(16) NOT NULL COMMENT 'close_negative / close_zero / close_null',
  `board` varchar(16) DEFAULT NULL,
  `era` varchar(16) DEFAULT NULL,
  `vwap_proxy` decimal(16,4) DEFAULT NULL COMMENT 'amount/NULLIF(volume,0)，仅供参考',
  `note` varchar(255) DEFAULT NULL,
  PRIMARY KEY (`stock_code`,`trade_date`),
  KEY `idx_run` (`run_date`),
  KEY `idx_defect` (`defect_type`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='全史 close<=0/空 行级清单（daily_value_bounds 全史盲区补全）';

-- ---------- [10/56] dq_gap_detail ----------
CREATE TABLE IF NOT EXISTS `dq_gap_detail` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `run_at` datetime NOT NULL COMMENT '本轮体检时间',
  `run_date` date NOT NULL COMMENT '体检日期',
  `stock_code` varchar(10) NOT NULL COMMENT '股票代码',
  `prev_date` date NOT NULL COMMENT '前一笔记录交易日',
  `next_date` date NOT NULL COMMENT '后一笔记录交易日',
  `gap_days` int NOT NULL COMMENT '两笔间隔自然日',
  `risk` varchar(10) NOT NULL DEFAULT 'suspect' COMMENT 'high(>=365 天)/suspect',
  `status` varchar(10) NOT NULL DEFAULT 'open' COMMENT 'open/fixed/ignored（L3 修复闭环状态）',
  `note` varchar(255) DEFAULT NULL COMMENT '备注（停牌/退市/已补等）',
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_run_date` (`run_date`),
  KEY `idx_code` (`stock_code`),
  KEY `idx_status` (`status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='全史疑似缺口明细（L1 gap_scan 产出 → L3 消费）';

-- ---------- [11/56] dq_pct_anomaly_detail ----------
CREATE TABLE IF NOT EXISTS `dq_pct_anomaly_detail` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `run_date` date NOT NULL COMMENT '盘点日',
  `stock_code` varchar(10) NOT NULL,
  `trade_date` date NOT NULL,
  `close` decimal(16,4) DEFAULT NULL,
  `pre_close` decimal(16,4) DEFAULT NULL,
  `prev_close` decimal(16,4) DEFAULT NULL,
  `change_pct` decimal(12,4) DEFAULT NULL,
  `change_amount` decimal(16,4) DEFAULT NULL,
  `adj_factor` decimal(24,12) DEFAULT NULL,
  `prev_adj_factor` decimal(24,12) DEFAULT NULL,
  `data_source` varchar(20) DEFAULT NULL,
  `domain` varchar(16) NOT NULL COMMENT 'pre1996 / seg_4_8_92 / constrained',
  `same_factor` tinyint(1) DEFAULT NULL COMMENT '本行因子与前一行相同=无除权',
  `eq_violated` tinyint(1) DEFAULT NULL COMMENT '因子未变却 pre_close<>prev_close',
  `judgement` varchar(20) NOT NULL COMMENT 'confirmed_dirty / possible_dirty / possible_legal',
  `dev_ratio` decimal(24,6) DEFAULT NULL COMMENT '|pre_close-prev_close|/prev_close',
  `note` varchar(255) DEFAULT NULL,
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_code_date` (`stock_code`,`trade_date`),
  KEY `idx_judgement` (`judgement`),
  KEY `idx_run_date` (`run_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='|change_pct|>31% 行级清单（取证报告 2026-09-22 建议4）';

-- ---------- [12/56] dq_recon_detail ----------
CREATE TABLE IF NOT EXISTS `dq_recon_detail` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `run_at` datetime NOT NULL COMMENT '本轮对账时间',
  `run_date` date NOT NULL COMMENT '对账日期',
  `mode` varchar(10) NOT NULL COMMENT 'window(滚动窗口)/sample(全史抽样)',
  `stock_code` varchar(10) NOT NULL COMMENT '股票代码',
  `trade_date` date DEFAULT NULL COMMENT '差异日期（sample 模式为 NULL）',
  `diff_type` varchar(20) NOT NULL COMMENT 'missing_local/missing_remote/value_diff/count_diff/range_diff/sum_diff',
  `col_name` varchar(20) DEFAULT NULL COMMENT '差异字段（value_diff 时）',
  `local_value` varchar(50) DEFAULT NULL COMMENT '本地值',
  `remote_value` varchar(50) DEFAULT NULL COMMENT '远端值',
  `remote_source` varchar(20) DEFAULT NULL COMMENT '对账源 tencent/eastmoney',
  `note` varchar(255) DEFAULT NULL COMMENT '备注（除权豁免等）',
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_run_date` (`run_date`),
  KEY `idx_code` (`stock_code`),
  KEY `idx_mode` (`mode`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='外部对账差异明细（L2 recon 产出）';

-- ---------- [13/56] dq_report ----------
CREATE TABLE IF NOT EXISTS `dq_report` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `run_at` datetime NOT NULL COMMENT '本轮体检执行时间',
  `run_date` date NOT NULL COMMENT '体检日期（按天冗余）',
  `rule_id` int DEFAULT NULL COMMENT '规则 id',
  `rule_name` varchar(80) NOT NULL COMMENT '规则名',
  `table_name` varchar(80) NOT NULL COMMENT '被检查表',
  `check_type` varchar(30) NOT NULL COMMENT '检查器类型',
  `severity` varchar(10) NOT NULL DEFAULT 'warning' COMMENT '严重级（冗余规则当时值）',
  `status` varchar(10) NOT NULL COMMENT 'pass/warning/fail/error',
  `metric_value` varchar(200) DEFAULT NULL COMMENT '实测值',
  `message` varchar(500) DEFAULT NULL COMMENT '判定明细/期望说明',
  PRIMARY KEY (`id`),
  KEY `idx_run_at` (`run_at`),
  KEY `idx_run_date` (`run_date`),
  KEY `idx_rule` (`rule_name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='数据质量体检结果表';

-- ---------- [14/56] dq_rules ----------
CREATE TABLE IF NOT EXISTS `dq_rules` (
  `id` int NOT NULL AUTO_INCREMENT,
  `rule_name` varchar(80) NOT NULL COMMENT '规则名（唯一）',
  `table_name` varchar(80) NOT NULL COMMENT '被检查表',
  `check_type` varchar(30) NOT NULL COMMENT '检查器类型',
  `rule_group` varchar(10) NOT NULL DEFAULT 'daily' COMMENT '规则组 daily/weekly（weekly 走独立低频任务）',
  `params` json DEFAULT NULL COMMENT '检查参数',
  `severity` varchar(10) NOT NULL DEFAULT 'warning' COMMENT '严重级 warning/critical/info',
  `enabled` tinyint(1) NOT NULL DEFAULT '1' COMMENT '是否启用',
  `description` varchar(255) DEFAULT NULL COMMENT '规则说明',
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `update_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_rule_name` (`rule_name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='数据质量规则配置表';

-- ---------- [15/56] eu_money_supply_monthly ----------
CREATE TABLE IF NOT EXISTS `eu_money_supply_monthly` (
  `stat_month` date NOT NULL COMMENT '统计月份（源 period 形如 2026-07，取当月 1 日）',
  `m3` decimal(20,2) DEFAULT NULL COMMENT '★M3（百万欧元，工作日与季节调整）。DBnomics ECB/BSI/M.U2.Y.V.M30.X.1.U2.2300.Z01.E。实测 17,613,983 @2026-07',
  `m2` decimal(20,2) DEFAULT NULL COMMENT '★M2（百万欧元）。M20。实测 16,436,438',
  `m1` decimal(20,2) DEFAULT NULL COMMENT '★M1（百万欧元）。M10。实测 11,292,842',
  `m3_yoy` decimal(8,2) DEFAULT NULL COMMENT '【派生】M3 同比增长（%）',
  `m2_yoy` decimal(8,2) DEFAULT NULL COMMENT '【派生】M2 同比增长（%）',
  `m1_yoy` decimal(8,2) DEFAULT NULL COMMENT '【派生】M1 同比增长（%）',
  `update_time` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `data_source` varchar(100) DEFAULT NULL,
  PRIMARY KEY (`stat_month`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='欧元区·货币供应量（月度，ECB BSI，1980-01 起 559 期）。⭐ 欧元区是全球第二大货币区，「欧元区 M2/M3 增速 vs 美国」是判断全球美元/欧元流动性相对松紧的直接读数。⚠️ 唯一免费通道：ECB 直连三域实测**全部阻断**（data-api.ecb.europa.eu / data.ecb.europa.eu / sdw-wsrest.ecb.europa.eu 均 SSL timeout 或 DNS 失败），本表经 DBnomics 镜像取得 ⇒ **源单一、必须做「失效即告警」**（DQ 含 freshness 规则）。⚠️ 序列口径为「Euro area (changing composition)」= 按当期成员国组成回溯，与固定组成口径有差异。';

-- ---------- [16/56] finance_calendar ----------
CREATE TABLE IF NOT EXISTS `finance_calendar` (
  `id` int NOT NULL AUTO_INCREMENT,
  `event_date` date NOT NULL COMMENT '日期',
  `title` varchar(200) DEFAULT NULL COMMENT '事件标题',
  `content` varchar(600) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci DEFAULT NULL COMMENT '事件内容',
  `update_time` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  `data_source` varchar(100) DEFAULT NULL COMMENT '数据来源',
  PRIMARY KEY (`id`),
  KEY `idx_event_date` (`event_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='财经日历数据表';

-- ---------- [17/56] finance_concept_analysis ----------
CREATE TABLE IF NOT EXISTS `finance_concept_analysis` (
  `id` int NOT NULL AUTO_INCREMENT,
  `event_date` date NOT NULL COMMENT '日期',
  `title` varchar(200) DEFAULT NULL COMMENT '事件标题',
  `content` varchar(400) DEFAULT NULL COMMENT '事件内容',
  `concept_code` varchar(20) NOT NULL COMMENT '概念代码',
  `concept_name` varchar(100) DEFAULT NULL COMMENT '概念名称',
  `relation_type` varchar(10) DEFAULT NULL COMMENT '关联类型(利好/利空)',
  `relation_degree` int DEFAULT NULL COMMENT '关联程度(1-10)',
  `analysis` text COMMENT '分析依据',
  `update_time` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_event_date` (`event_date`),
  KEY `idx_concept_code` (`concept_code`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='财经事件与概念关联分析结果';

-- ---------- [18/56] fund_info ----------
CREATE TABLE IF NOT EXISTS `fund_info` (
  `id` int NOT NULL AUTO_INCREMENT,
  `fund_code` varchar(10) NOT NULL COMMENT '基金代码',
  `fund_name` varchar(50) DEFAULT NULL COMMENT '基金简称',
  `fund_type` varchar(50) DEFAULT NULL COMMENT '基金类型',
  `update_time` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  `data_source` varchar(100) DEFAULT NULL COMMENT '数据来源',
  PRIMARY KEY (`id`),
  KEY `idx_fund_code` (`fund_code`),
  KEY `idx_update_time` (`update_time`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='基金基本信息表';

-- ---------- [19/56] fund_new_issue ----------
CREATE TABLE IF NOT EXISTS `fund_new_issue` (
  `fund_code` varchar(12) NOT NULL COMMENT '基金代码',
  `fund_name` varchar(100) DEFAULT NULL COMMENT '基金简称',
  `company` varchar(60) DEFAULT NULL COMMENT '发行公司',
  `fund_type` varchar(40) DEFAULT NULL COMMENT '基金类型（混合型-偏股/债券型…）',
  `subs_period` varchar(40) DEFAULT NULL COMMENT '集中认购期（源格式 YY/MM/DD～YY/MM/DD）',
  `raise_share` decimal(16,4) DEFAULT NULL COMMENT '募集份额（亿份）',
  `establish_date` date DEFAULT NULL COMMENT '成立日期',
  `manager` varchar(80) DEFAULT NULL COMMENT '基金经理',
  `purchase_status` varchar(20) DEFAULT NULL COMMENT '申购状态',
  `update_time` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `data_source` varchar(100) DEFAULT NULL,
  PRIMARY KEY (`fund_code`),
  KEY `idx_establish` (`establish_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='新基金发行与成立（蓝影E 基金发行冰点=反向底部信号，2026-09-19 落地）';

-- ---------- [20/56] futures_spot_price ----------
CREATE TABLE IF NOT EXISTS `futures_spot_price` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `trade_date` date NOT NULL COMMENT '交易日期',
  `good_name` varchar(20) DEFAULT NULL COMMENT '商品名称',
  `spot_price` decimal(8,2) DEFAULT NULL COMMENT '现货价格',
  `main_contract_code` varchar(20) DEFAULT NULL COMMENT '主力合约代码',
  `main_contract_price` decimal(8,2) DEFAULT NULL COMMENT '主力合约价格',
  `main_contract_basis` decimal(8,2) DEFAULT NULL COMMENT '主力合约基差',
  `main_contract_change_pct` decimal(8,2) DEFAULT NULL COMMENT '主力合约变动百分比',
  `main_basis_high_180d` decimal(8,2) DEFAULT NULL COMMENT '180日内主力基差最高',
  `main_basis_low_180d` decimal(8,2) DEFAULT NULL COMMENT '180日内主力基差最低',
  `main_basis_avg_180d` decimal(8,2) DEFAULT NULL COMMENT '180日内主力基差平均',
  `update_time` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  `data_source` varchar(100) DEFAULT NULL COMMENT '数据来源',
  PRIMARY KEY (`id`),
  KEY `idx_trade_date` (`trade_date`),
  KEY `idx_good_name` (`good_name`),
  KEY `idx_update_time` (`update_time`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='现货期货价格表';

-- ---------- [21/56] global_liquidity_bis ----------
CREATE TABLE IF NOT EXISTS `global_liquidity_bis` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `time_period` date NOT NULL COMMENT '统计期（季度末，源为 2026-Q1 形式 → 2026-03-31）',
  `freq` varchar(4) NOT NULL DEFAULT 'Q' COMMENT '频率（Q=季度）',
  `curr_denom` varchar(8) NOT NULL COMMENT '计价货币（USD）—— 本表只采 USD 口径，即「全球美元信贷」',
  `borrowers_cty` varchar(8) NOT NULL COMMENT '借款人国家/地区代码（BIS 自有码，非 ISO2）。实测 19 个：US/CN/IN/MX/ID/RU/SA/TR/AR/CL/BR… 及 4T/3C/4U/4Y 等聚合组',
  `borrowers_sector` varchar(4) NOT NULL COMMENT '借款人部门：N=非银行 / G=政府 / P=私人',
  `lenders_sector` varchar(4) NOT NULL COMMENT '贷款方部门：B=银行 / A=全部',
  `l_pos_type` varchar(4) NOT NULL COMMENT '头寸类型：I=国际(international) / A=全部',
  `l_instr` varchar(4) NOT NULL COMMENT '工具：B=银行贷款 / D=债券 / G=合计',
  `unit_measure` varchar(8) NOT NULL COMMENT '计量单位：USD=百万美元 / 771=占比(%)',
  `obs_value` decimal(24,4) DEFAULT NULL COMMENT '读数。unit_measure=USD 时为百万美元',
  `title` varchar(255) DEFAULT NULL COMMENT '源侧英文口径全称（BIS 原文，保留供口径溯源）',
  `update_time` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `data_source` varchar(100) DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_gliquidity` (`time_period`,`curr_denom`,`borrowers_cty`,`borrowers_sector`,`lenders_sector`,`l_pos_type`,`l_instr`,`unit_measure`),
  KEY `idx_period_cty` (`time_period`,`borrowers_cty`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='全球·BIS 全球流动性指标（WS_GLI，季度）。⭐ 这是「全球美元有多少」的**官方口径**，替代 v1.0 原计划的自建全球 M2 汇总（那需要拼 N 国 × 汇率换算，且非官方）。核心用法：`BORROWERS_CTY != US` 的美元信贷 = **离岸美元**（回答「美元流出美国了没有」）。⚠️ 实测 API 形式（与 v1.0/v2.0 文档结论**相反**，已勘误）：resource key 只有 **3 段** `FREQ.CURR_DENOM.BORROWERS_CTY`（如 `Q.USD`、`Q.USD.US`），**多给一段即 404**；且 `/all` 形式实测 **404**。⚠️ BIS 的借款人国家用自有码（US/CN/4T…），不是 ISO2 —— `Q.USD.JP`、`Q.USD.GB` 均 404。⚠️ 美国借款人（Q.USD.US）只报 G/P 两个部门、**无 N（非银行）**，故「离岸美元」不能简单用「全量 − 美国」推导，须按 borrowers_cty 直接筛非美组。⚠️ 季度数据、T+1 季发布，时效天然滞后。';

-- ---------- [22/56] global_usd_index_daily ----------
CREATE TABLE IF NOT EXISTS `global_usd_index_daily` (
  `trade_date` date NOT NULL COMMENT '交易日期（人民币中间价口径的报价日）',
  `dxy_sina` decimal(10,4) DEFAULT NULL COMMENT '★美元指数官方日线收盘（ICE 口径）。源：新浪 NewForexService.getDayKLine?symbol=DINIW（1985-11-08 起，实测 10573 行、列序 日期,开,低,高,收）。与官方实时快照偏差仅 0.004% ⇒ 本列为主口径',
  `dxy_calc` decimal(10,4) DEFAULT NULL COMMENT '自算美元指数（ICE DXY 标准公式 × 6 成分货币交叉汇率）',
  `dxy_snapshot` decimal(10,4) DEFAULT NULL COMMENT '新浪 DINIW 实时快照（仅采集当日写入，作标定基准）',
  `eur_usd` decimal(12,6) DEFAULT NULL COMMENT '成分货币交叉汇率 欧元/美元（由人民币中间价反算）',
  `usd_jpy` decimal(12,6) DEFAULT NULL COMMENT '成分货币交叉汇率 美元/日元',
  `gbp_usd` decimal(12,6) DEFAULT NULL COMMENT '成分货币交叉汇率 英镑/美元',
  `usd_cad` decimal(12,6) DEFAULT NULL COMMENT '成分货币交叉汇率 美元/加元',
  `usd_sek` decimal(12,6) DEFAULT NULL COMMENT '成分货币交叉汇率 美元/瑞典克朗',
  `usd_chf` decimal(12,6) DEFAULT NULL COMMENT '成分货币交叉汇率 美元/瑞士法郎',
  `update_time` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `data_source` varchar(100) DEFAULT NULL,
  PRIMARY KEY (`trade_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='美元指数日频（自算为主 + 新浪 DINIW 快照标定）。口径：自算走人民币中间价交叉汇率（SAFE 25 币种宽表），权重用 ICE DXY 标准公式。实测（2026-09-24）自算 101.3532 vs 新浪官方快照 101.2675，偏差 +0.085% —— 两套口径基本一致，可直接用绝对值。⚠️ 勘误（2026-09-25）：设计文档初稿曾记「偏差 -3.09%、中间价口径有约 ±3% 系统性偏离」，该结论实为标价法单位错误所致（把间接标价的瑞典克朗当直接标价，USDSEK 算成 4.58 而真值 9.94，DXY 因此偏低 3.3%）；修正后偏差 <0.1%。6 个成分货币交叉汇率一并存表，便于口径溯源复核（cross-rate 量级断言见采集器 _CROSS_RANGE）。';

-- ---------- [23/56] index_constituents ----------
CREATE TABLE IF NOT EXISTS `index_constituents` (
  `id` int NOT NULL AUTO_INCREMENT,
  `index_code` varchar(20) NOT NULL COMMENT '指数代码',
  `stock_code` varchar(10) NOT NULL COMMENT '成分股代码',
  `stock_name` varchar(50) DEFAULT NULL COMMENT '成分股名称',
  `weight` decimal(10,4) DEFAULT NULL COMMENT '权重(%)，无权重源时为 NULL',
  `trade_date` date NOT NULL COMMENT '快照日（本轮采集日）',
  `sample_date` date DEFAULT NULL COMMENT '源侧样本日期（中证官网「日期」列；国证源无则 NULL）',
  `source` varchar(50) NOT NULL COMMENT 'csindex/cni/stock_info_members/derived_sh',
  `update_time` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `data_source` varchar(100) DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_index_stock_date` (`index_code`,`stock_code`,`trade_date`),
  KEY `idx_index_code` (`index_code`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='指数成分股快照';

-- ---------- [24/56] index_profile ----------
CREATE TABLE IF NOT EXISTS `index_profile` (
  `id` int NOT NULL AUTO_INCREMENT,
  `index_code` varchar(20) NOT NULL COMMENT '指数代码',
  `index_name` varchar(100) NOT NULL COMMENT '指数名称',
  `description` text COMMENT '指数释义',
  `base_date` varchar(20) DEFAULT NULL COMMENT '基日',
  `base_point` varchar(20) DEFAULT NULL COMMENT '基点',
  `source` varchar(50) NOT NULL DEFAULT 'seed' COMMENT 'seed/cnindex/csindex',
  `update_time` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_index_code` (`index_code`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='指数档案（释义/基准）';

-- ---------- [25/56] index_valuation_daily ----------
CREATE TABLE IF NOT EXISTS `index_valuation_daily` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `index_code` varchar(20) NOT NULL COMMENT '指数代码（全A用 ALL_A）',
  `index_name` varchar(50) DEFAULT NULL COMMENT '指数名称',
  `trade_date` date NOT NULL COMMENT '估值日期',
  `source` varchar(20) NOT NULL COMMENT 'csindex(中证官网)/legu(乐咕)/all_a(全A等权)',
  `pe_lyr` decimal(12,4) DEFAULT NULL COMMENT '静态市盈率（整体法；中证「市盈率1」/乐咕「静态市盈率」）',
  `pe_ttm` decimal(12,4) DEFAULT NULL COMMENT '滚动市盈率 TTM（整体法；中证「市盈率2」/乐咕「滚动市盈率」）',
  `pe_ttm_median` decimal(12,4) DEFAULT NULL COMMENT '滚动市盈率中位数（仅乐咕/全A 源提供）',
  `pe_lyr_median` decimal(12,4) DEFAULT NULL COMMENT '静态市盈率中位数（仅乐咕/全A 源提供）',
  `dividend_yield` decimal(10,4) DEFAULT NULL COMMENT '股息率(%)',
  `dividend_yield2` decimal(10,4) DEFAULT NULL COMMENT '股息率2（中证口径：近12月 vs 近3年分红）',
  `pe_ttm_pct10y` decimal(8,4) DEFAULT NULL COMMENT '滚动PE近十年分位（0~1，仅 all_a 源直接给）',
  `close_point` decimal(16,4) DEFAULT NULL COMMENT '指数点位（乐咕/全A 源提供，供勾稽）',
  `update_time` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `data_source` varchar(100) DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_index_date_source` (`index_code`,`trade_date`,`source`),
  KEY `idx_trade_date` (`trade_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='指数估值日频（股债性价比 ERP 的估值分母，2026-09-19 P2 落地）';

-- ---------- [26/56] interbank_rate_daily ----------
CREATE TABLE IF NOT EXISTS `interbank_rate_daily` (
  `trade_date` date NOT NULL COMMENT '交易日',
  `shibor_on` decimal(8,4) DEFAULT NULL COMMENT 'Shibor 隔夜(%)',
  `shibor_1w` decimal(8,4) DEFAULT NULL COMMENT 'Shibor 1周(%)',
  `shibor_1m` decimal(8,4) DEFAULT NULL COMMENT 'Shibor 1月(%)',
  `shibor_3m` decimal(8,4) DEFAULT NULL COMMENT 'Shibor 3月(%)',
  `lpr_1y` decimal(8,4) DEFAULT NULL COMMENT 'LPR 1年(%)',
  `lpr_5y` decimal(8,4) DEFAULT NULL COMMENT 'LPR 5年(%)',
  `update_time` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `data_source` varchar(100) DEFAULT NULL,
  PRIMARY KEY (`trade_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='银行间拆借利率 + LPR（蓝图A「钱贵不贵」维度，2026-09-19 落地）';

-- ---------- [27/56] market_style_daily ----------
CREATE TABLE IF NOT EXISTS `market_style_daily` (
  `trade_date` date NOT NULL COMMENT '交易日',
  `ret_bench_20` decimal(10,4) DEFAULT NULL COMMENT '市场基准组等权20日收益%',
  `ret_bench_60` decimal(10,4) DEFAULT NULL COMMENT '市场基准组等权60日收益%',
  `ret_size_20` decimal(10,4) DEFAULT NULL COMMENT '市值风格组等权20日收益%',
  `ret_size_60` decimal(10,4) DEFAULT NULL COMMENT '市值风格组等权60日收益%',
  `ret_tech_20` decimal(10,4) DEFAULT NULL COMMENT '科技成长组等权20日收益%',
  `ret_tech_60` decimal(10,4) DEFAULT NULL COMMENT '科技成长组等权60日收益%',
  `ret_sent_20` decimal(10,4) DEFAULT NULL COMMENT '情绪温度组等权20日收益%',
  `ret_sent_60` decimal(10,4) DEFAULT NULL COMMENT '情绪温度组等权60日收益%',
  `ret_div_20` decimal(10,4) DEFAULT NULL COMMENT '股息防守组等权20日收益%',
  `ret_div_60` decimal(10,4) DEFAULT NULL COMMENT '股息防守组等权60日收益%',
  `ret_pol_20` decimal(10,4) DEFAULT NULL COMMENT '政策周期组等权20日收益%',
  `ret_pol_60` decimal(10,4) DEFAULT NULL COMMENT '政策周期组等权60日收益%',
  `scissors_20` decimal(10,4) DEFAULT NULL COMMENT '大小盘剪刀差20日：小盘组-大盘组',
  `scissors_60` decimal(10,4) DEFAULT NULL COMMENT '大小盘剪刀差60日',
  `risk_appetite_20` decimal(10,4) DEFAULT NULL COMMENT '风偏分数：科技成长-股息防守(20日)',
  `sentiment_20` decimal(10,4) DEFAULT NULL COMMENT '情绪温度：券商-中证全指(20日超额)',
  `policy_excess_20` decimal(10,4) DEFAULT NULL COMMENT '政策敏感：地产-中证全指(20日超额)',
  `bench_pos_pct` decimal(6,2) DEFAULT NULL COMMENT '大势位置：中证全指近250日高低区间分位%',
  `update_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `breadth_total` int DEFAULT NULL COMMENT '当日有效个股数（有收盘价）',
  `breadth_up` int DEFAULT NULL COMMENT '上涨家数',
  `breadth_down` int DEFAULT NULL COMMENT '下跌家数',
  `breadth_up_ratio` decimal(6,2) DEFAULT NULL COMMENT '上涨家数占比%',
  `breadth_adl` bigint DEFAULT NULL COMMENT '腾落线ADL累计值（Σ上涨-下跌）',
  `limit_up` int DEFAULT NULL COMMENT '涨停家数（主板≥9.8%，创业板/科创板≥19.8%，近似口径）',
  `limit_down` int DEFAULT NULL COMMENT '跌停家数',
  `above_ma20_pct` decimal(6,2) DEFAULT NULL COMMENT '收盘站上MA20个股占比%',
  `above_ma60_pct` decimal(6,2) DEFAULT NULL COMMENT '收盘站上MA60个股占比%（分母为有60日历史个股）',
  `new_high60` int DEFAULT NULL COMMENT '创60日新高家数',
  `new_low60` int DEFAULT NULL COMMENT '创60日新低家数',
  `hl_diff60` int DEFAULT NULL COMMENT '创60日新高-新低家数差',
  `market_amount` decimal(18,2) DEFAULT NULL COMMENT '全市场成交额（亿元，个股 amount 求和）',
  `amount_ratio_20` decimal(8,2) DEFAULT NULL COMMENT '成交额 / 20日均值 ×100%（>100 放量，<100 缩量）',
  `scissors_adj20` decimal(10,4) DEFAULT NULL COMMENT '剪刀差风险调整：scissors_20 ÷ 其近250日滚动标准差（0=两腿同收益）',
  `risk_appetite_adj20` decimal(10,4) DEFAULT NULL COMMENT '风偏风险调整：risk_appetite_20 ÷ 其近250日滚动标准差（0=两腿同收益）',
  `turnover_med` decimal(8,4) DEFAULT NULL COMMENT '个股换手率中位数%（源 turnover_ratio 小数×100；中位数抗异常值）',
  PRIMARY KEY (`trade_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='市场风格日频物化表（分析研究·市场风向模块数据源，口径见交互设计规范 §4.1）';

-- ---------- [28/56] market_xcheck_daily ----------
CREATE TABLE IF NOT EXISTS `market_xcheck_daily` (
  `trade_date` date NOT NULL COMMENT '与 market_style_daily.trade_date 对齐的交易日',
  `items_n` tinyint unsigned DEFAULT NULL COMMENT '参与判定的项数（当前 7）',
  `agree_n` tinyint unsigned DEFAULT NULL COMMENT '一致项数',
  `diverge_n` tinyint unsigned DEFAULT NULL COMMENT '背离项数 —— 本表的核心产出',
  `neutral_n` tinyint unsigned DEFAULT NULL COMMENT '中性项数（两维都在死区内）',
  `nodata_n` tinyint unsigned DEFAULT NULL COMMENT '数据缺失项数',
  `diverge_keys` varchar(255) DEFAULT NULL COMMENT '背离项 key，逗号分隔（供「哪些项在背离」比对）',
  `computed_at` datetime NOT NULL COMMENT '写入时刻',
  PRIMARY KEY (`trade_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='交叉印证背离数日频物化（market_wind 卡片分位用）';

-- ---------- [29/56] news ----------
CREATE TABLE IF NOT EXISTS `news` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `title` varchar(500) NOT NULL COMMENT '新闻标题',
  `source` varchar(50) DEFAULT NULL COMMENT '来源（cls=财联社/em=东财）',
  `published_at` datetime DEFAULT NULL COMMENT '发布时间',
  `content` text COMMENT '正文',
  `url` varchar(500) DEFAULT NULL COMMENT '原文链接',
  `created_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP COMMENT '入库时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_source_url` (`source`,`url`),
  KEY `idx_pubtime` (`published_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='财经资讯新闻表';

-- ---------- [30/56] overseas_index_daily ----------
CREATE TABLE IF NOT EXISTS `overseas_index_daily` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `index_code` varchar(20) NOT NULL COMMENT 'HSI/DJI/SPX/IXIC',
  `index_name` varchar(50) DEFAULT NULL COMMENT '恒生指数/道琼斯/标普500/纳斯达克',
  `trade_date` date NOT NULL COMMENT '交易日期（当地）',
  `open` decimal(16,4) DEFAULT NULL,
  `high` decimal(16,4) DEFAULT NULL,
  `low` decimal(16,4) DEFAULT NULL,
  `close` decimal(16,4) DEFAULT NULL,
  `volume` decimal(24,2) DEFAULT NULL,
  `amount` decimal(28,2) DEFAULT NULL,
  `update_time` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `data_source` varchar(100) DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_code_date` (`index_code`,`trade_date`),
  KEY `idx_trade_date` (`trade_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='海外/港股指数日线（蓝图E 跨市场，2026-09-19 落地）';

-- ---------- [31/56] securities_margin ----------
CREATE TABLE IF NOT EXISTS `securities_margin` (
  `id` int NOT NULL AUTO_INCREMENT,
  `trade_date` date DEFAULT NULL COMMENT '交易日期',
  `rzye` decimal(24,2) DEFAULT NULL COMMENT '融资余额（元）',
  `rqye` decimal(24,2) DEFAULT NULL COMMENT '融券余额（元）',
  `rzrqye` decimal(24,2) DEFAULT NULL COMMENT '融资融券余额（元）',
  `rzrqyecz` decimal(24,2) DEFAULT NULL COMMENT '融资融券余额差值（元）',
  `update_time` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  `data_source` varchar(100) DEFAULT NULL COMMENT '数据来源',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_trade_date` (`trade_date`),
  KEY `idx_trade_date` (`trade_date`),
  KEY `idx_update_time` (`update_time`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='融资融券余额数据';

-- ---------- [32/56] stock_capital_flow ----------
CREATE TABLE IF NOT EXISTS `stock_capital_flow` (
  `id` int NOT NULL AUTO_INCREMENT,
  `stock_code` varchar(10) NOT NULL COMMENT '股票代码',
  `short_name` varchar(50) DEFAULT NULL COMMENT '股票简称',
  `trade_date` date NOT NULL COMMENT '交易日期',
  `main_net_inflow` decimal(24,4) DEFAULT NULL COMMENT '主力资金净流入(元)',
  `max_net_inflow` decimal(24,4) DEFAULT NULL COMMENT '特大单净流入(元)',
  `lg_net_inflow` decimal(24,4) DEFAULT NULL COMMENT '大单净流入(元)',
  `mid_net_inflow` decimal(24,4) DEFAULT NULL COMMENT '中单净流入(元)',
  `sm_net_inflow` decimal(24,4) DEFAULT NULL COMMENT '小单净流入(元)',
  `update_time` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  `data_source` varchar(100) DEFAULT NULL COMMENT '数据来源',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_stock_date` (`stock_code`,`trade_date`),
  KEY `idx_stock_code` (`stock_code`),
  KEY `idx_trade_date` (`trade_date`),
  KEY `idx_update_time` (`update_time`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='日度资金流向表';

-- ---------- [33/56] stock_code_mapping ----------
CREATE TABLE IF NOT EXISTS `stock_code_mapping` (
  `id` int NOT NULL AUTO_INCREMENT,
  `old_code` varchar(10) NOT NULL COMMENT '变更前代码（北交所新三板时期 43/83/87 等段）',
  `new_code` varchar(10) DEFAULT NULL COMMENT '变更后代码（920 段）；已退市标的为 NULL',
  `short_name` varchar(50) DEFAULT NULL COMMENT '证券简称（北交所名册口径）',
  `list_date` date DEFAULT NULL COMMENT '北交所上市日期（名册口径，非主表 list_date）',
  `change_date` date DEFAULT NULL COMMENT '代码切换生效日；退市标的为摘牌日',
  `status` varchar(20) NOT NULL DEFAULT 'switched' COMMENT 'switched=已切换 920 段 / retired=标的已退市无新代码',
  `match_rule` varchar(50) DEFAULT NULL COMMENT '映射依据：简称归一 / 人工核证 / 官方通报',
  `evidence` varchar(255) DEFAULT NULL COMMENT '映射证据（人工核证与退市项必填）',
  `source` varchar(50) NOT NULL DEFAULT 'bse',
  `data_source` varchar(100) DEFAULT NULL,
  `update_time` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_old_code` (`old_code`),
  KEY `idx_new_code` (`new_code`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='证券代码变更对照（北交所 920 代码切换）';

-- ---------- [34/56] stock_financial_abstract_ths ----------
CREATE TABLE IF NOT EXISTS `stock_financial_abstract_ths` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `stock_code` varchar(10) NOT NULL COMMENT '股票代码',
  `stock_name` varchar(200) NOT NULL COMMENT '股票名称',
  `report_date` date DEFAULT NULL COMMENT '报告期',
  `net_profit` varchar(50) DEFAULT NULL COMMENT '净利润',
  `net_profit_yoy_gr` varchar(50) DEFAULT NULL COMMENT '净利润同比增长率',
  `total_operating_revenue` varchar(50) DEFAULT NULL COMMENT '营业总收入',
  `total_operating_yoy_gr` varchar(50) DEFAULT NULL COMMENT '营业总收入同比增长率',
  `basic_eps` decimal(8,2) DEFAULT NULL COMMENT '基本每股收益(元)',
  `net_asset_ps` decimal(8,2) DEFAULT NULL COMMENT '每股净资产(元)',
  `roe` varchar(50) DEFAULT NULL COMMENT '净资产收益率(%)',
  `update_time` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  `data_source` varchar(100) DEFAULT NULL COMMENT '数据来源',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_stock_report` (`stock_code`,`report_date`),
  KEY `idx_stock_code_report_date` (`stock_code`,`report_date`),
  KEY `idx_stock_code` (`stock_code`),
  KEY `idx_report_date` (`report_date`),
  KEY `idx_update_time` (`update_time`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='股票关键指标';

-- ---------- [35/56] stock_hold_by_fund ----------
CREATE TABLE IF NOT EXISTS `stock_hold_by_fund` (
  `id` int NOT NULL AUTO_INCREMENT,
  `stock_code` varchar(10) NOT NULL COMMENT '股票代码',
  `short_name` varchar(50) DEFAULT NULL COMMENT '股票简称',
  `report_date` date DEFAULT NULL COMMENT '报告期',
  `hldfund_cnt` bigint DEFAULT NULL COMMENT '基金覆盖家数',
  `total_amount` bigint DEFAULT NULL COMMENT '持股总数',
  `total_asset` decimal(24,2) DEFAULT NULL COMMENT '持股总市值',
  `update_time` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  `data_source` varchar(100) DEFAULT NULL COMMENT '数据来源',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_stock_report` (`stock_code`,`report_date`),
  KEY `idx_stockcode` (`stock_code`),
  KEY `idx_update_time` (`update_time`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='股票基金股票重仓信息表';

-- ---------- [36/56] stock_industry_sw ----------
CREATE TABLE IF NOT EXISTS `stock_industry_sw` (
  `id` int NOT NULL AUTO_INCREMENT,
  `stock_code` varchar(10) NOT NULL COMMENT '股票代码',
  `sw_code` varchar(10) DEFAULT NULL COMMENT '申万行业代码',
  `industry_name` varchar(100) DEFAULT NULL COMMENT '行业名称',
  `industry_type` varchar(10) DEFAULT NULL COMMENT '行业类别',
  `source` varchar(100) DEFAULT NULL COMMENT '来源',
  `update_time` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  `data_source` varchar(100) DEFAULT NULL COMMENT '数据来源',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_stock_level` (`stock_code`,`industry_type`),
  KEY `idx_stock_code` (`stock_code`),
  KEY `idx_trade_date` (`industry_name`),
  KEY `idx_update_time` (`update_time`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='股票申万一二级行业信息数据表';

-- ---------- [37/56] stock_info ----------
CREATE TABLE IF NOT EXISTS `stock_info` (
  `id` int NOT NULL AUTO_INCREMENT,
  `stock_code` varchar(10) NOT NULL COMMENT '股票代码',
  `short_name` varchar(50) DEFAULT NULL COMMENT '股票简称',
  `exchange` varchar(5) DEFAULT NULL COMMENT '交易所编码(SH/SZ/BJ)：无现成源字段，按代码前缀推导 60/68→SH、00/30→SZ、43/8x/92→BJ',
  `list_date` date DEFAULT NULL COMMENT '上市日期：本地推断口径 MIN(stock_market_daily.trade_date) 为主；Baostock 官方 ipoDate 仅补空；巨潮整体上市口径弃用',
  `list_status` varchar(10) DEFAULT NULL COMMENT '上市状态（上市/退市，Baostock 来源）',
  `delist_date` date DEFAULT NULL COMMENT '退市日期（Baostock 来源，在市为空）',
  `company_name` varchar(200) DEFAULT NULL COMMENT '公司全称（巨潮）',
  `en_name` varchar(300) DEFAULT NULL COMMENT '英文名称',
  `prev_names` varchar(500) DEFAULT NULL COMMENT '曾用简称',
  `a_short` varchar(50) DEFAULT NULL COMMENT 'A股简称（巨潮披露口径，与 short_name 并存）',
  `b_code` varchar(20) DEFAULT NULL COMMENT 'B股代码',
  `b_short` varchar(50) DEFAULT NULL COMMENT 'B股简称',
  `h_code` varchar(20) DEFAULT NULL COMMENT 'H股代码',
  `h_short` varchar(50) DEFAULT NULL COMMENT 'H股简称',
  `index_members` varchar(500) DEFAULT NULL COMMENT '入选指数（逗号分隔）',
  `market` varchar(30) DEFAULT NULL COMMENT '所属市场（上交所/深交所等）',
  `industry` varchar(50) DEFAULT NULL COMMENT '所属行业（证监会行业分类）',
  `legal_rep` varchar(50) DEFAULT NULL COMMENT '法人代表',
  `reg_capital` varchar(50) DEFAULT NULL COMMENT '注册资金（万元）',
  `establish_date` date DEFAULT NULL COMMENT '成立日期',
  `website` varchar(200) DEFAULT NULL COMMENT '官方网站',
  `email` varchar(200) DEFAULT NULL COMMENT '电子邮箱',
  `phone` varchar(100) DEFAULT NULL COMMENT '联系电话',
  `fax` varchar(100) DEFAULT NULL COMMENT '传真',
  `reg_address` varchar(300) DEFAULT NULL COMMENT '注册地址',
  `office_address` varchar(300) DEFAULT NULL COMMENT '办公地址',
  `postcode` varchar(20) DEFAULT NULL COMMENT '邮政编码',
  `main_business` text COMMENT '主营业务',
  `business_scope` text COMMENT '经营范围',
  `org_intro` text COMMENT '机构简介',
  `profile_updated_at` datetime DEFAULT NULL COMMENT '公司档案最后刷新时间（巨潮日更维护）',
  `update_time` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '最近维护时间（任一采集器刷新该行自动更新）',
  `data_source` varchar(100) DEFAULT 'EM;BAOSTOCK;CNINFO' COMMENT '来源构成（东财名单;Baostock状态;巨潮档案，三源共同维护；列级细分来源见各列注释）',
  PRIMARY KEY (`id`),
  KEY `idx_stock_code` (`stock_code`),
  KEY `idx_exchange` (`exchange`),
  KEY `idx_update_time` (`update_time`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='股票基本信息+公司档案宽表（东财名单/Baostock状态/巨潮档案三源构成：证券核心列+档案24列+管理列）';

-- ---------- [38/56] stock_info_ex ----------
CREATE TABLE IF NOT EXISTS `stock_info_ex` (
  `id` int NOT NULL AUTO_INCREMENT,
  `stock_code` varchar(10) NOT NULL COMMENT '股票代码',
  `short_name` varchar(50) DEFAULT NULL COMMENT '股票简称',
  `is_gxlstock` varchar(50) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci DEFAULT NULL COMMENT '股票分类',
  `exchange` varchar(5) DEFAULT NULL COMMENT '交易所(SZ/SH/BJ)',
  `list_date` date DEFAULT NULL COMMENT '上市日期',
  `update_time` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  `data_source` varchar(100) DEFAULT NULL COMMENT '数据来源',
  PRIMARY KEY (`id`),
  KEY `idx_stock_code` (`stock_code`),
  KEY `idx_exchange` (`exchange`),
  KEY `idx_update_time` (`update_time`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='股票信息拓展表';

-- ---------- [39/56] stock_jgdy_detail ----------
CREATE TABLE IF NOT EXISTS `stock_jgdy_detail` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `stock_code` varchar(10) NOT NULL COMMENT '股票代码',
  `stock_name` varchar(200) NOT NULL COMMENT '股票名称',
  `new` decimal(10,3) DEFAULT NULL COMMENT '最新价',
  `change_pct` decimal(8,4) DEFAULT NULL COMMENT '涨跌幅(%)',
  `received_institution_count` bigint DEFAULT NULL COMMENT '接待机构数量',
  `received_method` text COMMENT '接待方式',
  `receptionist_name` text COMMENT '接待人员',
  `receptionist_place` text COMMENT '接待地点',
  `receptionist_date` date DEFAULT NULL COMMENT '接待日期',
  `announcement_date` date DEFAULT NULL COMMENT '公告日期',
  `update_time` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  `data_source` varchar(100) DEFAULT NULL COMMENT '数据来源',
  PRIMARY KEY (`id`),
  KEY `idx_receptionist_date_stock_code` (`stock_code`,`receptionist_date`),
  KEY `idx_stock_code` (`stock_code`),
  KEY `idx_receptionist_date` (`receptionist_date`),
  KEY `idx_update_time` (`update_time`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='机构调研详细表';

-- ---------- [40/56] stock_market_current ----------
CREATE TABLE IF NOT EXISTS `stock_market_current` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `stock_code` varchar(10) NOT NULL COMMENT '股票代码',
  `stock_name` varchar(200) NOT NULL COMMENT '股票名称',
  `new` decimal(10,3) DEFAULT NULL COMMENT '最新价',
  `change_pct` decimal(8,4) DEFAULT NULL COMMENT '涨跌幅(%)',
  `change_amount` decimal(10,3) DEFAULT NULL COMMENT '涨跌额',
  `volume` bigint DEFAULT NULL COMMENT '成交量(手)',
  `amount` decimal(15,2) DEFAULT NULL COMMENT '成交额(元)',
  `amplitude` decimal(8,4) DEFAULT NULL COMMENT '振幅(%)',
  `high` decimal(10,3) DEFAULT NULL COMMENT '最高价',
  `low` decimal(10,3) DEFAULT NULL COMMENT '最低价',
  `open` decimal(10,3) DEFAULT NULL COMMENT '开盘价',
  `pre_close` decimal(10,3) DEFAULT NULL COMMENT '昨收价',
  `volume_ratio` decimal(8,4) DEFAULT NULL COMMENT '量比',
  `turnover_ratio` decimal(8,4) DEFAULT NULL COMMENT '换手率(%)',
  `dynamic_pe` decimal(24,4) DEFAULT NULL COMMENT '市盈率-动态',
  `pb` decimal(8,4) DEFAULT NULL COMMENT '市净率',
  `total_captital` decimal(15,2) DEFAULT NULL COMMENT '总市值(元)',
  `float_captital` decimal(15,2) DEFAULT NULL COMMENT '流通市值(元)',
  `rise_speed` decimal(8,4) DEFAULT NULL COMMENT '涨速',
  `5m_change_pct` decimal(8,4) DEFAULT NULL COMMENT '5分钟涨跌幅(%)',
  `60d_change_pct` decimal(8,4) DEFAULT NULL COMMENT '60日涨跌幅(%)',
  `ytd_change_pct` decimal(8,4) DEFAULT NULL COMMENT '年初至今涨跌幅(%)',
  `update_time` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  `data_source` varchar(100) DEFAULT NULL COMMENT '数据来源',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_stock_code` (`stock_code`),
  KEY `idx_stock_code` (`stock_code`),
  KEY `idx_update_time` (`update_time`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='股票最新行情数据当前表';

-- ---------- [41/56] stock_market_daily ----------
CREATE TABLE IF NOT EXISTS `stock_market_daily` (
  `id` bigint NOT NULL DEFAULT '0',
  `stock_code` varchar(10) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci NOT NULL COMMENT '股票代码',
  `trade_date` date NOT NULL COMMENT '交易日期',
  `open` decimal(10,3) DEFAULT NULL COMMENT '开盘价',
  `high` decimal(10,3) DEFAULT NULL COMMENT '最高价',
  `low` decimal(10,3) DEFAULT NULL COMMENT '最低价',
  `close` decimal(10,3) DEFAULT NULL COMMENT '收盘价',
  `pre_close` decimal(10,3) DEFAULT NULL COMMENT '昨收价',
  `change_amount` decimal(10,3) DEFAULT NULL COMMENT '涨跌额',
  `change_pct` decimal(12,4) DEFAULT NULL COMMENT '涨跌幅(%)',
  `volume` bigint DEFAULT NULL COMMENT '成交量(手)',
  `amount` decimal(15,2) DEFAULT NULL COMMENT '成交额(元)',
  `turnover_ratio` decimal(8,4) DEFAULT NULL COMMENT '换手率(%)',
  `update_time` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  `data_source` varchar(100) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci DEFAULT NULL COMMENT '数据来源，如aushare，adata',
  `adj_factor` decimal(18,8) DEFAULT NULL COMMENT '后复权累计因子(hfq_close/close)，历史永不变；前复权价=close*adj_factor/latest(adj_factor)',
  UNIQUE KEY `uk_stock_code_trade_date` (`stock_code`,`trade_date`),
  KEY `idx_trade_date` (`trade_date`),
  KEY `idx_breadth_cover` (`stock_code`,`trade_date`,`close`,`change_pct`),
  KEY `idx_update_time` (`update_time`),
  KEY `idx_close` (`close`),
  KEY `idx_change_pct` (`change_pct`),
  KEY `idx_amount` (`amount`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='股票行情历史数据表';

-- ---------- [42/56] stock_market_daily_ex ----------
CREATE TABLE IF NOT EXISTS `stock_market_daily_ex` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `stock_code` varchar(10) NOT NULL COMMENT '股票代码',
  `trade_date` date NOT NULL COMMENT '交易日期',
  `open` decimal(10,3) DEFAULT NULL COMMENT '开盘价',
  `high` decimal(10,3) DEFAULT NULL COMMENT '最高价',
  `low` decimal(10,3) DEFAULT NULL COMMENT '最低价',
  `close` decimal(10,3) DEFAULT NULL COMMENT '收盘价',
  `pre_close` decimal(10,3) DEFAULT NULL COMMENT '昨收价',
  `change_amount` decimal(10,3) DEFAULT NULL COMMENT '涨跌额',
  `change_pct` decimal(8,4) DEFAULT NULL COMMENT '涨跌幅(%)',
  `volume` bigint DEFAULT NULL COMMENT '成交量(手)',
  `amount` decimal(15,2) DEFAULT NULL COMMENT '成交额(元)',
  `turnover_ratio` decimal(8,4) DEFAULT NULL COMMENT '换手率(%)',
  `update_time` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  `data_source` varchar(100) DEFAULT NULL COMMENT '数据来源',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_stock_date` (`stock_code`,`trade_date`),
  KEY `idx_stock_code` (`stock_code`),
  KEY `idx_trade_date` (`trade_date`),
  KEY `idx_update_time` (`update_time`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='股票日K线数据表除权';

-- ---------- [43/56] stock_repurchase ----------
CREATE TABLE IF NOT EXISTS `stock_repurchase` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `stock_code` varchar(10) NOT NULL COMMENT '股票代码',
  `stock_name` varchar(50) DEFAULT NULL COMMENT '股票简称',
  `start_date` date NOT NULL COMMENT '回购起始时间',
  `announce_date` date DEFAULT NULL COMMENT '最新公告日期',
  `progress` varchar(20) DEFAULT NULL COMMENT '实施进度（完成实施/股东大会通过/董事会预案…）',
  `plan_price_low` decimal(12,4) DEFAULT NULL COMMENT '计划回购价格区间-下限',
  `plan_price_high` decimal(12,4) DEFAULT NULL COMMENT '计划回购价格区间-上限',
  `plan_amount_low` decimal(22,2) DEFAULT NULL COMMENT '计划回购金额区间-下限（元）',
  `plan_amount_high` decimal(22,2) DEFAULT NULL COMMENT '计划回购金额区间-上限（元）',
  `plan_pct_low` decimal(12,6) DEFAULT NULL COMMENT '占总股本比例-下限(%)',
  `plan_pct_high` decimal(12,6) DEFAULT NULL COMMENT '占总股本比例-上限(%)',
  `done_amount` decimal(22,2) DEFAULT NULL COMMENT '已回购金额（元）',
  `done_shares` decimal(22,2) DEFAULT NULL COMMENT '已回购股份数量',
  `done_price_low` decimal(12,4) DEFAULT NULL COMMENT '已回购价格区间-下限',
  `done_price_high` decimal(12,4) DEFAULT NULL COMMENT '已回购价格区间-上限',
  `update_time` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `data_source` varchar(100) DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_code_start` (`stock_code`,`start_date`),
  KEY `idx_announce` (`announce_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='股票回购（蓝图D 产业资本态度，2026-09-19 落地）';

-- ---------- [44/56] stock_shares ----------
CREATE TABLE IF NOT EXISTS `stock_shares` (
  `id` int NOT NULL AUTO_INCREMENT,
  `stock_code` varchar(10) NOT NULL COMMENT '股票代码',
  `change_date` date DEFAULT NULL COMMENT '变动时间',
  `total_shares` bigint DEFAULT NULL COMMENT '总股本：股',
  `limit_shares` bigint DEFAULT NULL COMMENT '限售股本：股',
  `list_a_shares` decimal(24,2) DEFAULT NULL COMMENT '流通A股股本：股',
  `change_reason` varchar(1000) DEFAULT NULL COMMENT '变动原因',
  `update_time` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  `data_source` varchar(100) DEFAULT NULL COMMENT '数据来源',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_stock_date` (`stock_code`,`change_date`),
  KEY `idx_stock_code` (`stock_code`),
  KEY `idx_trade_date` (`change_date`),
  KEY `idx_update_time` (`update_time`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='股票股本信息表';

-- ---------- [45/56] table_meta ----------
CREATE TABLE IF NOT EXISTS `table_meta` (
  `table_name` varchar(64) NOT NULL COMMENT '目标表名',
  `category` varchar(20) DEFAULT NULL COMMENT '域分类（行情/指数/概念/日历/资讯/资料/基金/系统/质量等）',
  `source_desc` varchar(255) DEFAULT NULL COMMENT '数据源构成（分号分隔，前端拆标签）',
  `flow_desc` varchar(1000) DEFAULT NULL COMMENT '数据流与清洗口径说明',
  `writers` json DEFAULT NULL COMMENT '维护该表的采集任务名数组（对应 task_config.task_name，无任务为空）',
  `writer_cols` json DEFAULT NULL COMMENT '列级血缘：{任务名:{source,cols,derived,note,col_notes}} 该任务主要负责写哪些列',
  `note` varchar(255) DEFAULT NULL COMMENT '备注（停更/备份/缺口）',
  `update_time` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '元数据维护时间',
  PRIMARY KEY (`table_name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='数据中心表级元数据：数据源/清洗口径/维护任务（数据流卡数据源）';

-- ---------- [46/56] task_config ----------
CREATE TABLE IF NOT EXISTS `task_config` (
  `task_name` varchar(50) NOT NULL COMMENT '任务名',
  `enabled` tinyint(1) DEFAULT '1' COMMENT '是否启用',
  `cron` varchar(50) DEFAULT NULL COMMENT 'cron 表达式',
  `params` json DEFAULT NULL COMMENT '任务参数',
  `update_time` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`task_name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='采集任务配置表';

-- ---------- [47/56] task_runs ----------
CREATE TABLE IF NOT EXISTS `task_runs` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `task_name` varchar(50) NOT NULL COMMENT '任务名',
  `status` varchar(20) NOT NULL COMMENT 'running/success/failed',
  `started_at` datetime NOT NULL COMMENT '开始时间',
  `finished_at` datetime DEFAULT NULL COMMENT '结束时间',
  `records_written` int DEFAULT '0' COMMENT '写入条数',
  `error_message` text COMMENT '错误信息',
  `run_detail` json DEFAULT NULL COMMENT '运行快照(RUN_STEPS 步骤链+当轮实录)（试点：stock_daily_incr 等）',
  PRIMARY KEY (`id`),
  KEY `idx_task_time` (`task_name`,`started_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='数据采集作业记录表';

-- ---------- [48/56] ths_concept_info ----------
CREATE TABLE IF NOT EXISTS `ths_concept_info` (
  `id` int NOT NULL AUTO_INCREMENT,
  `index_code` varchar(20) NOT NULL COMMENT '指数代码',
  `concept_code` varchar(20) NOT NULL COMMENT '概念代码',
  `concept_name` varchar(100) DEFAULT NULL COMMENT '概念名称',
  `source` varchar(10) DEFAULT NULL COMMENT '数据源(ths/east)',
  `update_time` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  `data_source` varchar(100) DEFAULT NULL COMMENT '数据来源',
  PRIMARY KEY (`id`),
  KEY `idx_concept_name` (`concept_name`),
  KEY `idx_source` (`source`),
  KEY `idx_update_time` (`update_time`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='同花顺概念板块信息表';

-- ---------- [49/56] ths_concept_market ----------
CREATE TABLE IF NOT EXISTS `ths_concept_market` (
  `id` int NOT NULL AUTO_INCREMENT,
  `index_code` varchar(20) NOT NULL COMMENT '指数代码',
  `concept_code` varchar(20) NOT NULL COMMENT '概念代码',
  `concept_name` varchar(100) DEFAULT NULL COMMENT '概念名称',
  `trade_date` date NOT NULL COMMENT '交易日期',
  `open` decimal(10,3) DEFAULT NULL COMMENT '开盘价',
  `close` decimal(10,3) DEFAULT NULL COMMENT '收盘价',
  `high` decimal(10,3) DEFAULT NULL COMMENT '最高价',
  `low` decimal(10,3) DEFAULT NULL COMMENT '最低价',
  `volume` bigint DEFAULT NULL COMMENT '成交量(手)',
  `amount` decimal(15,2) DEFAULT NULL COMMENT '成交额(元)',
  `change_amount` decimal(10,3) DEFAULT NULL COMMENT '涨跌额',
  `change_pct` decimal(8,4) DEFAULT NULL COMMENT '涨跌幅(%)',
  `update_time` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  `data_source` varchar(100) DEFAULT NULL COMMENT '数据来源',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_index_date` (`index_code`,`trade_date`),
  KEY `idx_index_code` (`index_code`),
  KEY `idx_trade_date` (`trade_date`),
  KEY `idx_update_time` (`update_time`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='同花顺概念指数K线数据表';

-- ---------- [50/56] ths_stock_concepts ----------
CREATE TABLE IF NOT EXISTS `ths_stock_concepts` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `stock_code` varchar(10) NOT NULL COMMENT '股票代码',
  `short_name` varchar(100) DEFAULT NULL COMMENT '股票简称',
  `index_code` varchar(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci DEFAULT NULL COMMENT '概念代码',
  `concept_name` varchar(100) DEFAULT NULL COMMENT '概念名称',
  `source` varchar(10) DEFAULT NULL COMMENT '数据源(ths/east)',
  `reason` varchar(1000) DEFAULT NULL COMMENT '概念原因	',
  `update_time` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  `data_source` varchar(100) DEFAULT NULL COMMENT '数据来源',
  PRIMARY KEY (`id`),
  KEY `idx_stock_code` (`stock_code`),
  KEY `idx_concept_code` (`index_code`),
  KEY `idx_source` (`source`),
  KEY `idx_update_time` (`update_time`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='同花顺股票概念关系表';

-- ---------- [51/56] ths_stock_dividend ----------
CREATE TABLE IF NOT EXISTS `ths_stock_dividend` (
  `id` int NOT NULL AUTO_INCREMENT,
  `stock_code` varchar(10) NOT NULL COMMENT '股票代码',
  `short_name` varchar(50) DEFAULT NULL COMMENT '股票简称',
  `report_period` varchar(20) DEFAULT NULL COMMENT '报告期',
  `board_date` date DEFAULT NULL COMMENT '董事会日期',
  `shareholders_meeting_date` date DEFAULT NULL COMMENT '股东大会预案公告日期',
  `implementation_date` date DEFAULT NULL COMMENT '实施公告日',
  `dividend_plan_desc` varchar(500) DEFAULT NULL COMMENT '分红方案说明',
  `ashare_record_date` date DEFAULT NULL COMMENT 'A股股权登记日',
  `ashare_ex_date` date DEFAULT NULL COMMENT 'A股除权除息日',
  `dividend_amount_total` varchar(20) DEFAULT NULL COMMENT 'AH分红总金额',
  `plan_progress` varchar(20) DEFAULT NULL COMMENT '方案进度',
  `dividend_payout_ratio` varchar(20) DEFAULT NULL COMMENT '股利支付率',
  `pre_tax_dividend_ratio` varchar(20) DEFAULT NULL COMMENT '税前分红率',
  `update_time` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  `data_source` varchar(100) DEFAULT NULL COMMENT '数据来源',
  PRIMARY KEY (`id`),
  KEY `idx_stockcode_exdate` (`stock_code`,`ashare_ex_date`),
  KEY `idx_ashare_ex_date` (`ashare_ex_date`),
  KEY `idx_update_time` (`update_time`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='股票分红派息信息表';

-- ---------- [52/56] trade_calendar ----------
CREATE TABLE IF NOT EXISTS `trade_calendar` (
  `id` int NOT NULL AUTO_INCREMENT,
  `trade_date` date NOT NULL COMMENT '日期',
  `is_trading_day` tinyint(1) DEFAULT '1' COMMENT '是否交易日(1:是, 0:否)',
  `year` int DEFAULT NULL COMMENT '年份',
  `month` int DEFAULT NULL COMMENT '月份',
  `day` int DEFAULT NULL COMMENT '日',
  `weekday` int DEFAULT NULL COMMENT '星期几(1-7)',
  `update_time` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_trade_date` (`trade_date`),
  KEY `idx_year` (`year`),
  KEY `idx_month` (`month`),
  KEY `idx_weekday` (`weekday`),
  KEY `idx_update_time` (`update_time`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='交易日历表';

-- ---------- [53/56] us_fed_balance_weekly ----------
CREATE TABLE IF NOT EXISTS `us_fed_balance_weekly` (
  `trade_date` date NOT NULL COMMENT '周三水平日（H.4.1 每周四发布、数据截至前一日周三）',
  `total_assets` decimal(20,2) DEFAULT NULL COMMENT '★总资产（百万美元）。DBnomics FED/H41/RESPPMA_N.WW。实测 6,747,704 @2026-09-23。扩表=放水、缩表=收水',
  `securities_total` decimal(20,2) DEFAULT NULL COMMENT '【派生】证券持仓合计（百万美元）= 美债 + 机构债 + MBS（源无单一合计序列，三项相加）',
  `treasury_securities` decimal(20,2) DEFAULT NULL COMMENT '其中·美国国债（百万美元）。FED/H41/RESPPALGUO_N.WW',
  `agency_debt` decimal(20,2) DEFAULT NULL COMMENT '其中·联邦机构债（百万美元）。FED/H41/RESPPALGAM_N.WW（近端已降至 23 亿，QE 遗留）',
  `mbs` decimal(20,2) DEFAULT NULL COMMENT '其中·抵押贷款支持证券 MBS（百万美元）。FED/H41/RESPPALGASMO_N.WW。实测 1,910,409',
  `reverse_repo_total` decimal(20,2) DEFAULT NULL COMMENT '【派生】逆回购合计（百万美元）= 外国官方 + 其他（两项相加）',
  `reverse_repo_foreign` decimal(20,2) DEFAULT NULL COMMENT '其中·外国官方与国际账户（百万美元）。FED/H41/RESPPLLRF_N.WW —— H.4.1 逆回购的绝对主力',
  `reverse_repo_other` decimal(20,2) DEFAULT NULL COMMENT '其中·其他（百万美元，即 ON RRP 主体）。FED/H41/RESPPLLRD_N.WW。实测 461',
  `currency_in_circ` decimal(20,2) DEFAULT NULL COMMENT '流通中货币（百万美元）。FED/H41/RESTBC_N.WW。实测 2,482,383',
  `reserve_balances` decimal(20,2) DEFAULT NULL COMMENT '★准备金余额（百万美元）。FED/H41/RESH4R_N.WW。实测 2,969,922 —— 银行体系可动用的「水位」',
  `other_deposits` decimal(20,2) DEFAULT NULL COMMENT '其他存款性机构存款（百万美元）。FED/H41/RESPPLLDD_N.WW',
  `official_check` decimal(20,2) DEFAULT NULL COMMENT '【交叉校验】美联储官网 H.4.1 发布页 HTML 解析的总资产（百万美元），**仅最新一期有值**。与 total_assets 实测偏差 0 ⇒ DQ 跨通道守护用',
  `update_time` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `data_source` varchar(100) DEFAULT NULL,
  PRIMARY KEY (`trade_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='美国·美联储资产负债表（周度，H.4.1，2002-12-18 起 1241 期）。⭐ 本域「美国数量维度」的核心：总资产 = 美联储放水/收水的总闸门。⚠️ 双轨制：主口径 = DBnomics FED/H41（历史完整）；校验口径 = 美联储官网 H.4.1 发布页 HTML 解析（仅最新一期，official_check 列），实测两通道逐位一致（6,747,704）⇒ 任一通道失效可自动降级且能即时发现。⚠️ 官网当期页只含最近数周、无法用于历史回填，故主口径必须是 DBnomics —— 这是对 v1.0 方案「主口径 = 官网」的实测修正。⚠️ DBnomics 该数据集的部分**已停报细分子项**（_F01~_F12）含 -999999 缺失哨兵（341/1241），本表只取核心汇总序列（实测零哨兵），采集器仍显式过滤哨兵。';

-- ---------- [54/56] us_money_market_daily ----------
CREATE TABLE IF NOT EXISTS `us_money_market_daily` (
  `trade_date` date NOT NULL COMMENT '交易日（三个源日期并集；每列只在各自有值的日期非空）',
  `sofr` decimal(8,4) DEFAULT NULL COMMENT 'SOFR 有担保隔夜融资利率（%）。纽约联储 markets API。实测 3.87 @2026-09-23',
  `sofr_p1` decimal(8,4) DEFAULT NULL COMMENT 'SOFR 1% 分位（%）—— 回购市场尾部利率，与中位数的背离是压力信号',
  `sofr_p25` decimal(8,4) DEFAULT NULL COMMENT 'SOFR 25% 分位（%）',
  `sofr_p75` decimal(8,4) DEFAULT NULL COMMENT 'SOFR 75% 分位（%）',
  `sofr_p99` decimal(8,4) DEFAULT NULL COMMENT 'SOFR 99% 分位（%）—— 与 p1 配合看回购市场尾部风险',
  `sofr_volume_bn` decimal(14,2) DEFAULT NULL COMMENT 'SOFR 成交量（十亿美元）。实测 2,946 —— 量缩说明回购市场在收缩',
  `effr` decimal(8,4) DEFAULT NULL COMMENT 'EFFR 有效联邦基金利率（%）。实测 3.88',
  `effr_target_low` decimal(8,4) DEFAULT NULL COMMENT '联邦基金目标区间下限（%）。实测 3.75',
  `effr_target_high` decimal(8,4) DEFAULT NULL COMMENT '联邦基金目标区间上限（%）。实测 4.00',
  `effr_volume_bn` decimal(14,2) DEFAULT NULL COMMENT 'EFFR 成交量（十亿美元）。实测 101 —— 联邦基金市场已萎缩，主要靠 ON RRP',
  `on_rrp_amt` decimal(20,2) DEFAULT NULL COMMENT 'ON RRP 隔夜逆回购接纳额（**百万美元**，源为美元已除以 1e6）。纽约联储 reverserepo operations。实测 630.0 @2026-09-24（ON RRP 余额已近耗尽）',
  `tga` decimal(20,2) DEFAULT NULL COMMENT '★TGA 财政部一般账户余额（**百万美元**）。美财政部 fiscaldata operating_cash_balance。实测 957,409 @2026-09-23 —— 抽水项：TGA 上升=财政抽走银行准备金',
  `debt_total` decimal(24,2) DEFAULT NULL COMMENT '美国未偿国债总额（**美元**，源单位直存）。fiscaldata debt_to_penny。实测 40.07 万亿 @2026-09-23',
  `update_time` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `data_source` varchar(100) DEFAULT NULL,
  PRIMARY KEY (`trade_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='美国·货币市场日度（纽约联储 SOFR/EFFR/ON RRP + 美财政部 TGA/国债总额）。⭐ 支撑两个关键口径：① 政策利率的**实际成交价**（EFFR 相对目标区间的位置 = 准备金是否充裕）；② 「美元净流动性」合成指标 A5 = 美联储总资产 − TGA − ON RRP（⚠️ 合成口径、非官方，须在 UI 显式标注）。⚠️ 单位不统一且无法统一（源就是这么给的）：利率 %、成交量与 TGA 与 ON RRP **百万美元**、国债总额 **美元** —— 一律以列注释为准，勿凭列名猜。⚠️ 三个源交易日历不同（回购市场 / 联邦基金 / 财政工作日）⇒ 日期是并集、增量水位必须**按列**取。⚠️ NY Fed 的 SOMA 接口实测全 400（路径已变），本表不采 SOMA。';

-- ---------- [55/56] us_money_supply_monthly ----------
CREATE TABLE IF NOT EXISTS `us_money_supply_monthly` (
  `stat_month` date NOT NULL COMMENT '统计月份（源 period 形如 2026-08-31，取当月 1 日）',
  `m2` decimal(20,2) DEFAULT NULL COMMENT '★M2（十亿美元，季调 SA）。DBnomics FED/H6_H6_M2/M2.M。实测 23,342.8 @2026-08',
  `m2_nsa` decimal(20,2) DEFAULT NULL COMMENT 'M2 未季调（十亿美元）。M2_N.M —— 与 SA 的差异是季节性，做同月同比时用 NSA 更准',
  `m1` decimal(20,2) DEFAULT NULL COMMENT '★M1（十亿美元，季调）。M1.M。实测 19,991.1 —— 2020 年口径改革后 M1 含储蓄存款，量级已接近 M2',
  `m1_nsa` decimal(20,2) DEFAULT NULL COMMENT 'M1 未季调（十亿美元）。M1_N.M',
  `monetary_base` decimal(20,2) DEFAULT NULL COMMENT '基础货币（十亿美元，未季调）。FED/H6_H6_MBASE/RESMO14A_N.M。实测 5,411.6',
  `reserve_balances` decimal(20,2) DEFAULT NULL COMMENT '准备金余额（十亿美元）。RESMOB14A_N.M。实测 2,936.0',
  `currency_in_circ` decimal(20,2) DEFAULT NULL COMMENT '流通中货币（十亿美元）。RESMOC14A_N.M。实测 2,475.6',
  `m2_yoy` decimal(8,2) DEFAULT NULL COMMENT '【派生】M2 同比增长（%，同月比，用 NSA 算）',
  `m1_yoy` decimal(8,2) DEFAULT NULL COMMENT '【派生】M1 同比增长（%）',
  `base_yoy` decimal(8,2) DEFAULT NULL COMMENT '【派生】基础货币同比增长（%）',
  `official_m2` decimal(20,2) DEFAULT NULL COMMENT '【交叉校验】美联储官网 H.6 发布页 HTML 解析的 M2（十亿美元），**仅最新一期有值**。DQ 跨通道守护用',
  `update_source` varchar(20) DEFAULT NULL COMMENT '本行 m2 的实际来源：dbnomics / official（主口径失效时自动降级，须能从表里看出来）',
  `update_time` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `data_source` varchar(100) DEFAULT NULL,
  PRIMARY KEY (`stat_month`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='美国·货币供应量（月度，H.6，1959-01 起 812 期）。单位一律**十亿美元**。⚠️ 口径变更：2020-05 起 M1 口径改革（纳入储蓄存款），故 M1 序列在 2020 前后不可直接跨期比绝对值，**一律看同比**。⚠️ 恒等式实测成立：准备金余额 + 流通中货币 = 基础货币（2,936.0 + 2,475.6 = 5,411.6）⇒ 可作 DQ 守护。⚠️ 双轨：主 = DBnomics（历史完整）；校验 = 官网 H.6（仅最新一期）。';

-- ---------- [56/56] user_prefs ----------
CREATE TABLE IF NOT EXISTS `user_prefs` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `owner` varchar(64) NOT NULL,
  `kind` varchar(32) NOT NULL,
  `scope` varchar(128) NOT NULL,
  `payload` json NOT NULL,
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_owner_kind_scope` (`owner`,`kind`,`scope`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='用户偏好（服务端持久化）';

-- ============================================================================
-- 完成：共 56 张表。
-- 下一步：跑 seed 脚本灌入配置，再执行采集器回补业务数据。
--   详见 scripts/data-bootstrap.md
-- ============================================================================
