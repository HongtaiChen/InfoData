#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
InvestBuddy 银行间拆借利率 + LPR 采集器（interbank_rate_daily）

蓝图的哪一块：**A · 杠杆与流动性（资金面）**里的「钱**贵不贵**」这一维。
  报告 §6 蓝图 A 原本只有三样能答「钱敢不敢借 / 放哪 / 愿不愿来」：
    两融余额（★库内，敢不敢借）· 10Y 国债（★库内，无风险锚）· 成交额（已有，愿不愿来）
  —— 唯独「钱贵不贵」（短端资金价格）**一个数据都没有**，这直接导致 A 块只能答一半。
  本采集器补上 Shibor（银行间短端）与 LPR（贷款端政策价）。

数据源与口径：
  · Shibor：`ak.rate_interbank(market='上海银行同业拆借市场', symbol='Shibor人民币')`
    取 隔夜 / 1周 / 1月 / 3月 四个期限（短端看「钱多不多」，3M 看「资金面趋势」）。
    源来自东财数据中心域（`datacenter-web`），实测可达；单期限约 3s（10 页）。
  · LPR：`ak.macro_china_lpr()` → LPR1Y / LPR5Y。

⚠️ 一个刻意的语义选择：**LPR 按「当日生效值」顺延填充**。
    LPR 每月 20 日报价一次，两次报价之间保持不变（这就是「贷款市场报价利率」的定义）。
    若只在公布日写一行，则任何按日 join 的分析都会在其余 29 天拿到 NULL。
    故本表把 LPR 铺到每个自然日（公布日起顺延到下次公布），列语义 = 「该日**生效**的 LPR」。
    这是本表唯一的派生行为，在此显式声明，避免后来者误以为库里存的是逐日原始报价。

幂等：主键 trade_date + 全量 upsert（表仅约 5 千行，重跑无成本）。
"""
import logging
from datetime import date, datetime

import pymysql
import akshare as ak

from ..db import get_db_config
from ._common import with_steps, call_with_timeout

logger = logging.getLogger(__name__)

RUN_STEPS = [
    {"no": 1, "name": "拉取 Shibor", "params": "rate_interbank × 4 期限（隔夜/1周/1月/3月，东财数据中心域）"},
    {"no": 2, "name": "拉取 LPR", "params": "macro_china_lpr（LPR1Y / LPR5Y，月度报价）"},
    {"no": 3, "name": "日期并集 + LPR 顺延", "params": "LPR 按「公布后生效至下次公布」铺到每个自然日（本表唯一边派生化）"},
    {"no": 4, "name": "全量 Upsert", "params": "PRIMARY KEY(trade_date)；表约 5 千行，重跑无成本"},
]

_MARKET = "上海银行同业拆借市场"
_SYMBOL = "Shibor人民币"
TENORS = [("隔夜", "shibor_on"), ("1周", "shibor_1w"), ("1月", "shibor_1m"), ("3月", "shibor_3m")]
_TGT = ["trade_date", "shibor_on", "shibor_1w", "shibor_1m", "shibor_3m", "lpr_1y", "lpr_5y"]


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


def _f(v):
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else round(f, 4)


class InterbankRateSyncCollector:
    """Shibor + LPR 日频同步"""

    def __init__(self, timeout_sec: float = 90):
        self.timeout_sec = float(timeout_sec)

    def run(self) -> dict:
        errors: list[str] = []
        series: dict[str, dict[date, float]] = {}

        for indicator, key in TENORS:
            try:
                df = call_with_timeout(ak.rate_interbank, self.timeout_sec,
                                       market=_MARKET, symbol=_SYMBOL, indicator=indicator)
            except Exception as e:                     # noqa: BLE001
                errors.append(f"Shibor {indicator}: {type(e).__name__} {str(e)[:60]}")
                series[key] = {}
                continue
            m: dict[date, float] = {}
            if df is not None and not df.empty:
                for _, r in df.iterrows():
                    d, v = _d(r.get("报告日")), _f(r.get("利率"))
                    if d is not None and v is not None:
                        m[d] = v
            series[key] = m
            logger.info("  Shibor %s：%s 行", indicator, len(m))

        # ---- LPR ----
        lpr: dict[date, tuple] = {}
        try:
            ldf = call_with_timeout(ak.macro_china_lpr, self.timeout_sec)
            if ldf is not None and not ldf.empty:
                for _, r in ldf.iterrows():
                    d = _d(r.get("TRADE_DATE"))
                    y1 = _f(r.get("LPR1Y"))
                    if d is not None and y1 is not None:
                        lpr[d] = (y1, _f(r.get("LPR5Y")))
        except Exception as e:                         # noqa: BLE001
            errors.append(f"LPR: {type(e).__name__} {str(e)[:60]}")
        logger.info("  LPR：%s 个公布日", len(lpr))

        if not any(series.values()) and not lpr:
            raise RuntimeError("Shibor 与 LPR 均未取到数据：" + "; ".join(errors[:4]))

        # ---- 组装 ----
        all_dates = sorted(set().union(*[set(m) for m in series.values()]) | set(lpr))
        rows, cur_lpr = [], (None, None)
        for d in all_dates:
            if d in lpr:
                cur_lpr = lpr[d]
            vals = [series[k].get(d) for _, k in TENORS]
            if all(v is None for v in vals) and all(v is None for v in cur_lpr):
                continue
            rows.append((d, *vals, cur_lpr[0], cur_lpr[1]))

        if not rows:
            raise RuntimeError("Shibor/LPR 组装后无有效行")

        conn = pymysql.connect(**get_db_config().to_dict(),
                               cursorclass=pymysql.cursors.DictCursor)
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS n FROM interbank_rate_daily")
                before = cur.fetchone()["n"]
                sql = (
                    f"INSERT INTO interbank_rate_daily ({', '.join(_TGT)}, update_time, data_source) "
                    f"VALUES ({', '.join(['%s'] * len(_TGT))}, NOW(), 'AKSHARE') "
                    "ON DUPLICATE KEY UPDATE "
                    + ", ".join(f"{c}=COALESCE(VALUES({c}), {c})" for c in _TGT if c != "trade_date")
                    + ", update_time=NOW()"
                )
                cur.executemany(sql, rows)
            conn.commit()
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS n, MAX(trade_date) AS d, "
                            "COUNT(shibor_on) AS ns, COUNT(lpr_1y) AS nl FROM interbank_rate_daily")
                r = cur.fetchone()
            new_rows = max(0, r["n"] - before)
        finally:
            conn.close()

        msg = (f"Shibor+LPR upsert {len(rows)} 天（新增 {new_rows}）｜"
               f"Shibor 覆盖 {r['ns']} 天 · LPR 覆盖 {r['nl']} 天 · 最新 {r['d']}")
        logger.info("✅ %s", msg)
        return with_steps(
            {"records_written": new_rows if new_rows else len(rows),
             "error_count": len(errors), "errors": errors[:20], "note": msg},
            RUN_STEPS,
            {
                1: " · ".join(f"{ind} {len(series[k])} 行" for ind, k in TENORS),
                2: f"{len(lpr)} 个公布日（月度报价）",
                3: f"日期并集 {len(all_dates)} 天，LPR 顺延填充",
                4: f"upsert {len(rows)} 行（表内 {r['n']} 行，最新 {r['d']}）",
            },
        )
