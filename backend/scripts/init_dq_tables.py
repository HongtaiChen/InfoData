#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
InvestBuddy 数据质量体检 · 表结构（幂等）
用法：python scripts/init_dq_tables.py
- 创建 dq_rules（规则配置）与 dq_report（每日体检结果）
- v2（2026-09-10）：dq_rules 增 `rule_group`（daily/weekly 分组执行，weekly 走独立低频任务）；
  新增 `dq_gap_detail`（全史疑似缺口明细，供 L3 修复闭环消费）
- CREATE TABLE IF NOT EXISTS + 信息模式判断后 ALTER ADD，重复执行安全
- 完成后可再执行 scripts/seed_dq_rules.py 写入规则
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.db import get_db_config  # noqa: E402
import pymysql  # noqa: E402

DDL = [
    """
    CREATE TABLE IF NOT EXISTS dq_rules (
      id INT AUTO_INCREMENT PRIMARY KEY,
      rule_name VARCHAR(80) NOT NULL COMMENT '规则名（唯一）',
      table_name VARCHAR(80) NOT NULL COMMENT '被检查表',
      check_type VARCHAR(30) NOT NULL COMMENT '检查器类型',
      rule_group VARCHAR(10) NOT NULL DEFAULT 'daily' COMMENT '规则组 daily/weekly（weekly 走独立低频任务）',
      params JSON NULL COMMENT '检查参数',
      severity VARCHAR(10) NOT NULL DEFAULT 'warning' COMMENT '严重级 warning/critical/info',
      enabled TINYINT(1) NOT NULL DEFAULT 1 COMMENT '是否启用',
      description VARCHAR(255) NULL COMMENT '规则说明',
      created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
      update_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
      UNIQUE KEY uk_rule_name (rule_name)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='数据质量规则配置表'
    """,
    """
    CREATE TABLE IF NOT EXISTS dq_report (
      id BIGINT AUTO_INCREMENT PRIMARY KEY,
      run_at DATETIME NOT NULL COMMENT '本轮体检执行时间',
      run_date DATE NOT NULL COMMENT '体检日期（按天冗余）',
      rule_id INT NULL COMMENT '规则 id',
      rule_name VARCHAR(80) NOT NULL COMMENT '规则名',
      table_name VARCHAR(80) NOT NULL COMMENT '被检查表',
      check_type VARCHAR(30) NOT NULL COMMENT '检查器类型',
      severity VARCHAR(10) NOT NULL DEFAULT 'warning' COMMENT '严重级（冗余规则当时值）',
      status VARCHAR(10) NOT NULL COMMENT 'pass/warning/fail/error',
      metric_value VARCHAR(200) NULL COMMENT '实测值',
      message VARCHAR(500) NULL COMMENT '判定明细/期望说明',
      KEY idx_run_at (run_at),
      KEY idx_run_date (run_date),
      KEY idx_rule (rule_name)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='数据质量体检结果表'
    """,
    """
    CREATE TABLE IF NOT EXISTS dq_gap_detail (
      id BIGINT AUTO_INCREMENT PRIMARY KEY,
      run_at DATETIME NOT NULL COMMENT '本轮体检时间',
      run_date DATE NOT NULL COMMENT '体检日期',
      stock_code VARCHAR(10) NOT NULL COMMENT '股票代码',
      prev_date DATE NOT NULL COMMENT '前一笔记录交易日',
      next_date DATE NOT NULL COMMENT '后一笔记录交易日',
      gap_days INT NOT NULL COMMENT '两笔间隔自然日',
      risk VARCHAR(10) NOT NULL DEFAULT 'suspect' COMMENT 'high(>=365 天)/suspect',
      status VARCHAR(10) NOT NULL DEFAULT 'open' COMMENT 'open/fixed/ignored（L3 修复闭环状态）',
      note VARCHAR(255) NULL COMMENT '备注（停牌/退市/已补等）',
      created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
      KEY idx_run_date (run_date),
      KEY idx_code (stock_code),
      KEY idx_status (status)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='全史疑似缺口明细（L1 gap_scan 产出 → L3 消费）'
    """,
    """
    CREATE TABLE IF NOT EXISTS dq_recon_detail (
      id BIGINT AUTO_INCREMENT PRIMARY KEY,
      run_at DATETIME NOT NULL COMMENT '本轮对账时间',
      run_date DATE NOT NULL COMMENT '对账日期',
      mode VARCHAR(10) NOT NULL COMMENT 'window(滚动窗口)/sample(全史抽样)',
      stock_code VARCHAR(10) NOT NULL COMMENT '股票代码',
      trade_date DATE NULL COMMENT '差异日期（sample 模式为 NULL）',
      diff_type VARCHAR(20) NOT NULL COMMENT 'missing_local/missing_remote/value_diff/count_diff/range_diff/sum_diff',
      col_name VARCHAR(20) NULL COMMENT '差异字段（value_diff 时）',
      local_value VARCHAR(50) NULL COMMENT '本地值',
      remote_value VARCHAR(50) NULL COMMENT '远端值',
      remote_source VARCHAR(20) NULL COMMENT '对账源 tencent/eastmoney',
      note VARCHAR(255) NULL COMMENT '备注（除权豁免等）',
      created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
      KEY idx_run_date (run_date),
      KEY idx_code (stock_code),
      KEY idx_mode (mode)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='外部对账差异明细（L2 recon 产出）'
    """,
]

# 幂等补列（老库升级）：列不存在才 ALTER ADD
ADD_COLUMNS = [
    ("dq_rules", "rule_group",
     "ALTER TABLE dq_rules ADD COLUMN rule_group VARCHAR(10) NOT NULL DEFAULT 'daily' "
     "COMMENT '规则组 daily/weekly（weekly 走独立低频任务）' AFTER check_type"),
]


def _has_column(cur, table: str, col: str) -> bool:
    cur.execute(
        "SELECT COUNT(*) FROM information_schema.columns "
        "WHERE table_schema = DATABASE() AND table_name = %s AND column_name = %s",
        (table, col),
    )
    return cur.fetchone()[0] > 0


def main():
    conn = pymysql.connect(**get_db_config().to_dict())
    try:
        with conn.cursor() as cur:
            for d in DDL:
                cur.execute(d)
            for table, col, ddl in ADD_COLUMNS:
                if not _has_column(cur, table, col):
                    cur.execute(ddl)
                    print(f"  + {table}.{col} 已补充")
        conn.commit()
        with conn.cursor() as cur:
            cur.execute("SHOW TABLES LIKE 'dq%'")
            print("init ok:", [r[0] for r in cur.fetchall()])
    finally:
        conn.close()


if __name__ == "__main__":
    main()
