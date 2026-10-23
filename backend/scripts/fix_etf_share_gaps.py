#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
InvestBuddy ETF 份额回补修复脚本 —— 修复深交所缺失的日期

背景：全史回补时，深交所 fund_scale_daily_szse 对大日期范围查询在「部分日期」
（多为月初/月末交界、节假日后首日）返回了不完整数据（缺深市 ETF），导致这些天
fund_count 骤降、total_share 少了约 70%。本脚本对这些日期用「单日精确查询」
（start=end=当天）重新拉深交所，与上交所合并重算，修正 total_share/fund_count。

用法：
    python backend/scripts/fix_etf_share_gaps.py            # 自动检测并修复
    python backend/scripts/fix_etf_share_gaps.py --dry-run  # 只检测不修复
"""
import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # backend 根

import pymysql
import akshare as ak

from app.collectors.etf_flow_sync import STOCK_ETF_TYPES, _f, call_with_timeout
from app.db import get_db_config

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _fetch_szse_single_day(d):
    """深交所单日精确查询：返回 {code: share}。"""
    ds = d.strftime("%Y%m%d")
    df = call_with_timeout(ak.fund_scale_daily_szse, 60, start_date=ds, end_date=ds, symbol="ETF")
    out: dict[str, float] = {}
    if df is None or df.empty:
        return out
    code_col = next((c for c in df.columns if "代码" in str(c)), None)
    share_col = next((c for c in df.columns if "份额" in str(c)), None)
    if not code_col or not share_col:
        raise RuntimeError(f"fund_scale_daily_szse 列名变更：{list(df.columns)}")
    for _, r in df.iterrows():
        code = str(r.get(code_col)).zfill(6)
        sh = _f(r.get(share_col))
        if sh is not None:
            out[code] = sh
    return out


def _fetch_sse_day(d):
    """上交所单日：返回 {code: share}。"""
    ds = d.strftime("%Y%m%d")
    df = call_with_timeout(ak.fund_etf_scale_sse, 60, date=ds)
    out: dict[str, float] = {}
    if df is None or df.empty:
        return out
    code_col = next((c for c in df.columns if "代码" in str(c)), None)
    share_col = next((c for c in df.columns if "份额" in str(c)), None)
    if not code_col or not share_col:
        raise RuntimeError(f"fund_etf_scale_sse 列名变更：{list(df.columns)}")
    for _, r in df.iterrows():
        code = str(r.get(code_col)).zfill(6)
        sh = _f(r.get(share_col))
        if sh is not None:
            out[code] = sh
    return out


def _detect_gaps(conn):
    """检测 fund_count 显著低于「前后各 3 日中位数」的日期（深交所缺失）。

    用移动中位数而非相邻日，是因为深交所缺失可能**连续出现**（如跨周末两天都缺），
    相邻日比较会漏检。中位数对「单个/连续异常」都鲁棒。
    """
    with conn.cursor() as cur:
        cur.execute("SELECT trade_date, fund_count FROM etf_share_daily ORDER BY trade_date")
        rows = cur.fetchall()
    fc = [r["fund_count"] for r in rows]
    gaps = []
    n = len(rows)
    for i in range(n):
        cur_fc = fc[i]
        if not cur_fc:
            continue
        # 前后各取 3 个非空 fund_count 作参考
        refs = []
        for j in range(max(0, i - 3), min(n, i + 4)):
            if j != i and fc[j]:
                refs.append(fc[j])
        if len(refs) < 3:
            continue
        refs.sort()
        median = refs[len(refs) // 2]
        if median and cur_fc < median * 0.6:
            gaps.append(rows[i]["trade_date"])
    return gaps


def main():
    ap = argparse.ArgumentParser(description="修复 ETF 份额回补的深交所缺失日期")
    ap.add_argument("--dry-run", action="store_true", help="只检测不修复")
    args = ap.parse_args()

    conn = pymysql.connect(**get_db_config().to_dict(), cursorclass=pymysql.cursors.DictCursor)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT fund_code FROM fund_info WHERE fund_type IN (%s)"
                % ",".join(["%s"] * len(STOCK_ETF_TYPES)),
                list(STOCK_ETF_TYPES),
            )
            stock_etf_codes = {str(r["fund_code"]) for r in cur.fetchall()}

        gaps = _detect_gaps(conn)
        logger.info("检测到 %d 个深交所缺失日期：%s", len(gaps), gaps)

        fixed = 0
        for d in gaps:
            try:
                sse = _fetch_sse_day(d)
                szse = _fetch_szse_single_day(d)
            except Exception as e:  # noqa: BLE001
                logger.warning("  %s 拉取失败（%s: %s），跳过", d, type(e).__name__, str(e)[:80])
                continue

            merged: dict[str, float] = {}
            merged.update(szse)
            for code, sh in sse.items():
                merged.setdefault(code, sh)

            total = 0.0
            cnt = 0
            for code, sh in merged.items():
                if stock_etf_codes and code not in stock_etf_codes:
                    continue
                total += sh
                cnt += 1

            if cnt == 0:
                continue

            if not args.dry_run:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE etf_share_daily SET total_share=%s, fund_count=%s, "
                        "update_time=NOW() WHERE trade_date=%s",
                        (round(total, 4), cnt, d),
                    )
                conn.commit()

            logger.info("  %s 修复：上交所 %d 只 + 深交所 %d 只 → total=%s, fc=%d",
                        d, len(sse), len(szse), f"{total:,.0f}", cnt)
            fixed += 1

        logger.info("✅ 修复完成：%d/%d 个日期", fixed, len(gaps))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
