#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
投资环境模块 · 国际指数「资金流与仓位」—— CFTC 期货持仓采集器（cftc_position_sync）

把美国 CFTC 每周货币期货非商业持仓落库到 cftc_position_weekly 表，
作为「机构多空敞口」的客观代理（用户口径：极端仓位往往对应反转风险）。

数据源：akshare `macro_usa_cftc_nc_holding`（非商业/投机持仓，周频，1986 起），
9 种货币 × 3 列（多头/空头/净仓位）。

⚠️ 采用**长表**存储（currency, metric, value），而非宽表——货币种类若源侧增减，
   长表无需改 DDL；宽表会因缺列/多列反复 ALTER。
⚠️ 源为周频，trade_date 实为「截至日」。增量按 MAX(trade_date) 过滤。
"""
import logging
from datetime import date, datetime

import pymysql

from ..db import get_db_config
from ._common import with_steps, call_with_timeout

logger = logging.getLogger(__name__)

RUN_STEPS = [
    {"no": 1, "name": "拉取 CFTC 非商业持仓", "params": "akshare macro_usa_cftc_nc_holding（周频）"},
    {"no": 2, "name": "宽表→长表归一", "params": "9 货币 × 多头/空头/净 三指标"},
    {"no": 3, "name": "增量过滤 + 批量 Upsert", "params": "uk(currency, metric, trade_date) 幂等"},
]

# 货币中文名 → 存储代码
CURRENCIES = [
    ("美元", "USD"), ("瑞郎", "CHF"), ("纽元", "NZD"), ("墨西哥比索", "MXN"),
    ("日元", "JPY"), ("欧元", "EUR"), ("加元", "CAD"), ("英镑", "GBP"), ("澳元", "AUD"),
]
METRICS = [("多头仓位", "long"), ("空头仓位", "short"), ("净仓位", "net")]


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


def _f(v, nd: int = 2):
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else round(f, nd)


class CftcPositionSyncCollector:
    """CFTC 货币期货非商业持仓周频同步"""

    def __init__(self, timeout_sec: float = 60):
        self.timeout_sec = float(timeout_sec)

    @staticmethod
    def _ensure_tables(conn):
        ddl = """
        CREATE TABLE IF NOT EXISTS cftc_position_weekly (
          currency VARCHAR(8) NOT NULL COMMENT '货币代码(USD/EUR/JPY/GBP/AUD/CAD/CHF/NZD/MXN)',
          metric VARCHAR(8) NOT NULL COMMENT '指标(long/short/net)',
          trade_date DATE NOT NULL COMMENT '截至日(周频)',
          value DECIMAL(20,2) DEFAULT NULL COMMENT '持仓手数',
          update_time TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          data_source VARCHAR(100) DEFAULT NULL,
          PRIMARY KEY (currency, metric, trade_date),
          KEY idx_trade_date (trade_date)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        COMMENT='CFTC 货币期货非商业持仓周频（akshare，2026-10-21）'
        """
        with conn.cursor() as cur:
            cur.execute(ddl)
        conn.commit()

    def run(self) -> dict:
        import akshare as ak

        conn = pymysql.connect(**get_db_config().to_dict(),
                               cursorclass=pymysql.cursors.DictCursor)
        written = 0
        errors = []
        try:
            self._ensure_tables(conn)
            with conn.cursor() as cur:
                cur.execute("SELECT MAX(trade_date) AS d FROM cftc_position_weekly")
                last = cur.fetchone()["d"]

            df = call_with_timeout(ak.macro_usa_cftc_nc_holding, self.timeout_sec)
            if df is None or df.empty:
                raise RuntimeError("CFTC 接口返回空")

            rows = []
            for _, row in df.iterrows():
                d = _d(row.get("日期"))
                if d is None or (last is not None and d <= last):
                    continue
                for cn, code in CURRENCIES:
                    for mn, metric in METRICS:
                        col = f"{cn}-{mn}"
                        v = _f(row.get(col), 2)
                        if v is None:
                            continue
                        rows.append((code, metric, d, v))
            if rows:
                with conn.cursor() as cur:
                    cur.executemany(
                        "INSERT INTO cftc_position_weekly (currency, metric, trade_date, value, "
                        "update_time, data_source) VALUES (%s,%s,%s,%s,NOW(),'AKSHARE') "
                        "ON DUPLICATE KEY UPDATE value=VALUES(value), update_time=NOW()",
                        rows,
                    )
                conn.commit()
            written = len(rows)
        except Exception as e:  # noqa: BLE001
            errors.append(f"CFTC: {type(e).__name__} {str(e)[:80]}")
            logger.warning("⚠️ CFTC 采集失败: %s", e)
        finally:
            conn.close()

        msg = f"CFTC 新增 {written} 行"
        if errors:
            msg += f"；异常 {len(errors)} 项"
        logger.info("✅ %s", msg)
        return with_steps(
            {"records_written": written, "error_count": len(errors),
             "errors": errors[:5], "note": msg},
            RUN_STEPS,
            {
                1: "akshare macro_usa_cftc_nc_holding",
                2: "9 货币 × long/short/net 三指标",
                3: f"本地水位 {last}，增量 {written} 行",
            },
        )
