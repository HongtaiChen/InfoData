#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""InvestBuddy 关键性能索引播种（幂等，可重复执行）—— 索引的唯一事实来源。

用法：python scripts/seed_indexes.py            # 只读预览（列出应存在/缺失/多余的索引）
      python scripts/seed_indexes.py --apply    # 实际创建缺失索引

为什么要有这个脚本（2026-09-14 立）：
  market_style_sync 新增「市场宽度」计算时发现 stock_market_daily（1,800 万行 / 2.2GB）
  的窗口函数查询会走 idx_trade_date + filesort，仅 96 个交易日就耗时 74 秒，
  全史（5,300 交易日）完全不可用。落地覆盖索引后查询可流式读取。
  索引此前只存在于 DBA 手工操作里，与「种子脚本是唯一事实来源」纪律不符，故固化于此。

⚠️ 维护纪律：手工加的索引要回写本文件，否则换机会漏建、性能静默退化
  （采集器只能打 WARNING 提示，不会失败）。
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pymysql  # noqa: E402

from app.db import get_db_config  # noqa: E402

# (table, index_name, ddl_columns, 用途说明)
INDEXES = [
    (
        "stock_market_daily",
        "idx_breadth_cover",
        "(stock_code, trade_date, close, change_pct)",
        "市场宽度窗口函数覆盖索引：按分区键顺序流式读取，免 filesort + 免回表",
    ),
]


def current_indexes(cur, table: str) -> dict:
    """{index_name: [cols...]}"""
    cur.execute("SHOW INDEX FROM `%s`" % table)
    out: dict = {}
    for r in cur.fetchall():
        # SHOW INDEX: Key_name(2), Seq_in_index(3), Column_name(4)
        name, seq, col = r[2], r[3], r[4]
        out.setdefault(name, {})[seq] = col
    return {k: [v[i] for i in sorted(v)] for k, v in out.items()}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="实际创建索引（默认仅预览）")
    args = ap.parse_args()

    conn = pymysql.connect(**get_db_config().to_dict())
    try:
        with conn.cursor() as cur:
            for table, name, cols, _purpose in INDEXES:
                have = current_indexes(cur, table)
                want_cols = [c.strip() for c in cols.strip("()").split(",")]
                if name in have:
                    ok = have[name] == want_cols
                    print(f"[{'OK ' if ok else 'DRIFT'}] {table}.{name} 现存列 = {have[name]}"
                          + ("" if ok else f"，期望 {want_cols}"))
                    continue
                print(f"[MISS] {table}.{name} 缺失，列 = {want_cols}")
                if not args.apply:
                    continue
                print(f"   → 创建中（{table} 行数较多，可能需数分钟）…", flush=True)
                import time
                t0 = time.time()
                cur.execute(f"ALTER TABLE `{table}` ADD INDEX `{name}` {cols}")
                conn.commit()
                print(f"   ✅ 完成，耗时 {time.time() - t0:.1f}s")

            if not args.apply:
                print("\n（预览模式，未做任何改动；加 --apply 执行）")
            else:
                print("\n=== 最终索引状态 ===")
                for table, name, _cols, _purpose in INDEXES:
                    have = current_indexes(cur, table)
                    print(f"  {table}.{name}: {'存在' if name in have else '缺失'}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
