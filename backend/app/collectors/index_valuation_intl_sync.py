#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
投资环境模块 · 国际指数「基本面分析」—— 估值采集器（index_valuation_intl_sync）

把国际指数的估值指标落库到 index_valuation_intl 表（长表：index_code, metric, trade_date, value）。

数据源：Siblis Research 免费 API（免 Key、免注册、HTTPS GET 返回 JSON）。
  · 免费层覆盖 31 个指数，但**只有 pe-trailing / pe-forward / cape 三个指标有值**，
    pb 和 dividend-yield 在免费层**全部返回 null**（付费才提供）。
  · 数据频率声明 daily，但免费层**实际只返回 8 个历史快照点**
    （2023-12 起的半年/季末），不是日频序列。

⚠️ 诚实标注（2026-10-21 实测）：
  · 国际指数的 PB / 股息率 免费源拿不到（Siblis 免费层为 null，MSCI 官网需登录）。
  · 估值为**稀疏快照**，仅够看「当前处于历史什么位置」的粗粒度趋势，
    不够做日频分位 / 回撤类比。
  · 指数映射：Siblis ticker 与项目指数代码不完全对齐——
    ACWI→ACWI、WORLD→MSWORLD、EMER→MSEM、USA→SPX（标普近似）、NDX→NDX、
    DAX/UKX/CAC/N225/KOSPI/NIFTY/IBOV/HSI 直接对应；DJI/SX5E/HSCEI/MSCI EAFE 无对应。
"""
import logging
from datetime import date, datetime

import pymysql

from ..db import get_db_config
from ._common import with_steps

logger = logging.getLogger(__name__)

RUN_STEPS = [
    {"no": 1, "name": "拉取估值快照", "params": "Siblis API 逐指数 pe-trailing/pe-forward/cape"},
    {"no": 2, "name": "归一 + 落库", "params": "长表 uk(index_code, metric, trade_date)"},
]

_API = "https://siblisresearch.com/api/v1"
_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120"}

# Siblis ticker → 项目 index_code（只映射免费层有值且项目里有的指数）
TICKER_MAP = {
    "ACWI": "ACWI", "WORLD": "MSWORLD", "EMER": "MSEM",
    "USA": "SPX", "NDX": "NDX",
    "DAX": "DAX", "UKX": "UKX", "CAC": "CAC", "SXXP": "SX5E",
    "N225": "N225", "KOSPI": "KOSPI", "NIFTY": "SENSEX", "IBOV": "IBOV",
    "HSI": "HSI",
}
# 免费层有值的指标
RATIOS = [("pe-trailing", "pe_ttm"), ("pe-forward", "pe_fwd"), ("cape", "cape")]


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


class IndexValuationIntlSyncCollector:
    """国际指数估值快照同步（Siblis 免费 API）"""

    def __init__(self, timeout_sec: float = 20):
        self.timeout_sec = float(timeout_sec)

    @staticmethod
    def _ensure_tables(conn):
        ddl = """
        CREATE TABLE IF NOT EXISTS index_valuation_intl (
          index_code VARCHAR(20) NOT NULL COMMENT '项目指数代码',
          metric VARCHAR(16) NOT NULL COMMENT '指标(pe_ttm/pe_fwd/cape)',
          trade_date DATE NOT NULL COMMENT '快照日',
          value DECIMAL(12,4) DEFAULT NULL,
          update_time TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          data_source VARCHAR(100) DEFAULT NULL,
          PRIMARY KEY (index_code, metric, trade_date),
          KEY idx_trade_date (trade_date)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        COMMENT='国际指数估值快照（Siblis 免费层，2026-10-21）'
        """
        with conn.cursor() as cur:
            cur.execute(ddl)
        conn.commit()

    def run(self) -> dict:
        import requests

        conn = pymysql.connect(**get_db_config().to_dict(),
                               cursorclass=pymysql.cursors.DictCursor)
        written = 0
        errors = []
        try:
            self._ensure_tables(conn)
            rows = []
            for siblis, code in TICKER_MAP.items():
                for ratio, metric in RATIOS:
                    try:
                        r = requests.get(f"{_API}/{siblis}/{ratio}",
                                         headers=_HEADERS, timeout=self.timeout_sec)
                        r.raise_for_status()
                        d = r.json()
                        for pt in d.get("data", []):
                            td = _d(pt.get("trading_day (EOD)"))
                            v = _f(pt.get("value"), 4)
                            if td is None or v is None:
                                continue
                            rows.append((code, metric, td, v))
                    except Exception as e:  # noqa: BLE001
                        errors.append(f"{siblis}/{ratio}: {type(e).__name__} {str(e)[:60]}")
            if rows:
                with conn.cursor() as cur:
                    cur.executemany(
                        "INSERT INTO index_valuation_intl (index_code, metric, trade_date, value, "
                        "update_time, data_source) VALUES (%s,%s,%s,%s,NOW(),'SIBLIS') "
                        "ON DUPLICATE KEY UPDATE value=VALUES(value), update_time=NOW()",
                        rows,
                    )
                conn.commit()
            written = len(rows)
        except Exception as e:  # noqa: BLE001
            errors.append(f"Siblis: {type(e).__name__} {str(e)[:80]}")
            logger.warning("⚠️ 国际指数估值采集失败: %s", e)
        finally:
            conn.close()

        msg = f"国际指数估值新增 {written} 行"
        if errors:
            msg += f"；异常 {len(errors)} 项"
        logger.info("✅ %s", msg)
        return with_steps(
            {"records_written": written, "error_count": len(errors),
             "errors": errors[:10], "note": msg},
            RUN_STEPS,
            {
                1: f"Siblis API {len(TICKER_MAP)} 指数 × 3 指标",
                2: f"落库 {written} 行（pe_ttm/pe_fwd/cape）",
            },
        )
