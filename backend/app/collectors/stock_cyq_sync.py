#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
个股筹码分布采集器（stock_cyq_sync）—— 纯库内派生，无外部源

把「筹码分布（B 方案）」物化到 stock_cyq_daily 表。

数据来源：stock_market_daily（OHLCV + turnover_ratio，已有表，无新外部源）。
算法：app/services/cyq.compute_cyq —— 复刻东财 CYQ「换手率衰减三角分布」。

设计要点：
  - 纯库内计算，幂等（每只股票：DELETE 该股近 90 交易日 + INSERT 重算结果）。
  - 只算「有换手率」的股票（无换手率的股票筹码算法无意义，如部分早期数据）。
  - 增量口径：每次全量重算所有目标股票的最近 90 交易日（股票数 × 130 根 × 150 桶，
    纯 Python 循环，单只 ~0.3s，几千只总量可控；挂盘后批量任务，非实时）。
  - 挂 stock_daily_incr 之后链式触发（筹码依赖当日个股日线）。

⚠️ 性能注意：compute_cyq 是纯 Python 逐日逐桶循环，单只股票 130 根 × 150 桶
   约 2 万次桶操作。全市场 5000+ 只若一次跑完会较慢，故支持 `codes` 参数
   （逗号分隔）与 `limit`（最多处理只数，默认全量），供分批/定向回填。
"""
import logging
from datetime import date, timedelta

import pymysql

from ..db import get_db_config
from ..services.cyq import compute_cyq, WINDOW
from ._common import with_steps

logger = logging.getLogger(__name__)

RUN_STEPS = [
    {"no": 1, "name": "加载个股日线", "params": "stock_market_daily 最近 130 交易日 OHLCV + turnover_ratio"},
    {"no": 2, "name": "计算筹码分布", "params": "CYQ 换手率衰减三角分布：获利比例/平均成本/90/70成本区间与集中度"},
    {"no": 3, "name": "落地 stock_cyq_daily", "params": "每只股票 DELETE 近90日 + INSERT 重算（幂等）"},
]

# 存储保留的交易日数（对齐 akshare stock_cyq_em 返回最近 90 交易日）
KEEP_DAYS = 90
# 取数预热（算法窗口 120 + 少量余量）
FETCH_DAYS = WINDOW + 10

INSERT_COLS = ["stock_code", "trade_date", "benefit_part", "avg_cost",
               "cost90_low", "cost90_high", "cost90_con",
               "cost70_low", "cost70_high", "cost70_con", "data_source"]
INSERT_SQL = f"""
    INSERT INTO stock_cyq_daily
        ({", ".join(INSERT_COLS)})
    VALUES ({", ".join(["%s"] * len(INSERT_COLS))})
    ON DUPLICATE KEY UPDATE
        benefit_part = VALUES(benefit_part), avg_cost = VALUES(avg_cost),
        cost90_low = VALUES(cost90_low), cost90_high = VALUES(cost90_high),
        cost90_con = VALUES(cost90_con),
        cost70_low = VALUES(cost70_low), cost70_high = VALUES(cost70_high),
        cost70_con = VALUES(cost70_con), data_source = VALUES(data_source)
"""


class StockCyqSyncCollector:
    """个股筹码分布物化（纯库内，无外部源）"""

    def __init__(self, codes: str = "", limit: int = 0):
        self.codes = [c.strip() for c in (codes or "").split(",") if c.strip()]
        self.limit = int(limit or 0)

    def _load_universe(self, conn) -> list[str]:
        """目标股票代码列表（有换手率的上市股票）。"""
        sql = """
            SELECT DISTINCT stock_code
            FROM stock_market_daily
            WHERE turnover_ratio IS NOT NULL AND turnover_ratio > 0
        """
        params: list = []
        if self.codes:
            placeholders = ",".join(["%s"] * len(self.codes))
            sql += f" AND stock_code IN ({placeholders})"
            params = self.codes
        if self.limit > 0:
            sql += " LIMIT %s"
            params.append(self.limit)
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return [r[0] for r in cur.fetchall()]

    def _load_kline(self, conn, code: str) -> list[dict]:
        """某股票最近 FETCH_DAYS 交易日（升序），OHLC 做**前复权**。

        ⚠️ 为什么前复权（2026-10-20 实测踩坑）：stock_market_daily 存的是**不复权实际价**，
        筹码「换手率衰减」算法假设价格连续。遇并股/缩股（如 600766 园城黄金 2024-06-05
        1:100 缩股，价格 17→0.35 元）或大比例送转时，窗口内价格跨数量级，筹码区间退化、
        90 集中度爆到 ~197%（数学正确但业务无意义）。东财 stock_cyq_em 默认 adjust='qfq'
        正是为规避此问题。前复权 = 实际价 × adj_factor / 最新 adj_factor。
        """
        with conn.cursor() as cur:
            cur.execute(
                """SELECT trade_date AS date, open, high, low, close,
                          turnover_ratio AS turnover, adj_factor
                   FROM stock_market_daily
                   WHERE stock_code = %s
                   ORDER BY trade_date DESC
                   LIMIT %s""",
                [code, FETCH_DAYS],
            )
            rows = [dict(zip([d[0] for d in cur.description], r)) for r in cur.fetchall()]
        rows.reverse()
        if not rows:
            return rows
        # 前复权：以最新一行的 adj_factor 为基准，历史价格整体缩放
        latest_af = float(rows[-1]["adj_factor"] or 1.0)
        for r in rows:
            af = float(r["adj_factor"] or 1.0)
            scale = af / latest_af if latest_af else 1.0
            for col in ("open", "high", "low", "close"):
                v = r.get(col)
                r[col] = float(v) * scale if v is not None else None
        return rows

    def _write(self, conn, code: str, results: list[dict]) -> int:
        """DELETE 该股近 KEEP_DAYS + INSERT 重算（幂等）。"""
        keep_rows = [r for r in results if r.get("date") is not None][-KEEP_DAYS:]
        n = 0
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM stock_cyq_daily WHERE stock_code = %s", [code]
            )
            if keep_rows:
                vals = []
                for r in keep_rows:
                    vals.append((
                        code, r["date"], r["获利比例"], r["平均成本"],
                        r["90成本-低"], r["90成本-高"], r["90集中度"],
                        r["70成本-低"], r["70成本-高"], r["70集中度"],
                        "cyq_local",
                    ))
                # 逐条插入避免超长回滚（字段均短，但保持稳健）
                n = cur.executemany(INSERT_SQL, vals)
        return n

    def run(self) -> dict:
        conn = pymysql.connect(**get_db_config().to_dict())
        try:
            universe = self._load_universe(conn)
            if not universe:
                raise RuntimeError("stock_market_daily 无带换手率的数据，无法计算筹码")

            n_codes = 0
            n_rows = 0
            errors: list[str] = []
            for code in universe:
                try:
                    kline = self._load_kline(conn, code)
                    if len(kline) < 30:  # 数据太少，筹码无意义
                        continue
                    results = compute_cyq(kline)
                    n = self._write(conn, code, results)
                    conn.commit()
                    n_codes += 1
                    n_rows += n
                except Exception as e:  # 单只失败不影响其余
                    conn.rollback()
                    errors.append(f"{code}: {type(e).__name__} {str(e)[:120]}")
                    logger.warning("筹码计算失败 %s: %s", code, e)

            msg = f"筹码分布物化：{n_codes} 只股票 / {n_rows} 行"
            logger.info("✅ %s", msg)
            return with_steps(
                {"records_written": n_rows, "error_count": len(errors),
                 "errors": errors[:20], "note": msg,
                 "codes": n_codes, "total_universe": len(universe)},
                RUN_STEPS,
                {
                    1: f"{len(universe)} 只股票日线",
                    2: f"{n_codes} 只股票筹码指标",
                    3: f"{n_rows} 行",
                },
            )
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
