#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
InvestBuddy 数据库配置模块
从环境变量读取数据库连接信息（密钥不硬编码、不入库、不进 Git）
开发期默认值对应本地 MySQL（adata 库）
"""
import os
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


def _connect() -> "pymysql.Connection":
    """创建数据库连接（自动转 datetime 为 str，便于 JSON 序列化）"""
    import pymysql

    return pymysql.connect(**get_db_config().to_dict())


def query_all(sql: str, params: tuple | list | None = None) -> list[dict]:
    """查询多行，返回 list[dict]"""
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        conn.close()


def query_one(sql: str, params: tuple | list | None = None) -> dict | None:
    """查询单行，返回 dict 或 None"""
    rows = query_all(sql + " LIMIT 1", params)
    return rows[0] if rows else None
