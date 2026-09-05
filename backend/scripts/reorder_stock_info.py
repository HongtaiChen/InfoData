#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
InvestBuddy stock_info 列序重排 + data_source 语义改造脚本

背景（2026-09-05 用户决策）：
- data_source 列在 v1.4 合并成「证券级+巨潮档案」宽表后语义失效（全表 5,856 行唯一常量 'ADTA'，
  与代码写入值 'AKSHARE'/'LOCAL' 均不符），但用户选择【保留】该列：
  - 移至表尾管理列区（证券核心 1-7 → 巨潮档案 24 列 → 管理列收尾）
  - 值统一为三源构成 'EM;BAOSTOCK;CNINFO'（东财名单 / Baostock 状态 / 巨潮档案），并设列 DEFAULT
  - 注释更新为「来源构成」口径；列级细分来源仍由各列注释表达（已有 Baostock/巨潮标注）
- update_time 注释澄清为「最近维护时间」（ON UPDATE CURRENT_TIMESTAMP，任一采集器刷新该行即更新）

列序目标（34 列）：
  id, stock_code, short_name, exchange, list_date, list_status, delist_date,
  company_name ... org_intro（巨潮档案 24 列）,
  profile_updated_at, update_time, data_source（管理列收尾）

幂等：若目标列序已满足且 data_source 默认值正确则跳过 ALTER，仅打印验证。
"""
import logging
import sys

sys.path.insert(0, __file__.rsplit("scripts", 1)[0])  # backend 根

import pymysql

from app.db import get_db_config

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

TABLE = "stock_info"
SRC_CONST = "EM;BAOSTOCK;CNINFO"
NEW_COMMENT = "股票基本信息+公司档案宽表（东财名单/Baostock状态/巨潮档案三源构成：证券核心列+档案24列+管理列）"

NEW_ORDER = [
    "id", "stock_code", "short_name", "exchange", "list_date",
    "list_status", "delist_date",
    # 巨潮档案 24 列
    "company_name", "en_name", "prev_names", "a_short", "b_code", "b_short",
    "h_code", "h_short", "index_members", "market", "industry", "legal_rep",
    "reg_capital", "establish_date", "website", "email", "phone", "fax",
    "reg_address", "office_address", "postcode", "main_business",
    "business_scope", "org_intro",
    # 管理列收尾
    "profile_updated_at", "update_time", "data_source",
]

# 特殊列口径覆盖
_OVERRIDE_COMMENT = {
    "update_time": "最近维护时间（任一采集器刷新该行自动更新）",
    "data_source": "来源构成（东财名单;Baostock状态;巨潮档案，三源共同维护；列级细分来源见各列注释）",
}
_DATA_SOURCE_DDL = "varchar(100) NULL DEFAULT 'EM;BAOSTOCK;CNINFO' COMMENT '来源构成（东财名单;Baostock状态;巨潮档案，三源共同维护；列级细分来源见各列注释）'"


def _cols(cur) -> dict[str, dict]:
    cur.execute(
        "SELECT column_name, column_type, is_nullable, column_default, extra, column_comment "
        "FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name=%s "
        "ORDER BY ordinal_position",
        (TABLE,),
    )
    out = {}
    for name, ctype, nullable, default, extra, comment in cur.fetchall():
        out[name] = {
            "type": ctype,
            "nullable": nullable,
            "default": default,
            "extra": extra,
            "comment": comment,
        }
    return out


def _order_ok(cur) -> bool:
    cur.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_schema=DATABASE() "
        "AND table_name=%s ORDER BY ordinal_position",
        (TABLE,),
    )
    return [r[0] for r in cur.fetchall()] == NEW_ORDER


def _modify_sql(info: dict, name: str) -> str:
    """按列元数据构造 MODIFY COLUMN 片段（保留类型/可空/默认/自增），可叠注释覆盖"""
    if name == "data_source":
        return f"MODIFY COLUMN data_source {_DATA_SOURCE_DDL}"
    ctype = info["type"]
    null_sql = "NOT NULL" if info["nullable"] == "NO" else "NULL"
    default = info["default"]
    extra = info["extra"]
    # timestamp ON UPDATE 需显式还原
    if default == "CURRENT_TIMESTAMP" or (extra and "on update CURRENT_TIMESTAMP" in extra):
        default_sql = "DEFAULT CURRENT_TIMESTAMP"
        if extra and "on update CURRENT_TIMESTAMP" in extra:
            default_sql += " ON UPDATE CURRENT_TIMESTAMP"
    elif default is not None:
        default_sql = f"DEFAULT {default}"
    else:
        default_sql = ""
    if extra and "auto_increment" in extra:
        default_sql = "AUTO_INCREMENT"
    comment = _OVERRIDE_COMMENT.get(name, info["comment"] or "")
    comment_sql = f"COMMENT '{comment.replace(chr(39), chr(92) + chr(39))}'" if comment else ""
    parts = [f"MODIFY COLUMN {name} {ctype}", null_sql]
    if default_sql:
        parts.append(default_sql)
    if comment_sql:
        parts.append(comment_sql)
    return " ".join(parts)


def main() -> None:
    conn = pymysql.connect(**get_db_config().to_dict())
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT column_default FROM information_schema.columns "
                "WHERE table_schema=DATABASE() AND table_name=%s AND column_name='data_source'",
                (TABLE,),
            )
            ds_default = cur.fetchone()[0]
            already = _order_ok(cur) and ds_default == SRC_CONST

            if already:
                logger.info("列序与 data_source DEFAULT 已符合目标，跳过 ALTER")
            else:
                info = _cols(cur)
                if set(info) != set(NEW_ORDER):
                    logger.error("列集合不一致: 库 %s vs 目标 %s", sorted(info), sorted(NEW_ORDER))
                    sys.exit(1)
                # 关键：MySQL 的 MODIFY 不带 FIRST/AFTER 不会移动列位置，
                # 必须逐条显式 AFTER 前一目标列形成重排链
                mods = []
                prev = NEW_ORDER[0]
                for name in NEW_ORDER[1:]:
                    mods.append(_modify_sql(info[name], name) + f" AFTER {prev}")
                    prev = name
                sql = (
                    "ALTER TABLE stock_info "
                    + ", ".join(mods)
                    + f", COMMENT='{NEW_COMMENT}'"
                )
                logger.info("执行 ALTER：%s 列重排 + 表注释", len(mods))
                cur.execute(sql)
                # 既有行统一为三源构成（触发 update_time 刷新属预期：最近维护语义）
                cur.execute(f"UPDATE {TABLE} SET data_source=%s", (SRC_CONST,))
                logger.info("UPDATE data_source -> %s（%s 行）", SRC_CONST, cur.rowcount)
            conn.commit()

            # 验证
            cur.execute(
                "SELECT column_name, column_type, column_default, column_comment "
                "FROM information_schema.columns WHERE table_schema=DATABASE() "
                "AND table_name=%s ORDER BY ordinal_position",
                (TABLE,),
            )
            rows = cur.fetchall()
            print(f"\n== {TABLE} 重排后列序（{len(rows)} 列）==")
            for i, (n, t, d, c) in enumerate(rows, 1):
                print(f"{i:>2}. {n:<22} {t:<28} default={str(d)[:34]:<34} {c or ''}")
            cur.execute(
                "SELECT table_comment FROM information_schema.tables "
                "WHERE table_schema=DATABASE() AND table_name=%s",
                (TABLE,),
            )
            print("表注释:", cur.fetchone()[0])
            cur.execute(
                f"SELECT data_source, COUNT(*) FROM {TABLE} GROUP BY data_source"
            )
            print("data_source 分布:", cur.fetchall())
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
