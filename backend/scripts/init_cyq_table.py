#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
筹码分布表初始化（幂等，可重复执行）
用法：python scripts/init_cyq_table.py

建 stock_cyq_daily 表（个股筹码分布日频物化），并注册 table_meta。

背景（2026-10-20）：支撑/阻力位（A+C）落地后，追加「筹码分布（B 方案）」。
探查结论：网上无「可直接拉取的筹码数据」，东财/akshare 的 stock_cyq_em 本质是
「东财日 K 线 + 换手率衰减三角分布算法本地重算」；本地 stock_market_daily 已具备
OHLCV + turnover_ratio，故纯库内离线计算（app/services/cyq.py），不联网、绕开代理拦截。

表设计：
  - 每行 = 某只股票某交易日的筹码指标（获利比例/平均成本/90、70 成本区间与集中度）
  - 主键 (stock_code, trade_date)，全量重建幂等
  - 只保留「近 90 交易日」（算法需 120 根预热，但展示与存储只留最近 90，对齐 akshare）
  - rebuild = derived（可由 stock_market_daily 重算），可移植性标注 derived
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pymysql  # noqa: E402

from app.db import get_db_config  # noqa: E402

COLLATE = "ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci"

DDL_CYQ = f"""
CREATE TABLE IF NOT EXISTS stock_cyq_daily (
  stock_code        VARCHAR(10)   NOT NULL COMMENT '股票代码（6 位）',
  trade_date        DATE          NOT NULL COMMENT '交易日',
  benefit_part      DECIMAL(8,2)  DEFAULT NULL COMMENT '★获利比例（%）：现价以下的筹码占比，0-100',
  avg_cost          DECIMAL(12,2) DEFAULT NULL COMMENT '★平均成本（元）：累计筹码 50% 分位所在价位',
  cost90_low        DECIMAL(12,2) DEFAULT NULL COMMENT '90% 筹码成本区间下限（元）',
  cost90_high       DECIMAL(12,2) DEFAULT NULL COMMENT '90% 筹码成本区间上限（元）',
  cost90_con        DECIMAL(8,2)  DEFAULT NULL COMMENT '90 集中度（%）：区间宽度/区间中点×100，越小越集中',
  cost70_low        DECIMAL(12,2) DEFAULT NULL COMMENT '70% 筹码成本区间下限（元）',
  cost70_high       DECIMAL(12,2) DEFAULT NULL COMMENT '70% 筹码成本区间上限（元）',
  cost70_con        DECIMAL(8,2)  DEFAULT NULL COMMENT '70 集中度（%）',
  data_source       VARCHAR(40)   DEFAULT NULL COMMENT '数据来源（cyq_local：本地 stock_market_daily 重算）',
  update_time       TIMESTAMP     NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (stock_code, trade_date),
  KEY idx_trade_date (trade_date)
) {COLLATE}
COMMENT='个股筹码分布日频（2026-10-20 起）。⭐ 算法复刻东财 CYQ「换手率衰减三角分布」，输入为本地 stock_market_daily 的 OHLCV（**前复权**）+ turnover_ratio，纯库内离线计算（app/services/cyq.py），不联网。⚠️ 筹码概念**只对个股成立**（需流通盘换手率），指数/行业无此概念。⚠️ 算法窗口 120 根 K 线预热，但本表只存最近 90 交易日（对齐 akshare stock_cyq_em 口径）。⚠️ 集中度越低=筹码越集中（主力控盘度高），获利比例越高=套牢盘越少。⚠️ 前复权口径：OHLC = 实际价 × adj_factor / 最新 adj_factor，规避除权除息的价格跳变；但缩股/并股（如 600766 1:100 缩股）因源 adj_factor 未记录，筹码区间仍会跨数量级（属数据源局限，非算法缺陷）。'
"""

TABLE_META = {
    "table_name": "stock_cyq_daily",
    "category": "行情",
    "source_desc": "本地派生：stock_market_daily（OHLCV + turnover_ratio）→ 东财 CYQ 三角分布算法离线重算",
    "flow_desc": "采集器 stock_cyq_sync 每日盘后跑（挂 stock_daily_incr 之后）：读 stock_market_daily 最近 130 交易日 → app/services/cyq.compute_cyq 算筹码指标 → 全量重建最近 90 交易日（DELETE 该股 + INSERT）。纯库内、幂等、无外部源。",
    "writers": ["stock_cyq_sync"],
    "writer_cols": None,
    "note": "筹码分布（B 方案），仅个股适用；指数/行业无流通盘换手率，不适用",
    "rebuild": "derived",
    "rebuild_note": "可由 stock_market_daily 的 OHLCV + turnover_ratio 经 app/services/cyq.py 全量重算，无外部依赖",
}


def main():
    conn = pymysql.connect(**get_db_config().to_dict())
    try:
        with conn.cursor() as cur:
            cur.execute(DDL_CYQ)
            # 注册 table_meta（幂等 upsert）
            cur.execute(
                """INSERT INTO table_meta
                   (table_name, category, source_desc, flow_desc, writers, writer_cols, note,
                    rebuild, rebuild_note, update_time)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                   ON DUPLICATE KEY UPDATE
                     category=VALUES(category), source_desc=VALUES(source_desc),
                     flow_desc=VALUES(flow_desc), writers=VALUES(writers),
                     writer_cols=VALUES(writer_cols), note=VALUES(note),
                     rebuild=VALUES(rebuild), rebuild_note=VALUES(rebuild_note),
                     update_time=NOW()""",
                (
                    TABLE_META["table_name"], TABLE_META["category"],
                    TABLE_META["source_desc"], TABLE_META["flow_desc"],
                    __import__("json").dumps(TABLE_META["writers"], ensure_ascii=False),
                    __import__("json").dumps(TABLE_META["writer_cols"], ensure_ascii=False)
                    if TABLE_META["writer_cols"] else None,
                    TABLE_META["note"], TABLE_META["rebuild"], TABLE_META["rebuild_note"],
                ),
            )
        conn.commit()
        with conn.cursor() as cur:
            cur.execute("SHOW TABLES LIKE 'stock_cyq_daily'")
            print("表已就绪:", cur.fetchall())
            cur.execute("SELECT table_name, category, rebuild FROM table_meta WHERE table_name='stock_cyq_daily'")
            print("table_meta:", cur.fetchall())
    finally:
        conn.close()


if __name__ == "__main__":
    main()
