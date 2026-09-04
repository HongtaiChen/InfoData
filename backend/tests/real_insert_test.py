#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""真实落库测试：拉取 5 只缺口股票并写入 stock_market_daily"""
import sys, os
sys.path.insert(0, r"D:\Project\InfoData\backend")

import logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

from app.collectors.stock_daily_incr import StockDailyIncrementalCollector

c = StockDailyIncrementalCollector(days_back=15)
conn = c._connect()
try:
    last_date = c.get_last_trade_date(conn)
    print(f"当前库内最新交易日: {last_date}")
    # 测试 5 只主流 A 股（含缺口较大的）
    test_codes = ["000002", "600036", "601318", "000858", "002594"]
    stocks = []
    with conn.cursor() as cur:
        for code in test_codes:
            cur.execute(
                "SELECT stock_code, short_name, (SELECT MAX(trade_date) FROM stock_market_daily b WHERE b.stock_code=%s) FROM stock_info WHERE stock_code=%s",
                (code, code),
            )
            r = cur.fetchone()
            if r:
                stocks.append(r)
    print(f"测试 {len(stocks)} 只主流股票: {[r[0] for r in stocks]}")

    total = 0
    for code, name, sdate in stocks:
        end = "20260901"
        if sdate:
            from datetime import datetime, timedelta
            start = (datetime.strptime(str(sdate), "%Y-%m-%d") - timedelta(days=1)).strftime("%Y%m%d")
        else:
            start = "20250101"
        print(f"\n处理 {code} {name} (缺口起点 {start})...")
        try:
            df, src = c.fetch_with_retry(code, start, end)
            n = c.insert_rows(conn, code, df, src)
            total += n
            print(f"  ✅ 源[{src}] 写入 {n} 行")
        except Exception as e:
            print(f"  ❌ {code} 失败: {str(e)[:80]}")
    print(f"\n✅ 总计写入 {total} 行")
    # 验证
    with conn.cursor() as cur:
        cur.execute("SELECT MAX(trade_date), COUNT(*) FROM stock_market_daily")
        print("库内最新日期/总数:", cur.fetchone())
finally:
    conn.close()
