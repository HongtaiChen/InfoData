#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""CYQ 加速版对拍验收脚本：compute_cyq（原版） vs compute_cyq_fast（numba）

用途：切换采集器前后，验证 numba 加速版与纯 Python 原版逐字段 = 0 差异。
这是「只信实测」的硬验收工具，可随时复跑。

用法：
  python -m scripts.bench_cyq_compare --stocks 100
"""
from __future__ import annotations

import argparse
import time

import numpy as np
import pymysql

from app.db import get_db_config
from app.services.cyq import compute_cyq
from app.services.cyq_fast import compute_cyq_fast, _HAS_NUMBA

FIELDS = ["获利比例", "平均成本", "90成本-低", "90成本-高", "90集中度",
          "70成本-低", "70成本-高", "70集中度"]


def load_kline(conn, code):
    with conn.cursor() as cur:
        cur.execute(
            """SELECT trade_date AS date, open, high, low, close,
                      turnover_ratio AS turnover, adj_factor
               FROM stock_market_daily WHERE stock_code=%s
               ORDER BY trade_date DESC LIMIT 130""", [code])
        rows = [dict(zip([d[0] for d in cur.description], r)) for r in cur.fetchall()]
    rows.reverse()
    if not rows:
        return rows
    laf = float(rows[-1]["adj_factor"] or 1.0)
    for r in rows:
        af = float(r["adj_factor"] or 1.0)
        sc = af / laf if laf else 1.0
        for col in ("open", "high", "low", "close"):
            r[col] = float(r[col]) * sc
    return rows


def compare(a, b):
    if len(a) != len(b):
        return 0, [f"行数 {len(a)} vs {len(b)}"]
    diffs = []
    c = 0
    for i, (ra, rb) in enumerate(zip(a, b)):
        for f in FIELDS:
            va, vb = ra.get(f), rb.get(f)
            if va is None and vb is None:
                c += 1
                continue
            if va is None or vb is None:
                diffs.append(f"行{i} {f}: {va!r} vs {vb!r}")
            elif abs(float(va) - float(vb)) > 1e-6:
                diffs.append(f"行{i} {f}: {va} vs {vb}")
            else:
                c += 1
    return c, diffs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stocks", type=int, default=100)
    args = ap.parse_args()

    print(f"加速路径: {'numba JIT' if _HAS_NUMBA else '纯 Python 回退（numba 缺失）'}\n")

    conn = pymysql.connect(**get_db_config().to_dict(), read_timeout=60)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT stock_code FROM stock_market_daily "
                "WHERE turnover_ratio IS NOT NULL AND turnover_ratio > 0 LIMIT %s",
                [args.stocks])
            codes = [r[0] for r in cur.fetchall()]
    finally:
        conn.close()

    total_c = 0
    total_d = 0
    t_orig = 0.0
    t_fast = 0.0
    n_stock = 0

    conn = pymysql.connect(**get_db_config().to_dict(), read_timeout=60)
    try:
        for code in codes:
            kline = load_kline(conn, code)
            if len(kline) < 30:
                continue
            n_stock += 1

            t0 = time.perf_counter()
            a = compute_cyq(kline)
            t_orig += time.perf_counter() - t0

            t0 = time.perf_counter()
            b = compute_cyq_fast(kline)
            t_fast += time.perf_counter() - t0

            c, diffs = compare(a, b)
            total_c += c
            total_d += len(diffs)
            if diffs:
                print(f"  ❌ {code}: {len(diffs)} 差异")
                for x in diffs[:5]:
                    print(f"     {x}")
    finally:
        conn.close()

    print(f"\n===== 对拍 {n_stock} 只 =====")
    print(f"  一致字段 {total_c}，差异 {total_d}")
    print(f"  原版 {t_orig:.3f}s | 加速版 {t_fast:.3f}s | 加速比 {t_orig / max(t_fast, 1e-9):.2f}x")
    print(f"  结论: {'✅ 完全一致' if total_d == 0 else '❌ 有差异，禁止切换'}")


if __name__ == "__main__":
    main()
