#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
InvestBuddy 股票档案域初始化脚本（幂等，可重复执行）

v1.4（2026-09-05）：stock_company_profile 已与 stock_info 物理合并为宽表（用户决策）。
本脚本不再创建独立档案表，改为确保 stock_info 上档案列齐全：
1. stock_info 上市状态列（若不存在）：
   - list_status VARCHAR(10)  '上市状态（上市/退市）'
   - delist_date  DATE        '退市日期（在市为空）'
2. stock_info 档案列 25 个（巨潮官方 24 字段 + profile_updated_at 管理列）：
   company_name/en_name/prev_names/a_short/b_code/b_short/h_code/h_short/index_members/
   market/industry/legal_rep/reg_capital/establish_date/website/email/phone/fax/
   reg_address/office_address/postcode/main_business/business_scope/org_intro
   （列定义与 scripts/merge_stock_company.py 的 _ARCHIVE_COLS 为唯一权威，此处复用）

历史备份：旧表 stock_company_profile_bak_20260905 保留作回滚点，确认稳定后手动 DROP。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # backend 根

import pymysql

from app.db import get_db_config

# 复用 merge 脚本的档案列权威定义（同目录 import，运行期 scripts 目录在 sys.path）
from merge_stock_company import _ARCHIVE_COLS


def ensure_stock_info_cols(cur) -> None:
    """list_status / delist_date 若缺失则补（v1.3 曾由本脚本负责）"""
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


def ensure_archive_cols(cur) -> int:
    """补齐 stock_info 档案列，返回本次新增列数"""
    cur.execute(
        """SELECT column_name FROM information_schema.columns
           WHERE table_schema=DATABASE() AND table_name='stock_info'"""
    )
    exist = {r[0] for r in cur.fetchall()}
    adds = [f"ADD COLUMN {c} {ddl}" for c, ddl in _ARCHIVE_COLS.items() if c not in exist]
    if adds:
        cur.execute("ALTER TABLE stock_info " + ", ".join(adds))
    return len(adds)


def main() -> None:
    conn = pymysql.connect(**get_db_config().to_dict())
    try:
        with conn.cursor() as cur:
            ensure_stock_info_cols(cur)
            added = ensure_archive_cols(cur)
            if added:
                print(f"stock_info +{added} 档案列")
            else:
                print(f"stock_info 档案列齐全（{len(_ARCHIVE_COLS)} 列）")
        conn.commit()
    finally:
        conn.close()
    print("stock_info 宽表就绪（旧独立表 stock_company_profile 已并入，勿再建）")


if __name__ == "__main__":
    main()
