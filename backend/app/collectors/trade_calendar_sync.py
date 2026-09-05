#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
InvestBuddy 交易日历采集器
用 akshare 新浪交易日历接口补齐 trade_calendar（投资日历"休市/开市"标记依赖此表）。
- 数据源：ak.tool_trade_date_hist_sina()（全量交易日，1990 - 次年）
- 去重：INSERT IGNORE + 唯一键 uk_trade_date
- 默认只补今年与明年，避免历史噪音
"""
import logging
from datetime import datetime, date

import pymysql
import akshare as ak

from ..db import get_db_config

logger = logging.getLogger(__name__)


class TradeCalendarSyncCollector:
    """交易日历采集器"""

    def __init__(self, years: list[int] | None = None):
        # 默认补当年 + 次年（如 2026-09 运行 → 2026 + 2027）
        now_year = datetime.now().year
        self.years = years or [now_year, now_year + 1]

    def run(self) -> dict:
        df = ak.tool_trade_date_hist_sina()
        if df is None or df.empty or "trade_date" not in df.columns:
            raise RuntimeError("交易日历接口返回为空，可能被风控或接口变更")

        conn = pymysql.connect(**get_db_config().to_dict())
        inserted = skipped = 0
        try:
            with conn.cursor() as cur:
                for _, row in df.iterrows():
                    d = row["trade_date"]
                    # 可能是 datetime/date/str
                    if isinstance(d, str):
                        d = datetime.strptime(d[:10], "%Y-%m-%d").date()
                    elif isinstance(d, datetime):
                        d = d.date()
                    if d.year not in self.years:
                        continue
                    cur.execute(
                        "INSERT IGNORE INTO trade_calendar "
                        "(trade_date, is_trading_day, year, month, day, weekday, update_time) "
                        "VALUES (%s, 1, %s, %s, %s, %s, NOW())",
                        (d, d.year, d.month, d.day, d.weekday()),
                    )
                    if cur.rowcount == 1:
                        inserted += 1
                    else:
                        skipped += 1
                conn.commit()
            logger.info(f"✅ 交易日历更新：新增 {inserted} 条（已存在 {skipped}），目标年份 {self.years}")
            return {
                "records_written": inserted,
                "error_count": 0,
                "errors": [],
                "note": f"交易日历补齐至 {self.years}（新增 {inserted} / 已存在 {skipped}）",
            }
        finally:
            conn.close()
