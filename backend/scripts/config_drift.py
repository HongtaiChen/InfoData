#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""配置漂移检测（只读）—— 库内 task_config 与 seed_task_config.py 的差异。

为什么需要这个脚本（2026-10-17 立，可移植性标准 §6）
-----------------------------------------------------
项目纪律是「种子脚本是配置的唯一事实来源，直改 DB 必须回写脚本」，
但这条纪律**一直只是口号**：前端「作业监控」可以热改 cron / enabled / params，
命令行也能直接 UPDATE，而**没有任何机制能发现库里已经和脚本不一致**。

后果是可移植性层面的：换机时 restore.py 会跑 seed_task_config.py --apply，
库里的热改被**静默覆盖回脚本里的旧值** —— 排期悄悄变回去，不报错、不告警。

实测（2026-10-17）：库内 `capital_flow_sync.cron = '15 22 * * *'`，
而 seed 写的是 `手动` —— 已漂移 1 处且长期无人发现。

用法：
    python scripts/config_drift.py            # 人读清单
    python scripts/config_drift.py --json     # 机器可读（给 CI / 前端）
    python scripts/config_drift.py --strict   # 有漂移则退出码 1（默认就是 1，此参数为显式语义）

退出码：0 = 无漂移；1 = 有漂移；2 = 环境/脚本加载失败。
"""
import argparse
import importlib.util
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pymysql  # noqa: E402

from app.db import get_db_config  # noqa: E402

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEED = os.path.join(BACKEND, "scripts", "seed_task_config.py")


def load_seed() -> dict:
    """动态载入 seed 脚本并取其 TASKS 清单（不执行 main）。"""
    if not os.path.isfile(SEED):
        raise FileNotFoundError(SEED)
    spec = importlib.util.spec_from_file_location("_seed_task_config", SEED)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # 只跑模块级定义，main() 不会被执行
    return {
        t[0]: {"enabled": int(t[1]), "cron": t[2], "params": t[3] or {}}
        for t in mod.TASKS
    }


def load_db(conn) -> dict:
    out = {}
    with conn.cursor() as cur:
        cur.execute("SELECT task_name, enabled, cron, params FROM task_config")
        for name, enabled, cron, params in cur.fetchall():
            if isinstance(params, (bytes, str)):
                try:
                    params = json.loads(params)
                except Exception:
                    params = {}
            out[name] = {"enabled": int(enabled), "cron": cron, "params": params or {}}
    return out


def diff(seed: dict, db: dict) -> list[dict]:
    items = []
    for name, s in seed.items():
        d = db.get(name)
        if d is None:
            items.append({"task": name, "field": "exists", "seed": "已登记", "db": "库内缺失"})
            continue
        if s["enabled"] != d["enabled"]:
            items.append({"task": name, "field": "enabled", "seed": s["enabled"], "db": d["enabled"]})
        if (s["cron"] or "") != (d["cron"] or ""):
            items.append({"task": name, "field": "cron", "seed": s["cron"], "db": d["cron"]})
        if (s["params"] or {}) != (d["params"] or {}):
            items.append({
                "task": name, "field": "params",
                "seed": json.dumps(s["params"], ensure_ascii=False, sort_keys=True),
                "db": json.dumps(d["params"], ensure_ascii=False, sort_keys=True),
            })
    for name in db:
        if name not in seed:
            items.append({"task": name, "field": "exists", "seed": "未登记", "db": "库内有、脚本无"})
    return items


def main() -> int:
    ap = argparse.ArgumentParser(description="配置漂移检测（只读，不写库）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--strict", action="store_true", help="显式要求漂移时退出码 1（默认行为）")
    args = ap.parse_args()

    try:
        seed = load_seed()
        conn = pymysql.connect(**get_db_config().to_dict())
        try:
            db = load_db(conn)
        finally:
            conn.close()
    except Exception as exc:
        print(f"❌ 加载失败：{type(exc).__name__}: {exc}")
        return 2

    items = diff(seed, db)

    if args.json:
        print(json.dumps(
            {"seed_count": len(seed), "db_count": len(db), "drift_count": len(items), "items": items},
            ensure_ascii=False, indent=2,
        ))
        return 1 if items else 0

    print(f"seed 任务数：{len(seed)}   库内任务数：{len(db)}")
    if not items:
        print("✅ 无漂移：库内 task_config 与 seed_task_config.py 完全一致")
        return 0

    print(f"\n⚠️ 检测到 {len(items)} 处漂移（换机跑 seed 时库内值会被**静默覆盖**）：")
    print(f"  {'任务':32s} {'字段':8s} {'seed 脚本':28s} {'库内现状'}")
    for it in items:
        print(f"  {it['task']:32s} {it['field']:8s} {str(it['seed'])[:28]:28s} {it['db']}")
    print("\n处置（二选一，别放任）：")
    print("  · 库内值是对的 → 回写 seed_task_config.py，再跑一次本脚本确认零漂移")
    print("  · 库内值是误改 → python scripts/seed_task_config.py --apply 恢复成脚本值")
    return 1


if __name__ == "__main__":
    sys.exit(main())
