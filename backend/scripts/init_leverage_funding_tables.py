#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
InvestBuddy 「杠杆与资金行为」域初始化脚本（幂等，可重复执行）

落地「杠杆与资金行为」章节（技术面分析，2026-10-20 新增）两张物化表：

  1. etf_share_daily        全市场股票型 ETF 份额与净流入（日度，采集器逐日积累）
  2. stock_account_monthly  上交所股票账户新开户（月度，官方源 + 东财历史拼接）

融资余额占比不建表：分子 securities_margin.rzye（已有）+ 分母流通市值
（stock_shares.list_a_shares × stock_market_daily.close）在分析模块现算，
无新表需求。

⚠️ 三条建表纪律（承接 init_money_flow_tables.py / init_liquidity_tables.py，勿回退）：
  (a) 显式声明 COLLATE = utf8mb4_0900_ai_ci（库默认 unicode_ci，JOIN 会报 1267）；
  (b) 幂等：CREATE TABLE IF NOT EXISTS；
  (c) 口径写进列注释（单位、口径差异、停更哨兵）。

运行：
    python backend/scripts/init_leverage_funding_tables.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # backend 根

import pymysql

from app.db import get_db_config

COLLATE = "ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci"

# ---------------------------------------------------------------- 1. ETF 份额与净流入（日度）
DDL_ETF_SHARE_DAILY = f"""
CREATE TABLE IF NOT EXISTS etf_share_daily (
  trade_date     DATE          NOT NULL COMMENT '交易日',
  total_share    DECIMAL(24,4) DEFAULT NULL COMMENT '★全市场股票型 ETF 总份额（份）。Σ 各只股票型 ETF 当日收盘清算份额。⚠️ 交易所官方源可回溯历史（2019 起）',
  net_inflow     DECIMAL(18,4) DEFAULT NULL COMMENT '★当日净流入（元）= Σ(Δ份额 × 净值)。Δ份额=当日份额−上一交易日份额；正=净申购/流入、负=净赎回/流出。⚠️ 仅覆盖采集启动后的日变化（历史价不可回溯）',
  fund_count     INT           DEFAULT NULL COMMENT '当日纳入统计的股票型 ETF 只数（口径：全市场股票ETF，含跨境/商品，不含债券/货币）',
  update_time    TIMESTAMP     NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  data_source    VARCHAR(100)  DEFAULT NULL,
  PRIMARY KEY (trade_date)
) {COLLATE}
COMMENT='全市场股票型 ETF 份额与净流入（日度）。⭐ 「ETF 资金流向」的原料：净流入=份额变化×净值。⚠️ 口径=全市场股票型 ETF（含跨境/商品，排除债券/货币）。⚠️ 2026-10-23 起主源切交易所官方双源（上交所 fund_etf_scale_sse + 深交所 fund_scale_daily_szse），历史可回溯（backfill_etf_share.py 回补 2019 起）；净流入仅采集启动后逐日积累（历史价不可回溯）。'
"""

# ---------------------------------------------------------------- 2. 上交所股票账户新开户（月度）
DDL_STOCK_ACCOUNT_MONTHLY = f"""
CREATE TABLE IF NOT EXISTS stock_account_monthly (
  stat_month     DATE          NOT NULL COMMENT '统计月份（当月 1 日）',
  person         DECIMAL(12,4) DEFAULT NULL COMMENT '★股票账户新开户·个人总数（万户）。上交所官方口径 COMMON_SSE_TZZ_M_STOCK_ACCT_C.PERSON',
  org            DECIMAL(12,4) DEFAULT NULL COMMENT '股票账户新开户·机构总数（万户）。ORG',
  a_person       DECIMAL(12,4) DEFAULT NULL COMMENT '★其中 A 股·个人（万户）。A_PERSON —— 主展示列（A股个人新开户）',
  a_org          DECIMAL(12,4) DEFAULT NULL COMMENT '其中 A 股·机构（万户）。A_ORG',
  b_person       DECIMAL(12,4) DEFAULT NULL COMMENT '其中 B 股·个人（万户）。B_PERSON（量级极小，参考）',
  b_org          DECIMAL(12,4) DEFAULT NULL COMMENT '其中 B 股·机构（万户）。B_ORG',
  total_person   DECIMAL(14,4) DEFAULT NULL COMMENT '累计总户数·个人（万户）。TERM=累计总户数 行的 PERSON',
  total_a_person DECIMAL(14,4) DEFAULT NULL COMMENT '累计总户数·A股个人（万户）。累计行的 A_PERSON',
  source_note    VARCHAR(200)  DEFAULT NULL COMMENT '口径来源标注：SSE=上交所官方（2023 至今）/ EM=东财全市场历史（2015~2023，⚠️ 与上交所口径不同，仅作背景趋势）',
  update_time    TIMESTAMP     NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  data_source    VARCHAR(100)  DEFAULT NULL,
  PRIMARY KEY (stat_month)
) {COLLATE}
COMMENT='上交所股票账户新开户（月度，2015-04 起）。⭐ 「新增开户」的原料：散户开户节奏是情绪极值读数（开户峰值≈顶部、冰点≈底部）。⚠️ 双口径拼接：① 上交所官方接口 COMMON_SSE_TZZ_M_STOCK_ACCT_C（2023 至今，只返回当前月报当年 1-12 月+累计，逐月采集积累）② 东财 stock_account_statistics_em（2015-04~2023-08，全市场口径）。⚠️ 两者口径不同（上交所单所 vs 全市场），严禁直接续接同一序列，source_note 必须标注来源，UI 分两段展示。'
"""

TABLES = [
    ("etf_share_daily", DDL_ETF_SHARE_DAILY),
    ("stock_account_monthly", DDL_STOCK_ACCOUNT_MONTHLY),
]


def main() -> None:
    conn = pymysql.connect(**get_db_config().to_dict())
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
                print(f"  {'[已存在]' if before else '[已创建]'} {name}")
            print()
            print("复核：")
            for name, _ in TABLES:
                cur.execute(
                    "SELECT table_collation FROM information_schema.tables "
                    "WHERE table_schema=DATABASE() AND table_name=%s", (name,))
                print(f"  {name:<26} collation={cur.fetchone()[0]}")
                cur.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema=DATABASE() AND table_name=%s ORDER BY ordinal_position", (name,))
                cols = [r[0] for r in cur.fetchall()]
                print(f"    列({len(cols)}) = {cols}")
        conn.commit()
    finally:
        conn.close()
    print()
    print("完成")


if __name__ == "__main__":
    main()
