#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
历史死表恢复采集 · 任务配置播种（幂等，可重复执行）
用法：python scripts/seed_recovered_tasks.py

背景（2026-09-13）：10 张历史导入表经调研确认 3 张可低成本恢复采集
（分红送配 / 融资融券 / 机构调研），对应采集器已在 app/collectors/ 落地。
本脚本负责把这三条任务写入 task_config，使调度器能自动装载。

⚠️ 维护纪律（与 seed_dq_rules.py 同理）：**task_config 的这三条记录以本脚本为事实来源**。
   前端「作业监控」热改 cron/params 后如需长期生效，请同步回写本文件，
   否则下次跑 seed 会把热改值静默覆盖（同 dq_rules 2026-09-13 踩过的坑）。

排期设计（避开现有 17:00~21:30 的盘后任务密集区）：
- margin_sync        每日 09:00   —— 沪深两融为 T+1 发布，次日早间补齐最稳
- jgdy_sync          每日 22:00   —— 调研公告盘后/次日发布（单次调用即拉全量增量）
- ths_dividend_sync  每月 1/15 日 03:00 —— 逐股型（约 5,100 次请求），避免高频空跑
"""
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.db import get_db_config  # noqa: E402
import pymysql  # noqa: E402

# (task_name, enabled, cron, params)
TASKS = [
    ("margin_sync", 1, "0 9 * * *",
     {"max_days": 0, "sleep_sec": 0.25, "timeout_sec": 60, "bse_retry": 2}),
    ("jgdy_sync", 1, "0 22 * * *",
     {"overlap_days": 30, "first_lookback_days": 365, "timeout_sec": 60}),
    ("ths_dividend_sync", 1, "0 3 1,15 * *",
     {"max_stocks": 0, "sleep_sec": 0.12, "retry": 1, "timeout_sec": 30}),
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
                "SELECT task_name, enabled, cron FROM task_config "
                "WHERE task_name IN (%s, %s, %s) ORDER BY task_name",
                tuple(t[0] for t in TASKS),
            )
            rows = cur.fetchall()
        print(f"seed 完成，task_config 共 {total} 条任务；本次三条：")
        for r in rows:
            print(f"  {r[0]:<22} enabled={r[1]}  cron={r[2]}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
