#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
InvestBuddy 数据质量体检（DQ）

- 读取 dq_rules（enabled=1）逐条执行检查器，结果写入 dq_report（每轮 run_at 一次快照）
- 纯库内 SQL、无外部数据源；挂在 APScheduler 每日盘后运行（task_config: data_quality_check）
- 大表一律限定「最新时间切片」查询，避免千万行全表扫描（stock_market_daily 秒级完成）
- 安全三原则（与 db_browser 一致）：
    1) 表名必须存在于 information_schema（白名单），否则该规则记 error
    2) 列名必须存在于该表 information_schema 列集合
    3) violation 的 where 表达式做标识符白名单校验（仅允许列名/函数/数字），杜绝注入
- 幂等可重跑：重跑产生新 run_at 形成新轮次，历史按 run_date 保留 30 天自动清理

dq_rules.params（JSON）契约，按 check_type 分：
  freshness_daily   {date_col, calendar, warn_days}        对齐交易日历，落后>warn_days fail
  freshness_interval {time_col, pass_hours, fail_hours}    距当前时长分级
  date_floor        {date_col, days_back}                  MAX(date_col) >= 今天-days_back
  row_count_slice   {date_col, min_rows}                   最新切片行数下限
  row_count_total   {min_rows}                             总行数下限（防清空/大面积缺失）
  null_rate_slice   {date_col, col, max_pct}               最新切片空值率上限(%)
  violation_count   {date_col, where, max_count}           最新切片脏数据行数上限
  where_count       {where, max_count}                     全表任意条件行数上限（静态/档案表）
  regex_count       {col, pattern}                         全表列值格式校验（仅小表）
  unique_index      {cols: [..], expect}                   结构体检：是否存在自然键唯一索引
"""
import logging
import json
import re
from datetime import datetime, timedelta

import pymysql

from ..db import get_db_config

logger = logging.getLogger(__name__)

# 保留轮次窗口（天）
REPORT_KEEP_DAYS = 30
# 空表统一判定 fail 文案
MSG_EMPTY = "表为空，请检查采集是否从未成功"

# 允许出现在 where 表达式中的函数/常量标识符（其余标识符必须属于表列白名单）
_WHERE_FUNCS = {"ABS", "ROUND", "COALESCE", "IFNULL", "NULL", "NOT", "AND", "OR", "IN", "IS", "LIKE"}
_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


class DataQualityCheckCollector:
    """数据质量体检采集器（读规则 → 执行 → 写 dq_report）"""

    def __init__(self):
        self.run_at = datetime.now()
        self.run_date = self.run_at.date()
        self._column_cache: dict[str, set[str]] = {}
        self._table_cache: dict[str, bool] = {}

    # ---------- 公共：白名单/连接 ----------

    def _connect(self):
        return pymysql.connect(**get_db_config().to_dict(), cursorclass=pymysql.cursors.DictCursor)

    def _norm_rows(self, rows: list[dict]) -> list[dict]:
        """information_schema 返回大写列名，统一小写化（MySQL 8 行为）"""
        return [{k.lower(): v for k, v in r.items()} for r in rows]

    def _table_exists(self, conn, table: str) -> bool:
        if table not in self._table_cache:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) AS n FROM information_schema.tables "
                    "WHERE table_schema = DATABASE() AND table_name = %s",
                    (table,),
                )
                self._table_cache[table] = self._norm_rows(cur.fetchall())[0]["n"] > 0
        return self._table_cache[table]

    def _columns(self, conn, table: str) -> set[str]:
        if table not in self._column_cache:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = DATABASE() AND table_name = %s",
                    (table,),
                )
                self._column_cache[table] = {r["column_name"] for r in self._norm_rows(cur.fetchall())}
        return self._column_cache[table]

    def _validate(self, conn, rule: dict) -> str | None:
        """规则静态校验，返回错误信息或 None"""
        table = rule["table_name"]
        if not self._table_exists(conn, table):
            return f"表 {table} 不存在"
        cols = self._columns(conn, table)
        p = rule.get("params") or {}
        for key in ("date_col", "time_col", "col"):
            c = p.get(key)
            if c and c not in cols:
                return f"列 {c} 不存在于 {table}"
        w = p.get("where")
        if w:
            bad = [t for t in _IDENT_RE.findall(w) if t not in cols and t.upper() not in _WHERE_FUNCS]
            if bad:
                return f"where 表达式含非白名单标识符: {bad[:3]}"
        for c in p.get("cols") or []:
            if c not in cols:
                return f"列 {c} 不存在于 {table}"
        return None

    # ---------- 检查器 ----------

    def _freshness_daily(self, conn, rule: dict) -> dict:
        """对齐交易日历：表 MAX(date_col) 与最近交易日比较，返回 report 字段"""
        p = rule.get("params") or {}
        date_col = p.get("date_col", "trade_date")
        calendar = p.get("calendar", "trade_calendar")
        warn_days = int(p.get("warn_days", 2))
        table = rule["table_name"]
        with conn.cursor() as cur:
            cur.execute(f"SELECT MAX(`{date_col}`) AS d FROM `{table}`")
            row = cur.fetchone()
            if not row or row["d"] is None:
                return {"status": "fail", "metric_value": "无数据", "message": MSG_EMPTY}
            max_date = str(row["d"])[:10]
            cur.execute(
                f"SELECT MAX(`trade_date`) AS d FROM `{calendar}` "
                "WHERE is_trading_day = 1 AND trade_date <= CURDATE()"
            )
            exp_row = cur.fetchone()
            if not exp_row or exp_row["d"] is None:
                return {"status": "error", "metric_value": max_date, "message": f"交易日历 {calendar} 不可用"}
            expected = str(exp_row["d"])[:10]
            cur.execute(
                f"SELECT COUNT(*) AS n FROM `{calendar}` "
                "WHERE is_trading_day = 1 AND trade_date > %s AND trade_date <= %s",
                (max_date, expected),
            )
            behind = cur.fetchone()["n"]
        if behind == 0:
            status = "pass"
        elif behind <= warn_days:
            status = "warning"
        else:
            status = "fail"
        if status == "pass":
            msg = f"最新 {max_date}，与交易日历一致"
        else:
            msg = f"最新 {max_date}，落后 {behind} 个交易日（应为 {expected}）"
        return {"status": status, "metric_value": max_date, "message": msg}

    def _freshness_interval(self, conn, rule: dict) -> dict:
        """距当前时长分级（高频流，如 news）"""
        p = rule.get("params") or {}
        time_col = p.get("time_col", "published_at")
        pass_h = float(p.get("pass_hours", 4))
        fail_h = float(p.get("fail_hours", 24))
        table = rule["table_name"]
        with conn.cursor() as cur:
            cur.execute(f"SELECT MAX(`{time_col}`) AS t FROM `{table}`")
            row = cur.fetchone()
            if not row or row["t"] is None:
                return {"status": "fail", "metric_value": "无数据", "message": MSG_EMPTY}
            last = row["t"]
            hours = max(0.0, (datetime.now() - last).total_seconds() / 3600)
        if hours <= pass_h:
            status = "pass"
        elif hours <= fail_h:
            status = "warning"
        else:
            status = "fail"
        val = str(last)[:19]
        msg = f"最新 {val}，距今 {hours:.1f}h"
        return {"status": status, "metric_value": val, "message": msg}

    def _date_floor(self, conn, rule: dict) -> dict:
        """日期下限：MAX(date_col) 不得早于今天-days_back（防停更）"""
        p = rule.get("params") or {}
        date_col = p.get("date_col", "event_date")
        days_back = int(p.get("days_back", 7))
        table = rule["table_name"]
        floor = (datetime.now() - timedelta(days=days_back)).date()
        with conn.cursor() as cur:
            cur.execute(f"SELECT MAX(`{date_col}`) AS d FROM `{table}`")
            row = cur.fetchone()
            if not row or row["d"] is None:
                return {"status": "fail", "metric_value": "无数据", "message": MSG_EMPTY}
            max_date = str(row["d"])[:10]
        status = "pass" if max_date >= str(floor) else "fail"
        msg = f"最新 {max_date}，应不早于 {floor}" if status == "pass" else f"停更风险：最新 {max_date}，应 ≥ {floor}"
        return {"status": status, "metric_value": max_date, "message": msg}

    def _latest_slice(self, conn, table: str, date_col: str):
        """最新时间切片计数（均走 MAX 索引定位，秒级）"""
        with conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) AS n FROM `{table}` WHERE `{date_col}` = (SELECT MAX(`{date_col}`) FROM `{table}`)")
            return cur.fetchone()["n"]

    def _row_count_slice(self, conn, rule: dict) -> dict:
        p = rule.get("params") or {}
        date_col = p.get("date_col", "trade_date")
        min_rows = int(p.get("min_rows", 1))
        table = rule["table_name"]
        with conn.cursor() as cur:
            cur.execute(f"SELECT MAX(`{date_col}`) AS d FROM `{table}`")
            row = cur.fetchone()
            if not row or row["d"] is None:
                return {"status": "fail", "metric_value": "0", "message": MSG_EMPTY}
            latest = str(row["d"])[:10]
        n = self._latest_slice(conn, table, date_col)
        status = "pass" if n >= min_rows else "fail"
        msg = f"最新日 {latest} 共 {n} 行（应 ≥ {min_rows}）"
        return {"status": status, "metric_value": str(n), "message": msg}

    def _row_count_total(self, conn, rule: dict) -> dict:
        p = rule.get("params") or {}
        min_rows = int(p.get("min_rows", 1))
        table = rule["table_name"]
        with conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) AS n FROM `{table}`")
            n = cur.fetchone()["n"]
        status = "pass" if n >= min_rows else "fail"
        msg = f"共 {n} 行（应 ≥ {min_rows}）"
        return {"status": status, "metric_value": str(n), "message": msg}

    def _null_rate_slice(self, conn, rule: dict) -> dict:
        p = rule.get("params") or {}
        date_col = p.get("date_col", "trade_date")
        col = p["col"]
        max_pct = float(p.get("max_pct", 1.0))
        table = rule["table_name"]
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT MAX(`{date_col}`) AS d, "
                f"COUNT(*) AS total, SUM(`{col}` IS NULL) AS nulls "
                f"FROM `{table}` WHERE `{date_col}` = (SELECT MAX(`{date_col}`) FROM `{table}`)"
            )
            row = cur.fetchone()
            if not row or row["total"] == 0:
                return {"status": "fail", "metric_value": "-", "message": MSG_EMPTY}
        pct = round(row["nulls"] / row["total"] * 100, 3)
        status = "pass" if pct <= max_pct else "fail"
        msg = f"最新日 {str(row['d'])[:10]}：{col} 空值 {row['nulls']}/{row['total']}（{pct}%，应 ≤ {max_pct}%）"
        return {"status": status, "metric_value": f"{pct}%", "message": msg}

    def _violation_count(self, conn, rule: dict) -> dict:
        p = rule.get("params") or {}
        date_col = p.get("date_col", "trade_date")
        where = p["where"]
        max_count = int(p.get("max_count", 0))
        table = rule["table_name"]
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT COUNT(*) AS n FROM `{table}` "
                f"WHERE `{date_col}` = (SELECT MAX(`{date_col}`) FROM `{table}`) AND ({where})"
            )
            n = cur.fetchone()["n"]
        status = "pass" if n <= max_count else "fail"
        msg = f"最新日切片脏数据 {n} 行（应 ≤ {max_count}）：{where}"
        return {"status": status, "metric_value": str(n), "message": msg}

    def _where_count(self, conn, rule: dict) -> dict:
        """任意条件行数上限（适用于无日期列的静态/档案表；where 来自受控 dq_rules）"""
        p = rule.get("params") or {}
        where = p["where"]
        max_count = int(p.get("max_count", 0))
        table = rule["table_name"]
        with conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) AS n FROM `{table}` WHERE {where}")
            n = cur.fetchone()["n"]
        status = "pass" if n <= max_count else "fail"
        msg = f"违反条件 {n} 行（应 ≤ {max_count}）：{where}"
        return {"status": status, "metric_value": str(n), "message": msg}

    def _regex_count(self, conn, rule: dict) -> dict:
        p = rule.get("params") or {}
        col = p["col"]
        pattern = p["pattern"]
        table = rule["table_name"]
        with conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) AS n FROM `{table}` WHERE `{col}` NOT REGEXP %s", (pattern,))
            n = cur.fetchone()["n"]
        status = "pass" if n == 0 else "fail"
        msg = f"不符合格式 {pattern} 的 {col} 共 {n} 个（应 = 0）"
        return {"status": status, "metric_value": str(n), "message": msg}

    def _unique_index(self, conn, rule: dict) -> dict:
        """结构体检：期望表存在（或不存在）覆盖指定列前导的唯一索引"""
        p = rule.get("params") or {}
        cols = p.get("cols") or []
        expect = p.get("expect", "exists")
        table = rule["table_name"]
        sql = (
            "SELECT DISTINCT index_name FROM information_schema.statistics "
            "WHERE table_schema = DATABASE() AND table_name = %s AND non_unique = 0 "
            "AND seq_in_index = 1 AND column_name = %s"
        )
        found = False
        with conn.cursor() as cur:
            cur.execute(sql, (table, cols[0]))
            for r in self._norm_rows(cur.fetchall()):
                cur.execute(
                    "SELECT GROUP_CONCAT(column_name ORDER BY seq_in_index) AS cs "
                    "FROM information_schema.statistics WHERE table_schema = DATABASE() "
                    "AND table_name = %s AND index_name = %s AND non_unique = 0",
                    (table, r["index_name"]),
                )
                idx_cols = (self._norm_rows(cur.fetchall())[0]["cs"] or "").split(",")
                if idx_cols[: len(cols)] == cols:
                    found = True
                    break
        ok = found if expect == "exists" else not found
        status = "pass" if ok else "fail"
        key = "+".join(cols)
        if expect == "exists":
            msg = f"已具备唯一索引 ({key})" if found else f"缺少唯一索引 ({key}) —— 建议 DDL 补充以保障幂等"
        else:
            msg = f"不存在唯一索引 ({key})" if not found else f"存在唯一索引 ({key})"
        return {"status": status, "metric_value": key, "message": msg}

    CHECKERS = {
        "freshness_daily": _freshness_daily,
        "freshness_interval": _freshness_interval,
        "date_floor": _date_floor,
        "row_count_slice": _row_count_slice,
        "row_count_total": _row_count_total,
        "null_rate_slice": _null_rate_slice,
        "violation_count": _violation_count,
        "where_count": _where_count,
        "regex_count": _regex_count,
        "unique_index": _unique_index,
    }

    # ---------- 主流程 ----------

    def run(self) -> dict:
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, rule_name, table_name, check_type, params, severity, description "
                    "FROM dq_rules WHERE enabled = 1 ORDER BY table_name, id"
                )
                rules = self._norm_rows(cur.fetchall())
            for rule in rules:
                raw = rule.get("params")
                rule["params"] = json.loads(raw) if isinstance(raw, str) else (raw or {})
            if not rules:
                return {"records_written": 0, "error_count": 1, "errors": ["dq_rules 无启用规则"], "note": "未配置"}

            insert_sql = (
                "INSERT INTO dq_report "
                "(run_at, run_date, rule_id, rule_name, table_name, check_type, severity, status, metric_value, message) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
            )
            written = 0
            errors = []
            for rule in rules:
                err = self._validate(conn, rule)
                if err:
                    status, metric, msg = "error", "-", err
                    errors.append(f"{rule['rule_name']}: {err}")
                else:
                    checker = self.CHECKERS.get(rule["check_type"])
                    if checker is None:
                        status, metric, msg = "error", "-", f"未知检查器 {rule['check_type']}"
                        errors.append(f"{rule['rule_name']}: {msg}")
                    else:
                        try:
                            res = checker(self, conn, rule)
                            status, metric, msg = res["status"], res.get("metric_value", "-"), res["message"]
                        except Exception as e:  # noqa: BLE001 - 单条规则失败不影响整轮
                            status, metric, msg = "error", "-", str(e)
                            errors.append(f"{rule['rule_name']}: {e}")
                with conn.cursor() as cur:
                    cur.execute(
                        insert_sql,
                        (
                            self.run_at, self.run_date, rule["id"], rule["rule_name"],
                            rule["table_name"], rule["check_type"], rule["severity"],
                            status, metric[:200], msg[:500],
                        ),
                    )
                written += 1
            # 保留策略：只留最近 30 天轮次
            with conn.cursor() as cur:
                cur.execute("DELETE FROM dq_report WHERE run_date < %s", (self.run_date - timedelta(days=REPORT_KEEP_DAYS),))
            conn.commit()
            logger.info(f"✅ DQ 体检完成：本轮 {written} 条规则（run_at={self.run_at:%Y-%m-%d %H:%M:%S}）")
            note = f"体检完成 run_at={self.run_at:%Y-%m-%d %H:%M:%S}"
            return {"records_written": written, "error_count": len(errors), "errors": errors[:5], "note": note}
        finally:
            conn.close()
