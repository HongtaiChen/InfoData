#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
用户偏好 API（服务端持久化）

支持两种 kind：

1. `table_sort`：数据中心每张表记住用户自定义的默认排序
   （scope = 表名，payload = {"col": "...", "dir": "asc"|"desc"}）。

2. `sidebar_order`（2026-09-12 新增）：数据中心左侧表清单的手工排序
   （scope 固定 'table_list'，payload = {"cats": {...}, "tables": {...}}）。
   - `cats`  ：组内二级分类顺序，key = 一级组 key（biz/dq/sys/bak），value = 分类 key 数组
   - `tables`：容器内表行顺序，key = `{组key}::{分类key}`，value = 表名数组
   - **增量覆盖语义**：只记录用户动过的容器；读取端把未记录的表追加到末尾，
     因此数据库新增表永远不会"消失"。PUT 按 sub-key 合并，不会清掉其他容器。

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
_KIND_SIDEBAR_ORDER = "sidebar_order"
_SCOPE_SIDEBAR = "table_list"

# 排序 payload 的边界（防超长 payload 撑爆 JSON 列：JSON 列上限由 max_allowed_packet 决定）
_MAX_CONTAINERS = 200  # 最多多少个容器/分组
_MAX_ITEMS = 500  # 单个容器内最多多少项
_MAX_KEY_LEN = 160  # 容器 key 最大长度（'biz::行情' 这类组合 key 留足余量）
_MAX_NAME_LEN = 128  # 单项名称最大长度（MySQL 标识符上限 64，留余量）

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


# ---------- 左侧表清单手工排序偏好 ----------

class SidebarOrderBody(BaseModel):
    """按 sub-key 合并写入；两个字段都可选，只提交动过的容器"""
    cats: dict[str, list[str]] | None = None
    tables: dict[str, list[str]] | None = None


def _sanitize_map(raw: dict[str, list[str]]) -> dict[str, list[str]]:
    """清洗客户端提交的排序映射：只保留 string -> list[string]，超长/非字符串直接丢弃"""
    out: dict[str, list[str]] = {}
    for key, val in list(raw.items())[:_MAX_CONTAINERS]:
        if not isinstance(key, str) or not key or len(key) > _MAX_KEY_LEN:
            continue
        if not isinstance(val, list):
            continue
        names: list[str] = []
        for n in val[:_MAX_ITEMS]:
            if isinstance(n, str) and n and len(n) <= _MAX_NAME_LEN:
                names.append(n)
        if names:
            out[key] = names
    return out


def _load_sidebar_payload() -> dict[str, Any]:
    for row in _load_all(_KIND_SIDEBAR_ORDER):
        if row["scope"] == _SCOPE_SIDEBAR:
            return row["payload"]
    return {}


@router.get("/sidebar-order")
def get_sidebar_order() -> dict[str, Any]:
    """读取左侧清单手工排序：{ cats: {组key: [分类key]}, tables: {"组::分类": [表名]} }"""
    _ensure_table()
    payload = _load_sidebar_payload()
    cats = payload.get("cats")
    tables = payload.get("tables")
    return {
        "cats": cats if isinstance(cats, dict) else {},
        "tables": tables if isinstance(tables, dict) else {},
    }


@router.put("/sidebar-order")
def upsert_sidebar_order(body: SidebarOrderBody) -> dict[str, Any]:
    """合并写入动过的容器（不清除未提交的容器）；幂等"""
    _ensure_table()
    cur = _load_sidebar_payload()
    cats = dict(cur.get("cats") or {}) if isinstance(cur.get("cats"), dict) else {}
    tables = dict(cur.get("tables") or {}) if isinstance(cur.get("tables"), dict) else {}
    if body.cats:
        cats.update(_sanitize_map(body.cats))
    if body.tables:
        tables.update(_sanitize_map(body.tables))
    merged = {"cats": cats, "tables": tables}
    _execute(
        """
        INSERT INTO user_prefs (owner, kind, scope, payload)
        VALUES (%s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE payload = VALUES(payload)
        """,
        (_OWNER, _KIND_SIDEBAR_ORDER, _SCOPE_SIDEBAR, json.dumps(merged, ensure_ascii=False)),
    )
    return merged


@router.delete("/sidebar-order")
def clear_sidebar_order() -> dict[str, Any]:
    """清除左侧清单手工排序（恢复按名称/声明顺序）"""
    _ensure_table()
    _execute(
        "DELETE FROM user_prefs WHERE owner = %s AND kind = %s AND scope = %s",
        (_OWNER, _KIND_SIDEBAR_ORDER, _SCOPE_SIDEBAR),
    )
    return {"deleted": True}
