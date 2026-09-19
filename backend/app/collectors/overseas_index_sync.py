#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
InvestBuddy 海外/港股指数日线采集器（overseas_index_daily）—— 蓝图 E「跨市场与外部情绪」

为什么需要（报告 §1.1 / §6 蓝图 E）：
  市场风向现有 35 列全部是「A 股内部比」，没有任何外部参照物。跨市场是最便宜的外部参照：
  外盘大跌而 A 股抗跌 = 韧性；外盘领涨而 A 股不跟 = 内部资金问题。
  **只有两边都有，才谈得上「抗跌」这个判断**——单看 A 股跌幅说明不了任何事。

两只源（均为新浪，实测 0.1~0.3s，全量历史）：
  · 恒生指数      stock_hk_index_daily_sina(symbol='HSI')        ~3,220 行
  · 美股三大指数  index_us_stock_sina(symbol='.DJI/.INX/.IXIC')  ~5,716 行

幂等：uk_code_date(index_code, trade_date) + 增量（起点 = 各指数本地 MAX(trade_date) 的次日）。
首次运行会全量补齐（约 2 万行），之后每日只写增量。
"""
import logging
from datetime import date, datetime, timedelta

import pymysql
import akshare as ak

from ..db import get_db_config
from ._common import with_steps, call_with_timeout

logger = logging.getLogger(__name__)

RUN_STEPS = [
    {"no": 1, "name": "读取本地水位", "params": "逐指数 MAX(trade_date)（首次无数据则回扫 first_lookback_days 天，但源会返回全量）"},
    {"no": 2, "name": "拉取海外指数", "params": "恒生 stock_hk_index_daily_sina(HSI) + 美股 index_us_stock_sina(.DJI/.INX/.IXIC)"},
    {"no": 3, "name": "增量过滤", "params": "仅保留 trade_date > 本地 MAX 的行；剔除非交易日空行"},
    {"no": 4, "name": "批量 Upsert", "params": "uk_code_date(index_code, trade_date) 幂等"},
]

# (index_code, 中文名, 源类型, 源 symbol)
OVERSEAS = [
    ("HSI", "恒生指数", "hk", "HSI"),
    ("DJI", "道琼斯工业平均", "us", ".DJI"),
    ("SPX", "标普500", "us", ".INX"),
    ("IXIC", "纳斯达克综合", "us", ".IXIC"),
]
_TGT = ["index_code", "index_name", "trade_date",
        "open", "high", "low", "close", "volume", "amount"]


def _d(v) -> date | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()[:10]
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _f(v, nd: int = 4):
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else round(f, nd)


class OverseasIndexSyncCollector:
    """海外/港股指数日线增量同步"""

    def __init__(self, timeout_sec: float = 60):
        self.timeout_sec = float(timeout_sec)

    @staticmethod
    def _fetch(kind: str, symbol: str, timeout: float):
        fn = ak.stock_hk_index_daily_sina if kind == "hk" else ak.index_us_stock_sina
        return call_with_timeout(fn, timeout, symbol=symbol)

    def run(self) -> dict:
        conn = pymysql.connect(**get_db_config().to_dict(),
                               cursorclass=pymysql.cursors.DictCursor)
        written, errors, per = 0, [], {}
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT index_code, MAX(trade_date) AS d FROM overseas_index_daily "
                            "GROUP BY index_code")
                wm = {r["index_code"]: r["d"] for r in cur.fetchall()}

                insert_sql = (
                    f"INSERT INTO overseas_index_daily ({', '.join(_TGT)}, update_time, data_source) "
                    f"VALUES ({', '.join(['%s'] * len(_TGT))}, NOW(), 'AKSHARE') "
                    "ON DUPLICATE KEY UPDATE "
                    + ", ".join(f"{c}=VALUES({c})" for c in _TGT if c not in
                                ("index_code", "trade_date"))
                    + ", update_time=NOW()"
                )

                for code, name, kind, symbol in OVERSEAS:
                    try:
                        df = self._fetch(kind, symbol, self.timeout_sec)
                    except Exception as e:              # noqa: BLE001
                        errors.append(f"{code} {name}: {type(e).__name__} {str(e)[:60]}")
                        continue
                    if df is None or df.empty:
                        errors.append(f"{code} {name}: 返回为空")
                        continue

                    last = wm.get(code)
                    rows = []
                    for _, r in df.iterrows():
                        d = _d(r.get("date"))
                        if d is None or (last is not None and d <= last):
                            continue
                        c = _f(r.get("close"), 4)
                        if c is None:
                            continue          # 无收盘价的行（停牌/占位）不入库
                        rows.append((code, name, d,
                                     _f(r.get("open"), 4), _f(r.get("high"), 4),
                                     _f(r.get("low"), 4), c,
                                     _f(r.get("volume"), 2), _f(r.get("amount"), 2)))
                    if rows:
                        cur.executemany(insert_sql, rows)
                        conn.commit()
                    written += len(rows)
                    per[code] = len(rows)
                    logger.info("  %s %s：源 %s 行 → 增量 %s 行（本地至 %s）",
                                code, name, len(df), len(rows), last)

            with conn.cursor() as cur:
                cur.execute("SELECT index_code, COUNT(*) AS n, MAX(trade_date) AS d "
                            "FROM overseas_index_daily GROUP BY index_code ORDER BY index_code")
                stat = cur.fetchall()
        finally:
            conn.close()

        msg = f"海外指数新增 {written} 行（{len(per)}/{len(OVERSEAS)} 个指数）"
        if errors:
            msg += f"；异常 {len(errors)} 项"
        logger.info("✅ %s", msg)
        return with_steps(
            {"records_written": written, "error_count": len(errors), "errors": errors[:20], "note": msg},
            RUN_STEPS,
            {
                1: " · ".join(f"{c}至{wm.get(c) or '无'}" for c, _, _, _ in OVERSEAS),
                2: f"拉取 {len(per)}/{len(OVERSEAS)} 个",
                3: " · ".join(f"{c} +{per[c]}" for c in per) or "无增量",
                4: "；".join(f"{r['index_code']} {r['n']}行至{r['d']}" for r in stat),
            },
        )
