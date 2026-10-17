#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
环境自检（preflight）—— 把「换机器后静默错配」变成「启动时一句人话」。

为什么需要（2026-10-17 可移植性审计）
================================================================================
换一台机器部署时，有一类问题**不会报错**，只会悄悄给出错误结果：

  * 服务器时区是 UTC（云主机默认），而 cron 与 `CURDATE()` 按本地时间设计
    ⇒ 班次偏移 8 小时、水位线判定错一天，采集「跑成功」但少采一天。
  * `task_config` 是空表（忘了跑 seed）⇒ 调度器 `sync_from_db()` 排出 0 个任务，
    日志里只有一行「计划 0 个任务」，服务看起来完全正常。
  * MySQL 是 MariaDB / 5.7 ⇒ 建表时才会炸，且报错信息指向 `utf8mb4_0900_ai_ci`，
    不查文档很难联想到「版本不对」。
  * 配了通知渠道却漏了 token ⇒ 永不推送，也不报错。

本脚本把这些检查固化成可执行代码，既可单跑（部署后验收），
也可在服务启动时以 advisory 模式跑（默认只告警、不阻断）。

用法
================================================================================
两种入口，同一份实现（本模块是「库」，`scripts/preflight.py` 只是转发壳）：

    cd backend
    python scripts/preflight.py                 # 人读输出，全过退出码 0，否则 1
    python -m app.preflight                     # 等价写法
    python scripts/preflight.py --json          # 机器可读（CI 用）
    python scripts/preflight.py --skip-seed     # 跳过 seed 完整性检查
    python scripts/preflight.py --quiet         # 只输出失败/警告项

服务启动时自动以 **advisory 模式**跑（见 `advisory()`）：只打日志、不阻断，
避免「数据库一时连不上 ⇒ 后端直接起不来」这种更糟的结局。
需要「不过就不许启动」时设 `INFO_DATA_PREFLIGHT=strict`。
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sys
from pathlib import Path

MIN_MYSQL = (8, 0, 0)
MIN_PYTHON = (3, 10)

# 时区偏差多少秒算「不一致」。允许 120s 是为了容忍时钟漂移/NTP 未同步，
# 但 8 小时的时区差一定会被抓到。
TZ_TOLERANCE_SEC = 120


class Result:
    """一条检查结果。level: ok / warn / fail"""

    __slots__ = ("name", "level", "detail", "fix")

    def __init__(self, name: str, level: str, detail: str, fix: str = "") -> None:
        self.name, self.level, self.detail, self.fix = name, level, detail, fix

    def to_dict(self) -> dict:
        return {"name": self.name, "level": self.level, "detail": self.detail, "fix": self.fix}


# ---------------------------------------------------------------------------
# 检查 1：Python 版本
# ---------------------------------------------------------------------------
def check_python() -> Result:
    v = sys.version_info
    cur = (v.major, v.minor)
    got = f"{v.major}.{v.minor}.{v.micro}"
    if cur >= MIN_PYTHON:
        return Result("Python 版本", "ok", f"{got}（要求 ≥ {MIN_PYTHON[0]}.{MIN_PYTHON[1]}）")
    return Result(
        "Python 版本", "fail", f"{got} 过低",
        f"安装 Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+（建议 3.12），或设 INFO_DATA_PYTHON 指向新解释器",
    )


# ---------------------------------------------------------------------------
# 检查 2：MySQL 版本
# ---------------------------------------------------------------------------
def check_mysql_version(cur) -> Result:
    cur.execute("SELECT VERSION()")
    raw = cur.fetchone()[0] or ""
    nums = re.findall(r"\d+", raw)[:3]
    if len(nums) < 2:
        return Result("MySQL 版本", "warn", f"无法解析版本号：{raw!r}", "确认数据库类型与版本")
    triple = tuple(int(n) for n in nums) + (0,) * (3 - len(nums))
    if triple >= MIN_MYSQL:
        return Result("MySQL 版本", "ok", f"{raw}（要求 ≥ 8.0）")
    return Result(
        "MySQL 版本", "fail", f"{raw} 过低",
        "schema.sql 使用 utf8mb4_0900_ai_ci（MySQL 8 专有）。"
        "升级到 MySQL 8.0+；MariaDB 与 MySQL 5.7 均不支持。",
    )


# ---------------------------------------------------------------------------
# 检查 3：时区一致性（最容易静默出错的一项）
# ---------------------------------------------------------------------------
def check_timezone(cur) -> Result:
    cur.execute("SELECT NOW(), @@session.time_zone, @@global.time_zone")
    db_now, sess_tz, glob_tz = cur.fetchone()
    local_now = datetime.datetime.now()
    drift = abs((db_now - local_now).total_seconds())

    if drift <= TZ_TOLERANCE_SEC:
        return Result(
            "时区一致性", "ok",
            f"数据库与宿主一致（偏差 {drift:.0f}s，session={sess_tz} global={glob_tz}）",
        )
    hours = drift / 3600
    return Result(
        "时区一致性", "fail",
        f"数据库 NOW()={db_now} 与本机 {local_now} 相差 {hours:.1f} 小时"
        f"（session={sess_tz} global={glob_tz}）",
        "定时任务（cron）与采集水位线（CURDATE()）均按本地时间设计，"
        "时区不一致会导致「跑成功但少采一天」。修法（任选）：\n"
        "    · 启动 MySQL 时加 --default-time-zone=+08:00\n"
        "    · 或 my.cnf 里 default-time-zone='+08:00'\n"
        "    · 容器部署：compose 已设 TZ=Asia/Shanghai 与 --default-time-zone=+08:00",
    )


# ---------------------------------------------------------------------------
# 检查 4：schema.sql 里的表是否齐全
# ---------------------------------------------------------------------------
def parse_schema_tables(schema_path: Path) -> set[str]:
    if not schema_path.is_file():
        return set()
    text = schema_path.read_text(encoding="utf-8", errors="replace")
    names = set()
    for m in re.finditer(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?`([A-Za-z0-9_]+)`", text, re.I):
        names.add(m.group(1))
    return names


def check_tables(cur, expected: set[str]) -> Result:
    if not expected:
        return Result("表结构完整性", "warn", "未找到 scripts/schema.sql，无法比对", "确认仓库完整")
    cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema=DATABASE()")
    have = {r[0] for r in cur.fetchall()}
    missing = sorted(expected - have)
    if not missing:
        return Result("表结构完整性", "ok", f"{len(expected)} 张表齐全")
    preview = "、".join(missing[:6]) + (" …" if len(missing) > 6 else "")
    return Result(
        "表结构完整性", "fail", f"缺 {len(missing)} 张表：{preview}",
        "导入建表脚本：mysql -u root -p < scripts/schema.sql（幂等，可重复执行）",
    )


# ---------------------------------------------------------------------------
# 检查 5：seed 是否灌过（空表会静默导致「0 个任务」）
# ---------------------------------------------------------------------------
SEED_EXPECT = [
    ("task_config", "定时任务配置", "python scripts/seed_task_config.py --apply"),
    ("table_meta", "表元数据", "python scripts/seed_table_meta.py"),
    ("dq_rules", "数据质量规则", "python scripts/seed_dq_rules.py"),
]


def check_seeds(cur) -> list[Result]:
    out = []
    for table, label, fix in SEED_EXPECT:
        try:
            cur.execute(f"SELECT COUNT(*) FROM `{table}`")
            n = cur.fetchone()[0]
        except Exception as exc:                      # 表不存在
            out.append(Result(f"{label}（{table}）", "fail", f"查询失败：{exc}", fix))
            continue
        if n == 0:
            out.append(Result(
                f"{label}（{table}）", "fail", "表存在但 0 行",
                f"跑 seed：{fix}" + (
                    "　⚠️ 该脚本不加 --apply 只做只读预览，**照样打印成功**"
                    if "--apply" in fix else ""
                ),
            ))
        else:
            out.append(Result(f"{label}（{table}）", "ok", f"{n} 行"))
    return out


# ---------------------------------------------------------------------------
# 检查 6：通知渠道配置自洽（配了 provider 却没 token ⇒ 永不推送）
# ---------------------------------------------------------------------------
NOTIFY_REQUIRED = {
    "wecom": ["INFO_DATA_WECOM_WEBHOOK"],
    "serverchan": ["INFO_DATA_SERVERCHAN_KEY"],
    "pushplus": ["INFO_DATA_PUSHPLUS_TOKEN"],
    "wxpusher": ["INFO_DATA_WXPUSHER_TOKEN", "INFO_DATA_WXPUSHER_UID"],
}


def check_notify() -> Result:
    provider = (os.getenv("INFO_DATA_NOTIFY_PROVIDER") or "none").strip().lower()
    if provider in ("", "none"):
        return Result("通知渠道", "ok", "未启用（provider=none）")
    if provider not in NOTIFY_REQUIRED:
        return Result(
            "通知渠道", "fail", f"未知 provider：{provider!r}",
            f"取值只能是 {' / '.join(NOTIFY_REQUIRED)} / none",
        )
    missing = [k for k in NOTIFY_REQUIRED[provider] if not (os.getenv(k) or "").strip()]
    if missing:
        return Result(
            "通知渠道", "fail", f"provider={provider} 但缺少 {', '.join(missing)}",
            "补上对应凭证；否则异动告警**永远不会推送**且不报错",
        )
    return Result("通知渠道", "ok", f"provider={provider}，凭证齐全")


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def run_checks(*, skip_seed: bool = False, skip_db: bool = False) -> list[Result]:
    results = [check_python()]

    if skip_db:
        return results

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from app.db import get_db_config  # noqa: PLC0415
    import pymysql  # noqa: PLC0415

    cfg = get_db_config()
    try:
        conn = pymysql.connect(**cfg.to_dict(), connect_timeout=6)
    except Exception as exc:
        results.append(Result(
            "数据库连通性", "fail",
            f"连不上 {cfg.user}@{cfg.host}:{cfg.port}/{cfg.database} —— {exc}",
            "① 确认 MySQL 已启动；② 确认 .env / 环境变量里的 5 个 INFO_DATA_DB_* 正确；"
            "③ 确认库已创建（CREATE DATABASE adata）",
        ))
        return results

    results.append(Result(
        "数据库连通性", "ok",
        f"{cfg.user}@{cfg.host}:{cfg.port}/{cfg.database}",
    ))

    try:
        cur = conn.cursor()
        results.append(check_mysql_version(cur))
        results.append(check_timezone(cur))

        schema = Path(__file__).resolve().parent.parent.parent / "scripts" / "schema.sql"
        results.append(check_tables(cur, parse_schema_tables(schema)))

        if not skip_seed:
            results.extend(check_seeds(cur))
    finally:
        conn.close()

    results.append(check_notify())
    return results


_COLOR = {"ok": "\033[32m✅\033[0m", "warn": "\033[33m⚠️ \033[0m", "fail": "\033[31m❌\033[0m"}


def advisory(logger) -> bool:
    """服务启动时调用：跑一遍检查，结果写进日志。返回 False 表示「不该继续启动」。

    默认 **advisory（只告警、不阻断）** —— 这是有意的取舍：
    若把 fail 一律当成「拒绝启动」，那么「数据库重启中」这类瞬时故障会让后端
    永远起不来、调度器也不在，反而失去自动恢复的机会。
    需要强约束时设 `INFO_DATA_PREFLIGHT=strict`，任一 fail 即返回 False。
    """
    try:
        results = run_checks()
    except Exception as exc:                      # 自检自身异常绝不能拖垮启动
        logger.warning(f"环境自检执行失败（已忽略，不影响启动）：{exc}")
        return True

    fails = [r for r in results if r.level == "fail"]
    warns = [r for r in results if r.level == "warn"]

    for r in results:
        if r.level == "ok":
            continue
        icon = "❌" if r.level == "fail" else "⚠️"
        logger.warning(f"{icon} 自检·{r.name}：{r.detail}")
        for line in (r.fix or "").splitlines():
            logger.warning(f"      ↳ {line}")

    if fails:
        strict = os.getenv("INFO_DATA_PREFLIGHT", "").strip().lower() in ("strict", "1", "true")
        if strict:
            logger.error(
                f"环境自检 {len(fails)} 项不通过，且 INFO_DATA_PREFLIGHT=strict —— 拒绝启动"
            )
            return False
        logger.warning(
            f"环境自检 {len(fails)} 项不通过 —— 服务仍会启动，但相关功能可能异常。"
            f"（设 INFO_DATA_PREFLIGHT=strict 可改为拒绝启动；"
            f"单独复跑：python scripts/preflight.py）"
        )
        return True

    logger.info(f"🩺 环境自检通过（{len(results)} 项，{len(warns)} 项告警）")
    return True


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    ap = argparse.ArgumentParser(description="InvestBuddy 环境自检")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--skip-seed", action="store_true", help="跳过 seed 完整性检查")
    ap.add_argument("--skip-db", action="store_true", help="只查 Python 版本，不连库")
    ap.add_argument("--quiet", action="store_true", help="只显示 warn / fail")
    args = ap.parse_args()

    results = run_checks(skip_seed=args.skip_seed, skip_db=args.skip_db)
    fails = [r for r in results if r.level == "fail"]
    warns = [r for r in results if r.level == "warn"]

    if args.json:
        print(json.dumps(
            {"ok": not fails, "fail": len(fails), "warn": len(warns),
             "results": [r.to_dict() for r in results]},
            ensure_ascii=False, indent=2,
        ))
        return 1 if fails else 0

    show_color = sys.stdout.isatty()
    for r in results:
        if args.quiet and r.level == "ok":
            continue
        tag = _COLOR[r.level] if show_color else {"ok": "[OK]  ", "warn": "[WARN]", "fail": "[FAIL]"}[r.level]
        print(f"{tag} {r.name}：{r.detail}")
        if r.fix and r.level != "ok":
            for line in r.fix.splitlines():
                print(f"       ↳ {line}")

    print()
    if fails:
        print(f"结论：{len(fails)} 项不通过、{len(warns)} 项警告 —— 环境不可用，请按上面的修法处理后重跑。")
        return 1
    if warns:
        print(f"结论：全部通过（{len(warns)} 项警告）。")
        return 0
    print("结论：全部通过 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
