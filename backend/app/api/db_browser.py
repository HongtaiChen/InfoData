#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
数据浏览 API（只读）：库表清单 / 列结构 / 表元信息 / 行数据分页查询
安全约束：
- 表名 / 列名必须来自 information_schema 白名单（进程内 60s 缓存），拼 SQL 时用反引号包裹
- 过滤值一律参数化绑定；contains 时转义 LIKE 通配符
- 行查询强制 LIMIT 上限，不做 COUNT 大表扫描
"""
import json
import threading
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from ..db import get_db_config, query_all

router = APIRouter()

_SCHEMA = get_db_config().database
_EXACT_COUNT_THRESHOLD = 300_000  # 估算行数低于此阈值才执行精确 COUNT（保证秒级）
_ROW_LIMIT_MAX = 200
_CACHE_TTL = 60.0

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
    rows = query_all(sql, params)
    has_more = len(rows) > limit
    return {
        "table": table,
        "offset": offset,
        "page_size": limit,
        "count": len(rows[:limit]),
        "has_more": has_more,
        "rows": rows[:limit],
    }
