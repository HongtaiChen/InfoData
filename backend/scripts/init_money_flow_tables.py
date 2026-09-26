#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
InvestBuddy 货币流动性「数量维度」域初始化脚本（幂等，可重复执行）

落地《货币流动性补源方案 v2.0（无 FRED 密钥版）》§7 批次 A/B/C/D，新建 6 张表：

  1. us_fed_balance_weekly      美国·美联储资产负债表（周度，H.4.1）
  2. us_money_supply_monthly    美国·货币供应量（月度，H.6：M1/M2/基础货币/准备金）
  3. us_money_market_daily      美国·货币市场日度（NY Fed SOFR/EFFR/ON RRP + 财政部 TGA/债务）
  4. eu_money_supply_monthly    欧元区·货币供应量（月度，ECB BSI：M1/M2/M3）
  5. global_liquidity_bis       全球·BIS 全球流动性指标（季度，美元计价跨境信贷）
  6. cn_omo_daily               中国·央行公开市场操作日度（人行「公开市场业务交易公告」）

并给既有 cn_liquidity_monthly 幂等补 M2 **结构分解**列（macro_china_supply_of_money）。

⭐ 2026-09-26 追加（批次 **C2**，见《货币流动性补源方案 v2.1》§11.3）：

  7. cn_reserve_monthly         中国·官方外汇储备（亿美元）+ 黄金储备（万盎司），月度
     —— 中国层「央行对外资产」的**国际可比口径**。⚠️ 新建而非扩列：
        `cn_cb_balance_monthly.fx_reserve`（亿元）是**人民币表内口径**（已有、未停更到 2026-08），
        两者是**三套口径中的两套**，严禁相加、严禁按市价互校（隐含折算率非汇率）。

⚠️ 三条建表纪律（承接 init_liquidity_tables.py，勿回退）：

  (a) **显式声明 COLLATE = utf8mb4_0900_ai_ci**：库默认是 utf8mb4_unicode_ci，
      继承库默认会让新表与业务表 JOIN 报 1267 Illegal mix of collations。

  (b) **幂等**：一律 CREATE TABLE IF NOT EXISTS；重复执行不报错、不丢数据。

  (c) **口径写进列注释**：本域核心风险是「单位」与「哨兵值」——
      百万美元 vs 十亿美元 vs 元、季度末 vs 周三水平、
      以及 **DBnomics 的 -999999 缺失哨兵**（合法数字、SQL 过滤不掉、会污染同比），
      都必须能从列注释直接读到。

⭐ 实测依据（全部 2026-09-25 实跑，脚本留 `_scratch/_impl_probe_*.py`）：

  · DBnomics `FED/H41/RESPPMA_N.WW`  总资产 **6,747,704** @2026-09-23（1241 期，零哨兵）
  · 同一数值与美联储官网 H.4.1 HTML 解析结果**逐位一致** ⇒ 双轨互校成立
  · DBnomics `FED/H6_H6_M2`  M2 **23,342.8** / M1 **19,991.1**（十亿）@2026-08（1959 起 812 期）
  · 恒等式实测成立：准备金 2,936.0 + 流通中货币 2,475.6 = 基础货币 5,411.6
  · DBnomics `ECB/BSI`  欧元区 M3 **17,613,983** / M2 16,436,438 / M1 11,292,842（百万欧元）@2026-07
  · NY Fed  SOFR **3.87%** / EFFR **3.88%** @2026-09-23；ON RRP 逐次操作
  · 美财政部 TGA **957,409**（百万美元）/ 美债总额 **40.07 万亿**（美元）@2026-09-23
  · BIS `WS_GLI` / `Q.USD` **832 行**（2023-Q1~2026-Q1，19 个借款人国家/地区）
  · 人行 OMO 列表页 **191 页 / 3810 条**（实测 190×20+10，192 页 404）；
    翻页真实形式为 `17081-{n}.html`（n=1 最新、**严格单调递减**：n1=2026-09-01~09-24、
    n2=08-05~08-31、n3=07-08~08-04…），而 `?page=N` 与 `index_N.html` **均无效/404**；
    公告 ID 为 19 位且**前 8 位即日期**（`2026092408454713496` → 2026-09-24）
  · 人行 OMO 公告正文类型实测（前 40 条）：常规逆回购 24 / **零操作 14** / 央票 2
    ⇒ 零操作日**仍发公告**、措辞为「**N天期逆回购操作量为零**」（不是「未开展」），必须支持
  · 人行 OMO 详情页目标表实测结构 `['7天','1.40%','515亿元','515亿元']`
    （期限 / 操作利率 / 投标量 / 中标量）⇒ 利率**不在正文、只在表格**，且表索引**不可硬编码**
  · ⚠️ 同日可发多份公告（实测 2026-09-23 既有第187号逆回购、又有第188号香港央票）⇒ 主键须含 op_type

运行：
    python backend/scripts/init_money_flow_tables.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # backend 根

import pymysql

from app.db import get_db_config

COLLATE = "ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci"

# ---------------------------------------------------------------- 1. 美联储资产负债表（周度）
DDL_US_FED_BALANCE = f"""
CREATE TABLE IF NOT EXISTS us_fed_balance_weekly (
  trade_date             DATE          NOT NULL COMMENT '周三水平日（H.4.1 每周四发布、数据截至前一日周三）',
  total_assets           DECIMAL(20,2) DEFAULT NULL COMMENT '★总资产（百万美元）。DBnomics FED/H41/RESPPMA_N.WW。实测 6,747,704 @2026-09-23。扩表=放水、缩表=收水',
  securities_total       DECIMAL(20,2) DEFAULT NULL COMMENT '【派生】证券持仓合计（百万美元）= 美债 + 机构债 + MBS（源无单一合计序列，三项相加）',
  treasury_securities    DECIMAL(20,2) DEFAULT NULL COMMENT '其中·美国国债（百万美元）。FED/H41/RESPPALGUO_N.WW',
  agency_debt            DECIMAL(20,2) DEFAULT NULL COMMENT '其中·联邦机构债（百万美元）。FED/H41/RESPPALGAM_N.WW（近端已降至 23 亿，QE 遗留）',
  mbs                    DECIMAL(20,2) DEFAULT NULL COMMENT '其中·抵押贷款支持证券 MBS（百万美元）。FED/H41/RESPPALGASMO_N.WW。实测 1,910,409',
  reverse_repo_total     DECIMAL(20,2) DEFAULT NULL COMMENT '【派生】逆回购合计（百万美元）= 外国官方 + 其他（两项相加）',
  reverse_repo_foreign   DECIMAL(20,2) DEFAULT NULL COMMENT '其中·外国官方与国际账户（百万美元）。FED/H41/RESPPLLRF_N.WW —— H.4.1 逆回购的绝对主力',
  reverse_repo_other     DECIMAL(20,2) DEFAULT NULL COMMENT '其中·其他（百万美元，即 ON RRP 主体）。FED/H41/RESPPLLRD_N.WW。实测 461',
  currency_in_circ       DECIMAL(20,2) DEFAULT NULL COMMENT '流通中货币（百万美元）。FED/H41/RESTBC_N.WW。实测 2,482,383',
  reserve_balances       DECIMAL(20,2) DEFAULT NULL COMMENT '★准备金余额（百万美元）。FED/H41/RESH4R_N.WW。实测 2,969,922 —— 银行体系可动用的「水位」',
  other_deposits         DECIMAL(20,2) DEFAULT NULL COMMENT '其他存款性机构存款（百万美元）。FED/H41/RESPPLLDD_N.WW',
  official_check         DECIMAL(20,2) DEFAULT NULL COMMENT '【交叉校验】美联储官网 H.4.1 发布页 HTML 解析的总资产（百万美元），**仅最新一期有值**。与 total_assets 实测偏差 0 ⇒ DQ 跨通道守护用',
  update_time            TIMESTAMP     NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  data_source            VARCHAR(100)  DEFAULT NULL,
  PRIMARY KEY (trade_date)
) {COLLATE}
COMMENT='美国·美联储资产负债表（周度，H.4.1，2002-12-18 起 1241 期）。⭐ 本域「美国数量维度」的核心：总资产 = 美联储放水/收水的总闸门。⚠️ 双轨制：主口径 = DBnomics FED/H41（历史完整）；校验口径 = 美联储官网 H.4.1 发布页 HTML 解析（仅最新一期，official_check 列），实测两通道逐位一致（6,747,704）⇒ 任一通道失效可自动降级且能即时发现。⚠️ 官网当期页只含最近数周、无法用于历史回填，故主口径必须是 DBnomics —— 这是对 v1.0 方案「主口径 = 官网」的实测修正。⚠️ DBnomics 该数据集的部分**已停报细分子项**（_F01~_F12）含 -999999 缺失哨兵（341/1241），本表只取核心汇总序列（实测零哨兵），采集器仍显式过滤哨兵。'
"""

# ---------------------------------------------------------------- 2. 美国货币供应量（月度）
DDL_US_MONEY_SUPPLY = f"""
CREATE TABLE IF NOT EXISTS us_money_supply_monthly (
  stat_month          DATE          NOT NULL COMMENT '统计月份（源 period 形如 2026-08-31，取当月 1 日）',
  m2                  DECIMAL(20,2) DEFAULT NULL COMMENT '★M2（十亿美元，季调 SA）。DBnomics FED/H6_H6_M2/M2.M。实测 23,342.8 @2026-08',
  m2_nsa              DECIMAL(20,2) DEFAULT NULL COMMENT 'M2 未季调（十亿美元）。M2_N.M —— 与 SA 的差异是季节性，做同月同比时用 NSA 更准',
  m1                  DECIMAL(20,2) DEFAULT NULL COMMENT '★M1（十亿美元，季调）。M1.M。实测 19,991.1 —— 2020 年口径改革后 M1 含储蓄存款，量级已接近 M2',
  m1_nsa              DECIMAL(20,2) DEFAULT NULL COMMENT 'M1 未季调（十亿美元）。M1_N.M',
  monetary_base       DECIMAL(20,2) DEFAULT NULL COMMENT '基础货币（十亿美元，未季调）。FED/H6_H6_MBASE/RESMO14A_N.M。实测 5,411.6',
  reserve_balances    DECIMAL(20,2) DEFAULT NULL COMMENT '准备金余额（十亿美元）。RESMOB14A_N.M。实测 2,936.0',
  currency_in_circ    DECIMAL(20,2) DEFAULT NULL COMMENT '流通中货币（十亿美元）。RESMOC14A_N.M。实测 2,475.6',
  m2_yoy              DECIMAL(8,2)  DEFAULT NULL COMMENT '【派生】M2 同比增长（%，同月比，用 NSA 算）',
  m1_yoy              DECIMAL(8,2)  DEFAULT NULL COMMENT '【派生】M1 同比增长（%）',
  base_yoy            DECIMAL(8,2)  DEFAULT NULL COMMENT '【派生】基础货币同比增长（%）',
  official_m2         DECIMAL(20,2) DEFAULT NULL COMMENT '【交叉校验】美联储官网 H.6 发布页 HTML 解析的 M2（十亿美元），**仅最新一期有值**。DQ 跨通道守护用',
  update_source       VARCHAR(20)   DEFAULT NULL COMMENT '本行 m2 的实际来源：dbnomics / official（主口径失效时自动降级，须能从表里看出来）',
  update_time         TIMESTAMP     NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  data_source         VARCHAR(100)  DEFAULT NULL,
  PRIMARY KEY (stat_month)
) {COLLATE}
COMMENT='美国·货币供应量（月度，H.6，1959-01 起 812 期）。单位一律**十亿美元**。⚠️ 口径变更：2020-05 起 M1 口径改革（纳入储蓄存款），故 M1 序列在 2020 前后不可直接跨期比绝对值，**一律看同比**。⚠️ 恒等式实测成立：准备金余额 + 流通中货币 = 基础货币（2,936.0 + 2,475.6 = 5,411.6）⇒ 可作 DQ 守护。⚠️ 双轨：主 = DBnomics（历史完整）；校验 = 官网 H.6（仅最新一期）。'
"""

# ---------------------------------------------------------------- 3. 美国货币市场日度
DDL_US_MONEY_MARKET = f"""
CREATE TABLE IF NOT EXISTS us_money_market_daily (
  trade_date          DATE          NOT NULL COMMENT '交易日（三个源日期并集；每列只在各自有值的日期非空）',
  sofr                DECIMAL(8,4)  DEFAULT NULL COMMENT 'SOFR 有担保隔夜融资利率（%）。纽约联储 markets API。实测 3.87 @2026-09-23',
  sofr_p1             DECIMAL(8,4)  DEFAULT NULL COMMENT 'SOFR 1% 分位（%）—— 回购市场尾部利率，与中位数的背离是压力信号',
  sofr_p25            DECIMAL(8,4)  DEFAULT NULL COMMENT 'SOFR 25% 分位（%）',
  sofr_p75            DECIMAL(8,4)  DEFAULT NULL COMMENT 'SOFR 75% 分位（%）',
  sofr_p99            DECIMAL(8,4)  DEFAULT NULL COMMENT 'SOFR 99% 分位（%）—— 与 p1 配合看回购市场尾部风险',
  sofr_volume_bn      DECIMAL(14,2) DEFAULT NULL COMMENT 'SOFR 成交量（十亿美元）。实测 2,946 —— 量缩说明回购市场在收缩',
  effr                DECIMAL(8,4)  DEFAULT NULL COMMENT 'EFFR 有效联邦基金利率（%）。实测 3.88',
  effr_target_low     DECIMAL(8,4)  DEFAULT NULL COMMENT '联邦基金目标区间下限（%）。实测 3.75',
  effr_target_high    DECIMAL(8,4)  DEFAULT NULL COMMENT '联邦基金目标区间上限（%）。实测 4.00',
  effr_volume_bn      DECIMAL(14,2) DEFAULT NULL COMMENT 'EFFR 成交量（十亿美元）。实测 101 —— 联邦基金市场已萎缩，主要靠 ON RRP',
  on_rrp_amt          DECIMAL(20,2) DEFAULT NULL COMMENT 'ON RRP 隔夜逆回购接纳额（**百万美元**，源为美元已除以 1e6）。纽约联储 reverserepo operations。实测 630.0 @2026-09-24（ON RRP 余额已近耗尽）',
  tga                 DECIMAL(20,2) DEFAULT NULL COMMENT '★TGA 财政部一般账户余额（**百万美元**）。美财政部 fiscaldata operating_cash_balance。实测 957,409 @2026-09-23 —— 抽水项：TGA 上升=财政抽走银行准备金',
  debt_total          DECIMAL(24,2) DEFAULT NULL COMMENT '美国未偿国债总额（**美元**，源单位直存）。fiscaldata debt_to_penny。实测 40.07 万亿 @2026-09-23',
  update_time         TIMESTAMP     NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  data_source         VARCHAR(100)  DEFAULT NULL,
  PRIMARY KEY (trade_date)
) {COLLATE}
COMMENT='美国·货币市场日度（纽约联储 SOFR/EFFR/ON RRP + 美财政部 TGA/国债总额）。⭐ 支撑两个关键口径：① 政策利率的**实际成交价**（EFFR 相对目标区间的位置 = 准备金是否充裕）；② 「美元净流动性」合成指标 A5 = 美联储总资产 − TGA − ON RRP（⚠️ 合成口径、非官方，须在 UI 显式标注）。⚠️ 单位不统一且无法统一（源就是这么给的）：利率 %、成交量与 TGA 与 ON RRP **百万美元**、国债总额 **美元** —— 一律以列注释为准，勿凭列名猜。⚠️ 三个源交易日历不同（回购市场 / 联邦基金 / 财政工作日）⇒ 日期是并集、增量水位必须**按列**取。⚠️ NY Fed 的 SOMA 接口实测全 400（路径已变），本表不采 SOMA。'
"""

# ---------------------------------------------------------------- 4. 欧元区货币供应量（月度）
DDL_EU_MONEY_SUPPLY = f"""
CREATE TABLE IF NOT EXISTS eu_money_supply_monthly (
  stat_month   DATE          NOT NULL COMMENT '统计月份（源 period 形如 2026-07，取当月 1 日）',
  m3           DECIMAL(20,2) DEFAULT NULL COMMENT '★M3（百万欧元，工作日与季节调整）。DBnomics ECB/BSI/M.U2.Y.V.M30.X.1.U2.2300.Z01.E。实测 17,613,983 @2026-07',
  m2           DECIMAL(20,2) DEFAULT NULL COMMENT '★M2（百万欧元）。M20。实测 16,436,438',
  m1           DECIMAL(20,2) DEFAULT NULL COMMENT '★M1（百万欧元）。M10。实测 11,292,842',
  m3_yoy       DECIMAL(8,2)  DEFAULT NULL COMMENT '【派生】M3 同比增长（%）',
  m2_yoy       DECIMAL(8,2)  DEFAULT NULL COMMENT '【派生】M2 同比增长（%）',
  m1_yoy       DECIMAL(8,2)  DEFAULT NULL COMMENT '【派生】M1 同比增长（%）',
  update_time  TIMESTAMP     NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  data_source  VARCHAR(100)  DEFAULT NULL,
  PRIMARY KEY (stat_month)
) {COLLATE}
COMMENT='欧元区·货币供应量（月度，ECB BSI，1980-01 起 559 期）。⭐ 欧元区是全球第二大货币区，「欧元区 M2/M3 增速 vs 美国」是判断全球美元/欧元流动性相对松紧的直接读数。⚠️ 唯一免费通道：ECB 直连三域实测**全部阻断**（data-api.ecb.europa.eu / data.ecb.europa.eu / sdw-wsrest.ecb.europa.eu 均 SSL timeout 或 DNS 失败），本表经 DBnomics 镜像取得 ⇒ **源单一、必须做「失效即告警」**（DQ 含 freshness 规则）。⚠️ 序列口径为「Euro area (changing composition)」= 按当期成员国组成回溯，与固定组成口径有差异。'
"""

# ---------------------------------------------------------------- 5. BIS 全球流动性（季度）
DDL_GLOBAL_LIQUIDITY_BIS = f"""
CREATE TABLE IF NOT EXISTS global_liquidity_bis (
  id                BIGINT        NOT NULL AUTO_INCREMENT,
  time_period       DATE          NOT NULL COMMENT '统计期（季度末，源为 2026-Q1 形式 → 2026-03-31）',
  freq              VARCHAR(4)    NOT NULL DEFAULT 'Q' COMMENT '频率（Q=季度）',
  curr_denom        VARCHAR(8)    NOT NULL COMMENT '计价货币（USD）—— 本表只采 USD 口径，即「全球美元信贷」',
  borrowers_cty     VARCHAR(8)    NOT NULL COMMENT '借款人国家/地区代码（BIS 自有码，非 ISO2）。实测 19 个：US/CN/IN/MX/ID/RU/SA/TR/AR/CL/BR… 及 4T/3C/4U/4Y 等聚合组',
  borrowers_sector  VARCHAR(4)    NOT NULL COMMENT '借款人部门：N=非银行 / G=政府 / P=私人',
  lenders_sector    VARCHAR(4)    NOT NULL COMMENT '贷款方部门：B=银行 / A=全部',
  l_pos_type        VARCHAR(4)    NOT NULL COMMENT '头寸类型：I=国际(international) / A=全部',
  l_instr           VARCHAR(4)    NOT NULL COMMENT '工具：B=银行贷款 / D=债券 / G=合计',
  unit_measure      VARCHAR(8)    NOT NULL COMMENT '计量单位：USD=百万美元 / 771=占比(%)',
  obs_value         DECIMAL(24,4) DEFAULT NULL COMMENT '读数。unit_measure=USD 时为百万美元',
  title             VARCHAR(255)  DEFAULT NULL COMMENT '源侧英文口径全称（BIS 原文，保留供口径溯源）',
  update_time       TIMESTAMP     NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  data_source       VARCHAR(100)  DEFAULT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uk_gliquidity (time_period, curr_denom, borrowers_cty, borrowers_sector,
                            lenders_sector, l_pos_type, l_instr, unit_measure),
  KEY idx_period_cty (time_period, borrowers_cty)
) {COLLATE}
COMMENT='全球·BIS 全球流动性指标（WS_GLI，季度）。⭐ 这是「全球美元有多少」的**官方口径**，替代 v1.0 原计划的自建全球 M2 汇总（那需要拼 N 国 × 汇率换算，且非官方）。核心用法：`BORROWERS_CTY != US` 的美元信贷 = **离岸美元**（回答「美元流出美国了没有」）。⚠️ 实测 API 形式（与 v1.0/v2.0 文档结论**相反**，已勘误）：resource key 只有 **3 段** `FREQ.CURR_DENOM.BORROWERS_CTY`（如 `Q.USD`、`Q.USD.US`），**多给一段即 404**；且 `/all` 形式实测 **404**。⚠️ BIS 的借款人国家用自有码（US/CN/4T…），不是 ISO2 —— `Q.USD.JP`、`Q.USD.GB` 均 404。⚠️ 美国借款人（Q.USD.US）只报 G/P 两个部门、**无 N（非银行）**，故「离岸美元」不能简单用「全量 − 美国」推导，须按 borrowers_cty 直接筛非美组。⚠️ 季度数据、T+1 季发布，时效天然滞后。'
"""

# ---------------------------------------------------------------- 6. 中国·央行公开市场操作（日度）
DDL_CN_OMO_DAILY = f"""
CREATE TABLE IF NOT EXISTS cn_omo_daily (
  section      VARCHAR(24)   NOT NULL DEFAULT 'omo_trade' COMMENT '来源栏目。⚠️ **各栏目各自独立编号**（同一年「交易公告第1号」与「买断式第1号」并存）⇒ 主键必须含本列。取值：omo_trade=公开市场业务交易公告（7天期逆回购 + 香港央票，日度）/ outright_repo=公开市场买断式逆回购业务公告（月度，2024-10 起）',
  notice_year  SMALLINT      NOT NULL COMMENT '公告年份（人行**按年重新编号**，故「年份+序号」才唯一）。与 section、notice_no 共同构成主键',
  notice_no    INT           NOT NULL COMMENT '公告编号 = 标题 [2026]第189号 中的 189。与 section、notice_year 共同构成主键',
  trade_date   DATE          DEFAULT NULL COMMENT '操作日：正文日期优先，其次中文落款日期，最后取公告 ID 前 8 位。⚠️ **刻意不作主键** —— 人行存在**批量补发**（实测 2025-10-09 这一个发布日下挂了 17 条公告、2025-11-14 挂了 27 条），且买断式公告是**招标预告**（发布日 9-14、操作日 9-15）⇒ 「发布日 ≠ 操作日」是常态',
  op_type      VARCHAR(24)   NOT NULL DEFAULT 'reverse_repo' COMMENT '操作类型：reverse_repo=7天期逆回购 / outright_reverse_repo=买断式逆回购 / cbb=央行票据(香港) / treasury_deposit=国库现金定存 / other（含现券买断等）',
  tenor_days   INT           DEFAULT NULL COMMENT '期限（天）。7=7天期逆回购；买断式 91/181；香港央票 91/182。来源：公告表格（按**列名**定位，不可硬编码表索引）或正文「期限为6个月（181天）」',
  op_rate      DECIMAL(8,4)  DEFAULT NULL COMMENT '操作利率 / 中标利率（%）。⚠️ 逆回购的该值**不在正文、只在公告表格里**；买断式是「利率招标、多重价位中标」故**无单一利率**（本列为 NULL）。2024-07 起逆回购为「固定利率、数量招标」',
  bid_amount   DECIMAL(18,4) DEFAULT NULL COMMENT '投标量（亿元）。「固定利率、数量招标」下与中标量相等（全额满足一级交易商需求）',
  win_amount   DECIMAL(18,4) DEFAULT NULL COMMENT '★中标量 / 操作量（亿元）。⚠️ 零操作日**也发公告**、本列 = 0（不是 NULL）—— 措辞为「N天期逆回购操作量为零」；NULL 才表示源侧没给',
  notice_url   VARCHAR(255)  DEFAULT NULL COMMENT '公告详情页 URL（溯源用）',
  raw_text     VARCHAR(500)  DEFAULT NULL COMMENT '首个含操作信息的正文句（原文留档，供口径复核）',
  update_time  TIMESTAMP     NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  data_source  VARCHAR(100)  DEFAULT NULL,
  PRIMARY KEY (section, notice_year, notice_no),
  KEY idx_date (trade_date),
  KEY idx_type_date (op_type, trade_date)
) {COLLATE}
COMMENT='中国·央行公开市场操作（人行「公开市场业务」栏目群）。⭐ 中国层数量维度的**短端抓手**：M2 是月度存量、OMO 是日度流量。⚠️ 现采 2 个栏目：①`omo_trade` 公开市场业务交易公告（7天期逆回购，日度，约 3800 条；含香港央票）②`outright_repo` 公开市场买断式逆回购业务公告（2024-10 起，月度，39 条）—— 两者**各自独立编号**、且**都叫「公告」但业务完全不同**。⚠️ 已知未采的同级栏目（按需再补）：公开市场国债买卖业务公告、中央国库现金管理业务公告、中央银行票据业务公告、央行票据互换(CBS)、互换便利(SFISF)、其他业务公告(SLO)。⚠️ 两种公告 ID 形式并存：19 位时间戳型（2025-10-09 改版后，前 8 位=日期）与 7 位纯序号型（改版前，**不含日期**）。⚠️ 翻页 modulekey **不一定是数字**（交易公告=17081，买断式=b0da893b 这种 UUID）⇒ 必须从页面动态解析。⚠️ 只有事实列、**没有净投放/未到期余额**：净投放需按期限滚动推算到期日，口径选择权属分析层，刻意不在采集层固化。'
"""

# ---------------------------------------------------------------- 7. 中国·官方外储 / 黄金（月度）
# 批次 C2（v2.1 §11.3）：中国层储备的**国际可比口径**。
# ⚠️ 新建而非扩列的三条理由（v2.1 §11.3.3）：
#   ① 单位与语义不同轴（亿美元 / 万盎司 ≠ 亿元表内科目）；
#   ② cn_cb_balance_monthly 有**资产负债表恒等式 DQ**（总资产=总负债），塞入不同口径的列会污染该约束语义；
#   ③ 独立表便于独立设口径守护，未来加 SDR 计价 / 黄金市价折算不牵动主干。
DDL_CN_RESERVE_MONTHLY = f"""
CREATE TABLE IF NOT EXISTS cn_reserve_monthly (
  stat_month          DATE          NOT NULL COMMENT '统计月份（源 统计时间 形如 2026.8 → 2026-08-01）',
  fx_reserve_usd      DECIMAL(18,2) DEFAULT NULL COMMENT '★国家外汇储备（亿美元，官方口径）。源 akshare macro_china_foreign_exchange_gold。实测 34,383.25 @2026-08。⚠️ 中国层「外储」是三套口径之一，与 cn_cb_balance_monthly.fx_reserve（亿元、历史成本口径）严禁相加、严禁按市价互校',
  fx_reserve_usd_yoy  DECIMAL(10,2) DEFAULT NULL COMMENT '【派生】外储同比增长（%，同月比，本表自算）。两期都存在且基期 >0 才算；早期量级极小故同比噪声大，UI 不用',
  gold_reserve_oz     DECIMAL(18,2) DEFAULT NULL COMMENT '★黄金储备（万盎司，实物量）。实测 7,673 @2026-08',
  gold_reserve_oz_chg DECIMAL(18,2) DEFAULT NULL COMMENT '【派生】较上月增减（万盎司；**正 = 增持**）。⚠️ 未增持月该值是常数而非 0 变动，故「连续增持 N 月」必须判 chg<>0，不能按月序连续推断',
  update_time         TIMESTAMP     NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  data_source         VARCHAR(100)  DEFAULT NULL,
  PRIMARY KEY (stat_month)
) {COLLATE}
COMMENT='中国·官方外汇储备与黄金储备（月度，akshare macro_china_foreign_exchange_gold，1978-12 起 419 期）。⭐ 中国层「央行对外资产」的**国际可比口径**：外储环比变动 ≈ 估值效应（汇率/金价/债价）+ 交易变动；黄金实物量支撑「央行储备多元化/去美元化」叙事。⚠️ 中国层储备有**三套口径、三种单位，严禁相加、严禁互校**：① 本表 fx_reserve_usd（亿美元·官方）② 本表 gold_reserve_oz（万盎司·实物量）③ cn_cb_balance_monthly.fx_reserve（亿元·货币当局资产负债表「国外资产」的人民币表内口径）。实测「人民币口径 ÷ 美元口径」的隐含折算率 24 期由 6.886 **单调降至** 6.318（同期市场汇率约 7.0~7.3）⇒ 它是**历史成本/复合折算而非汇率**，按市价互校会**稳定误报** ⇒ 互校只保留区间 [6.0,7.4] + 月度变动 <2%，且**放在采集器内断言**（DQ 检查器是单表+where、无 JOIN，跨表校验写不成规则）。⚠️ 源 `统计时间` 是字符串 `YYYY.M`：**字符串序 ≠ 时间序**（`2025.10 < 2025.2`）⇒ 采集器按 `(年,月)` 元组排序（公共件 `parse_cn_month`），**禁止 `iloc[-1]` 取最新**。⚠️ 早期量级极端（1980-12 外储 −12.96 亿美元）⇒ 量级 DQ 只守 2015 年后。'
"""

# 幂等列迁移：cn_liquidity_monthly 补 M2 结构分解列
# （源：akshare macro_china_supply_of_money，实测 584 行、1978.1 起，比既有表长 30 年、多 5 组结构列）
_CN_MIGRATIONS: list[tuple[str, str]] = [
    ("quasi_money", "货币和准货币（广义货币M2）的准货币部分"),
    ("demand_deposit", "活期存款"),
    ("time_deposit", "定期存款"),
    ("savings_deposit", "储蓄存款"),
    ("other_deposit", "其他存款"),
]
COLUMN_MIGRATIONS: list[tuple[str, str, str]] = []
for _col, _cn in _CN_MIGRATIONS:
    COLUMN_MIGRATIONS.append((
        "cn_liquidity_monthly", _col,
        f"ADD COLUMN {_col} DECIMAL(20,2) DEFAULT NULL "
        f"COMMENT '{_cn}（亿元）。源 macro_china_supply_of_money。⚠️ 源侧结构列**停更时点不齐**（实测 2026-09-25）：M2/准货币/其他存款仍到最新，而活期·定期存款停在 2025-05、储蓄存款停在 2024-12 —— 不是采集失败，是源口径调整' AFTER rrr_effective_date",
    ))
    COLUMN_MIGRATIONS.append((
        "cn_liquidity_monthly", f"{_col}_yoy",
        f"ADD COLUMN {_col}_yoy DECIMAL(8,2) DEFAULT NULL COMMENT '{_cn}同比（%）。⚠️ 源侧同比列比数量列停得更早，近端多为空' "
        f"AFTER {_col}",
    ))


def ensure_column(cur, table: str, column: str, ddl_fragment: str) -> bool:
    """幂等补列 → True=本次新增，False=已存在"""
    cur.execute(
        "SELECT COUNT(*) FROM information_schema.columns "
        "WHERE table_schema=DATABASE() AND table_name=%s AND column_name=%s",
        (table, column))
    if cur.fetchone()[0]:
        return False
    cur.execute(f"ALTER TABLE {table} {ddl_fragment}")
    return True


TABLES = [
    ("us_fed_balance_weekly", DDL_US_FED_BALANCE),
    ("us_money_supply_monthly", DDL_US_MONEY_SUPPLY),
    ("us_money_market_daily", DDL_US_MONEY_MARKET),
    ("eu_money_supply_monthly", DDL_EU_MONEY_SUPPLY),
    ("global_liquidity_bis", DDL_GLOBAL_LIQUIDITY_BIS),
    ("cn_omo_daily", DDL_CN_OMO_DAILY),
    ("cn_reserve_monthly", DDL_CN_RESERVE_MONTHLY),
]


def main() -> None:
    conn = pymysql.connect(**get_db_config().to_dict())
    created, existed = [], []
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT @@collation_database AS c")
            print(f"库默认排序规则: {cur.fetchone()[0]}（本脚本对每张表显式写 utf8mb4_0900_ai_ci）")
            print()
            for name, ddl in TABLES:
                cur.execute(
                    "SELECT COUNT(*) FROM information_schema.tables "
                    "WHERE table_schema=DATABASE() AND table_name=%s", (name,))
                before = cur.fetchone()[0]
                cur.execute(ddl)
                if before:
                    existed.append(name)
                    print(f"  [已存在] {name}")
                else:
                    created.append(name)
                    print(f"  [已创建] {name}")
            print()
            print("列迁移（cn_liquidity_monthly 补 M2 结构分解）：")
            added_n = 0
            for table, column, frag in COLUMN_MIGRATIONS:
                added = ensure_column(cur, table, column, frag)
                added_n += int(added)
                print(f"  {'[已补列]' if added else '[列已存在]'} {table}.{column}")
            print()
            print("复核：")
            cur.execute(
                "SELECT table_name, table_collation FROM information_schema.tables "
                "WHERE table_schema=DATABASE() AND table_name IN (%s)"
                % ",".join(["%s"] * len(TABLES)), [t for t, _ in TABLES])
            for r in cur.fetchall():
                print(f"  {r[0]:<28} collation={r[1]}")
            for t in ("us_fed_balance_weekly", "us_money_market_daily", "global_liquidity_bis",
                      "cn_omo_daily", "cn_reserve_monthly"):
                cur.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema=DATABASE() AND table_name=%s ORDER BY ordinal_position", (t,))
                cols = [r[0] for r in cur.fetchall()]
                print(f"  {t} 列({len(cols)}) = {cols}")
            cur.execute(
                "SELECT COUNT(*) FROM information_schema.columns "
                "WHERE table_schema=DATABASE() AND table_name='cn_liquidity_monthly'")
            print(f"  cn_liquidity_monthly 现列数 = {cur.fetchone()[0]}（补了 {added_n} 列）")
        conn.commit()
    finally:
        conn.close()
    print()
    print(f"完成：新建 {len(created)} 张 {created}；已存在 {len(existed)} 张")


if __name__ == "__main__":
    main()
