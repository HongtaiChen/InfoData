#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
InvestBuddy 「估值水平 / 盈利质量与成长」章节补齐初始化脚本（幂等，可重复执行）

2026-10-20 拆分「估值与盈利」为两个章节后，补齐两个缺失指标的数据原料表：

  1. stock_cash_flow_ths   个股经营活动现金流量净额（按报告期，同花顺现金流量表）
                            → 派生指数级「经营性现金流」及「现金流/净利润含金量」
  2. stock_pb_daily        个股 PB（市净率）日频快照
                            → 派生指数成分股「PB 离散度」（横截面标准差/分位差）

其余 7 个指标（PE-TTM/PB/股息率/ERP/ROE/净利润增速/营收增速）复用已有物化表
env_fundamental_daily / env_fundamental_quarter，无需新表。

⚠️ 三条建表纪律（承接 init_leverage_funding_tables.py，勿回退）：
  (a) 显式声明 COLLATE = utf8mb4_0900_ai_ci（库默认 unicode_ci，JOIN 会报 1267）；
  (b) 幂等：CREATE TABLE IF NOT EXISTS；
  (c) 口径写进列注释（单位、口径差异、停更哨兵）。

运行：
    python backend/scripts/init_valuation_tables.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # backend 根

import pymysql

from app.db import get_db_config

COLLATE = "ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci"

# ---------------------------------------------------------------- 1. 个股经营现金流（按报告期）
DDL_STOCK_CASH_FLOW_THS = f"""
CREATE TABLE IF NOT EXISTS stock_cash_flow_ths (
  stock_code          VARCHAR(10)   NOT NULL COMMENT '股票代码（6 位）',
  report_date         DATE          NOT NULL COMMENT '报告期（季度，如 2026-06-30）',
  operating_cash_flow DECIMAL(20,4) DEFAULT NULL COMMENT '★经营活动产生的现金流量净额（元）。同花顺现金流量表「报告期」口径。⚠️ 累计值（年初至今），非单季值',
  update_time         TIMESTAMP     NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  data_source         VARCHAR(100)  DEFAULT NULL,
  PRIMARY KEY (stock_code, report_date)
) {COLLATE}
COMMENT='个股经营活动现金流量净额（按报告期，同花顺现金流量表）。⭐「经营性现金流」指标原料：逐成分股聚合→指数级现金流趋势；现金流净额/净利润→盈利含金量。⚠️ 净额是累计值（年初至今），跨期需注意口径；源为 stock_financial_cash_ths（akshare）。'
"""

# ---------------------------------------------------------------- 2. 个股 PB 日频快照
DDL_STOCK_PB_DAILY = f"""
CREATE TABLE IF NOT EXISTS stock_pb_daily (
  trade_date   DATE          NOT NULL COMMENT '交易日',
  stock_code   VARCHAR(10)   NOT NULL COMMENT '股票代码（6 位）',
  pb           DECIMAL(12,4) DEFAULT NULL COMMENT '★市净率 PB（倍）。东财个股行情快照「市净率」列（stock_zh_a_spot_em）',
  close        DECIMAL(12,4) DEFAULT NULL COMMENT '收盘价（元），供交叉校验',
  update_time  TIMESTAMP     NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  data_source  VARCHAR(100)  DEFAULT NULL,
  PRIMARY KEY (trade_date, stock_code),
  KEY idx_code (stock_code)
) {COLLATE}
COMMENT='个股 PB（市净率）日频快照。⭐「PB 离散度」指标原料：按 index_constituents 成分股取横截面 PB，算标准差/分位差。⚠️ 仅采集日累积，历史不回溯（源无历史个股 PB 横截面序列）；依赖东财 push2 域，网络恢复后首采。'
"""

TABLES = [
    ("stock_cash_flow_ths", DDL_STOCK_CASH_FLOW_THS),
    ("stock_pb_daily", DDL_STOCK_PB_DAILY),
]

# 物化表补列（幂等 ALTER）：env_fundamental_quarter 加现金流两列、env_fundamental_daily 加 PB 离散度
ALTERS = [
    ("env_fundamental_quarter", "ocf_sum",
     "DECIMAL(20,2) DEFAULT NULL COMMENT '成分股经营活动现金流净额之和（元）。来自 stock_cash_flow_ths 逐成分股聚合'"),
    ("env_fundamental_quarter", "ocf_ratio",
     "DECIMAL(10,4) DEFAULT NULL COMMENT '现金流含金量 = 经营现金流净额之和 / 净利润之和（倍）。>1 利润含金量高，<0 现金流为负'"),
    ("env_fundamental_daily", "pb_dispersion",
     "DECIMAL(10,4) DEFAULT NULL COMMENT '成分股 PB 离散度（横截面标准差）。来自 stock_pb_daily 按 index_constituents 成分股计算'"),
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
            print("补列（幂等 ALTER）：")
            for tbl, col, ddl in ALTERS:
                cur.execute(
                    "SELECT COUNT(*) FROM information_schema.columns "
                    "WHERE table_schema=DATABASE() AND table_name=%s AND column_name=%s",
                    (tbl, col))
                exists = cur.fetchone()[0]
                if not exists:
                    cur.execute(f"ALTER TABLE {tbl} ADD COLUMN {col} {ddl}")
                    print(f"  [已加列] {tbl}.{col}")
                else:
                    print(f"  [已存在] {tbl}.{col}")
            print()
            print("复核：")
            for name, _ in TABLES:
                cur.execute(
                    "SELECT table_collation FROM information_schema.tables "
                    "WHERE table_schema=DATABASE() AND table_name=%s", (name,))
                print(f"  {name:<22} collation={cur.fetchone()[0]}")
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
