#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
InvestBuddy ETF 份额历史回补脚本（etf_share_daily 全史回溯）

背景（2026-10-23）：原采集器主源 fund_etf_spot_em（东财）只给「当前最新份额」、
无逐日历史，导致 etf_share_daily 只能从采集器上线日起逐日积累、历史不可回溯。
现发现交易所官方源可回溯历史，故本脚本一次性把历史铺满：

  · 上交所官方 `fund_etf_scale_sse(date)` —— 指定任意历史交易日，返回当日全沪市
    ETF 收盘清算份额（份）。可回溯到 2019 年甚至更早（逐日拉取）。
  · 深交所官方 `fund_scale_daily_szse(start,end)` —— 日频历史，单次查询 ≤6 个月，
    返回该区间内深市 ETF 每日份额（份）。按 ≤6 个月分段循环拉取（一次多日，提速）。

口径 = 全市场股票型 ETF（含跨境/商品，排除债券/货币），筛选用库内
  fund_info.fund_type 按代码映射（与增量采集器 etf_flow_sync.STOCK_ETF_TYPES 同源）。

净流入（net_inflow）：历史**不回溯**（历史价不可回溯——腾讯只给当前价、东财历史价
  被 WAF 封禁），留 NULL；由增量采集器从采集启动后逐日正确积累（当日价 × Δ份额）。

幂等：PRIMARY KEY(trade_date) + ON DUPLICATE KEY UPDATE，重复执行安全（覆盖更新）。

⚠️ 边界：
  1. 上交所接口**当日数据晚间才发布**，回补到「昨天」为止（今天由增量采集器写）。
  2. 上交所接口对**非交易日/无数据日**抛 KeyError，本脚本按「跳过该日」处理。
  3. 深交所单次 ≤6 个月，超范围返回空表头，故严格按 6 个月分段。

用法：
    python backend/scripts/backfill_etf_share.py                # 默认 2019-01-01 起回补到昨天
    python backend/scripts/backfill_etf_share.py --start 20240101   # 指定起始日
    python backend/scripts/backfill_etf_share.py --end 20261022     # 指定结束日（含）
    python backend/scripts/backfill_etf_share.py --dry-run          # 只统计不落库
"""
import argparse
import logging
import os
import sys
import time
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # backend 根

import pymysql

from app.collectors.etf_flow_sync import STOCK_ETF_TYPES, _f, call_with_timeout
import akshare as ak

from app.db import get_db_config

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DEFAULT_START = date(2019, 1, 1)
SZSE_BATCH_DAYS = 180  # 深交所单次查询 ≤6 个月（~180 天）
_SSE_WORKERS = 4  # 上交所逐日并发线程数（官方接口单日独立，温和并发）


def _trading_days(start: date, end: date) -> list[date]:
    """生成 [start, end] 区间内的工作日序列（周一~周五，忽略节假日）。

    用工作日近似交易日；非交易日会被官方源返回空/报错，脚本会跳过。
    """
    days = []
    d = start
    while d <= end:
        if d.weekday() < 5:  # 周一~周五
            days.append(d)
        d += timedelta(days=1)
    return days


def fetch_stock_etf_codes(conn) -> set[str]:
    """股票型 ETF 代码集合（与增量采集器同源筛选）。"""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT fund_code FROM fund_info WHERE fund_type IN (%s)"
            % ",".join(["%s"] * len(STOCK_ETF_TYPES)),
            list(STOCK_ETF_TYPES),
        )
        return {str(r["fund_code"]) for r in cur.fetchall()}


def _fetch_sse_day(d: date) -> dict[str, float]:
    """上交所单日：返回 {code: share}。非交易日/无数据抛异常。"""
    df = call_with_timeout(ak.fund_etf_scale_sse, 60, date=d.strftime("%Y%m%d"))
    if df is None or df.empty:
        return {}
    code_col = next((c for c in df.columns if "代码" in str(c)), None)
    share_col = next((c for c in df.columns if "份额" in str(c)), None)
    if not code_col or not share_col:
        raise RuntimeError(f"fund_etf_scale_sse 列名变更：{list(df.columns)}")
    out: dict[str, float] = {}
    for _, r in df.iterrows():
        code = str(r.get(code_col)).zfill(6)
        sh = _f(r.get(share_col))
        if sh is not None:
            out[code] = sh
    return out


def _fetch_sse_days_parallel(days: list[date], workers: int = 4) -> dict[date, dict[str, float]]:
    """上交所逐日并发拉取（官方接口单日独立，可温和并发）。返回 {date: {code: share}}。"""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    result: dict[date, dict[str, float]] = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        future_map = {ex.submit(_fetch_sse_day, d): d for d in days}
        done = 0
        for fut in as_completed(future_map):
            d = future_map[fut]
            try:
                result[d] = fut.result()
            except Exception:  # noqa: BLE001 - 非交易日/无数据，跳过
                result[d] = {}
            done += 1
            if done % 500 == 0:
                logger.info("  上交所并发进度 %d/%d", done, len(days))
    return result


def _fetch_szse_batch(start: date, end: date) -> dict[date, dict[str, float]]:
    """深交所分段（≤6 个月）：返回 {date: {code: share}}。"""
    df = call_with_timeout(ak.fund_scale_daily_szse, 120,
                           start_date=start.strftime("%Y%m%d"),
                           end_date=end.strftime("%Y%m%d"), symbol="ETF")
    out: dict[date, dict[str, float]] = {}
    if df is None or df.empty:
        return out
    code_col = next((c for c in df.columns if "代码" in str(c)), None)
    share_col = next((c for c in df.columns if "份额" in str(c)), None)
    date_col = next((c for c in df.columns if "日期" in str(c)), None)
    if not code_col or not share_col or not date_col:
        raise RuntimeError(f"fund_scale_daily_szse 列名变更：{list(df.columns)}")
    for _, r in df.iterrows():
        dval = r.get(date_col)
        # 统一成 date：pandas 可能返回 datetime.date / datetime.datetime / Timestamp
        if isinstance(dval, date):
            pass  # 已是 date（含 date 子类）
        else:
            try:
                dval = dval.date()  # datetime / pandas Timestamp
            except AttributeError:
                continue
        code = str(r.get(code_col)).zfill(6)
        sh = _f(r.get(share_col))
        if sh is None:
            continue
        out.setdefault(dval, {})[code] = sh
    return out


def aggregate_share(sse: dict[str, float], szse: dict[str, float],
                    stock_etf_codes: set[str]) -> tuple[float, int]:
    """沪深合并 + 股票型筛选：返回 (total_share, fund_count)。"""
    merged: dict[str, float] = {}
    merged.update(szse)  # 深市在前（代码 15/16 段）
    for code, sh in sse.items():
        merged.setdefault(code, sh)  # 沪市（代码 5 段），不覆盖深市同名
    total = 0.0
    cnt = 0
    for code, sh in merged.items():
        if stock_etf_codes and code not in stock_etf_codes:
            continue
        total += sh
        cnt += 1
    return total, cnt


def main() -> None:
    ap = argparse.ArgumentParser(description="ETF 份额历史回补")
    ap.add_argument("--start", default=DEFAULT_START.strftime("%Y%m%d"),
                    help="起始日 YYYYMMDD（默认 20190101）")
    ap.add_argument("--end", default="", help="结束日 YYYYMMDD（默认昨天）")
    ap.add_argument("--dry-run", action="store_true", help="只统计不落库")
    args = ap.parse_args()

    start = date(int(args.start[:4]), int(args.start[4:6]), int(args.start[6:8]))
    end = (date(int(args.end[:4]), int(args.end[4:6]), int(args.end[6:8]))
           if args.end else date.today() - timedelta(days=1))

    conn = pymysql.connect(**get_db_config().to_dict(),
                           cursorclass=pymysql.cursors.DictCursor)
    try:
        stock_etf_codes = fetch_stock_etf_codes(conn)
        logger.info("股票型 ETF 代码集合：%d 只", len(stock_etf_codes))

        days = _trading_days(start, end)
        logger.info("回补区间 %s ~ %s，工作日 %d 天（含节假日会跳过）", start, end, len(days))

        # ① 深交所：分段一次拉多日（≤6 个月/段，失败自动对半拆分重试）
        logger.info("拉取深交所历史（分段 ≤%d 天/段，失败降半重试）...", SZSE_BATCH_DAYS)
        szse_map: dict[date, dict[str, float]] = {}

        def _szse_seg(seg_start: date, seg_end: date):
            """递归拉取一段；失败则对半拆分重试（应对 BadZipFile 等大段偶发失败）。"""
            if seg_start > seg_end:
                return
            span = (seg_end - seg_start).days
            try:
                batch = _fetch_szse_batch(seg_start, seg_end)
                szse_map.update(batch)
                logger.info("  深交所 %s ~ %s：%d 个交易日", seg_start, seg_end, len(batch))
            except Exception as e:  # noqa: BLE001
                if span <= 0:
                    logger.warning("  深交所 %s 失败（%s: %s），放弃该日",
                                   seg_start, type(e).__name__, str(e)[:80])
                    return
                mid = seg_start + timedelta(days=span // 2)
                logger.warning("  深交所 %s ~ %s 失败（%s: %s），对半拆分重试",
                               seg_start, seg_end, type(e).__name__, str(e)[:60])
                time.sleep(1.0)
                _szse_seg(seg_start, mid)
                _szse_seg(mid + timedelta(days=1), seg_end)

        seg_start = start
        while seg_start <= end:
            seg_end = min(seg_start + timedelta(days=SZSE_BATCH_DAYS), end)
            _szse_seg(seg_start, seg_end)
            seg_start = seg_end + timedelta(days=1)

        # ② 上交所逐日并发拉取
        logger.info("拉取上交所历史（并发 %d 线程，%d 个工作日）...", _SSE_WORKERS, len(days))
        sse_map = _fetch_sse_days_parallel(days, workers=_SSE_WORKERS)

        # ③ 合并 + 落库
        written = 0
        skipped = 0
        for d in days:
            sse = sse_map.get(d, {})
            szse = szse_map.get(d, {})
            if not sse and not szse:
                skipped += 1
                continue

            total_share, fund_count = aggregate_share(sse, szse, stock_etf_codes)
            if fund_count == 0:
                skipped += 1
                continue

            if not args.dry_run:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO etf_share_daily "
                        "(trade_date, total_share, net_inflow, fund_count, update_time, data_source) "
                        "VALUES (%s, %s, NULL, %s, NOW(), 'exchange') "
                        "ON DUPLICATE KEY UPDATE total_share=VALUES(total_share), "
                        "fund_count=VALUES(fund_count), update_time=NOW(), data_source='exchange'",
                        (d, round(total_share, 4), fund_count),
                    )
                conn.commit()

            written += 1
            if written % 100 == 0 or written == 1:
                logger.info("  [%d/%d] %s total_share=%s fund_count=%d",
                            written, len(days), d, f"{total_share:,.0f}", fund_count)

        logger.info("✅ 回补完成：写入 %d 天，跳过 %d 天（非交易日/无数据）", written, skipped)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
