#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""table_meta 幽灵行对账与清理

背景（2026-09-25）
------------------
`table_meta` 是「数据中心」左侧表清单的唯一数据源，但它与真实库表之间
**没有外键、也没有校验**，历史上表被 DROP 后元数据行会留在原地——表现为
前端出现一张点不开、查不到的「幽灵表」。

已发现一例：`stock_market_daily_bak_20250802`（表已 DROP，元数据行仍在）。

用法
----
    python backend/scripts/clean_ghost_table_meta.py            # 只扫描，不改库
    python backend/scripts/clean_ghost_table_meta.py --apply     # 备份后删除幽灵行

安全约束
--------
- 默认 dry-run，必须显式 `--apply` 才写库；
- 写库前先把待删行完整导出到 `_scratch/table_meta_ghost_backup_<ts>.json`；
- 删除走独立连接 + **显式 commit**（`conn.autocommit=True` 在 pymysql 下实测不可信）；
- 复核**另起新连接**（同连接自检不是落库证据）。

退出码：0 = 已对齐（或无待清理项）；1 = 仍存在幽灵行（dry-run 发现差异）。
"""
import argparse
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db import get_db_config  # noqa: E402


def _conn():
    import pymysql

    # 独立连接：需要显式事务语义，不走 db.py 的线程长连接
    return pymysql.connect(**get_db_config().to_dict(), autocommit=False)


def scan() -> dict:
    """对账三组集合：元数据表名 / 真实库表名 / 幽灵行"""
    c = _conn()
    try:
        with c.cursor() as cur:
            # 以实际 schema 为准（2026-09-25 实测列序）：
            # table_name / category / source_desc / flow_desc / writers / writer_cols / note / update_time
            cur.execute("SELECT table_name, category, source_desc FROM table_meta ORDER BY table_name")
            meta_rows = [
                {"table_name": r[0], "category": r[1], "source_desc": r[2]}
                for r in cur.fetchall()
            ]
            cur.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = %s AND table_type = 'BASE TABLE'",
                (get_db_config().database,),
            )
            real_tables = {r[0] for r in cur.fetchall()}
    finally:
        c.close()

    meta_names = {r["table_name"] for r in meta_rows}
    ghosts = [r for r in meta_rows if r["table_name"] not in real_tables]
    missing = sorted(real_tables - meta_names)  # 有表无元数据（信息项，不自动补）
    return {
        "meta_count": len(meta_rows),
        "real_count": len(real_tables),
        "ghosts": ghosts,
        "missing_meta": missing,
    }


def apply(ghosts: list[dict]) -> int:
    """备份后删除幽灵行，返回实际删除行数"""
    if not ghosts:
        print("[apply] 无幽灵行，跳过")
        return 0

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    out_dir = os.path.join(root, "_scratch")
    os.makedirs(out_dir, exist_ok=True)
    bak = os.path.join(out_dir, f"table_meta_ghost_backup_{ts}.json")
    with open(bak, "w", encoding="utf-8") as f:
        json.dump(ghosts, f, ensure_ascii=False, indent=2)
    print(f"[apply] 已备份 {len(ghosts)} 行 → {bak}")

    names = [g["table_name"] for g in ghosts]
    c = _conn()
    try:
        with c.cursor() as cur:
            ph = ",".join(["%s"] * len(names))
            n = cur.execute(f"DELETE FROM table_meta WHERE table_name IN ({ph})", names)
        c.commit()  # 显式提交（pymysql autocommit 不可信，见 db.py 备注）
        print(f"[apply] DELETE 返回 rowcount={n}，已 commit")
    finally:
        c.close()
    return n


def verify(expected_gone: list[str]) -> bool:
    """新连接复核：幽灵行是否真的不在了"""
    c = _conn()
    try:
        with c.cursor() as cur:
            ph = ",".join(["%s"] * len(expected_gone))
            cur.execute(f"SELECT COUNT(*) FROM table_meta WHERE table_name IN ({ph})", expected_gone)
            return cur.fetchone()[0] == 0
    finally:
        c.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="备份后删除幽灵行（默认只扫描）")
    args = ap.parse_args()

    r = scan()
    print("=" * 62)
    print(f"table_meta 行数 : {r['meta_count']}")
    print(f"库内真实表数     : {r['real_count']}")
    print(f"幽灵行（有元数据无表）: {len(r['ghosts'])}")
    for g in r["ghosts"]:
        print(f"  ✗ {g['table_name']}  [{g['category']}] {(g['source_desc'] or '')[:50]}")
    print(f"缺元数据的表（信息项，不自动处理）: {len(r['missing_meta'])}")
    for m in r["missing_meta"]:
        print(f"  ! {m}")
    print("=" * 62)

    if not args.apply:
        return 1 if r["ghosts"] else 0

    names = [g["table_name"] for g in r["ghosts"]]
    apply(r["ghosts"])
    if names:
        ok = verify(names)
        print(f"[verify] 新连接复核：{'✅ 幽灵行已全部清除' if ok else '❌ 仍能查到，未生效'}")
        if not ok:
            return 1
    after = scan()
    print(f"[after] table_meta 行数 = {after['meta_count']}，幽灵 = {len(after['ghosts'])}")
    return 0 if not after["ghosts"] else 1


if __name__ == "__main__":
    sys.exit(main())
