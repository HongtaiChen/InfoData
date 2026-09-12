#!/usr/bin/env python
# -*- coding: utf-8 -*-
import os
import pymysql

CFG = dict(host="127.0.0.1", port=3306, user="root", password="root", database="adata", charset="utf8mb4")

conn = pymysql.connect(**CFG, cursorclass=pymysql.cursors.DictCursor)
try:
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS v FROM stock_market_current")
        print("stock_market_current 总行数:", cur.fetchone()["v"])
        cur.execute(
            "SELECT SUBSTRING(stock_code,1,1) AS p, COUNT(*) AS c FROM stock_market_current GROUP BY p ORDER BY c DESC"
        )
        for r in cur.fetchall():
            print("  prefix", r["p"], "=", r["c"])
        cur.execute(
            "SELECT stock_market_current.* FROM stock_market_current "
            "LEFT JOIN stock_market_daily d ON d.stock_code = stock_market_current.stock_code "
            "AND d.trade_date = (SELECT MAX(trade_date) FROM stock_market_daily) "
            "WHERE d.id IS NULL LIMIT 5"
        )
        miss = cur.fetchall()
        print("不在 daily 最新日的 current 股票:", len(miss), "（样本前 5 行）")
        for m in miss[:5]:
            print("  ", m["stock_code"])
finally:
    conn.close()
