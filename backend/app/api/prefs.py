#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
用户偏好 API（服务端持久化）

当前支持 kind = 'table_sort'：数据中心每张表记住用户自定义的默认排序
（scope = 表名，payload = {"col": "...", "dir": "asc"|"desc"}）。

- 存储：adata.user_prefs（owner + kind + scope 唯一键，payload JSON）
- 无鉴权（本地单用户项目），owner 固定 'local'，为将来多用户预留字段
- 惰性建表：首个请求时 CREATE TABLE IF NOT EXISTS（幂等，进程内只尝试一次）
"""
import json
import threading
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..db import get_db_config, query_all

router = APIRouter()

_OWNER = "local"
_KIND_TABLE_SORT = "table_sort"

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS user_prefs (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  owner VARCHAR(64) NOT NULL,
  kind VARCHAR(32) NOT NULL,
  scope VARCHAR(128) NOT NULL,
  payload JSON NOT NULL,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_owner_kind_scope (owner, kind, scope)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='用户偏好（服务端持久化）'
"""

_ensure_lock = threading.Lock()
_ensure_done = False


def _ensure_table() -> None:
    """惰性建表（幂等）；建表失败仅抛给上层 500"""
    global _ensure_done
    if _ensure_done:
        return
    with _ensure_lock:
        if _ensure_done:
            return
        import pymysql

        conn = pymysql.connect(**get_db_config().to_dict())
        try:
            with conn.cursor() as cur:
                cur.execute(_CREATE_SQL)
            conn.commit()
        finally:
            conn.close()
        _ensure_done = True


def _execute(sql: str, params: tuple | list) -> None:
    """执行写语句（自开连接，自动提交）"""
    import pymysql

    conn = pymysql.connect(**get_db_config().to_dict())
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


def _load_all(kind: str) -> list[dict]:
    """读全部 scope->payload（返回原始 dict）"""
    rows = query_all(
        "SELECT scope, payload FROM user_prefs WHERE owner = %s AND kind = %s ORDER BY scope",
        (_OWNER, kind),
    )
    out: list[dict] = []
    for r in rows:
        raw = r["payload"]
        try:
            payload = json.loads(raw) if isinstance(raw, str) else raw
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(payload, dict):
            out.append({"scope": r["scope"], "payload": payload})
    return out


# ---------- 表排序偏好 ----------

class TableSortBody(BaseModel):
    col: str = Field(..., min_length=1, max_length=64)
    dir: str = Field("asc", pattern="^(asc|desc)$")


@router.get("/table-sorts")
def list_table_sorts() -> dict[str, Any]:
    """全部表的自定义排序偏好：{ items: { 表名: {col, dir} } }"""
    _ensure_table()
    items = {}
    for row in _load_all(_KIND_TABLE_SORT):
        items[row["scope"]] = {
            "col": str(row["payload"].get("col", "")),
            "dir": row["payload"].get("dir", "asc"),
        }
    return {"items": items}


@router.put("/table-sorts/{table}")
def upsert_table_sort(table: str, body: TableSortBody) -> dict[str, Any]:
    """记住某表的默认排序（幂等 upsert）"""
    _ensure_table()
    _execute(
        """
        INSERT INTO user_prefs (owner, kind, scope, payload)
        VALUES (%s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE payload = VALUES(payload)
        """,
        (_OWNER, _KIND_TABLE_SORT, table, json.dumps({"col": body.col, "dir": body.dir})),
    )
    return {"table": table, "col": body.col, "dir": body.dir}


@router.delete("/table-sorts/{table}")
def delete_table_sort(table: str) -> dict[str, Any]:
    """清除某表的默认排序（回到数据库自然序）"""
    _ensure_table()
    _execute(
        "DELETE FROM user_prefs WHERE owner = %s AND kind = %s AND scope = %s",
        (_OWNER, _KIND_TABLE_SORT, table),
    )
    return {"table": table, "deleted": True}


@router.delete("/table-sorts")
def clear_table_sorts() -> dict[str, Any]:
    """清空全部表的默认排序"""
    _ensure_table()
    _execute(
        "DELETE FROM user_prefs WHERE owner = %s AND kind = %s",
        (_OWNER, _KIND_TABLE_SORT),
    )
    return {"deleted": True}
