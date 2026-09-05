#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
InvestBuddy 上市/退市状态回填脚本（Baostock 权威源，全市场沪深股票）

- 源：bs.query_stock_basic() —— 一次返回沪深全部股票（含退市），字段 code/code_name/ipoDate/outDate/type/status
  status: 1=上市 0=退市；outDate 非空即退市日期；ipoDate 为官方上市日期
- 作用：
  1. 回填 stock_info.list_status（'上市'/'退市'）与 delist_date
  2. 顺带用官方 ipoDate 补 stock_info.list_date 为 NULL 的沪深股票（覆盖 Baostock 的 5,552 只范围）
- 幂等：按 stock_code 全量覆盖 list_status/delist_date；list_date 仅补 NULL 不覆盖现有值
- 边界：北交所代码 Baostock 不覆盖 → 保持 NULL 不误标；重复执行安全
"""
import logging
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # backend 根

import pymysql

import baostock as bs

from app.db import get_db_config

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# baostock code 形如 'sh.600000' / 'sz.000001'；type=1 股票；status 1上市/0退市
_PREFIX = ("sh.", "sz.")


def fetch_all_stocks() -> list[dict]:
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
            code = row[0]
            if not code.startswith(_PREFIX):
                continue
            out.append(
                {
                    "stock_code": code[3:],
                    "ipo_date": row[2] or None,
                    "out_date": row[3] or None,
                    "status": "退市" if row[5] == "0" else "上市",
                }
            )
        return out
    finally:
        bs.logout()


def run() -> dict:
    stocks = fetch_all_stocks()
    logger.info("Baostock 返回沪深股票 %s 只", len(stocks))
    if len(stocks) < 4000:
        raise RuntimeError(f"Baostock 仅返回 {len(stocks)} 只，疑似异常，拒绝覆盖")

    conn = pymysql.connect(**get_db_config().to_dict())
    upd_status = upd_delist = upd_listdate = 0
    try:
        with conn.cursor() as cur:
            for s in stocks:
                # 1. 全量覆盖 list_status
                cur.execute(
                    "UPDATE stock_info SET list_status=%s WHERE stock_code=%s",
                    (s["status"], s["stock_code"]),
                )
                upd_status += cur.rowcount
                # 2. 退市日期（退市才有值）
                if s["out_date"]:
                    cur.execute(
                        "UPDATE stock_info SET delist_date=%s WHERE stock_code=%s",
                        (s["out_date"], s["stock_code"]),
                    )
                    upd_delist += cur.rowcount
                # 3. 上市日期仅补 NULL（不覆盖已有值）
                if s["ipo_date"]:
                    cur.execute(
                        "UPDATE stock_info SET list_date=%s WHERE stock_code=%s AND list_date IS NULL",
                        (s["ipo_date"], s["stock_code"]),
                    )
                    upd_listdate += cur.rowcount
        conn.commit()
    finally:
        conn.close()

    msg = f"Baostock 状态回填：覆盖 list_status {upd_status} 只 / 写退市日期 {upd_delist} 只 / 补上市日期 {upd_listdate} 只"
    logger.info("✅ %s", msg)
    return {"updated": upd_status, "delist_date_written": upd_delist, "list_date_backfilled": upd_listdate, "note": msg}


if __name__ == "__main__":
    import sys

    sys.path.insert(0, ".")
    r = run()
    print(r)
