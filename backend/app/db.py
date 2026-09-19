#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
InvestBuddy 数据库配置模块
从环境变量读取数据库连接信息（密钥不硬编码、不入库、不进 Git）
开发期默认值对应本地 MySQL（adata 库）

🔴 连接复用（2026-09-19 性能优化，改动本文件前必读）
   原实现每次 query_all / execute_write 都 pymysql.connect() + close()。

   实测代价：一次 GET /api/analysis/market-wind 会发起 **36 次** query_all
   → 36 次 TCP+SSL 握手；其中仅「重建 SSL 上下文」一项
   （load_default_certs → enum_certificates 读 Windows 证书库）就耗 0.49s，
   建连总计 0.79s ≈ 该接口耗时的 **37%**。cProfile 证据：
       _connect        36 calls / 0.786s cum
       _create_ssl_ctx 36 calls / 0.487s cum
       enum_certificates 72 calls / 0.318s tottime

   现改为**每线程复用一条长连接**：
   - 隔离：uvicorn 同步路由跑在 AnyIO threadpool，用 threading.local 线程私有；
   - 自愈：取用前 ping(reconnect=True)，被 MySQL wait_timeout 断开时自动重连；
   - 重试：**仅读操作**遇连接层异常时丢弃长连接重试一次
     （写操作不重试——commit 已发出但响应中断时重试会重复写入）；
   - 事务：autocommit=True。单语句自动提交，避免长连接上残留未提交事务，
     与原「每次新建连接 + 显式 commit」语义等价；
   - 回退：设 INFO_DATA_DB_POOL=0 退回逐次建连的旧行为（排查用）。

   ⚠️ 边界：需要**独立会话或显式事务**的场景（采集器多语句 BEGIN/COMMIT、
       sql_explorer 的 SET SESSION 只读沙箱）都自己 pymysql.connect()，
       不经本模块，不受影响。新增此类代码请沿用该写法，勿复用 query_all。
"""
import os
import threading
from dataclasses import dataclass


@dataclass
class DBConfig:
    host: str
    port: int
    user: str
    password: str
    database: str
    charset: str = "utf8mb4"

    @classmethod
    def from_env(cls) -> "DBConfig":
        return cls(
            host=os.getenv("INFO_DATA_DB_HOST", "127.0.0.1"),
            port=int(os.getenv("INFO_DATA_DB_PORT", "3306")),
            user=os.getenv("INFO_DATA_DB_USER", "root"),
            password=os.getenv("INFO_DATA_DB_PASSWORD", "root"),
            database=os.getenv("INFO_DATA_DB_NAME", "adata"),
            charset=os.getenv("INFO_DATA_DB_CHARSET", "utf8mb4"),
        )

    def to_dict(self) -> dict:
        return {
            "host": self.host,
            "port": self.port,
            "user": self.user,
            "password": self.password,
            "database": self.database,
            "charset": self.charset,
        }


def get_db_config() -> DBConfig:
    return DBConfig.from_env()


# ==================== 连接复用 ====================

# 线程私有长连接（AnyIO threadpool 逐线程持有）
_local = threading.local()

# 轻量计数，便于用 pool_stats() 确认复用是否真的生效
_STATS = {"created": 0, "reused": 0, "retried": 0, "closed": 0}


def _reuse_enabled() -> bool:
    """INFO_DATA_DB_POOL=0 时退回「逐次建连」旧行为"""
    return os.getenv("INFO_DATA_DB_POOL", "1").strip() != "0"


def _new_conn() -> "pymysql.Connection":
    """新建连接。autocommit=True：单语句自动提交，复用长连接时不残留未提交事务"""
    import pymysql

    return pymysql.connect(**get_db_config().to_dict(), autocommit=True)


def _discard() -> None:
    """关闭并清空本线程持有的连接"""
    conn = getattr(_local, "conn", None)
    if conn is not None:
        try:
            conn.close()
            _STATS["closed"] += 1
        except Exception:
            pass
    _local.conn = None


def _acquire() -> tuple["pymysql.Connection", bool]:
    """取一条可用连接 → (conn, reused)

    复用模式下取线程私有长连接（ping 探活，断了自动重连）；
    否则每次新建，由调用方负责关闭。
    """
    if not _reuse_enabled():
        return _new_conn(), False

    conn = getattr(_local, "conn", None)
    if conn is not None:
        try:
            conn.ping(reconnect=True)
            _STATS["reused"] += 1
            return conn, True
        except Exception:
            _discard()

    conn = _new_conn()
    _local.conn = conn
    _STATS["created"] += 1
    return conn, True


def _is_conn_error(e: BaseException) -> bool:
    """是否连接层错误（区别于 SQL 语法错、约束冲突等业务错误）"""
    import pymysql

    return isinstance(e, (pymysql.err.OperationalError, pymysql.err.InterfaceError))


def _run(fn, retry: bool = True):
    """执行 fn(conn)。

    retry=True（读操作）：遇连接层错误丢弃长连接重试一次——ping 与真正执行之间
    仍存在被服务端断开的极小窗口。
    retry=False（写操作）：直接抛出，避免「commit 已到达但响应中断」时重复写入。
    """
    conn, reused = _acquire()
    try:
        return fn(conn)
    except Exception as e:
        if retry and reused and _is_conn_error(e):
            _discard()
            _STATS["retried"] += 1
            conn2, _ = _acquire()
            return fn(conn2)
        raise
    finally:
        if not reused:
            try:
                conn.close()
            except Exception:
                pass


def pool_stats() -> dict:
    """连接复用计数（诊断用；enabled=False 表示走旧行为）"""
    return dict(_STATS, enabled=_reuse_enabled())


def release_conn() -> None:
    """释放本线程连接。长驻进程一般不需要；脚本/测试收尾可调用。"""
    _discard()


def _connect() -> "pymysql.Connection":
    """新建一条独立连接（不复用）。

    ⚠️ 需要**独立会话语义**时（SET SESSION、显式多语句事务）请用它，
    不要用 query_all——后者可能落在别的线程的长连接上。
    """
    return _new_conn()


# ==================== 对外接口 ====================


def query_all(sql: str, params: tuple | list | None = None) -> list[dict]:
    """查询多行，返回 list[dict]"""

    def _do(conn):
        with conn.cursor() as cur:
            cur.execute(sql, params)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]

    return _run(_do)


def query_one(sql: str, params: tuple | list | None = None) -> dict | None:
    """查询单行，返回 dict 或 None"""
    rows = query_all(sql + " LIMIT 1", params)
    return rows[0] if rows else None


def execute_write(sql: str, params: tuple | list | None = None) -> int:
    """执行单条写语句（INSERT/UPDATE/DELETE），返回受影响行数。仅限元数据类小操作"""

    def _do(conn):
        with conn.cursor() as cur:
            affected = cur.execute(sql, params)
        conn.commit()
        return affected

    return _run(_do, retry=False)
