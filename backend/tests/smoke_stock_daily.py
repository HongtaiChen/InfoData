#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""增量采集冒烟测试：取 3 只股票跑全链路（验证多源降级）"""
import sys, os
sys.path.insert(0, r"D:\Project\InfoData\backend")

import logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

from app.collectors.stock_daily_incr import StockDailyIncrementalCollector

def test_single(code: str, name: str):
    print(f"\n===== 测试 {code} {name} =====")
    c = StockDailyIncrementalCollector(days_back=15)
    conn = c._connect()
    try:
        last_date = c.get_last_trade_date(conn)
        print(f"存量最新交易日: {last_date}")
        end = "20260901"
        # 直接测试多源降级（用最近2天窗口验证接口可用性）
        df, src = c.fetch_with_retry(code, "20260801", end)
        print(f"✅ 数据源 [{src}] 拉取到 {len(df)} 行")
        if not df.empty:
            last = df.iloc[-1]
            print(f"最新一行: 日期={last['日期']} 收盘={last['收盘']} 涨跌幅={last['涨跌幅']}")
    finally:
        conn.close()

test_single("000001", "平安银行")
test_single("600519", "贵州茅台")
test_single("300750", "宁德时代")
print("\n冒烟测试完成")
