#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""交叉印证背离数历史回填（market_xcheck_daily）

用法：
    python scripts/backfill_xcheck.py                 # 只读预览（算完打印分布，不写库）
    python scripts/backfill_xcheck.py --apply         # 写入
    python scripts/backfill_xcheck.py --days 500 --apply

为什么需要回填
    market_wind 卡片要用「背离数的近一年分位」回答「今天 4 项算不算多」，
    而 xcheck_sync 只能从上线那天起逐日积累 —— 不回溯就没有分布可比。
    实测单次 `cross_checks(as_of=D)` 约 330ms，250 日 ≈ 85 秒，属一次性成本。

⚠️ 两条纪律
    1. 必须走与日常任务**完全相同**的计算入口 `cross_check.cross_checks(as_of=D)`，
       不做任何简化或近似 —— 历史序列与当日值口径不一致，算出来的分位毫无意义。
    2. 回放模式（as_of 非空）下 market_wind 已把 stale_sessions 固定报 0，
       但 `_concept` 仍按「覆盖达标日」锚定，少数日子 anchor 会早于 D（源侧分批发布的
       历史残留）。这是该函数本身的既有行为，日常值与历史值同样如此，口径一致。
"""
import argparse
import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pymysql  # noqa: E402

from app.analysis import cross_check  # noqa: E402
from app.collectors.xcheck_sync import DDL, UPSERT  # noqa: E402
from app.db import get_db_config  # noqa: E402


def _bar(n: int, total: int, width: int = 28) -> str:
    return "#" * int(round(n / max(total, 1) * width))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=250, help="回填最近多少个交易日（默认 250）")
    ap.add_argument("--apply", action="store_true", help="实际写入（默认只读预览）")
    args = ap.parse_args()

    conn = pymysql.connect(**get_db_config().to_dict())
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT trade_date FROM market_style_daily "
                        "ORDER BY trade_date DESC LIMIT %s", (args.days,))
            dates = [str(r[0]) for r in cur.fetchall()]
    finally:
        conn.close()

    if not dates:
        print("market_style_daily 无数据，无法回填")
        return 1
    dates.reverse()                                     # 升序，便于看进度
    print(f"待计算 {len(dates)} 个交易日：{dates[0]} ~ {dates[-1]}")

    rows: list[tuple] = []
    t0 = time.time()
    for i, d in enumerate(dates, 1):
        res = cross_check.cross_checks(d)
        s = res["summary"]
        items = res["items"]
        keys = ",".join(x["key"] for x in items if x["level"] == "diverge")[:255]
        rows.append((d, len(items), s.get("agree"), s.get("diverge"),
                     s.get("neutral"), s.get("nodata"), keys, datetime.now()))
        if i % 25 == 0 or i == len(dates):
            print(f"  {i:>4}/{len(dates)}  {d}  背离 {s.get('diverge')}/{len(items)}"
                  f"  累计 {time.time() - t0:.0f}s")

    # ---- 分布（这是回填的真正目的：看清「几项背离」才算异常）----
    div = [r[3] for r in rows if r[3] is not None]
    total = len(div)
    counts: dict[int, int] = {}
    for v in div:
        counts[v] = counts.get(v, 0) + 1

    def pctile_of(cur_v: int) -> float:
        le = sum(1 for v in div if v <= cur_v)
        return round(le / total * 100, 1)

    print()
    print(f"背离项数分布（{total} 个交易日）：")
    for v in sorted(counts):
        mark = "  ← 最新" if v == div[-1] else ""
        print(f"   {v} 项  {counts[v]:>4} 日  {counts[v] / total * 100:>5.1f}%  "
              f"{_bar(counts[v], total)}{mark}")
    srt = sorted(div)
    print(f"   中位 {srt[total // 2]} · 最小 {srt[0]} · 最大 {srt[-1]} · "
          f"最新 {div[-1]}（分位 {pctile_of(div[-1])}%）")

    if not args.apply:
        print()
        print("[只读预览] 未写库。加 --apply 才会写入 market_xcheck_daily。")
        return 0

    conn = pymysql.connect(**get_db_config().to_dict())
    try:
        with conn.cursor() as cur:
            cur.execute(DDL)
            cur.executemany(UPSERT, rows)
        conn.commit()
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM market_xcheck_daily")
            n = cur.fetchone()[0]
    finally:
        conn.close()
    print()
    print(f"✅ 已写入 {len(rows)} 行（market_xcheck_daily 现有 {n} 行，耗时 {time.time() - t0:.0f}s）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
