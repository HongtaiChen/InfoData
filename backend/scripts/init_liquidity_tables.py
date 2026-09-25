#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
InvestBuddy 货币流动性域初始化脚本（幂等，可重复执行）

落地「货币流动性观测体系」（docs/design/货币流动性观测体系设计_2026-09-25.md）批次 2，
新建 4 张表：

  1. cn_liquidity_monthly      中国货币与信用月度（M0/M1/M2 + 信贷 + 社融 + 准备金率）
  2. cn_cb_balance_monthly     中国央行资产负债表月度（28 科目）
  3. cb_policy_rate            多国央行政策利率（决议事件表：美/欧/日/英）
  4. global_usd_index_daily    美元指数日频（新浪官方日线为主 + 自算交叉校验 + 实时快照标定）

⚠️ 本脚本同时承担**幂等列迁移**（见 `ensure_column`）：`CREATE TABLE IF NOT EXISTS`
   对已存在的表不会补列，故新增列必须显式走 ALTER + information_schema 判存，
   否则「本地已有该表」的环境永远拿不到新列（本项目已被此坑绊过）。

⚠️ 三条建表纪律（本项目已踩过的坑，勿回退）：

  (a) **显式声明 CHARACTER SET / COLLATE = utf8mb4_0900_ai_ci**。
      库默认排序规则是 utf8mb4_unicode_ci，而多数业务表是 utf8mb4_0900_ai_ci；
      自建临时表/新表若继承库默认，与业务表 JOIN 会报
      `1267 Illegal mix of collations`。所以本脚本每张表都写死 collation。

  (b) **幂等**：一律 CREATE TABLE IF NOT EXISTS；重复执行不报错、不丢数据。

  (c) **口径写进列注释**：本域的关键风险是「单位」与「时效」——
      亿元 vs 元、% vs 小数、源停更 vs 真没有，都必须能从列注释/表注释直接读到，
      否则下游一定会用错（本项目 currency_boc_daily 的「每 100 外币 ÷100 归一」
      就是靠列注释守住的）。

运行：
    python backend/scripts/init_liquidity_tables.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # backend 根

import pymysql

from app.db import get_db_config

COLLATE = "ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci"

# ---------------------------------------------------------------- 1. 中国货币与信用月度
DDL_CN_LIQUIDITY = f"""
CREATE TABLE IF NOT EXISTS cn_liquidity_monthly (
  stat_month            DATE         NOT NULL COMMENT '统计月份（取当月 1 日）',
  m2                    DECIMAL(20,2) DEFAULT NULL COMMENT 'M2 数量（亿元）',
  m2_yoy                DECIMAL(8,2)  DEFAULT NULL COMMENT 'M2 同比增长（%）',
  m2_mom                DECIMAL(8,2)  DEFAULT NULL COMMENT 'M2 环比增长（%）',
  m1                    DECIMAL(20,2) DEFAULT NULL COMMENT 'M1 数量（亿元）',
  m1_yoy                DECIMAL(8,2)  DEFAULT NULL COMMENT 'M1 同比增长（%）',
  m1_mom                DECIMAL(8,2)  DEFAULT NULL COMMENT 'M1 环比增长（%）',
  m0                    DECIMAL(20,2) DEFAULT NULL COMMENT 'M0 流通中现金数量（亿元）',
  m0_yoy                DECIMAL(8,2)  DEFAULT NULL COMMENT 'M0 同比增长（%）',
  m0_mom                DECIMAL(8,2)  DEFAULT NULL COMMENT 'M0 环比增长（%）',
  m1_m2_gap             DECIMAL(8,2)  DEFAULT NULL COMMENT '【派生】M1同比 − M2同比（剪刀差，pp）。负值走阔=资金活化度下降（钱往定期走）',
  credit_month          DECIMAL(20,2) DEFAULT NULL COMMENT '当月新增人民币贷款（亿元）',
  credit_cum            DECIMAL(20,2) DEFAULT NULL COMMENT '年内累计新增人民币贷款（亿元）',
  credit_yoy            DECIMAL(8,2)  DEFAULT NULL COMMENT '当月新增人民币贷款同比（%）',
  shrzgm                DECIMAL(20,2) DEFAULT NULL COMMENT '社会融资规模增量（亿元）',
  shrzgm_rmb_loan       DECIMAL(20,2) DEFAULT NULL COMMENT '社融分项·人民币贷款（亿元）',
  shrzgm_fx_loan        DECIMAL(20,2) DEFAULT NULL COMMENT '社融分项·委托贷款外币贷款（亿元）',
  shrzgm_entrust        DECIMAL(20,2) DEFAULT NULL COMMENT '社融分项·委托贷款（亿元）',
  shrzgm_trust          DECIMAL(20,2) DEFAULT NULL COMMENT '社融分项·信托贷款（亿元）',
  shrzgm_undiscounted   DECIMAL(20,2) DEFAULT NULL COMMENT '社融分项·未贴现银行承兑汇票（亿元）',
  shrzgm_ent_bond       DECIMAL(20,2) DEFAULT NULL COMMENT '社融分项·企业债券（亿元）',
  shrzgm_equity         DECIMAL(20,2) DEFAULT NULL COMMENT '社融分项·非金融企业境内股票融资（亿元）',
  rrr_large             DECIMAL(8,2)  DEFAULT NULL COMMENT '【派生·顺延】大型金融机构法定存款准备金率（%），取该月末生效值',
  rrr_small             DECIMAL(8,2)  DEFAULT NULL COMMENT '【派生·顺延】中小金融机构法定存款准备金率（%）',
  rrr_effective_date    DATE          DEFAULT NULL COMMENT '当前生效准备金率的生效日（用于判断"距上次调整多久"）',
  update_time           TIMESTAMP     NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  data_source           VARCHAR(100)  DEFAULT NULL,
  PRIMARY KEY (stat_month)
) {COLLATE}
COMMENT='中国货币与信用月度（M0/M1/M2 数量+同比+环比 / 新增信贷 / 社融增量+分项 / 法定准备金率）。⚠️ 两条派生行为：① m1_m2_gap 两列相减；② rrr_* 由「准备金率调整事件表」按月末顺延填充（源是 58 条事件、非月度序列）。其余列均为源值直存。⚠️ 社融 shrzgm* 来自商务数据中心，源侧更新滞后（实测 2026-09-25 时最新仅到 2026-04），故本表以 M1/M2 与新增信贷为主力指标、社融为辅。'
"""

# ---------------------------------------------------------------- 2. 央行资产负债表
DDL_CN_CB_BALANCE = f"""
CREATE TABLE IF NOT EXISTS cn_cb_balance_monthly (
  stat_month              DATE          NOT NULL COMMENT '统计月份（源为 2026.8 形式，取当月 1 日）',
  foreign_assets          DECIMAL(20,2) DEFAULT NULL COMMENT '国外资产（亿元）',
  fx_reserve              DECIMAL(20,2) DEFAULT NULL COMMENT '外汇（亿元）—— 外汇占款，被动投放渠道',
  monetary_gold           DECIMAL(20,2) DEFAULT NULL COMMENT '货币黄金（亿元）',
  other_foreign_assets    DECIMAL(20,2) DEFAULT NULL COMMENT '其他国外资产（亿元）',
  claims_gov              DECIMAL(20,2) DEFAULT NULL COMMENT '对政府债权（亿元）',
  claims_central_gov      DECIMAL(20,2) DEFAULT NULL COMMENT '其中：中央政府（亿元）',
  claims_other_dep_banks  DECIMAL(20,2) DEFAULT NULL COMMENT '★对其他存款性公司债权（亿元）—— MLF/逆回购/PSL 投放余额，扩张=央行在主动放水',
  claims_other_fin_cos    DECIMAL(20,2) DEFAULT NULL COMMENT '对其他金融性公司债权（亿元）',
  claims_non_monetary     DECIMAL(20,2) DEFAULT NULL COMMENT '对非货币金融机构债权（亿元，1993~2000 期口径）',
  claims_non_fin_cos      DECIMAL(20,2) DEFAULT NULL COMMENT '对非金融性公司债权（亿元，历史口径）',
  other_assets            DECIMAL(20,2) DEFAULT NULL COMMENT '其他资产（亿元）',
  total_assets            DECIMAL(20,2) DEFAULT NULL COMMENT '总资产（亿元）',
  reserve_money           DECIMAL(20,2) DEFAULT NULL COMMENT '储备货币（亿元）= 基础货币',
  currency_issue          DECIMAL(20,2) DEFAULT NULL COMMENT '发行货币（亿元）',
  fin_cos_deposit         DECIMAL(20,2) DEFAULT NULL COMMENT '金融性公司存款（亿元）',
  other_dep_banks_dep     DECIMAL(20,2) DEFAULT NULL COMMENT '其他存款性公司存款（亿元）',
  other_fin_cos_dep       DECIMAL(20,2) DEFAULT NULL COMMENT '其他金融性公司存款（亿元，历史口径）',
  fin_liab                DECIMAL(20,2) DEFAULT NULL COMMENT '对金融机构负债（亿元，历史口径）',
  reserve_deposit         DECIMAL(20,2) DEFAULT NULL COMMENT '准备金存款（亿元，历史口径）',
  non_fin_cos_dep         DECIMAL(20,2) DEFAULT NULL COMMENT '非金融性公司存款（亿元，历史口径）',
  demand_deposit          DECIMAL(20,2) DEFAULT NULL COMMENT '活期存款（亿元，历史口径）',
  bonds                   DECIMAL(20,2) DEFAULT NULL COMMENT '发行债券（亿元）',
  foreign_liab            DECIMAL(20,2) DEFAULT NULL COMMENT '国外负债（亿元）',
  gov_deposit             DECIMAL(20,2) DEFAULT NULL COMMENT '政府存款（亿元）—— 财政存款，抽水/放水项',
  own_capital             DECIMAL(20,2) DEFAULT NULL COMMENT '自有资金（亿元）',
  other_liab              DECIMAL(20,2) DEFAULT NULL COMMENT '其他负债（亿元）',
  total_liab              DECIMAL(20,2) DEFAULT NULL COMMENT '总负债（亿元）',
  update_time             TIMESTAMP     NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  data_source             VARCHAR(100)  DEFAULT NULL,
  PRIMARY KEY (stat_month)
) {COLLATE}
COMMENT='中国人民银行资产负债表月度（28 科目，源 356 期，1993.3 起）。⭐ 本域最有价值的单项新增：中国没有官方「QE 规模」公告，只能从资产负债表倒推——「对其他存款性公司债权」即 MLF/逆回购/PSL 等主动投放工具余额，其扩张/收缩就是中国式扩表/缩表。⚠️ 纯源值镜像表，**不存派生列**（同比/环比在分析层按需算；避免派生值与源值不同步）。⚠️ 部分科目为历史口径（1993~2000 期），近端为 NULL。'
"""

# ---------------------------------------------------------------- 3. 多国央行政策利率
DDL_CB_POLICY_RATE = f"""
CREATE TABLE IF NOT EXISTS cb_policy_rate (
  id           BIGINT       NOT NULL AUTO_INCREMENT,
  country_code VARCHAR(8)   NOT NULL COMMENT '国家/地区代码（US/EU/JP/UK）',
  country_name VARCHAR(20)  DEFAULT NULL COMMENT '国家/地区中文名',
  central_bank VARCHAR(30)  DEFAULT NULL COMMENT '央行名（美联储/欧洲央行/日本央行/英国央行）',
  event_date   DATE         NOT NULL COMMENT '决议日（源侧发布日期）',
  rate         DECIMAL(8,4) DEFAULT NULL COMMENT '决议后政策利率（%）',
  prev_rate    DECIMAL(8,4) DEFAULT NULL COMMENT '决议前政策利率（%）',
  change_bp    DECIMAL(8,2) DEFAULT NULL COMMENT '本次变动（bp，负数=降息）',
  update_time  TIMESTAMP    NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  data_source  VARCHAR(100) DEFAULT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uk_country_date (country_code, event_date),
  KEY idx_event_date (event_date)
) {COLLATE}
COMMENT='多国央行政策利率决议（美/欧/日/英，事件表：只在决议日有行，非日频顺延）。⚠️【源时效风险·实测 2026-09-25】akshare 的 macro_bank_*_interest_rate **整族**接口最后非空「今值」停在 2025-07~08（美 2025-07-31=4.5 / 欧 2025-07-24=2.15 / 日 2025-07-31=0.5 / 英 2025-08-07=4.0），表内最后一行日期在 2025-09~10 但今值为空。这是上游（经济日历源）整体停更，**换接口无用**（全族 11 个 macro_bank_* 同病）。⇒ 本表可用作「历史与方向共振」，**不可当作当前利率**；下游必须用 MAX(event_date) 标出「源更新至」而不是默认它就是最新。若上游恢复更新，本表自动跟上。⚠️ 只存 rate 非空的**有效决议**行（源侧未公布的占位行不入库）。'
"""

# ---------------------------------------------------------------- 4. 美元指数
DDL_GLOBAL_USD_INDEX = f"""
CREATE TABLE IF NOT EXISTS global_usd_index_daily (
  trade_date   DATE          NOT NULL COMMENT '交易日期（两个源的日期**并集**：dxy_sina 走全球汇市日历、dxy_calc 走人民币中间价中国工作日历；每列只在各自有值的日期非空）',
  dxy_sina     DECIMAL(10,4) DEFAULT NULL COMMENT '★美元指数官方日线收盘（ICE 口径）。源：新浪 NewForexService.getDayKLine?symbol=DINIW（1985-11-08 起，实测 10573 行、列序 日期,开,低,高,收）。与官方实时快照偏差仅 0.004% ⇒ 本列为主口径',
  dxy_calc     DECIMAL(10,4) DEFAULT NULL COMMENT '自算美元指数（ICE DXY 标准公式 × 6 成分货币交叉汇率，源为人民币中间价宽表）。⚠️ 已降级为**交叉校验**列：与 dxy_sina 实测 2380 个共有日 |偏差| 中位 0.28% / p90 0.74% / p99 1.43% / 最大 2.29%（带符号均值仅 +0.074%，因正负相抵而低估离散度，勿当精度引用）；成因是机制差——中间价每日 9:15 定盘、基于前一交易日篮子，趋势日系统性滞后约 1 天',
  dxy_snapshot DECIMAL(10,4) DEFAULT NULL COMMENT '新浪 DINIW 实时快照（仅采集当日写入，作标定基准；需带 Referer 否则 403）',
  eur_usd      DECIMAL(12,6) DEFAULT NULL COMMENT '成分货币交叉汇率 欧元/美元（由人民币中间价反算）',
  usd_jpy      DECIMAL(12,6) DEFAULT NULL COMMENT '成分货币交叉汇率 美元/日元',
  gbp_usd      DECIMAL(12,6) DEFAULT NULL COMMENT '成分货币交叉汇率 英镑/美元',
  usd_cad      DECIMAL(12,6) DEFAULT NULL COMMENT '成分货币交叉汇率 美元/加元',
  usd_sek      DECIMAL(12,6) DEFAULT NULL COMMENT '成分货币交叉汇率 美元/瑞典克朗',
  usd_chf      DECIMAL(12,6) DEFAULT NULL COMMENT '成分货币交叉汇率 美元/瑞士法郎',
  update_time  TIMESTAMP     NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  data_source  VARCHAR(100)  DEFAULT NULL,
  PRIMARY KEY (trade_date)
) {COLLATE}
COMMENT='美元指数日频。★主口径 = dxy_sina（新浪官方日线，ICE 口径，1985-11-08 起 10573 行）；辅口径 = dxy_calc（人民币中间价交叉汇率自算，2016-12 起 2380 行，仅作交叉校验）+ dxy_snapshot（实时快照，标定用）。实测（2026-09-25）：dxy_sina 收盘 101.2632 vs 官方快照 101.2675 偏差 -0.004%；dxy_calc 101.3532 偏差 +0.085%。两口径互比：2380 个共有日 |偏差| 中位 0.28% / p90 0.74% / p99 1.43% / 最大 2.29%（带符号均值 +0.074% 会低估离散度，勿当精度引用）—— 成因是机制差：中间价每日 9:15 定盘、基于前一交易日篮子，趋势日系统性滞后约 1 天（实例 2022-09-23 英国迷你预算日官方日线跳升 1.6% 而自算几乎不动）。⚠️ 勘误（2026-09-25）：设计文档初稿曾记「自算偏差 -3.09%、中间价口径有约 ±3% 系统性偏离」，该结论实为标价法单位错误所致（把间接标价的瑞典克朗当直接标价，USDSEK 算成 4.58 而真值 9.94，DXY 因此偏低 3.3%）；修正后偏差 <0.1%。⚠️ 官方历史源复测（2026-09-25）：「东财 push2his 域名被拦」仍成立（全族含编号镜像与 http 均 RemoteDisconnected），新浪 gi 接口仍不支持 UDI/DINIW，外盘期货 DX 仍只返 13 行（2019 陈年）；**但新浪外汇 jsonp 的 getDayKLine 通道可用**，即 dxy_sina 的来源。6 个成分货币交叉汇率一并存表，便于口径溯源复核。'
"""

# 幂等列迁移：CREATE TABLE IF NOT EXISTS 不会给已存在的表补列，必须显式判存 + ALTER，
# 否则「本地已在跑该表」的环境永远拿不到新列（本项目已被此坑绊过：列不存在 → 采集器静默丢数据）。
COLUMN_MIGRATIONS: list[tuple[str, str, str]] = [
    # (表名, 列名, ALTER 片段)
    ("global_usd_index_daily", "dxy_sina",
     "ADD COLUMN dxy_sina DECIMAL(10,4) DEFAULT NULL "
     "COMMENT '★美元指数官方日线收盘（ICE 口径）。源：新浪 NewForexService.getDayKLine?symbol=DINIW"
     "（1985-11-08 起，实测 10573 行、列序 日期,开,低,高,收）。与官方实时快照偏差仅 0.004% ⇒ 本列为主口径' "
     "AFTER trade_date"),
]


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
    ("cn_liquidity_monthly", DDL_CN_LIQUIDITY),
    ("cn_cb_balance_monthly", DDL_CN_CB_BALANCE),
    ("cb_policy_rate", DDL_CB_POLICY_RATE),
    ("global_usd_index_daily", DDL_GLOBAL_USD_INDEX),
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
            # 幂等列迁移（给已存在的表补新列）
            print()
            print("列迁移：")
            for table, column, frag in COLUMN_MIGRATIONS:
                added = ensure_column(cur, table, column, frag)
                print(f"  {'[已补列]' if added else '[列已存在]'} {table}.{column}")
            # 复核：确认建出来的 collation 与业务表一致
            cur.execute(
                "SELECT table_name, table_collation, table_comment FROM information_schema.tables "
                "WHERE table_schema=DATABASE() AND table_name IN (%s)"
                % ",".join(["%s"] * len(TABLES)), [t for t, _ in TABLES])
            print()
            print("复核：")
            for r in cur.fetchall():
                print(f"  {r[0]:<24} collation={r[1]}")
            cur.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema=DATABASE() AND table_name='global_usd_index_daily' "
                "ORDER BY ordinal_position")
            print(f"  global_usd_index_daily 列 = {[r[0] for r in cur.fetchall()]}")
        conn.commit()
    finally:
        conn.close()
    print()
    print(f"完成：新建 {len(created)} 张 {created}；已存在 {len(existed)} 张 {existed}")


if __name__ == "__main__":
    main()
