#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
InvestBuddy 数据质量体检（DQ）

- 读取 dq_rules（enabled=1）逐条执行检查器，结果写入 dq_report（每轮 run_at 一次快照）
- 纯库内 SQL、无外部数据源；挂在 APScheduler 每日盘后运行（task_config: data_quality_check）
- **规则分组（2026-09-10 v2）**：dq_rules.rule_group = daily/weekly；
  daily 走每日盘后任务，weekly 走独立低频任务（全史窗口扫描耗时长，不与每日轮混跑）
- 大表一律限定「最新时间切片」查询，避免千万行全表扫描（stock_market_daily 秒级完成）
  —— 例外：weekly 组的 gap_scan/source_handoff/coverage 为全史/近端窗口扫描，单跑（实测 3~4 分钟）
- 安全三原则（与 db_browser 一致）：
    1) 表名（含参照表）必须存在于 information_schema（白名单），否则该规则记 error
    2) 列名必须存在于该表 information_schema 列集合
    3) violation 的 where 表达式做标识符白名单校验（仅允许列名/函数/数字），杜绝注入
- 幂等可重跑：重跑产生新 run_at 形成新轮次，历史按 run_date 保留 30 天自动清理
  （dq_gap_detail 明细同策略）

dq_rules.params（JSON）契约，按 check_type 分：
  freshness_daily   {date_col, calendar, warn_days, grace_days}
                                    对齐交易日历；落后 ≤grace_days 判 pass（源固有 T+1 滞后），
                                    落后 ≤warn_days 判 warning，否则 fail
  freshness_interval {time_col, pass_hours, fail_hours}    距当前时长分级
  date_floor        {date_col, days_back}                  MAX(date_col) >= 今天-days_back
  date_floor_where  {date_col, where, days_back}           MAX(date_col) >= 今天-days_back（限 where 子集，专治"某条腿停更"）
  row_count_slice   {date_col, min_rows}                   最新切片行数下限
  row_count_total   {min_rows}                             总行数下限（防清空/大面积缺失）
  null_rate_slice   {date_col, col, max_pct}               最新切片空值率上限(%)
  violation_count   {date_col, where, max_count}           最新切片脏数据行数上限
  where_count       {where, max_count}                     全表任意条件行数上限（静态/档案表）
  regex_count       {col, pattern}                         全表列值格式校验（仅小表）
  unique_index      {cols: [..], expect}                   结构体检：是否存在自然键唯一索引
  column_watermark  {date_col, value_col, max_gap_rows}     列水位线：value_col 最后非空日之后
                                                            还有多少行（>max_gap_rows 判列已断供）。
                                                            与 freshness_daily 互补——后者答「表还在更新吗」，
                                                            本检查器答「这一列还在更新吗」（2026-09-19 立）
  gap_scan          {date_col, gap_days, high_days}        全史疑似缺口扫描（LAG 窗口）→ 写 dq_gap_detail
  source_handoff    {date_col, source_col, since, max_pct} 跨源衔接一致性（相邻行 data_source 变化处）
  factor_link       {date_col, max_pct}                    复权因子自洽：pre_close ≈
                                                           LAG(close)×LAG(adj_factor)/adj_factor（全史）
  pct_limit         {date_col, since?, tol_pp?, limit_main?, limit_star?, limit_bj?}
                                                           涨跌幅「板块上限」违规数：按代码段取
                                                           主板±10/创业科创±20/北交所±30，超限即违规。
                                                           与口径无关的物理约束，守护换源/价格写错/
                                                           复权断阶三类故障（2026-09-21 立）
  per_key_coverage  {date_col, key_col, window_days, min_rows, min_listed_days,
                     ref_table, ref_key, ref_status_col, ref_status_val, ref_date_col,
                     exclude_prefixes}                     近端每票行数下限（在市老票）
  stale_running     {hours}                              僵尸 running 记录数（task_runs）
  long_finished_run {minutes, lookback_days}             已完成但耗时超长的运行数（task_runs）。
                                                         与 stale_running 互补：后者管「没跑完」，
                                                         本检查器管「跑完了但很久」。
                                                         ⚠️ 超长多因机器待机冻结进程，
                                                         是物理离线信号，不是任务变慢
"""
import logging
import json
import re
from datetime import datetime, timedelta

import pymysql

from ..db import get_db_config
from ._common import with_steps

logger = logging.getLogger(__name__)

# 运行步骤链模板（供前端「数据流·整链拓扑」展示运行逻辑）
RUN_STEPS = [
    {"no": 1, "name": "读启用规则", "params": "dq_rules enabled=1（按 rule_group 分组过滤，按表排序）"},
    {"no": 2, "name": "规则预校验", "params": "表/列（含参照表）information_schema 白名单 + where 标识符白名单（防注入）"},
    {"no": 3, "name": "执行检查器", "params": "15 类检查器（新鲜度/切片行数/空值率/列水位线/违规数/全史缺口/跨源衔接/覆盖率/僵尸运行…）；单条失败记 error 不中断"},
    {"no": 4, "name": "写入结果", "params": "dq_report 每规则一行 + gap_scan 明细写 dq_gap_detail（供 L3 修复闭环）"},
    {"no": 5, "name": "轮次保留清理", "params": "删除 run_date 早于 30 天的 dq_report / dq_gap_detail 历史"},
]

# 保留轮次窗口（天）
REPORT_KEEP_DAYS = 30
# 空表统一判定 fail 文案
MSG_EMPTY = "表为空，请检查采集是否从未成功"

# 允许出现在 where 表达式中的函数/常量标识符（其余标识符必须属于表列白名单）
# LEAST/GREATEST 用于 OHLC 自洽判定（high≥max(o,c) / low≤min(o,c)），2026-09-10 补充
_WHERE_FUNCS = {
    "ABS", "ROUND", "COALESCE", "IFNULL", "NULL", "NOT", "AND", "OR", "IN", "IS", "LIKE",
    "LEAST", "GREATEST",
}
_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


class DataQualityCheckCollector:
    """数据质量体检采集器（读规则 → 执行 → 写 dq_report）"""

    def __init__(self, groups: list[str] | None = None):
        self.run_at = datetime.now()
        self.run_date = self.run_at.date()
        # 规则组过滤：None = 全部（兼容旧行为）；["daily"]/["weekly"] 分组执行
        self.groups = groups
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
        for key in ("date_col", "time_col", "col", "source_col", "key_col"):
            c = p.get(key)
            if c and c not in cols:
                return f"列 {c} 不存在于 {table}"
        # 参照表（per_key_coverage 等跨表检查器）：表与列同样走白名单
        ref_table = p.get("ref_table")
        if ref_table:
            if not self._table_exists(conn, ref_table):
                return f"参照表 {ref_table} 不存在"
            ref_cols = self._columns(conn, ref_table)
            for key in ("ref_key", "ref_status_col", "ref_date_col"):
                c = p.get(key)
                if c and c not in ref_cols:
                    return f"参照表列 {c} 不存在于 {ref_table}"
        w = p.get("where")
        if w:
            # ⚠️ 先剥掉单引号字符串字面量再做标识符白名单：字面量是**值**不是标识符，
            # 而白名单要防的是「用未知标识符拼 SQL」。不剥离的话 `currency='USD'` 里的
            # USD 会被当成列名而误报（2026-09-19 新增汇率单位守护规则时踩到）。
            w_scan = re.sub(r"'[^']*'", "''", w)
            bad = [t for t in _IDENT_RE.findall(w_scan) if t not in cols and t.upper() not in _WHERE_FUNCS]
            if bad:
                return f"where 表达式含非白名单标识符: {bad[:3]}"
        for c in p.get("cols") or []:
            if c not in cols:
                return f"列 {c} 不存在于 {table}"
        return None

    # ---------- 检查器 ----------

    def _freshness_daily(self, conn, rule: dict) -> dict:
        """对齐交易日历：表 MAX(date_col) 与最近交易日比较，返回 report 字段

        `grace_days`（2026-09-14 新增，默认 0）：**允许的固有滞后**，落后 ≤ grace
        即判 pass。用于「源本身 T+1 发布」的表——两融（securities_margin）在交易日
        盘后天然只能拿到 T-1，此前 behind 恒为 1、warn_days 只区分 warning/fail，
        于是该规则在**每个交易日盘后必然 warning**，成了恒定的假信号。
        设 grace_days=1 后：正常 → pass，连停 2 日 → warning，超 warn_days → fail，
        仍能抓住真实停更（只是延后一个交易日暴露）。
        """
        p = rule.get("params") or {}
        date_col = p.get("date_col", "trade_date")
        calendar = p.get("calendar", "trade_calendar")
        warn_days = int(p.get("warn_days", 2))
        grace_days = int(p.get("grace_days", 0))
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
        if behind <= grace_days:
            status = "pass"
        elif behind <= warn_days:
            status = "warning"
        else:
            status = "fail"
        if status == "pass":
            msg = f"最新 {max_date}，与交易日历一致" if behind == 0 else f"最新 {max_date}，落后 {behind} 个交易日（≤ 固有滞后 {grace_days} 日，视为一致）"
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

    def _date_floor_where(self, conn, rule: dict) -> dict:
        """**带条件**的日期下限：MAX(date_col) 限定在满足 where 的子集内，不得早于今天-days_back。

        为什么需要（2026-09-19 立，随 Batch C 三个新分析模块一起加）：
        `date_floor` / `freshness_daily` 只看**整表**的 MAX(date_col)，看不见
        「多腿数据里某一条腿已停更」——因为另一条腿在更新，整表的 MAX 永远新鲜：

          · `overseas_index_daily` 有 4 个指数（恒生 + 3 个美股）。美股与港股交易日历不同，
            每日 3~4 行都算正常，故 `row_count_slice` 的 min_rows 只能设 3 ——
            **恒生腿整条停更时计数恰好是 3，规则照常通过**；
          · `stock_repurchase` 的既有规则 `repurchase_fresh` 看 announce_date，
            而 announce_date 会被后续公告覆盖、永远新鲜 → 它无法证明
            **start_date 这条腿**还活着，而新模块「资金温度」恰恰只按 start_date 统计；
          · `currency_boc_daily` 的 `currency_usd_mid_range` 把 `mid_price IS NOT NULL`
            写成前置条件 → mid_price 整列变空时违反数为 0，**规则反而更绿**。

        故本检查器把 MAX(date_col) 限定在 where 子集内，专治"这条腿还活着吗"。
        where 来自受控的 dq_rules，不接受外部输入。
        """
        p = rule.get("params") or {}
        date_col = p.get("date_col", "trade_date")
        where = p["where"]
        days_back = int(p.get("days_back", 7))
        table = rule["table_name"]
        floor = (datetime.now() - timedelta(days=days_back)).date()
        with conn.cursor() as cur:
            cur.execute(f"SELECT MAX(`{date_col}`) AS d FROM `{table}` WHERE {where}")
            row = cur.fetchone()
            if not row or row["d"] is None:
                return {"status": "fail", "metric_value": "无数据",
                        "message": f"子集内无任何数据 —— 该腿疑似整条停更（{where}）"}
            max_date = str(row["d"])[:10]
        status = "pass" if max_date >= str(floor) else "fail"
        msg = (f"子集最新 {max_date}，应不早于 {floor}（{where}）" if status == "pass"
               else f"停更风险：子集（{where}）最新 {max_date}，应 ≥ {floor}")
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
        """任意条件行数上限（适用于无日期列的静态/档案表；where 来自受控 dq_rules）

        2026-09-10 起 also 用于 stock_market_daily 全史数值自洽检查（OHLC/涨跌幅衔接）：
        此类规则必须在 where 内自带 `close>0` 等前置——该表为前复权口径，
        早期历史存在负价/近零价（前复权数学产物），不加前置会大面积误报。
        """
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

    def _column_watermark(self, conn, rule: dict) -> dict:
        """列水位线：某列「最后有值的那天」与「表最新那天」之间的空档行数。

        为什么需要（2026-09-19 立，起因是一次真实事故）：`bond_profit_daily` 的美债 4 列
        自 2026-09-07 起连续 10 个交易日全为 NULL，**断供 10 天没有任何规则报警**。
        原因是既有检查器在结构上都看不见这种形态：
          · `null_rate_slice` 只看「最新切片」，而本表的 NULL 分散在最新日之前的一整段
            （最新日反而可能已有值）→ 空值率恒为 0；
          · `freshness_daily` / `date_floor` 取的是 date_col 的 MAX，与 value_col 无关 →
            「表在正常更新、某列已死」它们不可能发现；
          · `row_count_*` / `where_count` 更不涉及列维度的时序。
        这就是「有列无值」陷阱：列还在、表还在长，值却停在了某天。

        本检查器专治这一类：先取 value_col 的**最后非空日**（水位线），再数表里还有多少行
        排在水位线之后。稳态下该数应为 0~1 —— 1 是「当日源尚未发布」的合理滞后
        （如美债在北京时间晚间确实还没出），> 1 就是真断供。`max_gap_rows` 用来切这两者。

        与 freshness_daily 的分工：freshness_daily 答「这张表还在更新吗」，
        本检查器答「这一列还在更新吗」。
        """
        p = rule.get("params") or {}
        date_col = p.get("date_col", "trade_date")
        value_col = p["value_col"]
        max_gap = int(p.get("max_gap_rows", 1))
        table = rule["table_name"]
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT MAX(CASE WHEN `{value_col}` IS NOT NULL THEN `{date_col}` END) AS wm, "
                f"       MAX(`{date_col}`) AS latest, COUNT(*) AS total FROM `{table}`"
            )
            row = cur.fetchone()
            if not row or row["total"] == 0:
                return {"status": "fail", "metric_value": "-", "message": MSG_EMPTY}
            wm, latest = row["wm"], row["latest"]
            if wm is None:
                return {"status": "fail", "metric_value": "0",
                        "message": f"{value_col} 全表无任何非空值（共 {row['total']} 行）——有列无值"}
            cur.execute(f"SELECT COUNT(*) AS n FROM `{table}` WHERE `{date_col}` > %s", (wm,))
            n = cur.fetchone()["n"]
        status = "pass" if n <= max_gap else "fail"
        msg = (f"{value_col} 水位线 {str(wm)[:10]}，表最新 {str(latest)[:10]}；"
               f"水位线之后仍有 {n} 行（应 ≤ {max_gap}）"
               + ("" if n <= max_gap else "—— 该列疑似断供：表在更新但此列不再有新值"))
        return {"status": status, "metric_value": str(n), "message": msg}

    # ---------- 全史窗口扫描类检查器（2026-09-10 新增，weekly 组） ----------

    def _gap_scan(self, conn, rule: dict) -> dict:
        """全史疑似缺口扫描：同票相邻两笔记录间隔 > gap_days 自然日

        只读 date_col（走 (stock_code, trade_date) 唯一索引有序扫描，18M 行实测 ~196s）；
        明细写入 dq_gap_detail（本轮幂等：先清同 run_at），供 L3 修复闭环消费。
        语义为「疑似」——当前无停牌表，长期停牌与真缺数无法自动区分。
        """
        p = rule.get("params") or {}
        date_col = p.get("date_col", "trade_date")
        gap_days = int(p.get("gap_days", 45))
        high_days = int(p.get("high_days", 365))
        table = rule["table_name"]
        sql = (
            f"SELECT stock_code, prev_date, next_date, DATEDIFF(next_date, prev_date) AS gap_days FROM ("
            f"  SELECT stock_code, `{date_col}` AS next_date, "
            f"         LAG(`{date_col}`) OVER (PARTITION BY stock_code ORDER BY `{date_col}`) AS prev_date "
            f"  FROM `{table}`"
            f") t WHERE prev_date IS NOT NULL AND DATEDIFF(next_date, prev_date) > %s"
        )
        logger.info(f"⏳ gap_scan 全史窗口扫描开始（gap_days>{gap_days}）…")
        with conn.cursor() as cur:
            cur.execute(sql, (gap_days,))
            rows = cur.fetchall()
        # 排序在 Python 端做：SQL 外层 ORDER BY 会让 MySQL 对 18M 行派生表全量排序，
        # 实测比"仅扫描"慢一个数量级（结果集仅数千行，内存排序无成本）
        rows.sort(key=lambda r: -int(r["gap_days"]))
        logger.info(f"⏳ gap_scan 扫描完成：{len(rows)} 段")
        with conn.cursor() as cur:
            cur.execute("DELETE FROM dq_gap_detail WHERE run_at = %s", (self.run_at,))
            if rows:
                cur.executemany(
                    "INSERT INTO dq_gap_detail "
                    "(run_at, run_date, stock_code, prev_date, next_date, gap_days, risk) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    [
                        (self.run_at, self.run_date, r["stock_code"], r["prev_date"], r["next_date"],
                         r["gap_days"], "high" if r["gap_days"] >= high_days else "suspect")
                        for r in rows
                    ],
                )
            # 明细保留策略与 dq_report 对齐
            cur.execute(
                "DELETE FROM dq_gap_detail WHERE run_date < %s",
                (self.run_date - timedelta(days=REPORT_KEEP_DAYS),),
            )
        n = len(rows)
        high = sum(1 for r in rows if r["gap_days"] >= high_days)
        status = "pass" if n == 0 else "warning"
        sample = "; ".join(
            f"{r['stock_code']} {r['prev_date']}→{r['next_date']}({r['gap_days']}d)" for r in rows[:3]
        )
        msg = f"疑似缺口 {n} 段（≥{high_days} 天高危 {high} 段）；明细已入 dq_gap_detail"
        if sample:
            msg += f"；最长：{sample}"
        return {"status": status, "metric_value": str(n), "message": msg}

    def _source_handoff(self, conn, rule: dict) -> dict:
        """跨源衔接一致性：相邻两笔记录 data_source 不同时，pre_close 应≈「上一笔 close × 因子比」

        实测（2026-09-10）：主表 AKSHARE(东财血缘) 与 TENCENT 增量在 2025-09 切换，
        部分票衔接处存在 0.02~0.12 的数值微差（复权基准/源算法差异），同源规则抓不到。
        只扫 since 之后（跨源切换只发生在新体系增量期）：1.3M 行回表扫描实测 ~41s。

        **2026-09-20 口径改造后的等式**（必须改，否则除权日必然误报）：
        主表已存**不复权实际价**，而 pre_close 是**按 hfq 反推出的除权调整后前收盘**：
            pre_close(t) = close(t) / (1 + ret_t) = close(t-1) × f(t-1) / f(t)
        其中 f = adj_factor。旧版直接比 `pre_close` 与 `LAG(close)`，在除权日会差一个
        分红幅度而误报 —— 那是前复权时代的写法。
        """
        p = rule.get("params") or {}
        date_col = p.get("date_col", "trade_date")
        source_col = p.get("source_col", "data_source")
        since = p.get("since", "2025-09-01")
        max_pct = float(p.get("max_pct", 0.5))
        table = rule["table_name"]
        sql = (
            f"SELECT COUNT(*) AS n FROM ("
            f"  SELECT pre_close, "
            f"         LAG(close) OVER w AS prev_close, "
            f"         LAG(adj_factor) OVER w AS prev_f, adj_factor AS f, "
            f"         LAG(`{source_col}`) OVER w AS prev_src, `{source_col}` AS src "
            f"  FROM `{table}` WHERE `{date_col}` >= %s AND adj_factor > 0 "
            f"  WINDOW w AS (PARTITION BY stock_code ORDER BY `{date_col}`)"
            f") t WHERE pre_close IS NOT NULL AND prev_close > 0 "
            f"  AND prev_f IS NOT NULL AND f IS NOT NULL AND prev_src <> src "
            f"  AND ABS(pre_close - prev_close * prev_f / f) / (prev_close * prev_f / f) * 100 > %s"
        )
        with conn.cursor() as cur:
            cur.execute(sql, (since, max_pct))
            n = cur.fetchone()["n"]
        status = "pass" if n == 0 else "warning"
        msg = (f"{since} 起跨源衔接偏差 >{max_pct}% 共 {n} 行"
               f"（相邻行 data_source 变化处，比较前收盘与上一笔 close×因子比）")
        return {"status": status, "metric_value": str(n), "message": msg}

    def _factor_link(self, conn, rule: dict) -> dict:
        """复权因子与昨收的**全史自洽**核验（2026-09-20 新增，守护「实际价 + 因子」口径）

        等式（数学必然，见 daily_rebuild 文件头）：
            pre_close(t) = close(t-1) × adj_factor(t-1) / adj_factor(t)

        为什么这条值得占一个全史扫描：主表换成「实际价 + 因子列」后，**任何一种
        因子写错**（阶梯切错段、跨源混写把两段基准拼在一起、漏写让倍率跳变）都会
        在这条等式上留下 >0.5% 的偏差。它是整个复权体系的单一最强判据 ——
        因子对了，前复权/后复权/收益率三个派生口径就都对。

        ⚠️ 窗口必须在**未过滤**的全序列上算（2026-09-20 修正）
        ------------------------------------------------------------------
        旧实现把 `WHERE adj_factor IS NOT NULL` 写在内层、再对过滤结果做 LAG ——
        这造出「跳跃相邻」：序列里被滤掉的行（典型是 920 段旧码前史 51,666 行
        AKSHARE 数据，因子整段为空）不参与，于是「上一笔」被错认成更早的有因子行，
        等式自然不成立 → 把「口径未知的空因子区」误报成「因子写错」。
        正确做法：LAG 在全序列上取；若真正的上一交易日因子为空，则 prev_f 为空，
        该行被自然排除，核验只发生在「因子连续的两行」之间。
        """
        p = rule.get("params") or {}
        date_col = p.get("date_col", "trade_date")
        max_pct = float(p.get("max_pct", 0.5))
        table = rule["table_name"]
        sql = (
            f"SELECT COUNT(*) AS n FROM ("
            f"  SELECT pre_close, close, adj_factor, "
            f"         LAG(close) OVER w AS prev_close, "
            f"         LAG(adj_factor) OVER w AS prev_f "
            f"  FROM `{table}` "
            f"  WINDOW w AS (PARTITION BY stock_code ORDER BY `{date_col}`)"
            f") t WHERE adj_factor IS NOT NULL AND adj_factor > 0 "
            f"  AND prev_f IS NOT NULL AND prev_f > 0 "
            f"  AND pre_close IS NOT NULL AND prev_close > 0 "
            f"  AND ABS(pre_close - prev_close * prev_f / adj_factor) "
            f"      / (prev_close * prev_f / adj_factor) * 100 > %s"
        )
        with conn.cursor() as cur:
            cur.execute(sql, (max_pct,))
            n = cur.fetchone()["n"]
        status = "pass" if n == 0 else "warning"
        msg = (f"全史 pre_close 与 close(前一日)×因子比 偏差 >{max_pct}% 共 {n} 行"
               f"（因子写错/阶梯切错的直接指纹）")
        return {"status": status, "metric_value": str(n), "message": msg}

    def _pct_limit(self, conn, rule: dict) -> dict:
        """涨跌幅「板块上限」违规：按股票所属板块取真实涨跌停上限，超限即违规

        2026-09-21 新增。为什么需要它（决策项 ⑧ 核查时发现的真缺口）
        ------------------------------------------------------------------
        原先用 `ABS(change_pct) > 11` 当「物理不可能」判据，隐含**错误前提**
        「全市场涨跌停均为 10%」。A 股实际分板块：主板 ±10%、创业板/科创板 ±20%、
        北交所 ±30%。实测该前提把 **73,285 行合法涨停**判成异常，其中 77% 是
        创业板 ±20% 被误杀（样本 300691 = +19.99%、301520 = +14.29%，均为合法板）。
        按板块重判后降到 **16,491 行**。

        这条判据的价值在于**与复权口径无关** —— 它是纯物理约束，不依赖
        adj_factor / pre_close 的推导，故能同时守护三类故障：
          ① 换源后价格单位对不上（如「元」被当「分」）；
          ② close 被写错（截断、错位）；
          ③ 复权断阶（因子跳变会把 change_pct 放大成天文数字）。
        与 factor_link 互补：后者查「因子之间自洽」，本检查器查「结果物理合理」。

        params:
          date_col      默认 trade_date
          since         可选，只查该日期之后（留空 = 全史）
          tol_pp        容差（百分点），默认 1.0，给一字板/退市整理/新股首日留余量
          limit_main    主板上限，默认 10
          limit_star    创业板+科创板上限，默认 20
          limit_bj      北交所上限，默认 30
          max_count     允许的行数上限，默认 0。实测真超限 16,491 行且成因是
                        「早期数据质量 + 低价股舍入退化」（非因子写错）→ 与
                        legacy_scale_rows 同策略改为**上账**：设固定上限，
                        平时静止、一旦增长即说明有新故障落入。
        """
        p = rule.get("params") or {}
        date_col = p.get("date_col", "trade_date")
        tol = float(p.get("tol_pp", 1.0))
        max_count = int(p.get("max_count", 0))
        lim_main = float(p.get("limit_main", 10))
        lim_star = float(p.get("limit_star", 20))
        lim_bj = float(p.get("limit_bj", 30))
        table = rule["table_name"]

        # 板块上限表达式（代码段口径与 code_to_symbol / 代码前缀约定保持一致）
        lim_expr = (
            "CASE "
            "WHEN stock_code LIKE '300%%' OR stock_code LIKE '301%%' OR stock_code LIKE '302%%' "
            "  OR stock_code LIKE '688%%' OR stock_code LIKE '689%%' THEN %s "
            "WHEN stock_code LIKE '92%%' OR stock_code LIKE '4%%' OR stock_code LIKE '8%%' THEN %s "
            "ELSE %s END"
        )
        args: list = [lim_star, lim_bj, lim_main]
        since_sql = ""
        if p.get("since"):
            since_sql = f" AND `{date_col}` >= %s"
            args.append(p["since"])
        args.append(tol)
        sql = (
            f"SELECT COUNT(*) AS n FROM ("
            f"  SELECT change_pct, ({lim_expr}) AS lim FROM `{table}` "
            f"  WHERE change_pct IS NOT NULL{since_sql}"
            f") t WHERE ABS(change_pct) > lim + %s"
        )
        with conn.cursor() as cur:
            cur.execute(sql, tuple(args))
            n = cur.fetchone()["n"]
        status = "pass" if n <= max_count else "warning"
        scope = f"{p['since']} 起" if p.get("since") else "全史"
        msg = (f"{scope}涨跌幅超「板块上限+{tol:g}pp」共 {n} 行（应 ≤ {max_count}）"
               f"（主板±{lim_main:g}/创业科创±{lim_star:g}/北交所±{lim_bj:g}）")
        return {"status": status, "metric_value": str(n), "message": msg}

    def _per_key_coverage(self, conn, rule: dict) -> dict:
        """近端每票覆盖率：窗口期内每只（在市且上市满 N 天的）股票行数下限

        抓「均匀稀疏」（gap_scan 阈值内漏不出的形态）。名单来自参照表（白名单校验），
        排除北交所前缀（现四源均不支持北交所，避免规则常红掩盖真问题）。
        """
        p = rule.get("params") or {}
        date_col = p.get("date_col", "trade_date")
        key_col = p.get("key_col", "stock_code")
        window_days = int(p.get("window_days", 250))
        min_rows = int(p.get("min_rows", 100))
        min_listed_days = int(p.get("min_listed_days", 365))
        ref_table = p.get("ref_table", "stock_info")
        ref_key = p.get("ref_key", "stock_code")
        ref_status_col = p.get("ref_status_col", "list_status")
        ref_status_val = p.get("ref_status_val", "上市")
        ref_date_col = p.get("ref_date_col", "list_date")
        excludes = p.get("exclude_prefixes") or ["4", "8", "920"]
        table = rule["table_name"]
        excl_sql = " AND ".join([f"d.`{key_col}` NOT LIKE %s"] * len(excludes))
        sql = (
            f"SELECT COUNT(*) AS n FROM ("
            f"  SELECT d.`{key_col}` AS k, COUNT(*) AS c FROM `{table}` d "
            f"  JOIN `{ref_table}` s ON s.`{ref_key}` = d.`{key_col}` "
            f"  WHERE d.`{date_col}` > DATE_SUB(CURDATE(), INTERVAL %s DAY) "
            f"    AND s.`{ref_status_col}` = %s AND s.`{ref_date_col}` IS NOT NULL "
            f"    AND DATEDIFF(CURDATE(), s.`{ref_date_col}`) >= %s "
            f"    AND {excl_sql} "
            f"  GROUP BY d.`{key_col}` HAVING c < %s"
            f") t"
        )
        args = [window_days, ref_status_val, min_listed_days] + [f"{x}%" for x in excludes] + [min_rows]
        with conn.cursor() as cur:
            cur.execute(sql, args)
            n = cur.fetchone()["n"]
        status = "pass" if n == 0 else "warning"
        msg = f"近 {window_days} 日行数 <{min_rows} 的在市老票 {n} 只（参照 {ref_table}，排除北交所前缀 {excludes}）"
        return {"status": status, "metric_value": str(n), "message": msg}

    def _stale_running(self, conn, rule: dict) -> dict:
        """僵尸 running 记录：status='running' 且 started_at 早于 N 小时前。

        为什么需要（2026-09-14 复盘）：uvicorn 重启会中断正在执行的任务，被中断的 run
        永久停在 running；而调度器的运行中保护是「同任务 2h 内 running 则跳过触发」，
        于是被中断的任务在重启后 2h 内无法重跑。实测 `daily_recon_window` 因此连续
        两个 20:45 班次被静默跳过（对账多日未按计划执行的真根因）。

        兜底层次：SchedulerManager.start() 已加「启动自愈」自动回收，本规则负责
        在**进程未重启**的情况下也能发现长挂任务（如无超时的外部调用卡死）。
        """
        p = rule.get("params") or {}
        hours = float(p.get("hours", 3))
        table = rule["table_name"]
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT COUNT(*) AS n FROM `{table}` "
                "WHERE status='running' AND started_at < DATE_SUB(NOW(), INTERVAL %s HOUR)",
                (hours,),
            )
            n = cur.fetchone()["n"]
        status = "pass" if n == 0 else "warning"
        msg = f"疑似僵尸 running {n} 条（running 且已启动 >{hours:g}h）"
        return {"status": status, "metric_value": str(n), "message": msg}

    def _long_finished_run(self, conn, rule: dict) -> dict:
        """「已完成」但耗时超长的运行（task_stale_running 覆盖不到的那半边）。

        为什么需要（2026-09-19）：`stale_running` 只盯**未收尾**的 running，
        覆盖不到「跑完了，但跑了很久」——而后者恰恰是本次盘点的真实缺口：
        `daily_recon_window` 09-17 21:15→09-18 18:14 **success 1259 分钟**、
        `financial_abstract_sync` 09-16 **success 711 分钟**。

        ⚠️ 判读口径：**这类超长绝大多数不是「任务真的慢」，而是机器待机冻结进程的信号**
        ——本机是家用电脑，夜间合盖/待机会把进程冻住，恢复后 finished_at 才落库，
        于是 started_at→finished_at 的跨度里绝大部分是冻结时长，不是执行时长。
        因此本规则 severity 取 warning，语义是「物理离线」告警，不是性能告警。

        ⚠️ 只看 status='success'：盘点报告里 9741/6515/5121 分钟那批「超长」记录，
        error_message 明写「进程重启中断，已由运维脚本标记为 failed」，
        其时长 = started_at→收尾时刻的跨度，非真实执行时长，不能用来判「任务慢」。
        """
        p = rule.get("params") or {}
        minutes = float(p.get("minutes", 180))
        lookback_days = int(p.get("lookback_days", 7))
        table = rule["table_name"]
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT COUNT(*) AS n, MAX(TIMESTAMPDIFF(MINUTE, started_at, finished_at)) AS mx "
                f"FROM `{table}` "
                "WHERE status='success' AND started_at IS NOT NULL AND finished_at IS NOT NULL "
                "  AND started_at >= DATE_SUB(NOW(), INTERVAL %s DAY) "
                "  AND TIMESTAMPDIFF(MINUTE, started_at, finished_at) > %s",
                (lookback_days, minutes),
            )
            row = cur.fetchone()
            n = row["n"] or 0
            mx = row["mx"]
        if n == 0:
            return {"status": "pass", "metric_value": "0",
                    "message": f"近 {lookback_days} 天无耗时 >{minutes:g} 分钟的已完成运行"}
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT task_name, TIMESTAMPDIFF(MINUTE, started_at, finished_at) AS mins "
                f"FROM `{table}` "
                "WHERE status='success' AND started_at IS NOT NULL AND finished_at IS NOT NULL "
                "  AND started_at >= DATE_SUB(NOW(), INTERVAL %s DAY) "
                "  AND TIMESTAMPDIFF(MINUTE, started_at, finished_at) > %s "
                "ORDER BY mins DESC",
                (lookback_days, minutes),
            )
            tops = cur.fetchall()
        top = ", ".join(f"{r['task_name']} {r['mins']}min" for r in tops[:3])
        status = "warning"
        msg = (f"已完成但耗时 >{minutes:g} 分钟的运行 {n} 条（近 {lookback_days} 天，最长 {mx} 分钟；"
               f"TOP: {top}）。⚠️ 此类超长多为「机器待机冻结进程」所致，"
               f"是**物理离线的信号**，不是任务本身变慢——请结合开机/睡眠时段判读")
        return {"status": status, "metric_value": str(n), "message": msg}

    CHECKERS = {
        "freshness_daily": _freshness_daily,
        "freshness_interval": _freshness_interval,
        "date_floor": _date_floor,
        "date_floor_where": _date_floor_where,
        "row_count_slice": _row_count_slice,
        "row_count_total": _row_count_total,
        "null_rate_slice": _null_rate_slice,
        "violation_count": _violation_count,
        "where_count": _where_count,
        "regex_count": _regex_count,
        "unique_index": _unique_index,
        "column_watermark": _column_watermark,
        "gap_scan": _gap_scan,
        "source_handoff": _source_handoff,
        "factor_link": _factor_link,
        "pct_limit": _pct_limit,
        "per_key_coverage": _per_key_coverage,
        "stale_running": _stale_running,
        "long_finished_run": _long_finished_run,
    }

    # ---------- 主流程 ----------

    def run(self) -> dict:
        conn = self._connect()
        try:
            where_sql = "WHERE enabled = 1"
            args: list = []
            if self.groups:
                where_sql += " AND rule_group IN (" + ",".join(["%s"] * len(self.groups)) + ")"
                args.extend(self.groups)
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, rule_name, table_name, check_type, rule_group, params, severity, description "
                    f"FROM dq_rules {where_sql} ORDER BY table_name, id",
                    args,
                )
                rules = self._norm_rows(cur.fetchall())
            for rule in rules:
                raw = rule.get("params")
                rule["params"] = json.loads(raw) if isinstance(raw, str) else (raw or {})
            if not rules:
                return with_steps(
                    {"records_written": 0, "error_count": 1, "errors": ["dq_rules 无启用规则"], "note": "未配置"},
                    RUN_STEPS, {1: f"无启用规则（groups={self.groups}），终止"},
                )

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
            logger.info(f"✅ DQ 体检完成：本轮 {written} 条规则（groups={self.groups}，run_at={self.run_at:%Y-%m-%d %H:%M:%S}）")
            note = f"体检完成 run_at={self.run_at:%Y-%m-%d %H:%M:%S}"
            return with_steps(
                {"records_written": written, "error_count": len(errors), "errors": errors[:5], "note": note},
                RUN_STEPS,
                {
                    1: f"{len(rules)} 条启用规则（groups={self.groups or '全部'}）",
                    2: f"预校验 {len(rules)} 条",
                    3: f"执行完成 · error {len(errors)} 条",
                    4: f"写入 dq_report {written} 条",
                    5: f"清理 ≤{REPORT_KEEP_DAYS} 天前历史",
                },
            )
        finally:
            conn.close()
