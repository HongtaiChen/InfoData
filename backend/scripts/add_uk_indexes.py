#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
补唯一索引（设计规范 §8 G9）：把已实证「0 重复」的天然键补成 UNIQUE INDEX，
让恢复采集的采集器获得 DB 级幂等保障（不再只靠 Python 侧判重）。

已核实可补（2026-09-13 逐表实测）：
  stock_industry_sw            (stock_code, industry_type)   0 重复 → uk_stock_level
  stock_financial_abstract_ths (stock_code, report_date)    0 重复（report_date 无 NULL）→ uk_stock_report
  stock_capital_flow           (stock_code, trade_date)     0 重复（无 NULL）→ uk_stock_date
  stock_hold_by_fund           (stock_code, report_date)    0 重复 → uk_stock_report

不可补（保留 Python 侧判重 + 记档）：
  futures_spot_price    (trade_date, good_name) 有 10,361 组重复，其中仅 39 行逐字段完全相同，
                        其余为同日同品种的不同快照值 —— 需先定业务口径（保留哪一份），本轮不动。
  ths_stock_dividend    仅 1 行历史重复（300315/2017年报）
  stock_jgdy_detail     12 行历史重复

用法：
  python scripts/add_uk_indexes.py             # 预检（默认，只报告）
  python scripts/add_uk_indexes.py --apply     # 实际建索引
"""
import argparse
import os
import sys
import warnings

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
warnings.filterwarnings("ignore")

import pymysql

from app.db import get_db_config

# (表, 索引名, 列清单, 业务说明)
TARGETS = [
    ("stock_industry_sw", "uk_stock_level", ["stock_code", "industry_type"],
     "股票在某一申万层级的唯一归属"),
    ("stock_financial_abstract_ths", "uk_stock_report", ["stock_code", "report_date"],
     "单股单报告期一行"),
    ("stock_capital_flow", "uk_stock_date", ["stock_code", "trade_date"],
     "单股单交易日一行"),
    ("stock_hold_by_fund", "uk_stock_report", ["stock_code", "report_date"],
     "单股单报告期一行（基金重仓）"),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="实际执行 DDL（默认仅预检）")
    args = ap.parse_args()

    conn = pymysql.connect(**get_db_config().to_dict(), cursorclass=pymysql.cursors.DictCursor)
    todo = []
    try:
        with conn.cursor() as cur:
            for tbl, idx, cols, note in TARGETS:
                cur.execute("""SELECT COUNT(*) AS n FROM information_schema.statistics
                               WHERE table_schema=DATABASE() AND table_name=%s AND index_name=%s""",
                            (tbl, idx))
                if cur.fetchone()["n"] > 0:
                    print("跳过 %-28s 索引 %s 已存在" % (tbl, idx))
                    continue
                collist = ", ".join("`%s`" % c for c in cols)
                cur.execute("SELECT COUNT(*) AS n, COUNT(DISTINCT %s) AS d FROM `%s`" % (collist, tbl))
                r = cur.fetchone()
                dup = r["n"] - r["d"]
                status = "✅ 可建" if dup == 0 else "❌ 有 %s 组重复" % dup
                print("%-28s %-38s n=%-9s 唯一=%-9s %s" % (tbl, "%s(%s)" % (idx, collist), r["n"], r["d"], status))
                if dup == 0:
                    todo.append((tbl, idx, collist, r["n"], note))

        if not args.apply:
            print("\n[预检模式] 待建 %d 个索引，加 --apply 执行" % len(todo))
            return 0

        for tbl, idx, collist, n, note in todo:
            sql = "ALTER TABLE `%s` ADD UNIQUE INDEX `%s` (%s)" % (tbl, idx, collist)
            print("\n执行: %s" % sql)
            with conn.cursor() as cur:
                cur.execute(sql)
            conn.commit()
            print("  ✅ 已建（%s 行，%s）" % (n, note))
    finally:
        conn.close()

    print("\n完成：新建 %d 个唯一索引" % len(todo))
    return 0


if __name__ == "__main__":
    sys.exit(main())
