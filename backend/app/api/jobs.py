#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""作业监控 API：任务配置（含调度状态）+ 运行记录 + 调度管理"""
import json

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from ..db import get_db_config, query_all
from ..scheduler import manager as scheduler_manager

router = APIRouter()


# ---------- 任务配置 ----------

@router.get("/tasks")
def task_list():
    """全部任务配置（合并调度器实时状态：是否有实现/是否挂载/下次运行/是否运行中）"""
    items = scheduler_manager.list_status()
    return {
        "items": items,
        "scheduler_running": scheduler_manager.running,
        "scheduled_count": sum(1 for x in items if x["scheduled"]),
    }


class TaskUpdateBody(BaseModel):
    enabled: bool | None = None
    cron: str | None = None
    params: dict | None = None


@router.put("/tasks/{task_name}")
def task_update(task_name: str, body: TaskUpdateBody):
    """更新任务配置（enabled / cron / params），热同步调度器"""
    import pymysql

    conn = pymysql.connect(**get_db_config().to_dict())
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT task_name FROM task_config WHERE task_name=%s", (task_name,))
            if cur.fetchone() is None:
                raise HTTPException(404, f"任务 {task_name} 不存在")
            sets, params = [], []
            if body.enabled is not None:
                sets.append("enabled=%s")
                params.append(1 if body.enabled else 0)
            if body.cron is not None:
                # 空串 / '手动' 视为手动触发模式
                cron = (body.cron or "").strip() or "手动"
                sets.append("cron=%s")
                params.append(cron)
            if body.params is not None:
                sets.append("params=%s")
                params.append(json.dumps(body.params, ensure_ascii=False))
            if sets:
                sets.append("update_time=NOW()")
                sql = f"UPDATE task_config SET {', '.join(sets)} WHERE task_name=%s"
                params.append(task_name)
                cur.execute(sql, params)
            conn.commit()
    finally:
        conn.close()

    # 配置变更 → 热同步调度器（新增/移除/重排）
    sync = scheduler_manager.sync_from_db()
    item = next((x for x in scheduler_manager.list_status() if x["task_name"] == task_name), None)
    return {"ok": True, "task": item, "sync": sync}


@router.post("/tasks/{task_name}/trigger")
def task_trigger(task_name: str):
    """立即执行任务（异步，带运行中保护），结果见运行记录"""
    from ..tasks.run import TASKS

    if task_name not in TASKS:
        raise HTTPException(400, f"任务 {task_name} 无执行实现")
    result = scheduler_manager.trigger_now(task_name)
    if not result["started"]:
        raise HTTPException(409, result["reason"])
    return {"ok": True, "message": result["reason"]}


# ---------- 运行记录 ----------

@router.get("/runs")
def task_runs(
    task_name: str = Query("", description="按任务过滤，缺省全部"),
    status: str = Query("", description="success / failed / running"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """任务运行记录（倒序）"""
    where = []
    params: list = []
    if task_name:
        where.append("task_name = %s")
        params.append(task_name)
    if status:
        where.append("status = %s")
        params.append(status)
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    total = query_all(f"SELECT COUNT(*) AS n FROM task_runs {where_sql}", params)[0]["n"]
    rows = query_all(
        f"""
        SELECT id, task_name, status, started_at, finished_at, records_written, error_message
        FROM task_runs
        {where_sql}
        ORDER BY id DESC
        LIMIT %s OFFSET %s
        """,
        params + [page_size, (page - 1) * page_size],
    )
    return {"total": total, "page": page, "page_size": page_size, "items": rows}


@router.get("/stats")
def task_stats():
    """任务运行概况：最近 24 小时各任务成功/失败次数"""
    rows = query_all(
        """
        SELECT task_name,
               SUM(status = 'success') AS ok_cnt,
               SUM(status = 'failed') AS fail_cnt,
               SUM(status = 'running') AS running_cnt,
               MAX(started_at) AS last_run
        FROM task_runs
        GROUP BY task_name
        ORDER BY task_name
        """
    )
    return {"items": rows}
