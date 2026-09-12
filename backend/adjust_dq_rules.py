#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
调阈值（2026-09-12）：
- ths_concept_market 行数（row_count_slice.min_rows）：300 → 240
  实测 9-11 akshare 接口只回 257 个概念（较 9-10 少 118 个，部分板块当天停牌/接口不全），
  300 偏紧，会一直 fail。240 留 17 行余量，适配同花顺 9 月初小幅回退。
- ths_concept_market freshness warn_days：2 → 5
  周/月末或长假期间允许落后多日，减少预警噪声。
"""
import os
import pymysql

CFG = dict(host="127.0.0.1", port=3306, user="root", password="root", database="adata", charset="utf8mb4")


def main() -> None:
    conn = pymysql.connect(**CFG, cursorclass=pymysql.cursors.DictCursor)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE dq_rules SET params = JSON_SET(params, '$.min_rows', 240) "
                "WHERE rule_name='concept_market_rows'"
            )
            print("concept_market_rows: min_rows 300 → 240 (影响行数", cur.rowcount, ")")
            cur.execute(
                "UPDATE dq_rules SET params = JSON_SET(params, '$.warn_days', 5) "
                "WHERE rule_name='concept_market_freshness'"
            )
            print("concept_market_freshness: warn_days 2 → 5 (影响行数", cur.rowcount, ")")
        conn.commit()

        with conn.cursor() as cur:
            cur.execute(
                "SELECT rule_name, params FROM dq_rules "
                "WHERE rule_name IN ('concept_market_rows','concept_market_freshness')"
            )
            for r in cur.fetchall():
                print("  ", r["rule_name"], "→", r["params"])
    finally:
        conn.close()


if __name__ == "__main__":
    main()
