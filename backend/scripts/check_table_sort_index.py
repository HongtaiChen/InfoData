#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""数据中心「表打开卡死」风险体检器（只读）—— 2026-09-21 事故后固化。

背景：数据中心前端打开表时的行为是
    selectTable() → applySortPref() → loadRows()
即**套用 user_prefs 里为该表记住的排序**再取第一页。若该排序列没有可用索引，
MySQL 只能全表扫 + filesort。实测 stock_market_daily（1,717 万行）上
    ORDER BY update_time ASC LIMIT 51   →  50.1 s（冷）/ 158.3 s（并发时）
而前端 axios timeout=30s，超时后请求被丢弃、服务端仍继续扫 → 表格永久转圈。

本脚本就是把「表体积 × 已记忆排序列 × 该列有无索引」做交叉核对，
提前找出同类定时炸弹（换机、手工删索引、新增大表后都可能复发）。

用法：
    python scripts/check_table_sort_index.py            # 体检全部已记忆排序的表
    python scripts/check_table_sort_index.py --all      # 连未记忆排序的大表也列出

判定阈值：
    行数 > 50 万且排序列无索引前缀 → 🔴 高危（首屏必然卡死）
    行数 > 5 万                    → 🟡 中危
    其余                           → ⚪ 可忽略
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pymysql  # noqa: E402

from app.db import get_db_config  # noqa: E402

HIGH_RISK = 500_000
MID_RISK = 50_000


def norm(rows):
    """information_schema 在 MySQL 8 返回大写列名，统一小写"""
    return [{k.lower(): v for k, v in r.items()} for r in rows]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="同时列出未记忆排序的大表")
    args = ap.parse_args()

    conn = pymysql.connect(**get_db_config().to_dict(), cursorclass=pymysql.cursors.DictCursor)
    try:
        cur = conn.cursor()

        # 1) 前端记忆的排序（数据中心靠它决定首屏 ORDER BY 哪一列）
        cur.execute(
            "SELECT scope, payload FROM user_prefs WHERE owner='local' AND kind='table_sort'"
        )
        prefs = {}
        for r in norm(cur.fetchall()):
            p = r["payload"]
            p = json.loads(p) if isinstance(p, str) else p
            prefs[r["scope"]] = p

        # 2) 表体积
        cur.execute(
            "SELECT table_name, table_rows, data_length, index_length "
            "FROM information_schema.tables WHERE table_schema=%s AND table_type='BASE TABLE'",
            (get_db_config().database,),
        )
        meta = {r["table_name"]: r for r in norm(cur.fetchall())}

        # 3) 各索引首列（只有首列能作为排序前缀被直接利用）
        cur.execute(
            "SELECT table_name, seq_in_index, column_name FROM information_schema.statistics "
            "WHERE table_schema=%s",
            (get_db_config().database,),
        )
        first_cols: dict = {}
        for r in norm(cur.fetchall()):
            if r["seq_in_index"] == 1:
                first_cols.setdefault(r["table_name"], set()).add(r["column_name"])

        print("=" * 100)
        print("「表体积 × 记忆排序 × 索引」风险矩阵")
        print("=" * 100)
        print(f"  {'表名':<32} {'体积MB':>9} {'行数':>12}  {'记忆排序':<28} {'判定'}")
        print("-" * 100)

        risky = []
        for t, p in sorted(prefs.items(), key=lambda kv: -(meta.get(kv[0], {}).get("table_rows") or 0)):
            m = meta.get(t)
            if not m:
                print(f"  {t:<32} {'—':>9} {'(表不存在)':>12}")
                continue
            mb = (m["data_length"] + m["index_length"]) / 1048576
            rows = m["table_rows"] or 0
            col = p.get("col", "")
            has = col in first_cols.get(t, set())
            sortdesc = f"{col} {(p.get('dir') or '')}".strip()

            if has:
                verdict = "✅ 有索引"
            elif rows > HIGH_RISK:
                verdict = "🔴 高危：全表扫 + filesort"
                risky.append((t, col, mb, rows))
            elif rows > MID_RISK:
                verdict = "🟡 中危"
                risky.append((t, col, mb, rows))
            else:
                verdict = "⚪ 小表可忽略"
            print(f"  {t:<32} {mb:>9,.1f} {rows:>12,}  {sortdesc:<28} {verdict}")

        print()
        if risky:
            print("=" * 100)
            print(f"⚠️ 需补索引（{len(risky)} 项）—— 补完请回写 scripts/seed_indexes.py")
            print("=" * 100)
            for t, col, mb, rows in sorted(risky, key=lambda x: -x[3]):
                print(f"  {t}  →  ALTER TABLE `{t}` ADD INDEX `idx_{col}` (`{col}`);")
                print(f"       （{mb:,.1f} MB / {rows:,} 行，排序列 {col}）")
        else:
            print("✅ 所有已记忆排序的表都有可用索引，无同类风险")

        if args.all:
            print()
            print("=" * 100)
            print("未记忆排序的大表（潜在排序面：前端一点列头就会命中）")
            print("=" * 100)
            for n, m in sorted(meta.items(), key=lambda kv: -(kv[1]["table_rows"] or 0)):
                rows = m["table_rows"] or 0
                if rows <= HIGH_RISK or n in prefs:
                    continue
                cols = ", ".join(sorted(first_cols.get(n, set()))) or "(无)"
                print(f"  {n:<32} {rows:>12,} 行   索引首列: {cols[:72]}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
