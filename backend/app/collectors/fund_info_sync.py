#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
InvestBuddy 基金基础信息采集器（fund_info 表，月更）
- 数据源：ak.fund_name_em()（东财全市场基金列表，含代码/简称/类型）
- 策略：全量重建（DELETE + 批量 INSERT 同一事务），源行数 < min_rows 护栏拒绝，防空表
- 表无唯一键，故不做 INSERT IGNORE
"""
import logging

import pymysql
import akshare as ak

from ..db import get_db_config

logger = logging.getLogger(__name__)


class FundInfoSyncCollector:
    """基金基础信息同步（月更全量重建）"""

    def __init__(self, min_rows: int = 20000):
        self.min_rows = min_rows

    def run(self) -> dict:
        df = ak.fund_name_em()
        if df is None or df.empty:
            raise RuntimeError("fund_name_em 返回为空（接口风控或变更）")
        if len(df) < self.min_rows:
            raise RuntimeError(
                f"fund_name_em 仅返回 {len(df)} 行 < 护栏 {self.min_rows}，拒绝覆盖，疑似接口异常"
            )
        if not {"基金代码", "基金简称", "基金类型"}.issubset(df.columns):
            raise RuntimeError(f"fund_name_em 列结构变化: {list(df.columns)}")

        rows = [
            (
                str(r["基金代码"]).strip(),
                str(r["基金简称"]).strip(),
                (str(r["基金类型"]).strip() if r["基金类型"] is not None else None) or None,
            )
            for _, r in df.iterrows()
        ]

        conn = pymysql.connect(**get_db_config().to_dict())
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM fund_info")
                cur.executemany(
                    "INSERT INTO fund_info (fund_code, fund_name, fund_type, update_time, data_source) "
                    "VALUES (%s, %s, %s, NOW(), 'AKSHARE')",
                    rows,
                )
                cur.execute("SELECT COUNT(*) FROM fund_info")
                final_n = cur.fetchone()[0]
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

        logger.info(f"✅ 基金基础信息全量重建：{len(rows)} 条 → 落库 {final_n}")
        return {
            "records_written": len(rows),
            "error_count": 0,
            "errors": [],
            "note": f"基金基础信息全量重建 {len(rows)} 条（东财基金列表）",
        }
