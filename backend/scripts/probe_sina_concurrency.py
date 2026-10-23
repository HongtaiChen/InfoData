#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""新浪源并发限流探针（只读诊断，不写库）

目的：实测 stock_zh_a_daily 在 N 线程并发下（每票拉 raw + hfq 两次）的
吞吐、错误率、是否有被限流/封 IP 的迹象。用于决定 stock_daily_incr 的
并发能否从 4 提到 6/8。

用法：
  python -m scripts.probe_sina_concurrency --workers 8 --stocks 60

安全：
  - 只读，不写任何表；
  - 默认只测 60 只、单票延时 0（并发下不加人工延时，压出真实上限）；
  - 每票拉「近 10 个自然日」的小窗口，快速返回。
"""
import argparse
import os
import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

os.environ["NO_PROXY"] = "*"
os.environ["no_proxy"] = "*"

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.collectors.stock_daily_core import code_to_symbol, warmup_js_engine  # noqa: E402


def fetch_pair(code: str, start: str, end: str):
    import akshare as ak
    sym = code_to_symbol(code)
    raw = ak.stock_zh_a_daily(symbol=sym, start_date=start, end_date=end, adjust="")
    hfq = ak.stock_zh_a_daily(symbol=sym, start_date=start, end_date=end, adjust="hfq")
    return len(raw) if raw is not None else 0, len(hfq) if hfq is not None else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--stocks", type=int, default=60)
    ap.add_argument("--delay", type=float, default=0.0, help="单票延时（秒），默认 0 压上限")
    args = ap.parse_args()

    from app.db import get_db_config
    import pymysql
    conn = pymysql.connect(**get_db_config().to_dict())
    cur = conn.cursor()
    cur.execute(
        "SELECT stock_code FROM stock_info "
        "WHERE stock_code NOT LIKE '200%%' AND stock_code NOT LIKE '201%%' "
        "AND stock_code NOT LIKE '900%%' AND short_name NOT LIKE '%%退%%' "
        "AND short_name NOT LIKE 'PT%%' ORDER BY stock_code LIMIT %s",
        (args.stocks,),
    )
    codes = [r[0] for r in cur.fetchall()]
    conn.close()

    from datetime import datetime, timedelta
    end = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=10)).strftime("%Y%m%d")

    print(f"预热 V8 ...")
    print(warmup_js_engine())
    print(f"开始：{len(codes)} 只 × 2 请求，{args.workers} 线程，延时 {args.delay}s\n")

    t0 = time.time()
    ok = 0
    errors = {}
    total_raw = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(fetch_pair, c, start, end): c for c in codes}
        for fut in as_completed(futs):
            c = futs[fut]
            try:
                r, h = fut.result()
                ok += 1
                total_raw += r
            except Exception as e:  # noqa: BLE001
                key = type(e).__name__
                errors[key] = errors.get(key, 0) + 1
    dt = time.time() - t0
    nreq = len(codes) * 2
    print(f"\n=== 结果 ===")
    print(f"成功 {ok}/{len(codes)} 只（{nreq} 次请求），耗时 {dt:.1f}s")
    print(f"吞吐：{nreq/dt:.2f} 次/秒（{ok/dt:.2f} 只/秒）")
    print(f"单票平均 {dt/len(codes)*1000:.0f} ms")
    if errors:
        print(f"错误分布：{errors}")
    else:
        print("错误：0")


if __name__ == "__main__":
    main()
