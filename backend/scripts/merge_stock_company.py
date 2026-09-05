#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
InvestBuddy 合并脚本：stock_company_profile（巨潮档案）并入 stock_info 宽表

背景（2026-09-05 用户决策：两张 1:1 表物理合并）：
- stock_info 为证券级主表（9 列），stock_company_profile 为公司级档案（28 列），
  行集关系为真子集（档案 5,134 只 ⊂ stock_info 5,856 只，1:1 by stock_code）。
- 将档案 25 列（24 业务 + profile_updated_at 管理列）ALTER 并入 stock_info，
  数据由 JOIN 搬迁，随后 RENAME 旧表为 *_bak_20260905 保留回滚点。

口径决策：
- list_date 不搬迁 —— stock_info 推断口径（daily MIN(trade_date) + Baostock ipoDate）覆盖更全；
  巨潮 list_date 对整体上市/换股公司口径不同（如 600018 上港 2006-10-26 为整体上市日，非证券首日 2000-07-19）
- data_source / update_time 保留 stock_info 既有列（档案刷新追踪用新增 profile_updated_at）；
  v1.5 起 data_source 为三源构成常量（EM;BAOSTOCK;CNINFO，列 DEFAULT 维护），本脚本不写该列
- a_short 保留为「巨潮披露简称口径」，与 short_name（东财实时名单）并存

幂等：重复执行安全（逐列检查缺失再 ALTER；RENAME 目标不存在才执行）
"""
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # backend 根

import pymysql

from app.db import get_db_config

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

BAK_NAME = "stock_company_profile_bak_20260905"

# 新增档案列（均不含 list_date / data_source / update_time —— 保留 stock_info 既有口径）
_ARCHIVE_COLS: dict[str, str] = {
    "company_name": "VARCHAR(200) NULL COMMENT '公司全称（巨潮）'",
    "en_name": "VARCHAR(300) NULL COMMENT '英文名称'",
    "prev_names": "VARCHAR(500) NULL COMMENT '曾用简称'",
    "a_short": "VARCHAR(50) NULL COMMENT 'A股简称（巨潮披露口径，与 short_name 并存）'",
    "b_code": "VARCHAR(20) NULL COMMENT 'B股代码'",
    "b_short": "VARCHAR(50) NULL COMMENT 'B股简称'",
    "h_code": "VARCHAR(20) NULL COMMENT 'H股代码'",
    "h_short": "VARCHAR(50) NULL COMMENT 'H股简称'",
    "index_members": "VARCHAR(500) NULL COMMENT '入选指数（逗号分隔）'",
    "market": "VARCHAR(30) NULL COMMENT '所属市场（上交所/深交所等）'",
    "industry": "VARCHAR(50) NULL COMMENT '所属行业（证监会行业分类）'",
    "legal_rep": "VARCHAR(50) NULL COMMENT '法人代表'",
    "reg_capital": "VARCHAR(50) NULL COMMENT '注册资金（万元）'",
    "establish_date": "DATE NULL COMMENT '成立日期'",
    "website": "VARCHAR(200) NULL COMMENT '官方网站'",
    "email": "VARCHAR(200) NULL COMMENT '电子邮箱'",
    "phone": "VARCHAR(100) NULL COMMENT '联系电话'",
    "fax": "VARCHAR(100) NULL COMMENT '传真'",
    "reg_address": "VARCHAR(300) NULL COMMENT '注册地址'",
    "office_address": "VARCHAR(300) NULL COMMENT '办公地址'",
    "postcode": "VARCHAR(20) NULL COMMENT '邮政编码'",
    "main_business": "TEXT NULL COMMENT '主营业务'",
    "business_scope": "TEXT NULL COMMENT '经营范围'",
    "org_intro": "TEXT NULL COMMENT '机构简介'",
    "profile_updated_at": "DATETIME NULL COMMENT '公司档案最后刷新时间（巨潮日更维护）'",
}

# 搬迁来源列（档案表列名 -> 目标列名，同一名称）
_SRC_TARGET = {c: c for c in _ARCHIVE_COLS if c != "profile_updated_at"}


def _existing_cols(cur, table: str) -> set[str]:
    cur.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema=DATABASE() AND table_name=%s",
        (table,),
    )
    return {r[0] for r in cur.fetchall()}


def main() -> None:
    conn = pymysql.connect(**get_db_config().to_dict())
    try:
        with conn.cursor() as cur:
            # 0. 前提：档案表存在才可搬迁
            cur.execute(
                "SELECT COUNT(*) FROM information_schema.tables "
                "WHERE table_schema=DATABASE() AND table_name='stock_company_profile'"
            )
            src_exists = cur.fetchone()[0] > 0
            if not src_exists:
                logger.warning("stock_company_profile 不存在 —— 已合并过或从未创建，仅确保列存在")

            # 1. 逐列补齐 stock_info 档案列
            exist = _existing_cols(cur, "stock_info")
            adds = [f"ADD COLUMN {c} {ddl}" for c, ddl in _ARCHIVE_COLS.items() if c not in exist]
            if adds:
                cur.execute("ALTER TABLE stock_info " + ", ".join(adds))
                logger.info("stock_info 新增 %s 列", len(adds))
            else:
                logger.info("stock_info 档案列已齐全（%s 列），无需 ALTER", len(_ARCHIVE_COLS))

            # 2. 搬迁数据（仅当档案表存在）
            if src_exists:
                set_clause = ", ".join(
                    f"s.{tgt}=p.{src}" for src, tgt in _SRC_TARGET.items()
                )
                set_clause += ", s.profile_updated_at=p.update_time"
                cur.execute(
                    f"UPDATE stock_info s JOIN stock_company_profile p ON s.stock_code=p.stock_code "
                    f"SET {set_clause}"
                )
                moved = cur.rowcount
                logger.info("搬迁档案 %s 只 -> stock_info", moved)
            else:
                moved = 0

            # 3. 校验
            cur.execute("SELECT COUNT(*) FROM stock_info WHERE company_name IS NOT NULL")
            filled = cur.fetchone()[0]
            cur.execute(
                "SELECT COUNT(*) FROM stock_info WHERE list_status='上市' "
                "AND (stock_code LIKE '6%' OR stock_code LIKE '0%' OR stock_code LIKE '3%') "
                "AND company_name IS NULL"
            )
            missing = cur.fetchone()[0]
            logger.info("stock_info.company_name 非空 %s 只；在市沪深A缺失 %s 只", filled, missing)

            # 4. RENAME 备份
            cur.execute(
                "SELECT COUNT(*) FROM information_schema.tables "
                "WHERE table_schema=DATABASE() AND table_name=%s",
                (BAK_NAME,),
            )
            if src_exists and cur.fetchone()[0] == 0:
                cur.execute("RENAME TABLE stock_company_profile TO %s" % BAK_NAME)
                logger.info("stock_company_profile -> %s（回滚点，确认稳定后可 DROP）", BAK_NAME)
            elif src_exists:
                logger.warning("%s 已存在，跳过 RENAME（保留原表）", BAK_NAME)

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    print(f"合并完成：搬迁 {moved} 只 / 档案非空 {filled} / 在市缺失 {missing} / 备份 {BAK_NAME}")


if __name__ == "__main__":
    main()
