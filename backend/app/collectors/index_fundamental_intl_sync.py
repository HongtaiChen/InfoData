#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
投资环境模块 · 国际指数「基本面分析」—— 估值扩展采集器（index_fundamental_intl_sync）

把国际指数基本面里「估值水平」块缺的两个指标补齐到 index_valuation_intl 表
（长表：index_code, metric, trade_date, value），并附带盈利质量锚点。

数据源分层（2026-10-21 实测）：
  1. **标普500 股息率 / CAPE / PE** —— Robert Shiller 官网 `ie_data.xls`（权威学术源，
     1871 年起月度，免费）。它是全球唯一「1871 至今」的连续股息率/CAPE 长序列。
     落 metric = dividend_yield（回溯 12 个月股息 ÷ 月末价格）。
     ⚠️ Shiller 数据更新滞后：最新完整到 2023-06（官网约每年/半年更新一次）。
  2. **国际指数 PB / 股息率** —— Siblis 免费层 pb / dividend-yield 恒为 null（付费才有），
     MSCI Fact Sheet 需登录，iShares/MacroMicro 仅网页展示无稳定 API。
     ⇒ 本采集器**不造假、不硬凑**：PB 与除标普500外的股息率**如实留空**，
     由前端标注「数据源待接入（需 Siblis 付费层 / MSCI Fact Sheet 登录）」。
  3. **ROE 锚点（盈利质量块）** —— Damodaran 年度国家 ROE 作为长期中枢锚点，
     但该源按「行业」非「指数」，口径不一致，本期**不落地**，前端同样标注缺口。

落库策略：复用 index_valuation_intl 表（uk: index_code+metric+trade_date 幂等 upsert），
只追加 metric='dividend_yield' 的 SPX 序列，不动已有 pe_ttm/pe_fwd/cape。

⚠️ 诚实标注：估值类指标「免费可得」的边界就在 pe_ttm/pe_fwd/cape（Siblis）+ 标普500
股息率（Shiller）。PB、其余指数股息率、指数级 ROE/EPS 增速/盈利修正比率**均无免费源**，
本采集器不伪造，前端以「数据源待接入」显式暴露缺口。
"""
import logging
from datetime import datetime

import pymysql
import pandas as pd

from ..db import get_db_config
from ._common import with_steps

logger = logging.getLogger(__name__)

RUN_STEPS = [
    {"no": 1, "name": "拉取 Shiller 标普500", "params": "ie_data.xls（股息/价格/CAPE）"},
    {"no": 2, "name": "算股息率序列", "params": "dividend_yield = 12M 股息 ÷ 月末价格"},
    {"no": 3, "name": "落库 index_valuation_intl", "params": "metric=dividend_yield, code=SPX 幂等 upsert"},
]

_SHILLER_URL = "http://www.econ.yale.edu/~shiller/data/ie_data.xls"


def _f(v, nd: int = 4):
    if v is None:
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    if x != x:  # NaN
        return None
    return round(x, nd)


class IndexFundamentalIntlSyncCollector:
    """国际指数估值扩展同步（Shiller 标普500 股息率）"""

    def __init__(self, timeout_sec: float = 60):
        self.timeout_sec = float(timeout_sec)

    def _fetch_shiller(self) -> list[tuple]:
        """解析 Shiller ie_data.xls → [(trade_date, dividend_yield_pct)]。

        列（skiprows=7 后）：Date / P / D / E / CPI / Fraction / Rate GS10 / ...
        股息率口径 = 过去 12 个月股息 ÷ 当月价格（Shiller 官方回溯口径）。
        注意 Date 是浮点年月（如 2023.06），需还原成月末日期。
        """
        df = pd.read_excel(_SHILLER_URL, sheet_name="Data", skiprows=7)
        df = df.dropna(subset=["Date"])
        out = []
        for _, r in df.iterrows():
            dval = _f(r.get("D"))
            pval = _f(r.get("P"))
            if dval is None or pval is None or pval <= 0:
                continue
            # Date 形如 2023.06 → 月末日
            raw = float(r["Date"])
            y = int(raw)
            m = int(round((raw - y) * 100))
            if m < 1 or m > 12:
                continue
            # 月末日
            if m == 12:
                nxt = datetime(y + 1, 1, 1)
            else:
                nxt = datetime(y, m + 1, 1)
            from datetime import timedelta
            last_day = (nxt - timedelta(days=1)).date()
            dy = round(dval / pval * 100, 4)
            out.append((last_day, dy))
        return out

    def run(self) -> dict:
        conn = pymysql.connect(**get_db_config().to_dict(),
                               cursorclass=pymysql.cursors.DictCursor)
        written = 0
        errors: list[str] = []
        try:
            rows = self._fetch_shiller()
            if not rows:
                raise RuntimeError("Shiller 标普500 数据解析为空")

            with conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO index_valuation_intl "
                    "(index_code, metric, trade_date, value, update_time, data_source) "
                    "VALUES (%s,%s,%s,%s,NOW(),'SHILLER') "
                    "ON DUPLICATE KEY UPDATE value=VALUES(value), update_time=NOW()",
                    [("SPX", "dividend_yield", d, v) for d, v in rows],
                )
            conn.commit()
            written = len(rows)

            with conn.cursor() as cur:
                cur.execute(
                    "SELECT MAX(trade_date) AS md FROM index_valuation_intl "
                    "WHERE metric='dividend_yield'"
                )
                latest = cur.fetchone()["md"]
        except Exception as e:  # noqa: BLE001
            errors.append(f"Shiller: {type(e).__name__} {str(e)[:80]}")
            logger.warning("⚠️ 国际指数估值扩展采集失败: %s", e)
        finally:
            conn.close()

        msg = f"标普500 股息率新增/更新 {written} 行（最新 {latest if 'latest' in dir() else '--'}）"
        if errors:
            msg += f"；异常 {len(errors)} 项"
        logger.info("✅ %s", msg)
        return with_steps(
            {"records_written": written, "error_count": len(errors),
             "errors": errors[:5], "note": msg},
            RUN_STEPS,
            {
                1: "Shiller ie_data.xls（1871 起）",
                2: f"股息率序列 {written} 点",
                3: f"upsert {written} 行（metric=dividend_yield, SPX）",
            },
        )
