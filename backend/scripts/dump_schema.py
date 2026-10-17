#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
从当前库导出**完整表结构**为 scripts/schema.sql（供全新环境一键初始化）。

用法（在 backend/ 下执行）：
    python scripts/dump_schema.py                    # 默认输出 ../scripts/schema.sql
    python scripts/dump_schema.py -o /tmp/schema.sql # 指定输出路径

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
为什么需要这个脚本（2026-10-09 可移植性审计）

仓库原本只有 5 个 init_*.py，覆盖 27 张表；另有 **23 张核心表**
（task_config / task_runs / stock_market_daily / stock_info / stock_info_ex /
 stock_shares / stock_industry_sw / stock_code_mapping / news / finance_calendar /
 finance_concept_analysis / ths_concept_* / trade_calendar / dc_index_market /
 securities_margin / bond_profit_daily / futures_spot_price / fund_info /
 stock_market_current / stock_jgdy_detail）
**完全没有建表脚本** ⇒ 别人 clone 后空库启动会**直接崩**：

    app/scheduler.py::sync_from_db()  →  SELECT ... FROM task_config
    → pymysql 抛 (1146, "Table 'adata.task_config' doesn't exist")
    → 该调用点在 SchedulerManager.start() 里**没有 try 保护**（scheduler.py:255）
    → 冒泡到 app/main.py::lifespan
    → uvicorn "Application startup failed"，进程起不来

（已用 _scratch/_probe_emptydb_boot.py 做过故障注入实测，调用栈可复现。）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

导出策略（每条都有理由，别随手改）：
  1. **仅结构、不含数据**（等价 mysqldump --no-data）。
  2. 全部 `CREATE TABLE IF NOT EXISTS` ⇒ **幂等**，可重复执行、不覆盖已有表。
     （有意不用 DROP TABLE：初始化脚本不该有删数据的能力。）
  3. **逐表显式 `DEFAULT CHARSET/COLLATE`** —— 直接沿用 SHOW CREATE TABLE 的原样输出。
     ⚠️ 必须保留，不能省：本库**默认排序规则是 utf8mb4_unicode_ci**，而实测
        **58/60 张表实际是 utf8mb4_0900_ai_ci**（仅 stock_market_daily 与
        market_style_daily_bak_20260914 是 unicode_ci）。若省略表级声明，
        新表会继承库默认 ⇒ 与业务表 JOIN 时报
        `1267 Illegal mix of collations`。
  4. **去掉表选项里的 `AUTO_INCREMENT=n`** ⇒ 新库自增从 1 开始（带了也不影响使用，
     但会让「初始化后的库」带上生产库的自增值，观感与语义都不对）。
  5. **排除备份/历史残留表**：`*_bak_YYYYMMDD` / `*_backup_YYYYMMDD`。
     实测这 4 张在库中但属一次性备份：market_style_daily_bak_20260914 /
     stock_market_daily_bak_20260920 / stock_company_profile_bak_20260905 /
     dq_pct_fix_backup_20260922。新环境不需要它们。
  6. 建库语句用 `utf8mb4_0900_ai_ci`（**有意与旧库默认的 unicode_ci 不同**）：
     表级 collation 已全部显式声明、不受影响；而把库默认设成 0900 可以让
     「以后手工新建的表」默认就与 58/60 的业务表一致，**顺手消掉上面 ③ 那个坑**。
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
import argparse
import datetime
import os
import re
import sys

import pymysql

# 备份/历史残留表：不导出（见文件头策略 ⑤）
EXCLUDE = (
    re.compile(r"_bak_\d{8}$"),
    re.compile(r"_backup_\d{8}$"),
)

HEADER = """-- ============================================================================
-- InvestBuddy / InfoData — 数据库结构定义（schema only，不含任何数据）
-- ============================================================================
-- 由 scripts/dump_schema.py 从现网 adata 库导出，导出时间：{ts}
-- 源库：{db}@{host}:{port}
-- 表数量：{n} 张（已排除 {skipped} 张 *_bak_* / *_backup_* 备份表）
--
-- 用途：全新环境初始化数据库结构。
--
-- 使用方式（二选一）：
--   ① 命令行：  mysql -u root -p < scripts/schema.sql
--   ② MySQL 客户端里： SOURCE /绝对路径/scripts/schema.sql;
--
-- 特性与注意事项：
--   * **幂等**：全部 CREATE TABLE IF NOT EXISTS，重复执行不报错、不覆盖已有表。
--   * **不含数据**：只建结构。业务数据需由采集器回补，见 scripts/data-bootstrap.md。
--   * **不删任何东西**：本文件不含 DROP，可安全执行。
--   * **每张表都显式声明了 CHARACTER SET / COLLATE**，请勿删除这些声明 ——
--     本库默认排序规则与多数表不同，省略会导致 JOIN 报
--     1267 Illegal mix of collations（详见 dump_schema.py 文件头）。
--   * 无外键、无视图、无触发器、无存储过程（已实测），故**导入顺序无关**。
--
-- 建表之后的步骤（**顺序不能颠倒**，否则启动会失败或页面空白）：
--   1) 导入本文件                    → 建出全部空表
--   2) 跑 seed 脚本                  → 灌入任务配置 / DQ 规则 / 表元数据
--   3) 跑采集器回补业务数据           → 见 scripts/data-bootstrap.md
--   4) 启动后端                      → uvicorn app.main:app
--   详细说明见仓库根目录 README.md 的「数据库初始化」章节。
-- ============================================================================

CREATE DATABASE IF NOT EXISTS `{db}`
    DEFAULT CHARACTER SET utf8mb4
    DEFAULT COLLATE utf8mb4_0900_ai_ci;

USE `{db}`;

-- 让本次导入不受客户端字符集影响（避免中文注释乱码）
SET NAMES utf8mb4;

"""


def get_conn():
    """连接当前库（与 app/db.py 相同的环境变量约定与默认值）"""
    return pymysql.connect(
        host=os.getenv("INFO_DATA_DB_HOST", "127.0.0.1"),
        port=int(os.getenv("INFO_DATA_DB_PORT", "3306")),
        user=os.getenv("INFO_DATA_DB_USER", "root"),
        password=os.getenv("INFO_DATA_DB_PASSWORD", "root"),
        database=os.getenv("INFO_DATA_DB_NAME", "adata"),
        charset="utf8mb4",
        autocommit=True,
        cursorclass=pymysql.cursors.DictCursor,
    )


def list_tables(conn, db):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT TABLE_NAME AS n FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA=%s AND TABLE_TYPE='BASE TABLE' ORDER BY TABLE_NAME",
            (db,),
        )
        return [r["n"] for r in cur.fetchall()]


def show_create(conn, table):
    with conn.cursor() as cur:
        cur.execute(f"SHOW CREATE TABLE `{table}`")
        row = cur.fetchone()
        # 列名随版本为 'Create Table' / 'Create View'，取「第二个值」最稳
        return list(row.values())[1]


def normalize(ddl: str) -> str:
    """SHOW CREATE TABLE 输出 → 幂等初始化语句"""
    # ① CREATE TABLE `x`  →  CREATE TABLE IF NOT EXISTS `x`
    ddl = re.sub(r"^CREATE TABLE\s+", "CREATE TABLE IF NOT EXISTS ", ddl, count=1)
    # ② 去掉表选项里的 AUTO_INCREMENT=n（保留列定义里的 AUTO_INCREMENT 关键字）
    ddl = re.sub(r"\s+AUTO_INCREMENT=\d+", "", ddl)
    return ddl.rstrip() + ";\n"


def parse_schema_columns(schema_text: str) -> dict:
    """从已导出的 schema.sql 里解析 {表名: [列名, ...]}。

    为什么不用正则一把梭：列定义里可能出现 CHECK(...) / 生成列等**嵌套括号**，
    按「配对括号」扫描才不会把 DDL 切在半截。
    只认「行首是 `列名`」的行 —— KEY / UNIQUE KEY / PRIMARY KEY 行都以关键字开头，自然被排除。
    """
    out: dict = {}
    pat = re.compile(
        r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?`([A-Za-z0-9_]+)`\s*\(",
        re.I,
    )
    for m in pat.finditer(schema_text):
        name = m.group(1)
        i = m.end()
        depth = 1
        while i < len(schema_text) and depth > 0:
            ch = schema_text[i]
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            i += 1
        body = schema_text[m.end():i - 1]
        cols = []
        for line in body.splitlines():
            cm = re.match(r"\s*`([A-Za-z0-9_]+)`\s", line)
            if cm:
                cols.append(cm.group(1))
        out[name] = cols
    return out


def db_columns(conn, db: str) -> dict:
    """库中实际列：{表名: [列名, ...]}（按定义顺序）"""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT TABLE_NAME AS t, COLUMN_NAME AS c FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA=%s ORDER BY TABLE_NAME, ORDINAL_POSITION",
            (db,),
        )
        out: dict = {}
        for r in cur.fetchall():
            out.setdefault(r["t"], []).append(r["c"])
        return out


def run_check(conn, db: str, schema_path: str) -> int:
    """比对 schema.sql 与现库，有漂移返回 1。供 CI / 提交前钩子使用。"""
    if not os.path.isfile(schema_path):
        print(f"❌ 找不到 {schema_path}", file=sys.stderr)
        return 2

    with open(schema_path, encoding="utf-8") as f:
        text = f.read()
    file_cols = parse_schema_columns(text)
    if not file_cols:
        print(f"❌ {schema_path} 里没解析出任何 CREATE TABLE，文件可能损坏", file=sys.stderr)
        return 2

    live_cols = db_columns(conn, db)
    # 备份表是**有意**排除在 schema.sql 之外的，比对时必须同样排除，否则每次都是「多了表」
    live_cols = {t: c for t, c in live_cols.items() if not any(p.search(t) for p in EXCLUDE)}

    only_live = sorted(set(live_cols) - set(file_cols))
    only_file = sorted(set(file_cols) - set(live_cols))
    common = sorted(set(live_cols) & set(file_cols))

    col_drift = []
    for t in common:
        a, b = set(file_cols[t]), set(live_cols[t])
        added, removed = sorted(b - a), sorted(a - b)
        if added or removed:
            col_drift.append((t, added, removed))

    drift = bool(only_live or only_file or col_drift)

    print(f"schema.sql : {len(file_cols)} 张表  （{schema_path}）")
    print(f"当前库     : {len(live_cols)} 张表  （{db}，已排除备份表）")
    print()

    if not drift:
        print("✅ 无漂移：schema.sql 与当前库一致。")
        return 0

    print("❌ 检测到漂移（schema.sql 需要重新导出）：")
    if only_live:
        print(f"\n  ▸ 库里多出 {len(only_live)} 张表 —— 新表**没有**被写进 schema.sql，"
              f"新机器重建时会缺表：")
        for t in only_live:
            print(f"      + {t}")
    if only_file:
        print(f"\n  ▸ schema.sql 里有 {len(only_file)} 张表在库中不存在（可能已删）：")
        for t in only_file:
            print(f"      - {t}")
    if col_drift:
        print(f"\n  ▸ {len(col_drift)} 张表字段不一致：")
        for t, added, removed in col_drift:
            bits = []
            if added:
                bits.append("库中多出 " + ", ".join(added))
            if removed:
                bits.append("schema 多出 " + ", ".join(removed))
            print(f"      ~ {t}: " + "；".join(bits))

    print("\n修法：确认这些变化是有意的之后，重新导出并提交：")
    print("    python scripts/dump_schema.py")
    return 1


def main():
    # 保证 print 中文在任何终端都不炸（Windows GBK 控制台尤其需要）
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    here = os.path.dirname(os.path.abspath(__file__))          # backend/scripts
    default_out = os.path.join(os.path.dirname(here), "..", "scripts", "schema.sql")
    default_out = os.path.normpath(default_out)                # 仓库根/scripts/schema.sql

    ap = argparse.ArgumentParser(description="导出当前库完整表结构为 schema.sql")
    ap.add_argument("-o", "--out", default=default_out, help="输出文件路径")
    ap.add_argument(
        "--check", action="store_true",
        help="只比对不写入：检查 schema.sql 相对当前库是否有漂移（有漂移退出码 1）",
    )
    args = ap.parse_args()

    conn = get_conn()
    db = os.getenv("INFO_DATA_DB_NAME", "adata")
    host = os.getenv("INFO_DATA_DB_HOST", "127.0.0.1")
    port = os.getenv("INFO_DATA_DB_PORT", "3306")

    # --check：只读比对，绝不写文件
    if args.check:
        try:
            return run_check(conn, db, args.out)
        finally:
            conn.close()

    all_tabs = list_tables(conn, db)
    keep, skipped = [], []
    for t in all_tabs:
        (skipped if any(p.search(t) for p in EXCLUDE) else keep).append(t)

    chunks = [
        HEADER.format(
            ts=datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            db=db, host=host, port=port,
            n=len(keep), skipped=len(skipped),
        )
    ]
    for i, t in enumerate(keep, 1):
        chunks.append(f"-- ---------- [{i}/{len(keep)}] {t} ----------\n")
        chunks.append(normalize(show_create(conn, t)))
        chunks.append("\n")

    chunks.append(
        "-- ============================================================================\n"
        f"-- 完成：共 {len(keep)} 张表。\n"
        "-- 下一步：跑 seed 脚本灌入配置，再执行采集器回补业务数据。\n"
        "--   详见 scripts/data-bootstrap.md\n"
        "-- ============================================================================\n"
    )
    conn.close()

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8", newline="\n") as f:
        f.write("".join(chunks))

    size = os.path.getsize(args.out)
    print(f"✅ 已导出 {len(keep)} 张表 → {args.out}")
    print(f"   文件大小 = {size/1024:.1f} KB")
    if skipped:
        print(f"   已排除 {len(skipped)} 张备份表：{skipped}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
