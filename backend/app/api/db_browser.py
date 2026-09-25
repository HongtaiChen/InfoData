#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
数据中心 API（只读）：库表清单 / 列结构 / 表元信息 / 行数据分页查询
安全约束：
- 表名 / 列名必须来自 information_schema 白名单（进程内 60s 缓存），拼 SQL 时用反引号包裹
- 过滤值一律参数化绑定；contains 时转义 LIKE 通配符
- 行查询强制 LIMIT 上限，不做 COUNT 大表扫描

🔴 语句级超时熔断（2026-09-21 立，事故驱动）
  起因：数据中心打开 stock_market_daily 永久转圈。根因是该表缺 update_time 排序索引，
  ORDER BY update_time 在 1,717 万行上全表扫 + filesort 实测 50~158 秒 ——
  而前端 axios timeout=30s，超时后**请求被客户端丢弃，服务端查询仍在全表扫**，
  白烧 I/O 且拖慢同库其它任务。索引已补（见 scripts/seed_indexes.py 的 idx_update_time 等），
  但「补索引」只修好这一张表；本段是**结构性防线**，保证任何未来的慢查询都有兜底：
  MySQL 8 的 MAX_EXECUTION_TIME 让服务端主动中断超过 _ROWS_TIMEOUT_MS 的 SELECT，
  返回 3024 (ER_QUERY_TIMEOUT) 而不是无限期占着连接和磁盘带宽。

  范式来源：同项目 app/api/sql_explorer.py 已用同一手法（SET SESSION MAX_EXECUTION_TIME），
  此处与之保持一致，避免两套超时口径。

  为什么用「独立连接」而不是 app.db.query_all（长连接复用）：
  MAX_EXECUTION_TIME 是**会话级**变量，且长连接是按线程复用的——若在长连接上设置，
  会把这个超时泄漏给同线程上后续的其它查询（含采集器写入路径），属于隐蔽的副作用。
  故行查询走 pymysql.connect() 独立会话，用完即关。与 db.py 顶部注释的边界约定一致。
"""
import json
import threading
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from ..db import execute_write, get_db_config, query_all

router = APIRouter()

_SCHEMA = get_db_config().database
_EXACT_COUNT_THRESHOLD = 300_000  # 估算行数低于此阈值才执行精确 COUNT（保证秒级）
_ROW_LIMIT_MAX = 200
_CACHE_TTL = 60.0

# 行查询语句级超时（毫秒）。取值理由：
#   索引齐备时正常首屏 <10ms；即便翻到 5,000,000 行深分页实测约 13s，
#   故 20s 给出充裕余量的同时，能把「无索引全表扫」这类 50s+ 异常挡在超时上。
_ROWS_TIMEOUT_MS = 20_000

_cache_lock = threading.Lock()
_cache = {"tables": None, "ts": 0.0, "cols": {}}

_OPS = {"eq": "=", "ne": "<>", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}


def _fmt(v: Any, is_null: bool = True) -> Any:
    """information_schema 空值统一转 None；datetime 直接返回交由 FastAPI 序列化"""
    if is_null:
        return None
    return v


def _norm(rows: list[dict]) -> list[dict]:
    """information_schema 在 MySQL 8 返回大写列名，统一小写化便于取键"""
    return [{k.lower(): v for k, v in r.items()} for r in rows]


def _table_rows(force: bool = False) -> list[dict]:
    """adata 库业务表清单（估算行数 / 注释 / 更新时间 / 体积），60s 缓存"""
    with _cache_lock:
        now = time.time()
        if not force and _cache["tables"] is not None and now - _cache["ts"] < _CACHE_TTL:
            return _cache["tables"]
    rows = _norm(
        query_all(
            """
            SELECT table_name, table_rows, table_comment, update_time,
                   data_length, index_length
            FROM information_schema.tables
            WHERE table_schema = %s AND table_type = 'BASE TABLE'
            ORDER BY table_name
            """,
            (_SCHEMA,),
        )
    )
    items = [
        {
            "name": r["table_name"],
            "rows_estimate": int(r["table_rows"] or 0),
            "comment": (r["table_comment"] or "").strip(),
            "update_time": r["update_time"].isoformat() if r["update_time"] else None,
            "data_bytes": int(r["data_length"] or 0) + int(r["index_length"] or 0),
        }
        for r in rows
    ]
    with _cache_lock:
        _cache["tables"], _cache["ts"] = items, now
    return items


def _col_map(table: str, force: bool = False) -> dict[str, dict]:
    """表列元数据（名称→{data_type, is_primary}），60s 缓存"""
    with _cache_lock:
        cached = _cache["cols"].get(table)
        if not force and cached and time.time() - cached["ts"] < _CACHE_TTL:
            return cached["map"]
    rows = _norm(
        query_all(
            """
            SELECT column_name, data_type, column_type, is_nullable,
                   column_key, column_comment
            FROM information_schema.columns
            WHERE table_schema = %s AND table_name = %s
            ORDER BY ordinal_position
            """,
            (_SCHEMA, table),
        )
    )
    col_map = {
        r["column_name"]: {
            "data_type": r["data_type"],
            "column_type": r["column_type"],
            "nullable": r["is_nullable"] == "YES",
            "is_primary": r["column_key"] == "PRI",
            "comment": (r["column_comment"] or "").strip(),
        }
        for r in rows
    }
    if not col_map:
        raise HTTPException(404, f"表 {table} 不存在或无访问权限")
    with _cache_lock:
        _cache["cols"][table] = {"map": col_map, "ts": time.time()}
    return col_map


def _require_table(table: str) -> None:
    if table not in {t["name"] for t in _table_rows()}:
        raise HTTPException(404, f"表 {table} 不存在")


def _query_rows_timed(sql: str, params: list, timeout_ms: int = _ROWS_TIMEOUT_MS) -> list[dict]:
    """带语句级超时的行查询（独立会话，见模块 docstring 的超时熔断说明）。

    超时（MySQL 3024）转 504 并给出可行动提示——把「无限期卡住」变成「明确失败」，
    前端可据此提示用户换列排序或加过滤条件，而不是无休止转圈。
    """
    import pymysql

    conn = pymysql.connect(**get_db_config().to_dict())
    try:
        with conn.cursor() as cur:
            cur.execute("SET SESSION MAX_EXECUTION_TIME = %s", (timeout_ms,))
            try:
                cur.execute(sql, params)
                cols = [d[0] for d in cur.description]
                return [dict(zip(cols, row)) for row in cur.fetchall()]
            except pymysql.err.OperationalError as e:
                code = e.args[0] if e.args else None
                msg = str(e.args[1] if len(e.args) > 1 else e)
                # 3024 = ER_QUERY_TIMEOUT（MAX_EXECUTION_TIME 触发）
                if code == 3024 or "MAX_EXECUTION_TIME" in msg.upper() or "QUERY EXECUTION WAS INTERRUPTED" in msg.upper():
                    raise HTTPException(
                        504,
                        f"查询超过 {timeout_ms / 1000:.0f} 秒被中断（{table_hint(sql)}）。"
                        f"该排序/过滤列可能缺少索引，建议：换一列排序、缩小时间范围或加过滤条件。",
                    )
                raise
    finally:
        try:
            conn.close()
        except Exception:
            pass


def table_hint(sql: str) -> str:
    """从 SQL 里抠出表名，仅用于错误文案"""
    try:
        seg = sql.split("FROM `", 1)[1]
        return f"表 {seg.split('`', 1)[0]}"
    except Exception:
        return "该表"


# ---------- 表清单 ----------

@router.get("/tables")
def db_tables():
    """库表清单（information_schema 估算，不触碰业务表）"""
    return {"schema": _SCHEMA, "items": _table_rows()}


@router.get("/tables/{table}/columns")
def table_columns(table: str):
    """表结构：列名 / 类型 / 主键 / 注释"""
    _require_table(table)
    rows = _norm(
        query_all(
            """
            SELECT column_name, column_type, data_type, is_nullable,
                   column_key, column_comment, ordinal_position
            FROM information_schema.columns
            WHERE table_schema = %s AND table_name = %s
            ORDER BY ordinal_position
            """,
            (_SCHEMA, table),
        )
    )
    return {
        "table": table,
        "items": [
            {
                "name": r["column_name"],
                "data_type": r["data_type"],
                "column_type": r["column_type"],
                "nullable": r["is_nullable"] == "YES",
                "is_primary": r["column_key"] == "PRI",
                "comment": (r["column_comment"] or "").strip(),
            }
            for r in rows
        ],
    }


@router.get("/tables/{table}/meta")
def table_meta(table: str):
    """表元信息：估算行数；小表补精确 COUNT（避免大表全扫）"""
    _require_table(table)
    t = next(x for x in _table_rows() if x["name"] == table)
    estimated = t["rows_estimate"]
    exact: int | None = None
    if estimated < _EXACT_COUNT_THRESHOLD:
        row = query_all(f"SELECT COUNT(*) AS n FROM `{table}`")
        exact = int(row[0]["n"])
    return {
        "table": table,
        "comment": t["comment"],
        "update_time": t["update_time"],
        "estimated_rows": estimated,
        "exact_rows": exact,
        "is_estimate": exact is None,
        "count_threshold": _EXACT_COUNT_THRESHOLD,
    }


@router.get("/tables/{table}/rows")
def table_rows(
    table: str,
    page_size: int = Query(50, ge=1, le=_ROW_LIMIT_MAX),
    offset: int = Query(0, ge=0),
    sort_col: str = Query("", description="排序列（须为表内列名）"),
    sort_dir: str = Query("asc", pattern="^(asc|desc)$"),
    filters: str = Query("[]", description='[{"col":"x","op":"eq|ne|gt|gte|lt|lte|contains","val":"v"}]'),
):
    """只读行查询：分页 + 白名单排序 + 参数化过滤。返回多取 1 行以判定 has_more"""
    _require_table(table)
    cols = _col_map(table)
    _col_names = set(cols)

    order_by = ""
    if sort_col:
        if sort_col not in _col_names:
            raise HTTPException(400, f"排序列 {sort_col} 不存在于表 {table}")
        order_by = f" ORDER BY `{sort_col}` {'asc' if sort_dir == 'asc' else 'desc'}"

    # 解析并校验过滤条件
    try:
        raw_filters = json.loads(filters)
    except json.JSONDecodeError:
        raise HTTPException(400, "filters 参数不是合法 JSON")
    if not isinstance(raw_filters, list):
        raise HTTPException(400, "filters 应为数组")

    where, params = [], []
    for i, f in enumerate(raw_filters):
        col = str(f.get("col", ""))
        op = str(f.get("op", ""))
        val = f.get("val")
        if col not in _col_names:
            raise HTTPException(400, f"第 {i + 1} 个过滤条件列 {col} 不存在于表 {table}")
        if op not in _OPS and op not in ("contains",):
            raise HTTPException(400, f"不支持的过滤操作符 {op}")
        if val is None or val == "":
            continue  # 空值条件跳过
        if op == "contains":
            # 转义 LIKE 通配符，按字面包含匹配
            escaped = str(val).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            where.append(f"`{col}` LIKE CONCAT('%%', %s, '%%')")
            params.append(escaped)
        else:
            where.append(f"`{col}` {_OPS[op]} %s")
            params.append(val)

    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    limit = min(page_size, _ROW_LIMIT_MAX)
    sql = (
        f"SELECT * FROM `{table}`{where_sql}{order_by} "
        f"LIMIT {limit + 1} OFFSET {offset}"
    )
    rows = _query_rows_timed(sql, params)
    has_more = len(rows) > limit
    return {
        "table": table,
        "offset": offset,
        "page_size": limit,
        "count": len(rows[:limit]),
        "has_more": has_more,
        "rows": rows[:limit],
    }


# ---------- 表级数据流聚合（数据源/清洗口径/维护任务调度/最近运行） ----------

@router.get("/tables/flow")
def tables_flow():
    """表级数据流聚合（一次返回全部表）：
    table_meta(数据源/清洗口径/writers) + 每个写表任务的实时调度（cron/启用/下次运行/运行中）
    + task_runs 最近一次运行状态与写入条数。供数据中心右侧「数据流」卡联动展示。
    """
    # 1. 静态元数据
    metas = _norm(
        query_all(
            "SELECT table_name, category, source_desc, flow_desc, writers, writer_cols, note "
            "FROM table_meta"
        )
    )
    meta_map: dict[str, dict] = {}
    for r in metas:
        writers = r.get("writers")
        if isinstance(writers, str):
            try:
                writers = json.loads(writers)
            except json.JSONDecodeError:
                writers = []
        writer_cols = r.get("writer_cols")
        if isinstance(writer_cols, str):
            try:
                writer_cols = json.loads(writer_cols)
            except json.JSONDecodeError:
                writer_cols = None
        meta_map[r["table_name"]] = {
            "table_name": r["table_name"],
            "category": r.get("category"),
            "source_desc": r.get("source_desc"),
            "flow_desc": r.get("flow_desc"),
            "note": r.get("note"),
            "writers": writers or [],
            "writer_cols": writer_cols or {},
            "jobs": [],
        }

    # 2. 任务调度实时状态（含 next_run / running / scheduled）
    try:
        from ..scheduler import manager as scheduler_manager

        status_items = scheduler_manager.list_status()
        task_cfg = {x["task_name"]: x for x in status_items}
    except Exception:
        task_cfg = {}

    # 3. 每个任务最近一次运行（task_runs 按任务取最大 id）
    try:
        runs = _norm(
            query_all(
                "SELECT task_name, status, started_at, finished_at, records_written, "
                "       error_message, run_detail, id "
                "FROM task_runs "
                "WHERE id IN (SELECT MAX(id) FROM task_runs GROUP BY task_name)"
            )
        )
    except Exception:
        runs = []
    last_map = {r["task_name"]: r for r in runs}

    def _parse_run_detail(raw):
        """run_detail JSON → dict（含 run_steps 步骤链 + 当轮实录）

        兼容历史行：2026-09-19 之前写入方把步骤链塞在第二层
        （`run_detail.run_detail.run_steps`），前端读第一层 → 实录值全空。
        写入侧已在 `app/tasks/run.py::_flatten_run_detail` 修正；这里对**存量行**
        读时展平，让尚未重跑的任务（如月频 index_cons_sync、手动 ai_concept_analysis）
        也能立刻显示实录值，不必等下一轮。
        """
        if not raw:
            return None
        if isinstance(raw, dict):
            d = raw
        else:
            try:
                d = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                return None
            if not isinstance(d, dict):
                return d
        inner = d.get("run_detail")
        if isinstance(inner, dict):
            d = {k: v for k, v in d.items() if k != "run_detail"}
            d.update(inner)
        return d

    def _collector_run_steps(task_name: str):
        """动态读取采集器模块的 RUN_STEPS 模板（代码即模板，改完即时生效）。
        任务名与采集器模块不一致的特例走 _MOD_FALLBACK；
        无 RUN_STEPS 的任务返回 None 前端不展示步骤链。"""
        import importlib

        # 任务名 ≠ 采集器模块名的特例（如 ai_concept_analysis 实现在 analysis/concept_ai）
        _MOD_FALLBACK = {"ai_concept_analysis": "app.analysis.concept_ai"}

        def _try_load(path: str):
            try:
                mod = importlib.import_module(path)
                steps = getattr(mod, "RUN_STEPS", None)
                if steps:
                    return [
                        {
                            "no": s.get("no"),
                            "name": s.get("name"),
                            "params": s.get("params"),
                        }
                        for s in steps
                    ]
            except Exception:
                pass
            return None

        steps = _try_load(f"app.collectors.{task_name}")
        if steps is None and task_name in _MOD_FALLBACK:
            steps = _try_load(_MOD_FALLBACK[task_name])
        return steps

    for tbl, m in meta_map.items():
        wcols = m["writer_cols"]
        for w in m["writers"]:
            cfg = task_cfg.get(w)
            last = last_map.get(w)
            wc = wcols.get(w) if isinstance(wcols, dict) else None
            m["jobs"].append(
                {
                    "task_name": w,
                    "cron": cfg.get("cron") if cfg else None,
                    # cron_human（2026-09-24 新增透传）：调度器已把 cron 渲染成中文语义，
                    # 但本接口此前没带出来 ⇒ 前端只能展示原始数字。
                    # 这是「排期权威源」：table_meta.flow_desc 里写死的时刻会随改 cron 漂移，
                    # 前端一律以本字段为准（见 DataView 数据流明细的「排期」区块）。
                    "cron_human": cfg.get("cron_human") if cfg else None,
                    "enabled": cfg.get("enabled") if cfg else None,
                    "scheduled": cfg.get("scheduled") if cfg else None,
                    "next_run": cfg.get("next_run") if cfg else None,
                    "running": cfg.get("running") if cfg else None,
                    "implemented": cfg.get("implemented") if cfg else None,
                    "last": {
                        "status": last["status"],
                        "started_at": last["started_at"],
                        "finished_at": last["finished_at"],
                        "records_written": last["records_written"],
                        "error_message": last["error_message"],
                        "run_detail": _parse_run_detail(last.get("run_detail")),
                    }
                    if last
                    else None,
                    # 运行步骤链模板（采集器 RUN_STEPS 常量，代码即模板）
                    "run_steps": _collector_run_steps(w),
                    # 列级血缘（writer_cols 中该任务的映射）
                    "lineage": {
                        "source": wc.get("source") if wc else None,
                        "cols": (wc.get("cols") or []) if wc else [],
                        "derived": (wc.get("derived") or []) if wc else [],
                        "note": wc.get("note") if wc else None,
                        "col_notes": (wc.get("col_notes") or {}) if wc else {},
                    }
                    if wc
                    else None,
                }
            )
    return {"items": meta_map}


# ---------- 表分类维护（数据中心前端可直接归类） ----------

# 允许的分类白名单（**必须与前端 DataView GROUPS 声明逐字一致**；空串 = 未分类）
_ALLOWED_CATEGORIES = {
    "行情", "资料", "概念", "日历", "基金", "资金", "财务", "债券", "指数",
    "AI", "商品", "资讯", "行业", "调研", "基本面",
    # 2026-09-25 补齐：这 7 个分类早已在 table_meta 里使用，但白名单与前端 GROUPS
    # 双双向漏 ⇒ 后果分两层：① 前端整类表行不渲染（10 张表在左侧树里凭空消失）；
    # ② 即便看到表，也**改不回**这个分类（下拉里没有它，API 直接 400）。
    "宏观", "利率", "汇率", "海外", "估值", "分析", "回购",
    "质量", "系统",
}


class CategoryReq(BaseModel):
    category: str = Field("", max_length=20, description="分类名；空串 = 移出分类（未分类）")


@router.patch("/tables/{table}/category")
def set_table_category(table: str, req: CategoryReq):
    """更新表分类（table_meta.category）。
    表未入库元数据时自动插入一行（仅 table_name + category）；
    传空串表示移出分类（左侧树落到「未分类」）。"""
    _require_table(table)
    cat = req.category.strip()
    if cat and cat not in _ALLOWED_CATEGORIES:
        raise HTTPException(
            400, f"未知分类「{cat}」，允许值：{'/'.join(sorted(_ALLOWED_CATEGORIES))}"
        )
    affected = execute_write(
        "INSERT INTO table_meta (table_name, category) VALUES (%s, NULLIF(%s, '')) "
        "ON DUPLICATE KEY UPDATE category = VALUES(category)",
        (table, cat),
    )
    return {"table": table, "category": cat or None, "changed": affected > 0}
