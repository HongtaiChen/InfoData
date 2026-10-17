#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
一键恢复（restore）—— 新机器 clone 后，把「结构 + 配置 + 数据」按正确顺序补齐。

为什么需要（2026-10-17）
================================================================================
此前换机器要照 `scripts/git-only-restore.md` 手工走 6 步，其中至少 3 处坑会
**静默失败**：

  * `seed_task_config.py` / `seed_indexes.py` 不加 `--apply` 只做只读预览，
    但**照样打印成功信息** ⇒ 以为灌好了，实际 task_config 是空表，
    调度器随后排出 0 个任务，页面「作业监控」一片空白。
  * `concept_market_sync` 空库时走 `FALLBACK_DAYS=400` 兜底
    ⇒ 只补最近一年，**48 万行历史悄悄缺失**（实测占全库 1.21%）。
  * `stock_market_daily_ex_sync` / `financial_abstract_sync` / `stock_shares_sync`
    默认是增量模式，空库下不传 `full_sweep` 就只能拿到很短的窗口。

本脚本把这些顺序、参数、确认步骤固化成代码，并在最后**逐表核对**，
输出一份可复核的恢复报告。

用法
================================================================================
    cd backend

    python scripts/restore.py --list                    # 只看恢复计划，不动任何数据
    python scripts/restore.py --dry-run                 # 预演：打印将执行的每一步
    python scripts/restore.py                           # 全量恢复（会二次确认）
    python scripts/restore.py --yes                     # 全量恢复，跳过确认

    只做某一阶段：
    python scripts/restore.py --schema-only             # 仅建表
    python scripts/restore.py --seeds-only              # 仅灌配置（含 --apply）
    python scripts/restore.py --data-only --stages core,daily
    python scripts/restore.py --verify-only             # 只体检，不采数据

安全边界
================================================================================
  * **不删任何东西**：建表用 `CREATE TABLE IF NOT EXISTS`，不含 DROP/TRUNCATE。
  * 采集器本身幂等（UPSERT / INSERT IGNORE），重复跑不会污染数据。
  * 唯一「不可逆」的是耗时 —— 全量拉历史可能要数小时，中途 Ctrl+C 可安全中断，
    重跑会从水位线继续。
"""
from __future__ import annotations

import argparse
import datetime
import json
import re
import subprocess
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent          # <repo>/backend
REPO = BACKEND.parent                                     # <repo>
SCHEMA_SQL = REPO / "scripts" / "schema.sql"
REPORT_DIR = BACKEND / "logs"

BACKUP_PAT = (re.compile(r"_bak_\d{8}$"), re.compile(r"_backup_\d{8}$"))

# ---------------------------------------------------------------------------
# 恢复阶段：**顺序不能随意调整**，后面依赖前面
#   stock_info 必须先有（票池），trade_calendar 必须先有（目标日/水位线判断），
#   日线必须在派生表（market_style / market_current）之前。
# ---------------------------------------------------------------------------
STAGES: list[tuple[str, str, list[str]]] = [
    ("core", "① 基础名册与日历（一切采集的前提）", [
        "stock_info_sync", "trade_calendar_sync", "stock_status_sync",
    ]),
    ("daily", "② 主日线与扩展行情（占全库约 87% 行数）", [
        "stock_daily_incr", "stock_market_daily_ex_sync",
    ]),
    ("reference", "③ 指数 / 概念 / 股本 / 财务 / 分红 / 调研", [
        "index_market_sync", "concept_sync", "concept_market_sync",
        "stock_shares_sync", "financial_abstract_sync", "ths_dividend_sync",
        "sw_industry_sync", "index_cons_sync", "index_valuation_sync",
        "stock_company_sync", "stock_repurchase_sync", "jgdy_sync",
        "margin_sync", "bond_profit_sync", "bj_stock_sync",
        "finance_calendar_sync",
    ]),
    ("fund", "④ 基金（信息 / 持仓 / 新发）", [
        "fund_info_sync", "fund_hold_sync", "fund_new_issue_sync",
    ]),
    ("macro", "⑤ 宏观与海外（流动性、汇率、期货、海外指数）", [
        "cn_liquidity_sync", "cn_omo_sync", "cn_reserve_sync", "cn_cb_balance_sync",
        "cb_policy_rate_sync", "interbank_rate_sync", "eu_money_supply_sync",
        "us_money_supply_sync", "us_money_market_sync", "us_fed_balance_sync",
        "global_liquidity_sync", "futures_sync", "currency_boc_sync",
        "usd_index_sync", "overseas_index_sync",
    ]),
    ("derived", "⑥ 派生与体检（依赖上面所有数据）", [
        "market_current_sync", "market_style_sync", "data_quality_check",
        "data_quality_check_weekly", "xcheck_sync", "daily_recon_window",
    ]),
    ("news", "⑦ 资讯（只能从现在起积累，补不回历史）", [
        "news_fetch",
    ]),
]

# 刻意**不**纳入恢复计划的任务，以及原因（写在这里，避免以后被当成遗漏）
NOT_RESTORED: dict[str, str] = {
    "capital_flow_sync": "源侧边界：东财硬限最近 120 个交易日（窗口已滑走的补不回）；"
                         "且本机/多数云网络对该域名不可达，跑也只会写 0 行，反而掩盖停更",
    "ai_concept_analysis": "手动触发的 AI 分析，依赖 ARK_API_KEY，不产生基础数据",
    "daily_backfill": "手动补缺工具（按 DQ 缺口定向补），恢复期应在核对后再按需跑",
    "daily_recon_sample": "随机抽样双源核对（seed 固定），恢复期无意义，日常由 cron 跑",
    "startup_converge": "启动收敛：后端每次启动自动执行，无需在恢复脚本里手工调用",
}

# 空库下**必须显式传参**才能补全史的覆盖项（默认参数只补一个短窗口）
#
# 🔴 与 table_meta.rebuild 的联动约定（2026-10-17 定）：
#    rebuild 标 `param` 表示「任务默认参数只补短窗口」，它**不是**给使用者看的提示，
#    而是**本表缺覆盖项的缺陷标记**。凡出现 param，正确处置是往这里加一条覆盖，
#    然后把 rebuild 升为 full —— 使用者不该被要求记住「跑这张表要传什么参」。
#    改完务必实测源侧真实起点，别照抄上一次的日期（本例 2018 就是错的，实际 2010）。
TASK_OVERRIDES: dict[str, dict] = {
    # 归档行情：auto 模式按水位线走，空库等于「每只票只补最近一段」；source=all 才全量重拉
    "stock_market_daily_ex_sync": {"source": "all", "max_codes": 0},
    # 股本变动：full_sweep=True 才回溯到 19900101
    "stock_shares_sync": {"full_sweep": True, "max_stocks": 0},
    # 财务摘要：full_sweep=True 才全量
    "financial_abstract_sync": {"full_sweep": True, "max_stocks": 0},
    # 概念行情：collector 只在「库内该概念无历史」时才用 start_date，
    # 否则一律走 last_date+1 增量 —— 所以空库恢复时这条覆盖是**唯一**的全史入口。
    # 2026-10-17 实测源侧可回溯远比 2018 早（akshare stock_board_concept_index_ths）：
    #   物联网   start=20050101 → 最早 2010-01-04 / 3,591 行（真起点，非过滤边界）
    #   智能电网 start=20050101 → 最早 2016-01-04（概念成立晚）
    #   负对照   start=20260101 → 最早 2026-01-05（确认确按 start_date 过滤）
    # 故取 20050101：足够早、靠源侧自然返回起点；实测耗时不随起点提前增加（7.3s vs 11.1s）。
    "concept_market_sync": {"start_date": "20050101"},
    # 分红：max_stocks=0 表示不限只数
    "ths_dividend_sync": {"max_stocks": 0},
}

# seed 脚本：(脚本名, 额外参数, 说明)。--apply 必须带上，否则只读预览。
SEEDS: list[tuple[str, list[str], str]] = [
    ("seed_table_meta.py", [], "表元数据"),
    ("seed_task_config.py", ["--apply"], "任务配置（⚠️ 不加 --apply 不落库）"),
    ("seed_dq_rules.py", [], "数据质量规则"),
    ("seed_indexes.py", ["--apply"], "性能索引（⚠️ 不加 --apply 不创建）"),
    ("seed_recovered_tasks.py", [], "历史恢复任务配置"),
    ("seed_market_style.py", [], "市场风格任务配置"),
]

# 恢复后允许为空的表（其余为空都算「没补上」）
EMPTY_OK = {
    "task_runs", "user_prefs",                             # 运营数据，天然很少
    "stock_capital_flow",                                  # 源在当前网络不可达（详见文档 §7）
    "dq_pct_anomaly_detail", "dq_close_nonpositive_detail",  # 审计明细，由 DQ 重跑产出
}

DATE_COLS = ("trade_date", "report_date", "report_period", "date", "trade_time", "update_time")


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
def _conn():
    sys.path.insert(0, str(BACKEND))
    import pymysql
    from app.db import get_db_config
    return pymysql.connect(**get_db_config().to_dict(), autocommit=True)


def ensure_database(create: bool = True) -> dict:
    """确保目标库存在；不存在则创建。

    新机器上 MySQL 刚装好时 `adata` 库并不存在，而 `pymysql.connect(database=...)`
    会直接抛 1049，导致 S0 就失败 —— `schema.sql` 里那句
    `CREATE DATABASE IF NOT EXISTS adata` 永远执行不到。所以必须在这里先补一步。

    `CREATE DATABASE` 不能在被选中的库内执行，故库不存在时要改用**不带 database**
    的连接来建。排序规则跟随生产库的 `utf8mb4_unicode_ci`：业务表是
    `utf8mb4_0900_ai_ci`，两者混用会在自建临时表 JOIN 时报 1267。
    """
    sys.path.insert(0, str(BACKEND))
    import pymysql
    from app.db import get_db_config

    cfg = get_db_config().to_dict()
    name = cfg.get("database") or "adata"
    # 库名要拼进 DDL，用白名单挡住反引号
    if not re.fullmatch(r"[A-Za-z0-9_]{1,64}", name):
        return {"ok": False, "created": False, "name": name,
                "error": f"库名 {name!r} 非法（只允许字母、数字、下划线）"}

    try:
        _conn().close()
        return {"ok": True, "created": False, "name": name, "error": None}
    except pymysql.err.OperationalError as exc:
        if not exc.args or exc.args[0] != 1049:      # 1049 = Unknown database
            return {"ok": False, "created": False, "name": name, "error": str(exc)}
    except Exception as exc:
        return {"ok": False, "created": False, "name": name, "error": str(exc)}

    if not create:
        return {"ok": False, "created": False, "name": name,
                "error": f"库 `{name}` 不存在（预演模式不建库，去掉 --dry-run 后重跑即可创建）"}

    bare = {k: v for k, v in cfg.items() if k != "database"}
    try:
        c = pymysql.connect(**bare, autocommit=True)
        with c.cursor() as cur:
            cur.execute(
                f"CREATE DATABASE IF NOT EXISTS `{name}` "
                "DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
        c.close()
        _conn().close()
        return {"ok": True, "created": True, "name": name, "error": None}
    except Exception as exc:
        return {"ok": False, "created": False, "name": name,
                "error": f"建库失败：{type(exc).__name__}: {exc}"}


def _schema_tables() -> list:
    if not SCHEMA_SQL.is_file():
        return []
    text = SCHEMA_SQL.read_text(encoding="utf-8", errors="replace")
    return re.findall(
        r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?`([A-Za-z0-9_]+)`", text, re.I
    )


def _hhmmss(sec: float) -> str:
    sec = int(sec)
    if sec < 60:
        return f"{sec}s"
    if sec < 3600:
        return f"{sec // 60}m{sec % 60:02d}s"
    return f"{sec // 3600}h{(sec % 3600) // 60:02d}m"


# ---------------------------------------------------------------------------
# S1 建表
# ---------------------------------------------------------------------------
def _split_sql(sql: str) -> list[str]:
    """把 SQL 文件切成单条语句 —— **引号与注释感知**。

    为什么不能用 `sql.split(";")`（2026-10-17 实测踩过）：
    schema.sql 里存在 `DEFAULT 'EM;BAOSTOCK;CNINFO'` 这种**字符串字面量内含分号**的
    写法（stock_info.data_source 的默认值），朴素切分会把它劈成两半，
    执行时报 `1064 ... near ''EM' at line 35`，而且报错位置与真因毫不相干。
    """
    out: list[str] = []
    buf: list[str] = []
    i, n = 0, len(sql)
    quote = ""            # 当前处于 ' / " / ` 之内
    while i < n:
        ch = sql[i]
        if quote:
            buf.append(ch)
            if ch == "\\" and quote in ("'", '"') and i + 1 < n:
                buf.append(sql[i + 1])
                i += 2
                continue
            if ch == quote:
                # '' 是 SQL 里的转义写法（反引号不适用）
                if quote != "`" and i + 1 < n and sql[i + 1] == quote:
                    buf.append(sql[i + 1])
                    i += 2
                    continue
                quote = ""
            i += 1
            continue

        # ── 字符串之外：先吃掉注释 ──
        if sql.startswith("--", i):
            j = sql.find("\n", i)
            i = n if j < 0 else j + 1
            continue
        if sql.startswith("/*", i):
            j = sql.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue

        if ch in ("'", '"', "`"):
            quote = ch
            buf.append(ch)
            i += 1
            continue
        if ch == ";":
            stmt = "".join(buf).strip()
            if stmt:
                out.append(stmt)
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1

    tail = "".join(buf).strip()
    if tail:
        out.append(tail)
    return out


def apply_schema() -> dict:
    """执行 schema.sql，并**核对表实际建在了哪个库**。

    ⚠️ 必须剔除 schema.sql 里的 `CREATE DATABASE adata` / `USE adata`
    （2026-10-17 空库实测踩到）：那两句会把连接切回**硬编码的 adata**，
    于是 `INFO_DATA_DB_NAME=别的库` 时 56 张表全建到生产库上，而脚本照样
    报「✅ 56/56」—— 典型假绿。库由 ensure_database() 负责创建、目标库由
    连接选定，这两条库级语句必须跳过。

    返回 {'executed','expected','present','target','skipped','error'}
    """
    if not SCHEMA_SQL.is_file():
        return {"executed": 0, "expected": 0, "present": 0, "target": None,
                "skipped": [], "error": f"找不到 {SCHEMA_SQL}"}

    tables = _schema_tables()
    expected = len(tables)

    statements, skipped = [], []
    for stmt in _split_sql(SCHEMA_SQL.read_text(encoding="utf-8", errors="replace")):
        if re.match(r"^\s*USE\b", stmt, re.I):
            skipped.append(f"USE {stmt.split(None, 1)[1].strip().strip('`')}"
                           if len(stmt.split(None, 1)) > 1 else "USE")
        elif re.match(r"^\s*CREATE\s+DATABASE\b", stmt, re.I):
            skipped.append("CREATE DATABASE")
        else:
            statements.append(stmt)

    conn = _conn()
    executed, present, target = 0, 0, None
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT DATABASE()")
            target = cur.fetchone()[0]
            for stmt in statements:
                cur.execute(stmt)
                if re.match(r"CREATE\s+TABLE", stmt, re.I):
                    executed += 1
            # 真校验：表到底在不在**当前库**里、数量够不够
            cur.execute(
                "SELECT COUNT(*) FROM information_schema.tables "
                "WHERE table_schema=DATABASE() AND table_name IN ("
                + ",".join(["%s"] * len(tables)) + ")",
                tables,
            )
            present = int(cur.fetchone()[0] or 0)
    except Exception as exc:
        return {"executed": executed, "expected": expected, "present": present,
                "target": target, "skipped": skipped,
                "error": f"{type(exc).__name__}: {exc}"}
    finally:
        conn.close()

    if present != expected:
        return {"executed": executed, "expected": expected, "present": present,
                "target": target, "skipped": skipped,
                "error": f"表数不符：`{target}` 库中只找到 {present}/{expected} 张，"
                         f"schema.sql 可能有不兼容语句"}
    return {"executed": executed, "expected": expected, "present": present,
            "target": target, "skipped": skipped, "error": None}


# ---------------------------------------------------------------------------
# S2 灌配置
# ---------------------------------------------------------------------------
def run_seeds(*, dry_run: bool = False) -> list[dict]:
    out = []
    for script, extra, label in SEEDS:
        path = BACKEND / "scripts" / script
        if not path.is_file():
            out.append({"script": script, "ok": False, "error": "脚本不存在"})
            continue
        cmd = [sys.executable, str(path)] + extra
        if dry_run:
            out.append({"script": script, "ok": True, "dry": True,
                        "cmd": " ".join([Path(sys.executable).name, f"scripts/{script}", *extra])})
            continue
        t0 = time.time()
        proc = subprocess.run(cmd, cwd=str(BACKEND), capture_output=True, text=True, errors="replace")
        out.append({
            "script": script, "label": label,
            "ok": proc.returncode == 0,
            "sec": round(time.time() - t0, 1),
            "tail": (proc.stdout or proc.stderr or "").strip().splitlines()[-1:] or [""],
            "error": None if proc.returncode == 0 else (proc.stderr or "").strip()[-300:],
        })
    return out


# ---------------------------------------------------------------------------
# S3 采数据
# ---------------------------------------------------------------------------
def _base_params(conn) -> dict:
    """从 task_config 读「生产在用的默认参数」——比在代码里硬编码更可靠。

    seed_task_config 刚灌过，所以这里拿到的就是权威默认值；
    再用 TASK_OVERRIDES 覆盖空库场景需要改的那几个。
    """
    out = {}
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT task_name, params FROM task_config")
            for name, params in cur.fetchall():
                if isinstance(params, (str, bytes)):
                    try:
                        params = json.loads(params)
                    except Exception:
                        params = {}
                out[name] = params or {}
    except Exception:
        pass
    return out


def run_tasks(names: list[str], *, dry_run: bool = False) -> list[dict]:
    sys.path.insert(0, str(BACKEND))
    from app.tasks.run import TASKS  # noqa: PLC0415

    conn = _conn()
    try:
        base = _base_params(conn)
    finally:
        conn.close()

    results = []
    for name in names:
        params = dict(base.get(name) or {})
        params.update(TASK_OVERRIDES.get(name) or {})
        fn = TASKS.get(name)
        if fn is None:
            results.append({"task": name, "ok": False, "error": "未在 TASKS 中注册"})
            continue
        if dry_run:
            shown = {k: v for k, v in params.items() if k in (TASK_OVERRIDES.get(name) or {})}
            mark = f"  ← 覆盖 {shown}" if shown else ""
            print(f"      · {name}{mark}", flush=True)
            results.append({"task": name, "ok": True, "dry": True, "params": params})
            continue

        print(f"  ▶ {name} …", flush=True)
        t0 = time.time()
        try:
            rows = fn(params) or 0
            sec = round(time.time() - t0, 1)
            print(f"    ✅ {int(rows):,} 行，耗时 {_hhmmss(sec)}", flush=True)
            results.append({"task": name, "ok": True, "rows": int(rows), "sec": sec, "params": params})
        except KeyboardInterrupt:
            results.append({"task": name, "ok": False, "error": "用户中断"})
            raise
        except Exception as exc:
            sec = round(time.time() - t0, 1)
            print(f"    ❌ {type(exc).__name__}: {exc}", flush=True)
            results.append({"task": name, "ok": False, "error": f"{type(exc).__name__}: {exc}", "sec": sec})
    return results


# ---------------------------------------------------------------------------
# S4 核对
# ---------------------------------------------------------------------------
def verify(*, fast: bool = False) -> list[dict]:
    """逐表统计行数与日期区间。fast=True 用 information_schema 估算行数（秒级但要容差）。"""
    conn = _conn()
    rows = []
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_schema=DATABASE() ORDER BY table_name, ordinal_position"
        )
        cols: dict[str, list[str]] = {}
        for t, c in cur.fetchall():
            cols.setdefault(t, []).append(c)

        for table in _schema_tables():
            if any(p.search(table) for p in BACKUP_PAT):
                continue
            date_col = next((c for c in DATE_COLS if c in cols.get(table, [])), None)
            try:
                if fast:
                    # 估算模式：只取 information_schema 的估算行数，**不扫表、不取日期区间**。
                    # 原因：MIN/MAX 在 1800 万行的大表上（date 列无索引时）会全表扫描，
                    # 56 张表加起来要 2~3 分钟，失去「快速看一眼」的意义。
                    cur.execute(
                        "SELECT table_rows FROM information_schema.tables "
                        "WHERE table_schema=DATABASE() AND table_name=%s", (table,)
                    )
                    r = cur.fetchone()
                    n = int((r[0] if r else 0) or 0)
                    mn = mx = None
                else:
                    if date_col:
                        cur.execute(f"SELECT COUNT(*), MIN(`{date_col}`), MAX(`{date_col}`) FROM `{table}`")
                        n, mn, mx = cur.fetchone()
                    else:
                        cur.execute(f"SELECT COUNT(*) FROM `{table}`")
                        n, mn, mx = cur.fetchone()[0], None, None
            except Exception as exc:
                rows.append({"table": table, "rows": 0, "error": str(exc)[:120]})
                continue
            rows.append({
                "table": table, "rows": int(n or 0), "date_col": date_col,
                "min": str(mn) if mn else None, "max": str(mx) if mx else None,
                "empty_ok": table in EMPTY_OK,
            })
    finally:
        conn.close()
    return rows


def print_verify(rows: list[dict], *, fast: bool = False) -> None:
    total = sum(r["rows"] for r in rows)
    empty_bad = [r for r in rows if r["rows"] == 0 and not r.get("empty_ok")]
    print()
    print(f"{'表':<34}{'行数':>14}  {'区间':<28} 状态")
    print("-" * 92)
    for r in sorted(rows, key=lambda x: -x["rows"]):
        if r["rows"] == 0:
            state = "— 允许为空" if r.get("empty_ok") else "⚠️ 空表（可能没补上）"
        else:
            state = "✅"
        if not r.get("date_col"):
            span = "（无日期列）"
        elif fast:
            span = "（估算模式未取区间）"
        else:
            span = f"{r.get('min') or '?'} ~ {r.get('max') or '?'}"
        print(f"{r['table']:<34}{r['rows']:>14,}  {span:<28} {state}")
    print("-" * 92)
    est = "（行数为 information_schema 估算值，误差约 ±10%）" if fast else ""
    print(f"合计 {len(rows)} 张表 / {total:,} 行{est}；空表（非预期）{len(empty_bad)} 张"
          + (f"：{'、'.join(x['table'] for x in empty_bad[:5])}" if empty_bad else ""))


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    stage_keys = [s[0] for s in STAGES]
    ap = argparse.ArgumentParser(
        description="InvestBuddy 一键恢复（结构 → 配置 → 数据 → 核对）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--list", action="store_true", help="打印恢复计划后退出")
    ap.add_argument("--dry-run", action="store_true", help="预演，不实际执行")
    ap.add_argument("--yes", "-y", action="store_true", help="跳过确认")
    ap.add_argument("--stages", default="", help=f"只跑这些阶段（逗号分隔，可选：{','.join(stage_keys)}）")
    ap.add_argument("--schema-only", action="store_true", help="只建表")
    ap.add_argument("--seeds-only", action="store_true", help="只灌配置")
    ap.add_argument("--data-only", action="store_true", help="只采数据")
    ap.add_argument("--verify-only", action="store_true", help="只核对")
    ap.add_argument("--skip-seeds", action="store_true")
    ap.add_argument("--no-data", action="store_true",
                    help="只建表+灌配置+核对，不采数据（新机器先让空壳跑起来时用）")
    ap.add_argument("--no-verify", action="store_true")
    ap.add_argument("--fast-verify", action="store_true", help="用估算行数核对（快，但有容差）")
    ap.add_argument("--no-report", action="store_true", help="不写 JSON 报告文件")
    args = ap.parse_args()

    # ---- 解析要跑哪些阶段 ----
    if args.stages:
        want = [s.strip() for s in args.stages.split(",") if s.strip()]
        bad = [s for s in want if s not in stage_keys]
        if bad:
            print(f"❌ 未知阶段：{bad}（可选：{stage_keys}）", file=sys.stderr)
            return 2
    else:
        want = stage_keys

    do_schema = not (args.seeds_only or args.data_only or args.verify_only)
    do_seeds = not (args.schema_only or args.data_only or args.verify_only or args.skip_seeds)
    do_data = not (args.schema_only or args.seeds_only or args.verify_only or args.no_data)
    do_verify = not args.no_verify

    if args.list or args.dry_run:
        print("恢复计划（按顺序执行）：")
        print("\n  S1 建表：scripts/schema.sql（幂等，不含 DROP）")
        if not args.skip_seeds:
            print("\n  S2 灌配置：")
            for script, extra, label in SEEDS:
                cmd = f"python scripts/{script}" + (f" {' '.join(extra)}" if extra else "")
                print(f"      {cmd:<44} # {label}")
        print("\n  S3 采数据：")
        for key, label, names in STAGES:
            mark = "  " if key in want else "（跳过）"
            print(f"    [{key:<9}] {label} {mark}")
            for n in names:
                ov = TASK_OVERRIDES.get(n)
                extra = f"  ← 覆盖参数 {ov}" if ov else ""
                print(f"        · {n}{extra}")
        print("\n  S4 核对：逐表统计行数与日期区间，输出 JSON 报告")
        if NOT_RESTORED:
            print("\n  未纳入的 5 个任务（不是遗漏，原因如下）：")
            for n, why in NOT_RESTORED.items():
                print(f"      · {n:<24} {why}")
        if args.list:
            return 0

    # 只读核对不需要确认；其余动作（建表/灌配置/采数据）都要
    read_only = do_verify and not (do_schema or do_seeds or do_data)
    if not args.yes and not args.dry_run and not read_only:
        stages_desc = "/".join(want) if do_data else "（不采数据）"
        print(f"\n即将执行：建表={'是' if do_schema else '否'} 灌配置={'是' if do_seeds else '否'} "
              f"采数据={stages_desc} 核对={'是' if do_verify else '否'}")
        if do_data:
            print("⚠️ 全量拉历史可能耗时数小时（实测：主日线约 20~30 分钟，其余长尾更久）。")
            print("   中途 Ctrl+C 可安全中断，重跑会从水位线继续。")
        try:
            answer = input("确认继续？输入 yes 回车：").strip().lower()
        except EOFError:
            # 非交互环境（CI / 被其它脚本调用 / stdout 重定向）读不到输入
            print("\n❌ 当前不是交互式终端，读不到确认输入。请显式加 --yes 重跑：")
            print("    python scripts/restore.py --yes")
            return 2
        if answer not in ("yes", "y"):
            print("已取消。")
            return 1

    started = datetime.datetime.now()
    report: dict = {"started_at": started.isoformat(timespec="seconds"), "steps": {}}

    # ---- S0 前置：连库（库不存在则自动创建）----
    print("\n[S0] 检查数据库连通性…")
    db = ensure_database(create=not args.dry_run)
    if not db["ok"]:
        print(f"     ❌ {db['error']}", file=sys.stderr)
        print("        先跑 python scripts/preflight.py 看详细修法。", file=sys.stderr)
        return 2
    if db["created"]:
        print(f"     ✅ 库 `{db['name']}` 原本不存在，已自动创建（utf8mb4_unicode_ci）")
    else:
        print(f"     ✅ 可连接（库 `{db['name']}`）")

    # ---- S1 建表 ----
    if do_schema:
        print("\n[S1] 导入 schema.sql（幂等）…")
        r = apply_schema()
        report["steps"]["schema"] = r
        if r["skipped"]:
            print(f"     跳过库级语句 {', '.join(r['skipped'])}"
                  f"（目标库由连接决定：`{r['target']}`）")
        if r["error"]:
            print(f"     ❌ {r['error']}", file=sys.stderr)
            return 2
        print(f"     ✅ 执行 {r['executed']}/{r['expected']} 条 CREATE TABLE；"
              f"核实 `{r['target']}` 库内 {r['present']}/{r['expected']} 张齐全")

    # ---- S2 配置 ----
    if do_seeds:
        print("\n[S2] 灌入基础配置…")
        sr = run_seeds(dry_run=args.dry_run)
        report["steps"]["seeds"] = sr
        for x in sr:
            if x.get("dry"):
                print(f"     · {x['cmd']}")
            elif x["ok"]:
                tail = x.get("tail") or [""]
                print(f"     ✅ {x['script']:<30} {_hhmmss(x.get('sec', 0)):>7}  {tail[0][:60]}")
            else:
                print(f"     ❌ {x['script']}：{x.get('error')}")

    # ---- S3 数据 ----
    if do_data:
        print("\n[S3] 回补业务数据…")
        all_results = []
        for key, label, names in STAGES:
            if key not in want:
                continue
            print(f"\n  ── {label}")
            try:
                all_results.extend(run_tasks(names, dry_run=args.dry_run))
            except KeyboardInterrupt:
                print("\n  ⏹ 已中断（已完成的部分已入库，重跑会续）")
                report["steps"]["data"] = all_results
                report["interrupted"] = True
                break
        report.setdefault("steps", {})["data"] = all_results
        ok = sum(1 for r in all_results if r.get("ok"))
        if all_results and not args.dry_run:
            print(f"\n  数据阶段：成功 {ok}/{len(all_results)} 个任务")

    # ---- S4 核对 ----
    if do_verify and not args.dry_run:
        print("\n[S4] 逐表核对…" + ("（估算模式）" if args.fast_verify else "（精确计数，约 1~2 分钟）"))
        rows = verify(fast=args.fast_verify)
        print_verify(rows, fast=args.fast_verify)
        report["steps"]["verify"] = rows

    # ---- 报告 ----
    report["finished_at"] = datetime.datetime.now().isoformat(timespec="seconds")
    report["elapsed_sec"] = round((datetime.datetime.now() - started).total_seconds(), 1)

    if not args.no_report and not args.dry_run:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        out = REPORT_DIR / f"restore_report_{started.strftime('%Y%m%d_%H%M%S')}.json"
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(f"\n📄 报告已写入 {out}")

    print(f"\n总耗时 {_hhmmss(report['elapsed_sec'])}")

    # 判定：数据阶段有失败任务 → 非零退出（便于自动化判断）
    failed = [r for r in (report.get("steps", {}).get("data") or []) if not r.get("ok")]
    if failed:
        print(f"⚠️ {len(failed)} 个采集任务失败：{'、'.join(r['task'] for r in failed[:6])}")
        print("   常见原因：源站限流/网络不通。可单独重跑：python scripts/restore.py --data-only --stages <阶段>")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
