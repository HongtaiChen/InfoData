#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
分析研究框架 · 市场风向模块 任务播种（幂等，可重复执行）
用法：python scripts/seed_market_style.py

- market_style_sync：市场风格日频物化表（market_style_daily）计算任务，
  纯库内 SQL 秒级，挂在 index_market_sync（18:30）之后 18:45 跑，
  避开 19:00 stock_daily_incr 与 19:30 market_current_sync 的盘后密集区。

⚠️ 维护纪律：task_config 的这条记录以本脚本为事实来源（同 seed_dq_rules/seed_recovered_tasks）。
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.db import get_db_config  # noqa: E402
import pymysql  # noqa: E402

# (task_name, enabled, cron, params)
TASKS = [
    ("market_style_sync", 1, "45 18 * * 0-4", {}),
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
