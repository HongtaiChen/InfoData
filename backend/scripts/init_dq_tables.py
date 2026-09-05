#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
InvestBuddy 数据质量体检 · 表结构（幂等）
用法：python scripts/init_dq_tables.py
- 创建 dq_rules（规则配置）与 dq_report（每日体检结果）
- CREATE TABLE IF NOT EXISTS 重复执行安全
- 完成后可再执行 scripts/seed_dq_rules.py 写入 28 条首批规则
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
]


def main():
    conn = pymysql.connect(**get_db_config().to_dict())
    try:
        with conn.cursor() as cur:
            for d in DDL:
                cur.execute(d)
        conn.commit()
        with conn.cursor() as cur:
            cur.execute("SHOW TABLES LIKE 'dq%'")
            print("init ok:", cur.fetchall())
    finally:
        conn.close()


if __name__ == "__main__":
    main()