#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
InvestBuddy 数据质量 API（数据质量栏目后端）
- 全部只读（除 POST /dq/trigger 触发体检，带运行中保护）
- 轮次概念：dq_report 每次体检生成一轮（run_at），展示/统计均取"最新一轮"
"""
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from ..db import get_db_config, query_all
from ..scheduler import manager as scheduler_manager

router = APIRouter()

_STATUS_ORDER = ["pass", "warning", "fail", "error"]


class RuleUpdateBody(BaseModel):
    enabled: bool | None = None


@router.put("/rules/{rule_name}")
def dq_rule_update(rule_name: str, body: RuleUpdateBody):
    """启停某条规则（热生效：下轮体检按新配置执行）"""
    import pymysql

    if body.enabled is None:
        raise HTTPException(400, "缺少 enabled")
    conn = pymysql.connect(**get_db_config().to_dict())
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT rule_name FROM dq_rules WHERE rule_name=%s", (rule_name,))
            if cur.fetchone() is None:
                raise HTTPException(404, f"规则 {rule_name} 不存在")
            cur.execute("UPDATE dq_rules SET enabled=%s WHERE rule_name=%s", (1 if body.enabled else 0, rule_name))
            conn.commit()
    finally:
        conn.close()
    return {"ok": True}


def _latest_run() -> str | None:
    row = query_all("SELECT MAX(run_at) AS m FROM dq_report")
    return row[0]["m"] if row and row[0]["m"] else None


@router.get("/summary")
def dq_summary():
    """最新一轮体检概况：时间 + 状态分布 + 严重级分布 + 异常明细"""
    latest = _latest_run()
    if latest is None:
        return {"run_at": None, "counts": {}, "by_severity": {}, "issues": []}

    dist = query_all(
        "SELECT status, COUNT(*) AS c FROM dq_report WHERE run_at=%s GROUP BY status",
        (latest,),
    )
    counts = {s: 0 for s in _STATUS_ORDER}
    counts.update({r["status"]: r["c"] for r in dist})

    sev_rows = query_all(
        "SELECT severity, status, COUNT(*) AS c FROM dq_report WHERE run_at=%s GROUP BY severity, status",
        (latest,),
    )
    by_severity: dict[str, dict] = {}
    for r in sev_rows:
        box = by_severity.setdefault(r["severity"], {s: 0 for s in _STATUS_ORDER})
        box[r["status"]] = r["c"]

    issues = query_all(
        """
        SELECT id, rule_name, table_name, check_type, severity, status,
               metric_value, message
        FROM dq_report WHERE run_at=%s AND status <> 'pass'
        ORDER BY FIELD(severity,'critical','warning','info'), FIELD(status,'error','fail','warning'), table_name
        """,
        (latest,),
    )
    return {
        "run_at": latest,
        "run_date": str(latest)[:10],
        "counts": counts,
        "by_severity": by_severity,
        "issues": issues,
        "total_rules": sum(counts.values()),
    }


@router.get("/report")
def dq_report(
    table: str = Query("", description="按表过滤"),
    status: str = Query("", description="pass/warning/fail/error"),
):
    """最新一轮逐规则明细"""
    latest = _latest_run()
    if latest is None:
        return {"run_at": None, "items": []}
    where = ["run_at = %s"]
    params: list = [latest]
    if table:
        where.append("table_name = %s")
        params.append(table)
    if status:
        where.append("status = %s")
        params.append(status)
    rows = query_all(
        f"""
        SELECT id, rule_name, table_name, check_type, severity, status,
               metric_value, message
        FROM dq_report WHERE {' AND '.join(where)}
        ORDER BY table_name, severity, id
        """,
        params,
    )
    return {"run_at": latest, "items": rows}


@router.get("/rules")
def dq_rules(enabled: int | None = Query(None, description="1/0 过滤")):
    """规则配置 + 最新一轮状态（用于栏目规则视图）"""
    latest = _latest_run()
    rows = query_all("SELECT * FROM dq_rules ORDER BY table_name, id")
    latest_map = {}
    if latest:
        lr = query_all(
            "SELECT rule_name, status, metric_value, message FROM dq_report WHERE run_at=%s",
            (latest,),
        )
        latest_map = {r["rule_name"]: r for r in lr}
    out = []
    for r in rows:
        item = dict(r)
        item.pop("params", None)
        if "update_time" in item:
            item["update_time"] = str(item["update_time"])[:19] if item["update_time"] else None
        item["enabled"] = bool(r["enabled"])
        if enabled is not None and item["enabled"] != bool(enabled):
            continue
        item["last_status"] = (latest_map.get(r["rule_name"]) or {}).get("status")
        out.append(item)
    return {"run_at": latest, "items": out}


@router.get("/history")
def dq_history(days: int = Query(14, ge=1, le=90)):
    """近 N 天每日体检状态分布（每天取最后一次轮次，供趋势图）"""
    rows = query_all(
        """
        SELECT r.run_date, r.status, COUNT(*) AS c
        FROM dq_report r
        JOIN (
            SELECT run_date, MAX(run_at) AS m FROM dq_report
            WHERE run_date >= DATE_SUB(CURDATE(), INTERVAL %s DAY)
            GROUP BY run_date
        ) t ON r.run_at = t.m
        GROUP BY r.run_date, r.status
        ORDER BY r.run_date
        """,
        (days,),
    )
    return {"days": days, "items": rows}


@router.post("/trigger")
def dq_trigger():
    """立即触发一轮体检（异步，复用 scheduler 运行中保护与 task_runs 审计）"""
    result = scheduler_manager.trigger_now("data_quality_check")
    if not result["started"]:
        raise HTTPException(409, result["reason"])
    return {"ok": True, "message": result["reason"]}


@router.get("/table-status")
def dq_table_status():
    """最新一轮按表聚合状态（供数据中心表清单叠加质量标记）"""
    latest = _latest_run()
    if latest is None:
        return {"run_at": None, "items": []}
    agg = query_all(
        """
        SELECT table_name,
               MAX(FIELD(status,'pass','warning','fail','error')) AS worst_n,
               SUM(status='pass')     AS pass_c,
               SUM(status='warning')  AS warning_c,
               SUM(status='fail')     AS fail_c,
               SUM(status='error')    AS error_c,
               COUNT(*)               AS total_c
        FROM dq_report WHERE run_at = %s
        GROUP BY table_name
        ORDER BY worst_n DESC, table_name
        """,
        (latest,),
    )
    issues = query_all(
        """
        SELECT table_name, rule_name, severity, status, metric_value, message
        FROM dq_report
        WHERE run_at = %s AND status <> 'pass'
        ORDER BY table_name,
                 FIELD(status, 'error', 'fail', 'warning'),
                 FIELD(severity, 'critical', 'warning', 'info')
        """,
        (latest,),
    )
    issues_by_table: dict[str, list] = {}
    for r in issues:
        issues_by_table.setdefault(r["table_name"], []).append(r)
    items = []
    for r in agg:
        table = r["table_name"]
        items.append(
            {
                "table_name": table,
                "worst": _STATUS_ORDER[(r["worst_n"] or 1) - 1],
                "counts": {
                    "pass": r["pass_c"],
                    "warning": r["warning_c"],
                    "fail": r["fail_c"],
                    "error": r["error_c"],
                    "total": r["total_c"],
                },
                "issues": issues_by_table.get(table, []),
            }
        )
    return {"run_at": latest, "items": items}
