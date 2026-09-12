#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
InvestBuddy 行情快照聚合采集器
从 stock_market_daily（日线，采集器持续更新）最新交易日聚合生成 stock_market_current（行情看板快照）。
- 纯本地 SQL 计算，无外部数据源、无风控风险
- 解决快照表停更问题：行情看板表格永远展示"最近一个交易日的收盘快照"
- 幂等：TRUNCATE 后全量重建（约 5400 行，秒级）

⚠️ 调度顺序契约（2026-09-12 修正）：
  stock_daily_incr（19:00 启动，跑 ~25 分钟）必须先完成，
  market_current_sync 才能拿到完整 daily 最新日切片。
  原 cron `45 18` 早于 `0 19`，导致 daily 还没补全就先聚合 → 6 字头股票漏 2220 行被锁死。
  修复：cron 改到 30 19（晚于 daily_incr 30 分钟），并移除「今日已跑跳过」保护——
  每次跑都重新聚合（TRUNCATE + INSERT 仅数秒），避免再次出现「daily 已补但 current 卡住」的状态。
  唯一保留的护栏：daily 最新日行数 < 1000 时拒绝覆盖（防日线表被清空的极端情形）。
"""
import logging
from datetime import datetime

import pymysql

from ..db import get_db_config
from ._common import with_steps

logger = logging.getLogger(__name__)

# 最小行数：少于该值视为异常，拒绝覆盖（防止日线表不完整时把快照清空）
MIN_ROWS = 1000

# 运行步骤链模板（供前端「数据流·整链拓扑」展示运行逻辑）
RUN_STEPS = [
    {"no": 1, "name": "读最新交易日", "params": "stock_market_daily 取 MAX(trade_date)"},
    {"no": 2, "name": "聚合当日行情", "params": "LEFT JOIN stock_info 名称 + 年初至今涨幅，全市场约 5400 行"},
    {"no": 3, "name": "行数护栏校验", "params": f"少于 {MIN_ROWS} 行拒绝覆盖（防 daily 极端缺失时空表）"},
    {"no": 4, "name": "全量重建写入", "params": "TRUNCATE 后单事务批量 INSERT（22 列, data_source=daily-agg）"},
]


class MarketCurrentSyncCollector:
    """行情快照聚合采集器"""

    def run(self) -> dict:
        conn = pymysql.connect(**get_db_config().to_dict(), cursorclass=pymysql.cursors.DictCursor)
        try:
            with conn.cursor() as cur:
                # 1. 最新交易日
                cur.execute("SELECT MAX(trade_date) AS d FROM stock_market_daily")
                latest = cur.fetchone()["d"]
                if not latest:
                    return with_steps(
                        {"records_written": 0, "error_count": 0, "errors": [], "note": "日线表为空，跳过"},
                        RUN_STEPS, {1: "stock_market_daily 为空，终止"},
                    )
                latest_date = str(latest)

                # 2. 聚合最新交易日数据（无日期幂等：每次都重建，详见模块顶部调度契约）
                year = datetime.now().year
                sql = """
                    SELECT d.stock_code,
                           IFNULL(i.short_name, d.stock_code) AS stock_name,
                           d.open, d.high, d.low, d.close AS new_price, d.pre_close,
                           d.change_amount, d.change_pct, d.volume, d.amount,
                           d.turnover_ratio,
                           ROUND((d.high - d.low) / NULLIF(d.pre_close, 0) * 100, 2) AS amplitude,
                           ROUND((d.close / y.close - 1) * 100, 2) AS ytd_change_pct
                    FROM stock_market_daily d
                    LEFT JOIN stock_info i ON d.stock_code = i.stock_code
                    LEFT JOIN (
                        SELECT m.stock_code, m.close
                        FROM stock_market_daily m
                        JOIN (
                            SELECT stock_code, MIN(trade_date) AS md
                            FROM stock_market_daily
                            WHERE trade_date >= %s
                            GROUP BY stock_code
                        ) g ON m.stock_code = g.stock_code AND m.trade_date = g.md
                    ) y ON d.stock_code = y.stock_code
                    WHERE d.trade_date = %s
                """
                cur.execute(sql, [f"{year}-01-01", latest_date])
                rows = cur.fetchall()
                if len(rows) < MIN_ROWS:
                    return with_steps(
                        {
                            "records_written": 0,
                            "error_count": 1,
                            "errors": [f"最新交易日 {latest_date} 仅 {len(rows)} 行，小于阈值 {MIN_ROWS}，拒绝覆盖"],
                            "note": "数据异常保护",
                        },
                        RUN_STEPS, {1: f"最新交易日 {latest_date}", 2: f"聚合仅 {len(rows)} 行", 3: f"< {MIN_ROWS}，拒绝覆盖"},
                    )

                # 3. TRUNCATE + 批量重建
                now = datetime.now()
                insert_sql = """
                    INSERT INTO stock_market_current
                    (stock_code, stock_name, `new`, change_pct, change_amount,
                     open, high, low, pre_close, volume, amount, turnover_ratio,
                     amplitude, ytd_change_pct, dynamic_pe, pb, volume_ratio,
                     rise_speed, 5m_change_pct, 60d_change_pct,
                     total_captital, float_captital, update_time, data_source)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                            NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,%s,%s)
                """
                cur.execute("TRUNCATE TABLE stock_market_current")
                params = [
                    (
                        r["stock_code"], r["stock_name"], r["new_price"], r["change_pct"], r["change_amount"],
                        r["open"], r["high"], r["low"], r["pre_close"], r["volume"], r["amount"],
                        r["turnover_ratio"], r["amplitude"], r["ytd_change_pct"],
                        now, "daily-agg",
                    )
                    for r in rows
                ]
                cur.executemany(insert_sql, params)
                conn.commit()
                logger.info(f"✅ 快照重建完成：{len(rows)} 行 @ {latest_date}")
                return with_steps(
                    {
                        "records_written": len(rows),
                        "error_count": 0,
                        "errors": [],
                        "note": f"快照已更新至 {latest_date}（{year} 年 ytd 已计算）",
                    },
                    RUN_STEPS,
                    {
                        1: f"最新交易日 {latest_date}",
                        2: f"聚合 {len(rows)} 行（含 {year} ytd）",
                        3: f"{len(rows)} ≥ {MIN_ROWS} 通过",
                        4: f"TRUNCATE 后写入 {len(rows)} 行",
                    },
                )
        finally:
            conn.close()
