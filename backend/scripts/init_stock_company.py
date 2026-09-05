#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
InvestBuddy 股票档案域初始化脚本（幂等，可重复执行）

1. stock_info 增补上市状态列（若不存在）：
   - list_status VARCHAR(10)  '上市状态（上市/退市）'
   - delist_date  DATE        '退市日期（在市为空）'
2. 新建公司档案宽表 stock_company_profile（巨潮资讯官方源，26 字段）：
   stock_code 主键唯一，与 stock_info 1:1；文本类档案字段 + 更新时间
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # backend 根

import pymysql

from app.db import get_db_config


def ensure_stock_info_cols(cur) -> None:
    cur.execute(
        """SELECT COUNT(*) FROM information_schema.columns
           WHERE table_schema=DATABASE() AND table_name='stock_info' AND column_name='list_status'"""
    )
    if cur.fetchone()[0] == 0:
        cur.execute(
            "ALTER TABLE stock_info ADD COLUMN list_status VARCHAR(10) NULL "
            "COMMENT '上市状态（上市/退市，Baostock 来源）' AFTER list_date"
        )
        print("stock_info + list_status")
    cur.execute(
        """SELECT COUNT(*) FROM information_schema.columns
           WHERE table_schema=DATABASE() AND table_name='stock_info' AND column_name='delist_date'"""
    )
    if cur.fetchone()[0] == 0:
        cur.execute(
            "ALTER TABLE stock_info ADD COLUMN delist_date DATE NULL "
            "COMMENT '退市日期（Baostock 来源，在市为空）' AFTER list_status"
        )
        print("stock_info + delist_date")


DDL_COMPANY = """
CREATE TABLE IF NOT EXISTS stock_company_profile (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  stock_code VARCHAR(10) NOT NULL COMMENT 'A股代码（6位）',
  company_name VARCHAR(200) NULL COMMENT '公司全称',
  en_name VARCHAR(300) NULL COMMENT '英文名称',
  prev_names VARCHAR(500) NULL COMMENT '曾用简称',
  a_short VARCHAR(50) NULL COMMENT 'A股简称',
  b_code VARCHAR(20) NULL COMMENT 'B股代码',
  b_short VARCHAR(50) NULL COMMENT 'B股简称',
  h_code VARCHAR(20) NULL COMMENT 'H股代码',
  h_short VARCHAR(50) NULL COMMENT 'H股简称',
  index_members VARCHAR(500) NULL COMMENT '入选指数（逗号分隔）',
  market VARCHAR(30) NULL COMMENT '所属市场（上交所/深交所等）',
  industry VARCHAR(50) NULL COMMENT '所属行业（证监会行业分类）',
  legal_rep VARCHAR(50) NULL COMMENT '法人代表',
  reg_capital VARCHAR(50) NULL COMMENT '注册资金（万元）',
  establish_date DATE NULL COMMENT '成立日期',
  list_date DATE NULL COMMENT '上市日期（巨潮口径）',
  website VARCHAR(200) NULL COMMENT '官方网站',
  email VARCHAR(200) NULL COMMENT '电子邮箱',
  phone VARCHAR(100) NULL COMMENT '联系电话',
  fax VARCHAR(100) NULL COMMENT '传真',
  reg_address VARCHAR(300) NULL COMMENT '注册地址',
  office_address VARCHAR(300) NULL COMMENT '办公地址',
  postcode VARCHAR(20) NULL COMMENT '邮政编码',
  main_business TEXT NULL COMMENT '主营业务',
  business_scope TEXT NULL COMMENT '经营范围',
  org_intro TEXT NULL COMMENT '机构简介',
  data_source VARCHAR(50) NULL COMMENT '数据来源（AKSHARE-CNINFO）',
  update_time TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  UNIQUE KEY uk_stock_code (stock_code),
  KEY idx_industry (industry)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='公司档案宽表（巨潮资讯官方源，逐只查询）'
"""


def main() -> None:
    conn = pymysql.connect(**get_db_config().to_dict())
    try:
        with conn.cursor() as cur:
            ensure_stock_info_cols(cur)
            cur.execute(DDL_COMPANY)
            # 存量库幂等补 data_source 列（旧版建表后新增）
            cur.execute(
                """SELECT COUNT(*) FROM information_schema.columns
                   WHERE table_schema=DATABASE() AND table_name='stock_company_profile' AND column_name='data_source'"""
            )
            if cur.fetchone()[0] == 0:
                cur.execute(
                    "ALTER TABLE stock_company_profile ADD COLUMN data_source VARCHAR(50) NULL "
                    "COMMENT '数据来源（AKSHARE-CNINFO）' AFTER org_intro"
                )
        conn.commit()
    finally:
        conn.close()
    print("stock_company_profile ready")


if __name__ == "__main__":
    main()
