#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
高成本 / 不可复原资产冷备（灾备 B0 · 格式中立冷备）

为什么单独做一套，而不复用 scripts/schema.sql + migrate-machine.bat
---------------------------------------------------------------
`migrate-machine.bat` 是**整库 dump**，适合换机；但它有两个结构性缺陷，不能作为灾备：

  1. **绑定 MySQL**：dump 是 SQL 文本，目标机必须 MySQL 8.0+（`.sql` 里带
     `utf8mb4_0900_ai_ci`），且要能重建索引。二十年后还读得出来吗？不一定。
  2. **只解决「有备份」，不解决「哪些必须备份」**：整库 9.7 GB 每天备不现实，
     多数人会因为「太慢」而最终不备。而真正「丢了就很贵」的只有 **≈3.5 GB**，
     且其中大多数是**冻结表**（历史导入后不再变），**备份一次 + 定期校验**即可。

本脚本把数据按「可不可再生」分成三个包，格式是 **TSV + gzip**（无 Schema 耦合、
Excel/awk/pandas 都能直接读），另附 `_schema.sql` 与 `manifest.json`（含逐表 DDL、
行数、sha256），保证既不依赖 MySQL 版本、也能原样还原。

三个包
------
  core    : 高成本 / 不可复原业务大表（≈3.4 GB → gz **545.6 MB**，2026-10-17 实测）
            无采集器、或上游只给滚动窗口、或上游会修正历史。
            ⚠️ **不等于「物理不可再生」**：其中 ex / hold_by_fund / 概念行情 三张
            经 2026-10-17 实测可用带时间参数的接口重建（详见 scripts/data-restore-plan.md §2 L3），
            真正的硬缺口只有 stock_capital_flow 的中间段。
  small   : 人工资产 + 配置（< 10 MB）
            台账/人工标注/偏好/人工判定/配置。体积小到可以进私有 git，
            天然带版本历史（能 diff、能回滚），比 dump 更适合。
  archive : *_bak_* 手工快照（≈1.65 GB，默认不导）
            本质是误操作保护，不是备份；要导需显式指定。

为什么冻结表只需要备一次
------------------------
`stock_market_daily_ex` / `stock_hold_by_fund` 的 `table_meta.source_desc = '历史导入'`、
`writers = []` —— **全项目没有任何采集器能写它们**，即数据不再变化。
`verify` 子命令因此只做两件事，秒级~分钟级：
  · 文件 sha256 是否仍等于导出值（查「备份本身有没有坏」）
  · 库内 COUNT(*) 是否仍等于导出值（查「源表有没有被人改动」）

⚠️ 2026-10-17 实测修正：这两张表**并非「物理不可再生」**，此前措辞过强
（详见 scripts/data-restore-plan.md §2 L3）：
  · `stock_market_daily_ex` 就是标准不复权日K —— 2024-06-28 单日 5,330 只中
    5,096 只 close 与主表**完全一致（0 差异）**，新浪 `stock_zh_a_daily` 按区间可拉；
    真正独有仅 **234 只 430xxx 老三板**。
  · `stock_hold_by_fund` 可用 `stock_report_fund_hold(date)` 逐期重建（2015-12-31 已验证），
    但**上游修正过历史数值** ⇒ 能重建内容、不能复原原版。

所以「备一次」的理由从「数据会永久消失」改述为三条：
**重建成本高（1690 万行需重拉 + 新写采集器）＋ 尾巴补不齐（老三板 234 只）
＋ 原版不可复原（上游改过历史）**。备份依然是更划算的选择，只是理由变了。

用法（在 backend 目录下执行）
----------------------------
  python scripts/backup_assets.py export --pack all
  python scripts/backup_assets.py export --pack small --out D:\\backup\\x
  python scripts/backup_assets.py verify                       # 校验最新一份
  python scripts/backup_assets.py verify --dir <导出目录>
  python scripts/backup_assets.py restore --dir <导出目录> --db adata_drill          # 只看计划
  python scripts/backup_assets.py restore --dir <导出目录> --db adata_drill --apply  # 真写入

⚠️ 落点纪律
-----------
本脚本**只负责生成**，不判断落点好坏。生成后请手动拷到**另一块物理盘 / 网盘 /
对象存储 / 移动硬盘**。放在同一个 `D:` 盘（包括 `D:\\backup`）**不算异地备份** ——
盘毁时数据和备份一起没。参见 docs/design/ 与 scripts/data-bootstrap.md 的灾备章节。
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import gzip
import hashlib
import io
import json
import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # backend 根
from app.db import get_db_config  # noqa: E402
import pymysql  # noqa: E402
from pymysql.cursors import SSCursor  # noqa: E402

# ---------------------------------------------------------------------------
# 备份清单（依据 table_meta.writers + 上游窗口实测，见 scripts/data-bootstrap.md §2）
# ---------------------------------------------------------------------------

CORE_TABLES: list[tuple[str, str]] = [
    ("stock_market_daily_ex", "扩展行情；无采集器，但**≠不可再生**：2026-10-17 实测即标准不复权日K，"
                              "单日 5,096/5,330 只 close 与主表全等、新浪按区间可拉；独有仅 234 只 430xxx"),
    ("stock_capital_flow", "资金流；东财硬限 120 条且拒绝 lmt>120（2026-10-17 实测 curl_exit=56）"
                           "⇒ 2023-08~2026-04 中间段**永久不可得**，全库唯一真缺口"),
    ("ths_concept_market", "概念行情；采集器 days_back=15，但**可重建**：同源 "
                           "stock_board_concept_index_ths 带区间、数值逐字段吻合（2026-10-17 实测）"),
    ("stock_hold_by_fund", "基金持仓；无采集器，但**可逐期重建**（stock_report_fund_hold 带报告期，"
                           "2015 已验证）；上游修正过历史 ⇒ 原版不可复原"),
    ("news", "资讯；上游快讯接口只吐最新列表 ⇒ **历史确实不可回溯**（库内亦仅 44 天）"),
]

SMALL_TABLES: list[tuple[str, str]] = [
    ("stock_code_mapping", "北交所旧→新代码台账，含**人工逐条核证 evidence**（人工资产）"),
    ("stock_info_ex", "名册影子表，承载人工 is_gxlstock 高股息标记（人工资产）"),
    ("user_prefs", "用户偏好（人工资产）"),
    ("dq_gap_detail", "缺口明细，含人工 status='ignored' 的判定（人工资产）"),
    ("stock_info", "股票名册（含 list_date，可重采但恢复期离不开它）"),
    ("dq_rules", "DQ 规则 209 条（配置，seed 之外的现场改动会丢）"),
    ("table_meta", "表元数据 59 条（同上）"),
    ("task_config", "任务配置 49 条（同上）"),
]

ARCHIVE_TABLES: list[tuple[str, str]] = [
    ("stock_market_daily_bak_20260920", "手工快照（误操作保护，非备份）"),
]

PACKS: dict[str, list[tuple[str, str]]] = {
    "core": CORE_TABLES,
    "small": SMALL_TABLES,
    "archive": ARCHIVE_TABLES,
    "all": CORE_TABLES + SMALL_TABLES,
}

NULL_TOKEN = "\\N"          # 与 MySQL LOAD DATA 默认一致
BATCH = 2000                # restore 单批行数
DEFAULT_BASE = r"D:\backup\InfoData\assets"


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------

def _conn(db: str | None = None, stream: bool = False):
    cfg = get_db_config().to_dict()
    if db:
        cfg["database"] = db
    cfg.pop("cursorclass", None)
    if stream:
        cfg["cursorclass"] = SSCursor
        # 流式游标必须逐行取，不能预取整个结果集
    return pymysql.connect(**cfg)


def _cell(v):
    """单值 → 文本。None 用 \\N（与 MySQL 一致），bytes 用 hex，时间用 ISO。"""
    if v is None:
        return NULL_TOKEN
    if isinstance(v, (bytes, bytearray)):
        return v.hex()
    if isinstance(v, dt.datetime):
        return v.strftime("%Y-%m-%d %H:%M:%S.%f") if v.microsecond else v.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(v, dt.date):
        return v.strftime("%Y-%m-%d")
    if isinstance(v, dt.timedelta):
        return str(v)
    if isinstance(v, Decimal):
        return format(v, "f")
    return str(v)


def _parse(v: str):
    """文本 → 值（\\N → None）。类型转换交给 MySQL，这里只还原 NULL。"""
    return None if v == NULL_TOKEN else v


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024.0
    return f"{n:.1f} GB"


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------

def export_table(table: str, note: str, out_dir: str) -> dict | None:
    path = os.path.join(out_dir, f"{table}.tsv.gz")
    conn = _conn(stream=True)
    try:
        cur = conn.cursor()
        cur.execute(f"SELECT COUNT(*) FROM `{table}`")
        expected = cur.fetchone()[0]
        cur.execute(f"SHOW CREATE TABLE `{table}`")
        create_sql = cur.fetchone()[1]

        cur.execute(f"SELECT * FROM `{table}`")
        cols = [d[0] for d in cur.description]
        written = 0
        t0 = dt.datetime.now()
        with gzip.open(path, "wt", encoding="utf-8", newline="", compresslevel=6) as gz:
            w = csv.writer(gz, delimiter="\t", quotechar='"',
                           quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
            w.writerow(cols)
            while True:
                batch = cur.fetchmany(10000)
                if not batch:
                    break
                for row in batch:
                    w.writerow([_cell(v) for v in row])
                written += len(batch)
                if written % 1000000 < 10000:
                    el = (dt.datetime.now() - t0).total_seconds()
                    print(f"    … {written:,}/{expected:,} 行  {el:.0f}s", flush=True)
    finally:
        conn.close()

    sha = _sha256(path)
    size = os.path.getsize(path)
    status = "OK" if written == expected else "!! 行数不符"
    print(f"  [{status}] {table:30s} {written:>10,} 行  {_human(size):>9s}  sha256={sha[:12]}…")
    if written != expected:
        print(f"       ⚠️ 预期 {expected:,} 行，实际写出 {written:,} 行（导出期间表可能被并发写入）")
    return {
        "rows": written,
        "rows_at_count": expected,
        "file": os.path.basename(path),
        "bytes_gz": size,
        "sha256": sha,
        "columns": cols,
        "create_sql": create_sql,
        "note": note,
    }


def cmd_export(args) -> int:
    if args.pack == "list":
        for name, tables in PACKS.items():
            print(f"\n[{name}] {len(tables)} 张")
            for t, note in tables:
                print(f"  {t:32s} {note}")
        return 0

    tables = PACKS.get(args.pack)
    if not tables:
        print(f"未知 pack: {args.pack}（可选 {list(PACKS)} 或 list）")
        return 2

    ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = args.out or os.path.join(DEFAULT_BASE, ts)
    os.makedirs(out_dir, exist_ok=True)

    cfg = get_db_config().to_dict()
    print(f"导出包     : {args.pack}（{len(tables)} 张表）")
    print(f"源库       : {cfg.get('user')}@{cfg.get('host')}:{cfg.get('port')}/{cfg.get('database')}")
    print(f"目标目录   : {out_dir}")

    conn = _conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT VERSION()")
        mysql_ver = cur.fetchone()[0]
    finally:
        conn.close()

    manifest = {
        "format": "investbuddy-asset-backup/tsv-gz-v1",
        "exported_at": dt.datetime.now().isoformat(timespec="seconds"),
        "pack": args.pack,
        "source": {
            "db": cfg.get("database"),
            "host": cfg.get("host"),
            "port": cfg.get("port"),
            "mysql_version": mysql_ver,
        },
        "null_token": NULL_TOKEN,
        "delimiter": "\\t",
        "encoding": "utf-8",
        "tables": {},
    }

    sql_lines = [
        "-- InvestBuddy 不可再生资产冷备 · 表结构（与同名 .tsv.gz 配套）",
        f"-- 导出时间: {manifest['exported_at']}   源库: {manifest['source']['db']} (MySQL {mysql_ver})",
        "-- 还原: 先执行本文件建表，再用 backup_assets.py restore 灌数据",
        "",
    ]

    failed: list[str] = []
    for table, note in tables:
        try:
            info = export_table(table, note, out_dir)
            if info:
                manifest["tables"][table] = info
                sql_lines.append(f"-- {table}: {note}")
                sql_lines.append(info["create_sql"].rstrip(";") + ";")
                sql_lines.append("")
        except Exception as e:                      # noqa: BLE001 - 单表失败不中断整包
            print(f"  [FAIL] {table}: {type(e).__name__}: {e}")
            failed.append(table)

    with open(os.path.join(out_dir, "_schema.sql"), "w", encoding="utf-8") as f:
        f.write("\n".join(sql_lines))
    with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    sums_path = os.path.join(out_dir, "SHA256SUMS")
    with open(sums_path, "w", encoding="utf-8") as f:
        for t, info in manifest["tables"].items():
            f.write(f"{info['sha256']}  {info['file']}\n")

    total_rows = sum(i["rows"] for i in manifest["tables"].values())
    total_gz = sum(i["bytes_gz"] for i in manifest["tables"].values())
    print("\n" + "=" * 68)
    print(f"完成：{len(manifest['tables'])} 张表 / {total_rows:,} 行 / gz 合计 {_human(total_gz)}")
    print(f"目录：{out_dir}")
    if failed:
        print(f"🔴 失败 {len(failed)} 张：{', '.join(failed)}")
    print("⚠️ 下一步：把整个目录拷到**另一块盘 / 网盘 / 对象存储**，同 D 盘不算异地")
    return 1 if failed else 0


# ---------------------------------------------------------------------------
# verify
# ---------------------------------------------------------------------------

def _latest_dir() -> str | None:
    if not os.path.isdir(DEFAULT_BASE):
        return None
    subs = [os.path.join(DEFAULT_BASE, d) for d in os.listdir(DEFAULT_BASE)
            if os.path.isdir(os.path.join(DEFAULT_BASE, d))]
    return max(subs) if subs else None


def cmd_verify(args) -> int:
    d = args.dir or _latest_dir()
    if not d:
        print("找不到导出目录，请用 --dir 指定")
        return 2
    mp = os.path.join(d, "manifest.json")
    if not os.path.exists(mp):
        print(f"{d} 下没有 manifest.json，不是本脚本产物")
        return 2
    with open(mp, encoding="utf-8") as f:
        manifest = json.load(f)

    print(f"校验目录 : {d}")
    print(f"导出时间 : {manifest.get('exported_at')}   源库: {manifest.get('source', {}).get('db')}")
    print("-" * 78)

    conn = _conn()
    bad = 0
    try:
        cur = conn.cursor()
        for table, info in manifest["tables"].items():
            problems: list[str] = []
            # ① 备份文件本身有没有坏
            fp = os.path.join(d, info["file"])
            if not os.path.exists(fp):
                problems.append("文件缺失")
            else:
                sha = _sha256(fp)
                if sha != info["sha256"]:
                    problems.append("sha256 不符（备份已损坏）")
                if os.path.getsize(fp) != info["bytes_gz"]:
                    problems.append("大小不符")
            # ② 源表有没有被改动
            try:
                cur.execute(f"SELECT COUNT(*) FROM `{table}`")
                now_rows = cur.fetchone()[0]
                if now_rows != info["rows"]:
                    problems.append(f"源表行数漂移 {info['rows']:,}→{now_rows:,}")
                cur.execute(f"SHOW COLUMNS FROM `{table}`")
                now_cols = [r[0] for r in cur.fetchall()]
                if now_cols != info["columns"]:
                    problems.append("源表列结构已变化")
            except Exception as e:                  # noqa: BLE001
                problems.append(f"源表不可查（{type(e).__name__}）")

            if problems:
                bad += 1
                print(f"  [DRIFT] {table:30s} " + "；".join(problems))
            else:
                print(f"  [OK]    {table:30s} {info['rows']:>10,} 行，文件与源表均未变")
    finally:
        conn.close()

    print("-" * 78)
    if bad:
        print(f"🟠 {bad} 张表需要处理（冻结表漂移通常意味着有人改过数据；请重新导出）")
    else:
        print("✅ 全部一致：备份文件未损坏，源表未漂移")
    return 1 if bad else 0


# ---------------------------------------------------------------------------
# restore
# ---------------------------------------------------------------------------

def cmd_restore(args) -> int:
    d = args.dir or _latest_dir()
    if not d:
        print("找不到导出目录，请用 --dir 指定")
        return 2
    with open(os.path.join(d, "manifest.json"), encoding="utf-8") as f:
        manifest = json.load(f)

    target = args.db
    print(f"源目录 : {d}")
    print(f"目标库 : {target}")
    print(f"表清单 : {', '.join(manifest['tables'])}")
    if not args.apply:
        print("\n（预览模式，未写入任何东西；加 --apply 才执行）")
        print("注意：本操作会 DELETE 目标库同名表的全部行，然后灌入备份数据。")
        return 0

    # 目标库不存在就建（演练场景常见：adata_drill 是新的）
    base = _conn()
    try:
        with base.cursor() as cur:
            cur.execute(f"CREATE DATABASE IF NOT EXISTS `{target}` "
                        "DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci")
    finally:
        base.close()

    conn = _conn(db=target)
    total = 0
    try:
        cur = conn.cursor()
        for table, info in manifest["tables"].items():
            cur.execute(info["create_sql"].replace("CREATE TABLE", "CREATE TABLE IF NOT EXISTS", 1))
            cur.execute(f"DELETE FROM `{table}`")
            fp = os.path.join(d, info["file"])
            cols = info["columns"]
            ph = ",".join(["%s"] * len(cols))
            sql = f"INSERT INTO `{table}` (" + ",".join(f"`{c}`" for c in cols) + f") VALUES ({ph})"
            n = 0
            with gzip.open(fp, "rt", encoding="utf-8", newline="") as gz:
                r = csv.reader(gz, delimiter="\t", quotechar='"')
                header = next(r)
                if header != cols:
                    raise RuntimeError(f"{table} 表头与 manifest 不一致：{header} != {cols}")
                batch: list[tuple] = []
                for row in r:
                    batch.append(tuple(_parse(v) for v in row))
                    if len(batch) >= BATCH:
                        cur.executemany(sql, batch)
                        n += len(batch)
                        batch.clear()
                if batch:
                    cur.executemany(sql, batch)
                    n += len(batch)
            conn.commit()
            flag = "OK" if n == info["rows"] else "!! 行数不符"
            print(f"  [{flag}] {table:30s} 写入 {n:,} 行（备份 {info['rows']:,}）")
            total += n
    finally:
        conn.close()
    print(f"\n完成：{total:,} 行写入 {target}")
    return 0


# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="InvestBuddy 不可再生资产冷备（格式中立）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    e = sub.add_parser("export", help="导出")
    e.add_argument("--pack", default="all", help="core | small | archive | all | list")
    e.add_argument("--out", default=None, help=f"输出目录（默认 {DEFAULT_BASE}\\<时间戳>）")
    e.set_defaults(func=cmd_export)

    v = sub.add_parser("verify", help="校验（文件 sha256 + 源表行数/结构）")
    v.add_argument("--dir", default=None)
    v.set_defaults(func=cmd_verify)

    r = sub.add_parser("restore", help="还原到指定库")
    r.add_argument("--dir", default=None)
    r.add_argument("--db", required=True, help="目标库名（建议 adata_drill，别指向生产库）")
    r.add_argument("--apply", action="store_true", help="真写入（否则只预览）")
    r.set_defaults(func=cmd_restore)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
