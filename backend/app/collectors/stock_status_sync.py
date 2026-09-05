#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
InvestBuddy 上市/退市状态采集器（stock_status_sync，Baostock 权威源，周更）

- 源：bs.query_stock_basic() —— 一次返回沪深全部股票（含退市 300+ 只）
  status: 1=上市 0=退市；outDate 非空即退市日期；ipoDate 为官方上市日期
- 写表 stock_info：
  1. list_status（'上市'/'退市'）与 delist_date 全量覆盖（按 stock_code）
  2. list_date 仅补 NULL（用官方 ipoDate，不覆盖已有值）
- 边界：Baostock 仅覆盖沪深 A 股；B 股/北交所代码不在名单 → 保持 NULL 不误标
- 幂等：整轮可安全重复执行
"""
import logging

import pymysql

import baostock as bs

from ..db import get_db_config

logger = logging.getLogger(__name__)

_PREFIX = ("sh.", "sz.")


class StockStatusSyncCollector:
    """上市/退市状态同步（Baostock）"""

    def _fetch_all_stocks(self) -> list[dict]:
        lg = bs.login()
        if lg.error_code != "0":
            raise RuntimeError(f"Baostock 登录失败: {lg.error_msg}")
        try:
            rs = bs.query_stock_basic()
            if rs.error_code != "0":
                raise RuntimeError(f"query_stock_basic 失败: {rs.error_msg}")
            out = []
            while rs.error_code == "0" and rs.next():
                row = rs.get_row_data()  # [code, code_name, ipoDate, outDate, type, status]
                if row[4] != "1":  # 只要股票
                    continue
                if not row[0].startswith(_PREFIX):
                    continue
                out.append(
                    {
                        "stock_code": row[0][3:],
                        "ipo_date": row[2] or None,
                        "out_date": row[3] or None,
                        "status": "退市" if row[5] == "0" else "上市",
                    }
                )
            return out
        finally:
            bs.logout()

    def run(self) -> dict:
        stocks = self._fetch_all_stocks()
        logger.info("Baostock 返回沪深股票 %s 只", len(stocks))
        if len(stocks) < 4000:
            raise RuntimeError(f"Baostock 仅返回 {len(stocks)} 只，疑似异常，拒绝覆盖")

        conn = pymysql.connect(**get_db_config().to_dict())
        upd_status = upd_delist = upd_listdate = 0
        try:
            with conn.cursor() as cur:
                for s in stocks:
                    cur.execute(
                        "UPDATE stock_info SET list_status=%s WHERE stock_code=%s",
                        (s["status"], s["stock_code"]),
                    )
                    upd_status += cur.rowcount
                    if s["out_date"]:
                        cur.execute(
                            "UPDATE stock_info SET delist_date=%s WHERE stock_code=%s",
                            (s["out_date"], s["stock_code"]),
                        )
                        upd_delist += cur.rowcount
                    if s["ipo_date"]:
                        cur.execute(
                            "UPDATE stock_info SET list_date=%s WHERE stock_code=%s AND list_date IS NULL",
                            (s["ipo_date"], s["stock_code"]),
                        )
                        upd_listdate += cur.rowcount
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

        msg = (
            f"上市/退市状态同步：覆盖 {upd_status} 只 / 写退市日期 {upd_delist} 只 / 补上市日期 {upd_listdate} 只"
        )
        logger.info("✅ %s", msg)
        return {
            "records_written": upd_status,
            "error_count": 0,
            "errors": [],
            "note": msg,
        }
