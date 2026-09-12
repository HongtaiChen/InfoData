#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
把「僵尸 running」任务记录清理为 failed（一次性运维脚本，2026-09-12）

背景：uvicorn 无 --reload，重启会中断正在执行的任务，被中断的 run 会永久停留在
running 状态（可能挡住后续 2 小时内的重入保护）。历史多次重启累积了多条僵尸记录。

约定：**执行本脚本前必须先停掉后端**，否则会把真正在跑的任务误标。
用法：
    python scripts/fix_stale_running.py            # 只看，不改
    python scripts/fix_stale_running.py --apply    # 实际写入
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db import get_db_config, query_all  # noqa: E402

NOTE = "进程重启中断，已由运维脚本标记为 failed（非任务本身失败）"


def main() -> int:
    apply = "--apply" in sys.argv
    rows = query_all(
        "SELECT id, task_name, started_at FROM task_runs WHERE status = 'running' ORDER BY id"
    )
    print(f"当前 running 记录：{len(rows)} 条")
    for r in rows:
        print(f"  id={r['id']:<6} {r['task_name']:<24} started={r['started_at']}")

    if not rows:
        print("无需处理。")
        return 0
    if not apply:
        print("\n[只读模式] 加 --apply 才会写入。")
        return 0

    import pymysql

    conn = pymysql.connect(**get_db_config().to_dict())
    try:
        with conn.cursor() as cur:
            cur.executemany(
                """
                UPDATE task_runs
                   SET status = 'failed',
                       finished_at = NOW(),
                       error_message = %s
                 WHERE id = %s AND status = 'running'
                """,
                [(NOTE, r["id"]) for r in rows],
            )
            affected = cur.rowcount
        conn.commit()
    finally:
        conn.close()

    left = query_all("SELECT COUNT(*) c FROM task_runs WHERE status = 'running'")[0]["c"]
    print(f"\n已标记 {affected} 条为 failed；剩余 running = {left}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
