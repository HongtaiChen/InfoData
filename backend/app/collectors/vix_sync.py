#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
投资环境模块 · 国际指数「波动率与风险」—— VIX 恐慌指数采集器（vix_sync）

把标普500 隐含波动率指数 VIX 落库到 vix_daily 表，作为「波动率与风险」块的
核心外部参照物（<15 平稳 / >25-30 恐慌，用户给定口径）。

数据源：新浪环球指数直连 `gi.finance.sina.com.cn/hq/daily?symbol=VIX`
（2026-10-20 实测可用，约 1,000 行，约 4 年历史）。

⚠️ 与 overseas_index_sync 的 "gi" 通道共用同一接口与归一化逻辑，
   字段 {d 日期, o 开, h 高, l 低, c 收, v 量}，无 amount。
"""
import logging
from datetime import date, datetime

import pymysql

from ..db import get_db_config
from ._common import with_steps

logger = logging.getLogger(__name__)

RUN_STEPS = [
    {"no": 1, "name": "拉取 VIX 日线", "params": "新浪 gi 直连 symbol=VIX"},
    {"no": 2, "name": "增量过滤", "params": "仅保留 trade_date > 本地 MAX 的行"},
    {"no": 3, "name": "批量 Upsert", "params": "uk_date(trade_date) 幂等"},
]

_GI_URL = "https://gi.finance.sina.com.cn/hq/daily"
_GI_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120",
               "Referer": "https://finance.sina.com.cn/"}


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


class VixSyncCollector:
    """VIX 恐慌指数日线同步"""

    def __init__(self, timeout_sec: float = 20):
        self.timeout_sec = float(timeout_sec)

    @staticmethod
    def _ensure_tables(conn):
        ddl = """
        CREATE TABLE IF NOT EXISTS vix_daily (
          trade_date DATE NOT NULL COMMENT '交易日',
          open DECIMAL(10,4) DEFAULT NULL,
          high DECIMAL(10,4) DEFAULT NULL,
          low DECIMAL(10,4) DEFAULT NULL,
          close DECIMAL(10,4) DEFAULT NULL COMMENT 'VIX 收盘',
          volume DECIMAL(20,2) DEFAULT NULL,
          update_time TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          data_source VARCHAR(100) DEFAULT NULL,
          PRIMARY KEY (trade_date)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        COMMENT='VIX 恐慌指数日线（新浪 gi 直连，2026-10-21）'
        """
        with conn.cursor() as cur:
            cur.execute(ddl)
        conn.commit()

    def run(self) -> dict:
        import requests
        import pandas as pd

        conn = pymysql.connect(**get_db_config().to_dict(),
                               cursorclass=pymysql.cursors.DictCursor)
        written = 0
        errors = []
        try:
            self._ensure_tables(conn)
            with conn.cursor() as cur:
                cur.execute("SELECT MAX(trade_date) AS d FROM vix_daily")
                last = cur.fetchone()["d"]

            r = requests.get(_GI_URL, params={"symbol": "VIX", "num": "10000"},
                             headers=_GI_HEADERS, timeout=self.timeout_sec)
            r.raise_for_status()
            js = r.json()
            data = (js.get("result") or {}).get("data") or []
            if not data:
                raise RuntimeError(f"VIX 接口返回空 code={js.get('code')}")

            df = pd.DataFrame(data)
            df = df.rename(columns={"d": "date", "o": "open", "h": "high",
                                    "l": "low", "c": "close", "v": "volume"})
            rows = []
            for _, row in df.iterrows():
                d = _d(row.get("date"))
                if d is None or (last is not None and d <= last):
                    continue
                c = _f(row.get("close"), 4)
                if c is None:
                    continue
                rows.append((d, _f(row.get("open"), 4), _f(row.get("high"), 4),
                             _f(row.get("low"), 4), c, _f(row.get("volume"), 2)))
            if rows:
                with conn.cursor() as cur:
                    cur.executemany(
                        "INSERT INTO vix_daily (trade_date, open, high, low, close, volume, "
                        "update_time, data_source) VALUES (%s,%s,%s,%s,%s,%s,NOW(),'SINA_GI') "
                        "ON DUPLICATE KEY UPDATE open=VALUES(open), high=VALUES(high), "
                        "low=VALUES(low), close=VALUES(close), volume=VALUES(volume), "
                        "update_time=NOW()",
                        rows,
                    )
                conn.commit()
            written = len(rows)
        except Exception as e:  # noqa: BLE001
            errors.append(f"VIX: {type(e).__name__} {str(e)[:80]}")
            logger.warning("⚠️ VIX 采集失败: %s", e)
        finally:
            conn.close()

        msg = f"VIX 新增 {written} 行"
        if errors:
            msg += f"；异常 {len(errors)} 项"
        logger.info("✅ %s", msg)
        return with_steps(
            {"records_written": written, "error_count": len(errors),
             "errors": errors[:5], "note": msg},
            RUN_STEPS,
            {
                1: "新浪 gi symbol=VIX",
                2: f"本地水位 {last}，增量 {written} 行",
                3: "uk_date(trade_date) 幂等",
            },
        )
