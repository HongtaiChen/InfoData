#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
InvestBuddy 公司档案采集器（stock_company_sync，巨潮资讯官方源，日更差量）

- 源：ak.stock_profile_cninfo(symbol) —— 证监会指定披露平台，单只返回 26 字段公司概况
  公司全称/英文名/曾用简称/ABH股/入选指数/市场/行业/法人/注册资金/成立日期/上市日期/
  官网/邮箱/电话/传真/地址/邮编/主营业务/经营范围/机构简介
- 覆盖：沪深 A 股在市股票（stock_info.list_status='上市'；B 股、北交所、退市股巨潮无档案，跳过）
- 策略：差量更新 —— 只处理 stock_company_profile 中缺失或超过 refresh_days 未刷新的代码；
  max_count 限制单轮处理数（防单轮过长），跑多轮即可铺满全市场
- 幂等：upsert（INSERT ... ON DUPLICATE KEY UPDATE）
- 容错：单只失败记录 error 不中断（下轮自动重试）
"""
import logging
from datetime import datetime, date

import pandas as pd
import pymysql

import akshare as ak

from ..db import get_db_config

logger = logging.getLogger(__name__)

# 巨潮返回中文列 -> stock_company_profile 列名
_COL_MAP = {
    "公司名称": "company_name",
    "英文名称": "en_name",
    "曾用简称": "prev_names",
    "A股简称": "a_short",
    "B股代码": "b_code",
    "B股简称": "b_short",
    "H股代码": "h_code",
    "H股简称": "h_short",
    "入选指数": "index_members",
    "所属市场": "market",
    "所属行业": "industry",
    "法人代表": "legal_rep",
    "注册资金": "reg_capital",
    "成立日期": "establish_date",
    "上市日期": "list_date",
    "官方网站": "website",
    "电子邮箱": "email",
    "联系电话": "phone",
    "传真": "fax",
    "注册地址": "reg_address",
    "办公地址": "office_address",
    "邮政编码": "postcode",
    "主营业务": "main_business",
    "经营范围": "business_scope",
    "机构简介": "org_intro",
}
_DATE_FIELDS = {"establish_date", "list_date"}

_B_PREFIX = ("200", "201", "900", "901")
_BJ_PREFIX = ("43", "82", "83", "87", "88", "89", "92", "920")


class StockCompanySyncCollector:
    """公司档案同步（巨潮资讯）"""

    def __init__(self, max_count: int = 200, refresh_days: int = 30):
        self.max_count = max_count
        self.refresh_days = refresh_days

    # ---------- 数据源 ----------
    def _fetch_profile(self, code: str) -> dict | None:
        df = ak.stock_profile_cninfo(symbol=code)
        if df is None or df.empty:
            return None
        row = df.iloc[0]
        out = {}
        for cn, col in _COL_MAP.items():
            v = row.get(cn)
            out[col] = _clean(v)
        return out

    # ---------- 主流程 ----------
    def run(self) -> dict:
        conn = pymysql.connect(**get_db_config().to_dict())
        try:
            # 1. 取候选代码：档案缺失 或 超过 refresh_days 未刷新
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT s.stock_code, s.exchange, s.list_status
                       FROM stock_info s
                       LEFT JOIN stock_company_profile p ON s.stock_code = p.stock_code
                       WHERE p.stock_code IS NULL
                          OR p.update_time < DATE_SUB(NOW(), INTERVAL %s DAY)
                       ORDER BY (p.stock_code IS NULL) DESC, s.stock_code""",
                    (self.refresh_days,),
                )
                rows = cur.fetchall()
        finally:
            conn.close()

        candidates = [
            (c, st)
            for c, ex, st in rows
            if _is_eligible(c, ex, st)
        ][: self.max_count]
        if not candidates:
            logger.info("无待更新档案（全部在市沪深 A 股均有且未过期）")
            return {"records_written": 0, "error_count": 0, "errors": [], "note": "无待更新档案"}

        logger.info("本轮待更新 %s 只（候选 %s 只）", len(candidates), len(rows))

        conn = pymysql.connect(**get_db_config().to_dict())
        written, errors = 0, []
        try:
            with conn.cursor() as cur:
                upsert_cols = [c for c in _COL_MAP.values()] + ["data_source"]
                placeholders = ", ".join(["%s"] * len(upsert_cols))  # 首列 stock_code 单独一个 %s
                set_clause = ", ".join(f"{c}=VALUES({c})" for c in upsert_cols)
                sql = (
                    f"INSERT INTO stock_company_profile (stock_code, {', '.join(upsert_cols)}) "
                    f"VALUES (%s, {placeholders}) "
                    f"ON DUPLICATE KEY UPDATE {set_clause}"
                )
                for i, (code, _st) in enumerate(candidates, start=1):
                    try:
                        profile = self._fetch_profile(code)
                    except Exception as e:  # 单只失败不中断
                        errors.append(f"{code}: {str(e)[:80]}")
                        continue
                    if profile is None:
                        errors.append(f"{code}: 巨潮无档案返回")
                        continue
                    values = [code] + [profile.get(c) for c in upsert_cols[:-1]] + ["AKSHARE-CNINFO"]
                    cur.execute(sql, values)
                    written += 1
                    if written % 50 == 0:
                        conn.commit()  # 分批提交：长跑中断最多丢一批，不整轮回滚
                        logger.info("  已写入 %s 只...", written)
                conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

        msg = f"公司档案同步：写入/更新 {written} 只（失败 {len(errors)}）"
        logger.info("✅ %s", msg)
        return {
            "records_written": written,
            "error_count": len(errors),
            "errors": errors[:50],
            "note": msg,
        }


def _clean(v):
    """NaN/None -> None；datetime/date 归一为 date；日期字符串 -> date；其余原样"""
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, float) and pd.isna(v):
        return None
    if isinstance(v, str) and not v.strip():
        return None
    s = str(v).strip()
    if s.lower() == "nan" or s == "None":
        return None
    # 日期形如 1999-11-10 或 19911101
    if len(s) == 10 and s[4] == "-" and s[7] == "-":
        try:
            return datetime.strptime(s, "%Y-%m-%d").date()
        except ValueError:
            return s
    if len(s) == 8 and s.isdigit():
        try:
            return datetime.strptime(s, "%Y%m%d").date()
        except ValueError:
            return s
    return s


def _is_eligible(code: str, exchange: str | None, list_status: str | None) -> bool:
    """只处理沪深 A 股在市股票（巨潮对该范围返回档案）"""
    if exchange == "BJ":
        return False
    if code.startswith(_B_PREFIX) or code.startswith(_BJ_PREFIX):
        return False
    if code.startswith(("6", "0", "3", "68", "60", "00", "30")) is False:
        return False
    if list_status == "退市":
        return False
    return True
