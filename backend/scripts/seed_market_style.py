#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
分析研究框架 · 市场风向模块 任务播种（幂等，可重复执行）
用法：python scripts/seed_market_style.py

- market_style_sync：市场风格日频物化表（market_style_daily）计算任务，纯库内 SQL 与 pandas 聚合，
  cron `5 20 * * 0-4`（每工作日 20:05）。

  ⚠️ 排期契约（2026-09-16 修正）：本任务的输入是**个股日线**，而 stock_daily_incr（19:00 起跑）
  常态耗时 15~50 分钟 —— 因此任何早于「日线完成」的时刻（原 18:45、以及 19:30）都会读到
  半量日线，把残缺的当日宽度写进库，使市场风向 / 交叉印证给出假信号
  （2026-09-16 实测：cross_checks 的 micro/pxvol 两项因宽度全 NULL 退化为「数据缺失」）。
  现排在 20:05，并新增「日线充分性护栏」兜底（不足上一交易日 90% 则上界退回）。

⚠️ 维护纪律：task_config 的权威定义在 `seed_task_config.py`（全量 26 条任务的唯一事实来源），
   本脚本仅为历史遗留的独立播种入口，两处 cron **必须保持一致**，否则重跑本脚本会把排期改回去。
   （2026-09-16：本脚本一度仍是 18:45，与 seed_task_config.py 的 20:05 冲突，已对齐。）
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.db import get_db_config  # noqa: E402
import pymysql  # noqa: E402

# (task_name, enabled, cron, params)
TASKS = [
    ("market_style_sync", 1, "5 20 * * 0-4", {}),
]


def main():
    conn = pymysql.connect(**get_db_config().to_dict())
    try:
        with conn.cursor() as cur:
            for name, enabled, cron, params in TASKS:
                cur.execute(
                    """INSERT INTO task_config (task_name, enabled, cron, params, update_time)
                       VALUES (%s, %s, %s, %s, NOW())
                       ON DUPLICATE KEY UPDATE enabled=VALUES(enabled), cron=VALUES(cron),
                       params=VALUES(params), update_time=NOW()""",
                    (name, enabled, cron, json.dumps(params, ensure_ascii=False)),
                )
        conn.commit()
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM task_config")
            total = cur.fetchone()[0]
            cur.execute(
                "SELECT task_name, enabled, cron FROM task_config WHERE task_name=%s",
                (TASKS[0][0],),
            )
            print(f"seed 完成，task_config 共 {total} 条任务：{cur.fetchall()}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
