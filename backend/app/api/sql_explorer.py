#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
数据探查 API：让用户在前端直接写 SQL 并预览结果（仅 SELECT 家族）

安全策略（自底向上四层）：
1. 会话级只读：SET SESSION TRANSACTION READ ONLY + autocommit=0
2. 关键字黑名单：拒 INSERT/UPDATE/DELETE/DROP/TRUNCATE/ALTER/CREATE/REPLACE/RENAME/GRANT/REVOKE/LOCK/UNLOCK/CALL/KILL/LOAD/OUTFILE/INFILE/FOR UPDATE
3. 多语句防御：剥离注释后只允许一条语句
4. LIMIT：SELECT 无 LIMIT 时自动追加（前端可选档位，默认 500、上限 20000）；
   前端可勾选「不限」（不追加 LIMIT），但服务端仍以 fetchmany 流式取数并施加
   _HARD_MAX（5 万行）硬上限，防止千万行大表全量拉回冲爆内存/浏览器

超时：SET SESSION MAX_EXECUTION_TIME = 15000（MySQL 8 强制执行毫秒）
"""
import re
import time
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..db import get_db_config

router = APIRouter()

_LIMIT_DEFAULT = 500
_LIMIT_MAX = 20000
_HARD_MAX = 50000
_FETCH_BATCH = 5000
_TIMEOUT_MS = 15000

# 黑名单：边界词匹配（大小写不敏感）
_FORBIDDEN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|TRUNCATE|ALTER|CREATE|REPLACE|RENAME|"
    r"GRANT|REVOKE|LOCK|UNLOCK|CALL|KILL|LOAD|OUTFILE|INFILE|FOR\s+UPDATE)\b",
    re.IGNORECASE,
)

# 允许开头的关键字（白名单第一种；再叠加黑名单兜底）
_ALLOWED_START = ("SELECT", "WITH", "EXPLAIN", "SHOW", "DESCRIBE", "DESC")
# 解析语句类型时识别首段
_FIRST_WORD = re.compile(r"^\s*([A-Za-z_]+)")


class SqlExploreReq(BaseModel):
    sql: str = Field(..., min_length=1, max_length=20000)
    limit: int | None = Field(None, ge=1, le=_LIMIT_MAX)
    no_limit: bool = False  # 「不限」：不自动追加 LIMIT；仍有 _HARD_MAX 硬上限兜底


def _strip_comments(sql: str) -> str:
    """移除 /* ... */ 与 -- 单行注释（保留字符串字面量）"""
    # 块注释
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    # 单行注释（仅当不在字符串内时启发式替换：逐行处理）
    out_lines = []
    for line in sql.splitlines():
        # 找第一个不被引号包围的 --
        s = line
        in_s = False
        ch = " "
        for i, c in enumerate(s):
            if c == "'":
                in_s = not in_s
            elif not in_s and i + 1 < len(s) and s[i] == "-" and s[i + 1] == "-":
                s = s[:i]
                break
        out_lines.append(s)
    return "\n".join(out_lines).strip()


def _validate(sql: str) -> str:
    """通过检查后返回『干净 SQL』（不含尾部分号）"""
    cleaned = _strip_comments(sql).rstrip().rstrip(";").strip()
    if not cleaned:
        raise HTTPException(status_code=400, detail="SQL_SYNTAX_ERROR: 语句为空")

    # 多语句：剥离后若再出现分号，说明有多条语句
    if ";" in cleaned:
        raise HTTPException(
            status_code=400,
            detail="SQL_SAFETY_BLOCKED: 仅允许单条语句；请去掉多余的分号",
        )

    # 黑名单
    bad = _FORBIDDEN.search(cleaned)
    if bad:
        raise HTTPException(
            status_code=400,
            detail=f"SQL_SAFETY_BLOCKED: 检测到禁用关键字 [{bad.group(0).upper()}]；仅支持 SELECT/WITH/EXPLAIN/SHOW/DESCRIBE",
        )

    # 白名单开头
    m = _FIRST_WORD.match(cleaned)
    if not m or m.group(1).upper() not in _ALLOWED_START:
        raise HTTPException(
            status_code=400,
            detail="SQL_SAFETY_BLOCKED: 语句必须以 SELECT/WITH/EXPLAIN/SHOW/DESCRIBE/DESC 开头",
        )
    return cleaned


def _ensure_limit(sql: str, user_limit: int | None, no_limit: bool = False) -> tuple[str, int, bool]:
    """
    返回 (final_sql, effective_limit, user_specified)
    - SELECT 类（无 LIMIT 自动追加，档位上限 _LIMIT_MAX）
    - no_limit=True 时不追加 LIMIT，effective_limit 取 _HARD_MAX（硬上限提示用）
    - 用户自写 LIMIT N：尊重原句，effective_limit = min(N, _HARD_MAX)（取数仍受硬上限约束）
    - EXPLAIN/SHOW/DESCRIBE 不追加（这些语法不接受 LIMIT）
    """
    upper = sql.upper()
    # 不允许 LIMIT 的语法
    needs_no_limit = upper.startswith("SHOW") or upper.startswith("DESCRIBE") or upper.startswith("DESC")
    if needs_no_limit:
        return sql, 0, False

    m = re.search(r"\bLIMIT\s+(\d+)", upper)
    if m:
        return sql, min(int(m.group(1)), _HARD_MAX), True

    if no_limit:
        return sql, _HARD_MAX, False

    target = user_limit if user_limit is not None else _LIMIT_DEFAULT
    target = min(target, _LIMIT_MAX)
    return f"{sql.rstrip()}\nLIMIT {target}", target, False


def _normalize_rows(cursor_desc, rows: list[tuple]) -> list[dict]:
    if not rows:
        return []
    cols = [d[0] for d in cursor_desc]
    return [dict(zip(cols, r)) for r in rows]


@router.post("/explore")
def explore(req: SqlExploreReq) -> dict[str, Any]:
    """执行只读 SQL 并返回结果；不支持事务外修改"""
    cleaned = _validate(req.sql)
    final_sql, eff_limit, user_limited = _ensure_limit(cleaned, req.limit, req.no_limit)
    # 取数硬上限：防止「不限」或用户自写大 LIMIT 时全量拉回千万行大表
    fetch_cap = eff_limit if eff_limit > 0 else _HARD_MAX

    import pymysql
    conn = pymysql.connect(**get_db_config().to_dict())
    try:
        cur = conn.cursor()
        # 1) 会话级 READ ONLY：让连接之后所有写都被拒绝（即使 SQL 绕过黑名单）
        # 2) 整连接单点超时（仅对 SELECT 生效，MySQL 8 MAX_EXECUTION_TIME）
        # 注意：autocommit 保持为默认 1，避免显式 BEGIN 与 SET TRANSACTION 冲突
        cur.execute("SET SESSION TRANSACTION READ ONLY")
        cur.execute(f"SET SESSION MAX_EXECUTION_TIME = {_TIMEOUT_MS}")

        start = time.perf_counter()
        try:
            cur.execute(final_sql)
            # 流式取数：分批 fetch，到硬上限即停并标记截断，服务端内存不随表大小失控
            rows: list[tuple] = []
            truncated = False
            while True:
                batch = cur.fetchmany(_FETCH_BATCH)
                if not batch:
                    break
                rows.extend(batch)
                if len(rows) >= fetch_cap:
                    if cur.fetchone() is not None:
                        truncated = True
                    break
            elapsed_ms = int((time.perf_counter() - start) * 1000)

            # 自动追加 LIMIT 的场景：行数打满档位也视为截断（提示调大 LIMIT）
            if not truncated and not user_limited and 0 < eff_limit <= len(rows):
                truncated = True

        except Exception:
            # READ ONLY 连接下若执意写，会抛 1795 等异常，统一转安全错
            raise

        cols_meta = []
        if cur.description:
            for d in cur.description:
                cols_meta.append({
                    "name": d[0],
                    "type": _mysql_type_label(d[1]),
                })

        payload_rows = _normalize_rows(cur.description, rows)

        return {
            "columns": cols_meta,
            "rows": payload_rows,
            "row_count": len(payload_rows),
            "elapsed_ms": elapsed_ms,
            "truncated": truncated,
            "limit": eff_limit,
        }
    except HTTPException:
        raise
    except Exception as e:
        msg = str(e)
        up = msg.upper()
        if "MAX_EXECUTION_TIME" in up or "3024" in msg or "QUERY EXECUTION WAS INTERRUPTED" in up:
            raise HTTPException(status_code=408, detail=f"SQL_TIMEOUT: 查询超时（>{_TIMEOUT_MS}ms）")
        if "READ ONLY" in up:
            raise HTTPException(status_code=400, detail="SQL_SAFETY_BLOCKED: 检测到写入语句被拒绝")
        raise HTTPException(status_code=400, detail=f"SQL_SYNTAX_ERROR: {msg[:200]}")
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _mysql_type_label(t: Any) -> str:
    """cursor.description[1] 是 pymysql.constants.FIELD_TYPE.* 整数；映射成 SQL 公认名

    驱动层名（VAR_STRING/NEWDECIMAL/LONGLONG 等）不利于前端展示，
    标准化为 VARCHAR/DECIMAL/BIGINT 等。
    """
    _TYPE_MAP = {
        1: 'TINYINT', 2: 'SMALLINT', 3: 'INT', 4: 'FLOAT', 5: 'DOUBLE',
        6: 'NULL', 7: 'TIMESTAMP', 8: 'BIGINT', 9: 'INT24',
        10: 'DATE', 11: 'TIME', 12: 'DATETIME', 13: 'YEAR',
        15: 'VARCHAR', 16: 'BIT',
        245: 'JSON', 246: 'DECIMAL', 247: 'ENUM', 248: 'SET',
        249: 'TINYBLOB', 250: 'MEDIUMBLOB', 251: 'LONGBLOB',
        252: 'BLOB', 253: 'VARCHAR', 254: 'CHAR', 255: 'GEOMETRY',
    }
    try:
        return _TYPE_MAP.get(int(t), str(t))
    except (TypeError, ValueError):
        return str(t)
