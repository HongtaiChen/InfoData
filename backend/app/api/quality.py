#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
InvestBuddy 数据质量 API（数据质量栏目后端）
- 全部只读（除 POST /dq/trigger 触发体检，带运行中保护）
- 轮次概念：dq_report 每次体检生成一轮（run_at）

⚠️ 结果语义（2026-09-12 修正）：
  日检（daily，每日 20:30，31 条）与周检（weekly，周一 21:30，5 条）是**两个独立轮次**，
  对账（check_type='recon'）又是**任务级**的第三类写入。
  若沿用"取全表 MAX(run_at) 那一轮"：
    ① 周一 21:30 周检跑完 → 明细只剩 5 条，31 条日检规则集体消失（直到次日 20:30）；
    ② 工作日 20:45 / 周日 10:00 对账跑完 → 明细只剩 1 条对账记录，36 条规则全消失。
  故统一改为 **「每条规则各自最近一次结果」**（JOIN dq_rules 限定在册规则），
  并让 run_at 仅作为"最近一次体检轮次"的展示字段。
"""
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from ..db import get_db_config, query_all
from ..scheduler import manager as scheduler_manager

router = APIRouter()

_STATUS_ORDER = ["pass", "warning", "fail", "error"]
_SEV_RANK = {"critical": 0, "warning": 1, "info": 2}
_ST_RANK = {"error": 0, "fail": 1, "warning": 2}
_RECON_TYPE = "recon"
_RECON_TABLE = "stock_market_daily"


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


def _latest_rule_run() -> str | None:
    """最近一次**规则体检**轮次（排除对账轮次）

    周检轮次同样算"体检轮次"，仅用于展示"最近体检时间"；统计口径见 _latest_per_rule()。
    """
    row = query_all(
        "SELECT MAX(rj.run_at) AS m FROM dq_report rj JOIN dq_rules dr ON dr.rule_name = rj.rule_name "
        "WHERE rj.check_type <> %s",
        (_RECON_TYPE,),
    )
    return row[0]["m"] if row and row[0]["m"] else None


def _latest_per_rule() -> dict[str, dict]:
    """每条在册规则各自最近一次结果（日检/周检轮次互不顶掉；对账不参与）"""
    rows = query_all(
        """
        SELECT r.rule_name, r.table_name, r.check_type, r.severity, r.status,
               r.metric_value, r.message, r.run_at
        FROM dq_report r
        JOIN dq_rules dr ON dr.rule_name = r.rule_name
        JOIN (
            SELECT rule_name, MAX(run_at) AS m
            FROM dq_report WHERE check_type <> %s GROUP BY rule_name
        ) t ON t.rule_name = r.rule_name AND t.m = r.run_at
        """,
        (_RECON_TYPE,),
    )
    out: dict[str, dict] = {}
    for r in rows:
        r["run_at"] = str(r["run_at"])[:19] if r["run_at"] else None
        out[r["rule_name"]] = r
    return out


def _rule_groups() -> dict[str, str]:
    return {r["rule_name"]: (r["rule_group"] or "daily") for r in query_all("SELECT rule_name, rule_group FROM dq_rules")}


def _empty_summary() -> dict:
    return {"run_at": None, "counts": {}, "by_severity": {}, "issues": [], "total_rules": 0}


@router.get("/summary")
def dq_summary():
    """概况：每条规则最近一次结果的状态分布 + 严重级分布 + 异常明细"""
    latest = _latest_rule_run()
    if latest is None:
        return _empty_summary()
    per = _latest_per_rule()
    counts = {s: 0 for s in _STATUS_ORDER}
    by_severity: dict[str, dict] = {}
    issues: list[dict] = []
    for name, r in per.items():
        counts[r["status"]] = counts.get(r["status"], 0) + 1
        box = by_severity.setdefault(r["severity"] or "warning", {s: 0 for s in _STATUS_ORDER})
        box[r["status"]] = box.get(r["status"], 0) + 1
        if r["status"] != "pass":
            issues.append(
                {
                    "id": r["rule_name"],
                    "rule_name": name,
                    "table_name": r["table_name"],
                    "check_type": r["check_type"],
                    "severity": r["severity"],
                    "status": r["status"],
                    "metric_value": r["metric_value"],
                    "message": r["message"],
                    "run_at": r["run_at"],
                }
            )
    issues.sort(key=lambda x: (_SEV_RANK.get(x["severity"], 9), _ST_RANK.get(x["status"], 9), x["table_name"] or ""))
    return {
        "run_at": latest,
        "run_date": str(latest)[:10],
        "counts": counts,
        "by_severity": by_severity,
        "issues": issues,
        "total_rules": len(per),
    }


@router.get("/report")
def dq_report(
    table: str = Query("", description="按表过滤"),
    status: str = Query("", description="pass/warning/fail/error"),
    group: str = Query("", description="daily / weekly 按频率过滤"),
):
    """每条规则最近一次结果的逐规则明细（含所属轮次时间）"""
    latest = _latest_rule_run()
    if latest is None:
        return {"run_at": None, "items": []}
    groups = _rule_groups()
    items = []
    for name, r in _latest_per_rule().items():
        g = groups.get(name, "daily")
        if group and g != group:
            continue
        if table and r["table_name"] != table:
            continue
        if status and r["status"] != status:
            continue
        items.append({**r, "rule_group": g})
    items.sort(key=lambda x: (x["table_name"] or "", x["rule_group"] == "weekly", x["rule_name"]))
    return {"run_at": latest, "items": items}


@router.get("/rules")
def dq_rules(enabled: int | None = Query(None, description="1/0 过滤")):
    """规则配置 + 每条规则最近一次结果（用于栏目规则视图）"""
    latest = _latest_rule_run()
    rows = query_all("SELECT * FROM dq_rules ORDER BY table_name, rule_group, id")
    latest_map = _latest_per_rule()
    out = []
    for r in rows:
        item = dict(r)
        item.pop("params", None)
        if "update_time" in item:
            item["update_time"] = str(item["update_time"])[:19] if item["update_time"] else None
        item["enabled"] = bool(r["enabled"])
        item["rule_group"] = r["rule_group"] or "daily"
        if enabled is not None and item["enabled"] != bool(enabled):
            continue
        hit = latest_map.get(r["rule_name"])
        item["last_status"] = hit["status"] if hit else None
        item["last_run_at"] = hit["run_at"] if hit else None
        item["last_metric"] = hit["metric_value"] if hit else None
        out.append(item)
    return {"run_at": latest, "items": out}


@router.get("/history")
def dq_history(days: int = Query(14, ge=1, le=90)):
    """近 N 天每日各规则**最终结果**的状态分布（同规则同日多轮取最后一轮，排除对账）"""
    rows = query_all(
        """
        SELECT t.run_date, t.status, COUNT(*) AS c
        FROM (
            SELECT r.run_date, r.rule_name, r.status,
                   ROW_NUMBER() OVER (PARTITION BY r.run_date, r.rule_name ORDER BY r.run_at DESC) AS rn
            FROM dq_report r
            JOIN dq_rules dr ON dr.rule_name = r.rule_name
            WHERE r.check_type <> %s AND r.run_date >= DATE_SUB(CURDATE(), INTERVAL %s DAY)
        ) t
        WHERE t.rn = 1
        GROUP BY t.run_date, t.status
        ORDER BY t.run_date
        """,
        (_RECON_TYPE, days),
    )
    for r in rows:
        r["run_date"] = str(r["run_date"])
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
    """每条规则最近一次结果按表聚合（供数据中心表清单叠加质量标记）"""
    latest = _latest_rule_run()
    if latest is None:
        return {"run_at": None, "items": []}
    per = _latest_per_rule()
    agg: dict[str, dict] = {}
    for r in per.values():
        t = r["table_name"]
        box = agg.setdefault(
            t,
            {"pass": 0, "warning": 0, "fail": 0, "error": 0, "total": 0, "worst_n": 0, "issues": []},
        )
        box[r["status"]] = box.get(r["status"], 0) + 1
        box["total"] += 1
        box["worst_n"] = max(box["worst_n"], _STATUS_ORDER.index(r["status"]) + 1 if r["status"] in _STATUS_ORDER else 1)
        if r["status"] != "pass":
            box["issues"].append(
                {
                    "table_name": t,
                    "rule_name": r["rule_name"],
                    "severity": r["severity"],
                    "status": r["status"],
                    "metric_value": r["metric_value"],
                    "message": r["message"],
                }
            )
    items = []
    for t, b in sorted(agg.items(), key=lambda kv: (-kv[1]["worst_n"], kv[0])):
        items.append(
            {
                "table_name": t,
                "worst": _STATUS_ORDER[max(0, b["worst_n"] - 1)],
                "counts": {k: b[k] for k in ("pass", "warning", "fail", "error", "total")},
                "issues": b["issues"],
            }
        )
    return {"run_at": latest, "items": items}


@router.get("/recon")
def dq_recon(
    limit: int = Query(10, ge=1, le=50, description="返回最近 N 轮对账"),
    run_at: str = Query("", description="指定轮次看差异明细，缺省取最近一轮"),
):
    """外部对账（L2）结果 + 全史缺口（L1/L3）待办概览"""
    rounds = query_all(
        """
        SELECT run_at, run_date, rule_name, status, metric_value, message
        FROM dq_report WHERE check_type = %s ORDER BY run_at DESC LIMIT %s
        """,
        (_RECON_TYPE, limit),
    )
    for r in rounds:
        r["run_at"] = str(r["run_at"])[:19] if r["run_at"] else None
        r["run_date"] = str(r["run_date"]) if r["run_date"] else None

    cur = run_at or (rounds[0]["run_at"] if rounds else "")
    details: list[dict] = []
    stats: list[dict] = []
    if cur:
        details = query_all(
            """
            SELECT stock_code, trade_date, diff_type, col_name, local_value, remote_value,
                   remote_source, note
            FROM dq_recon_detail WHERE run_at = %s
            ORDER BY diff_type, stock_code LIMIT 500
            """,
            (cur,),
        )
        for d in details:
            d["trade_date"] = str(d["trade_date"]) if d["trade_date"] else None
        stats = query_all(
            """
            SELECT diff_type, COUNT(*) AS c, COUNT(DISTINCT stock_code) AS codes
            FROM dq_recon_detail WHERE run_at = %s GROUP BY diff_type ORDER BY c DESC
            """,
            (cur,),
        )

    gaps = query_all(
        """
        SELECT
          (SELECT COUNT(*) FROM (SELECT DISTINCT stock_code, prev_date, next_date FROM dq_gap_detail) x) AS total,
          (SELECT COUNT(*) FROM (SELECT DISTINCT stock_code, prev_date, next_date FROM dq_gap_detail WHERE status = 'open') x) AS open_,
          (SELECT COUNT(*) FROM (SELECT DISTINCT stock_code, prev_date, next_date FROM dq_gap_detail WHERE status = 'fixed') x) AS fixed_,
          (SELECT COUNT(*) FROM (SELECT DISTINCT stock_code, prev_date, next_date FROM dq_gap_detail WHERE risk = 'high') x) AS high_
        """
    )
    top_gaps = query_all(
        """
        SELECT stock_code, MAX(prev_date) AS prev_date, MAX(next_date) AS next_date,
               MAX(gap_days) AS gap_days, MAX(risk) AS risk, MAX(status) AS status
        FROM dq_gap_detail WHERE status = 'open'
        GROUP BY stock_code ORDER BY gap_days DESC LIMIT 10
        """
    )
    for g in top_gaps:
        g["prev_date"] = str(g["prev_date"]) if g["prev_date"] else None
        g["next_date"] = str(g["next_date"]) if g["next_date"] else None

    return {
        "run_at": cur,
        "rounds": rounds,
        "stats": stats,
        "details": details,
        "gap_summary": gaps[0] if gaps else {},
        "top_gaps": top_gaps,
    }
