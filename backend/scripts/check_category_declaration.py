#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""表分类「声明 ↔ 数据」一致性体检器（只读）—— 2026-09-25 漏表事故后固化。

背景（实测事故，2026-09-25）：
  数据中心左侧树的分组由**三处**共同决定，三者必须互相覆盖 ——
    ① 库 `table_meta.category`             数据事实（表属于哪个分类）
    ② 后端 `db_browser._ALLOWED_CATEGORIES` PATCH 白名单（能不能**改成**这个分类）
    ③ 前端 `DataView.vue` 的 `GROUPS[].categories` 渲染声明（渲染在哪个二级分类下）

  任一处漏项都会造成**静默**故障 —— 页面不报错、接口返回正常、数据也都在，
  只是树上少了几行 / 某些分类再也改不回去：
    · 库里有、前端 GROUPS 未声明 → 整类表行**完全不渲染**，而组头计数照算
      （实测：组头写「业务数据 38」，DOM 仅 28 行 ⇒ 10 张表凭空消失，
        含 global_usd_index_daily / cn_liquidity_monthly 等投资日历链路的关键表）
    · 库里有、后端白名单没有     → 表看得见，却**改不回**该分类（下拉里没有、API 直接 400）

  根因是「新增分类时忘记同步声明」这种低级但必然复发的错误，故固化成体检项。

用法：
    python scripts/check_category_declaration.py
退出码：
    0 = 三方一致；1 = 存在不一致（逐条给出修法）
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db import query_all  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
VIEW_VUE = os.path.join(ROOT, "frontend", "src", "views", "DataView.vue")

# 前端哨兵值（不是真分类，只是分组容器 key）
_SENTINELS = {"__bak__", "__未分类__"}


def _db_categories() -> dict[str, list[str]]:
    """库内 table_meta.category → 该分类下的表名清单"""
    rows = query_all(
        "SELECT table_name, category FROM table_meta ORDER BY table_name"
    )
    out: dict[str, list[str]] = {}
    for r in rows:
        c = (r["category"] or "").strip()
        out.setdefault(c, []).append(r["table_name"])
    return out


def _backend_allowed() -> set[str]:
    sys.path.insert(0, os.path.join(ROOT, "backend"))
    from app.api.db_browser import _ALLOWED_CATEGORIES  # noqa: PLC0415

    return set(_ALLOWED_CATEGORIES)


def _front_declared() -> set[str]:
    """从 DataView.vue 源码解析 GROUPS[].categories（正则抓取，不执行前端代码）"""
    src = open(VIEW_VUE, encoding="utf-8").read()
    out: set[str] = set()
    for m in re.finditer(r"categories:\s*\[(.*?)\]", src, re.S):
        out.update(re.findall(r"'([^']+)'", m.group(1)))
    return {c for c in out if c not in _SENTINELS}


def main() -> int:
    db = _db_categories()
    db_cats = {c for c in db if c}
    allowed = _backend_allowed()
    front = _front_declared()

    print("=" * 78)
    print("表分类一致性体检（声明 ↔ 数据）")
    print("=" * 78)
    print(f"① 库内实际分类 {len(db_cats)} 个：{sorted(db_cats)}")
    print(f"② 后端白名单   {len(allowed)} 个")
    print(f"③ 前端声明     {len(front)} 个")
    print()

    bad = 0

    only_db_front = db_cats - front
    if only_db_front:
        bad += 1
        print(f"🔴 库里有、前端 GROUPS **未声明** {len(only_db_front)} 个 —— 这些分类的表行不会渲染：")
        for c in sorted(only_db_front):
            print(f"     [{c}] × {len(db[c])} 张：{db[c]}")
        print("   修法：把这些分类加进 frontend/src/views/DataView.vue 的 GROUPS[].categories")
        print()

    only_db_allowed = db_cats - allowed
    if only_db_allowed:
        bad += 1
        print(f"🟠 库里有、后端白名单缺失 {len(only_db_allowed)} 个 —— 表看得见却改不回该分类：")
        for c in sorted(only_db_allowed):
            print(f"     [{c}] × {len(db[c])} 张")
        print("   修法：加进 backend/app/api/db_browser.py 的 _ALLOWED_CATEGORIES")
        print()

    only_front = front - db_cats
    if only_front:
        print(f"🟡 前端声明了、但库内暂无表在用 {len(only_front)} 个（无害，可能是预留）：{sorted(only_front)}")
        print()

    only_allowed = allowed - db_cats
    if only_allowed:
        print(f"🟡 后端白名单有、库内暂无表在用 {len(only_allowed)} 个：{sorted(only_allowed)}")
        print()

    # 未分类（category 为空/NULL）
    empty = db.get("", [])
    if empty:
        print(f"⚪ 未分类（category 为空）{len(empty)} 张，左侧树落到「未分类」：")
        for t in empty:
            print(f"     {t}")
        print()

    # 幽灵元数据 / 未登记表
    live = {
        r["tb"]
        for r in query_all(
            "SELECT TABLE_NAME AS tb FROM information_schema.tables "
            "WHERE TABLE_SCHEMA = DATABASE()"
        )
    }
    ghost = sorted(set().union(*[set(v) for v in db.values()]) - live) if db else []
    if ghost:
        print(f"🔴 幽灵元数据（table_meta 有、库里已无此表）{len(ghost)} 条：{ghost}")
        print("   修法：确认不再需要后 DELETE 该行（或直接 DROP 前的清理遗漏）")
        print()
        bad += 1

    unreg = sorted(live - set().union(*[set(v) for v in db.values()]))
    if unreg:
        bak = [t for t in unreg if re.search(r"_bak_", t, re.I)]
        other = [t for t in unreg if t not in bak]
        print(f"⚪ 未登记元数据的表 {len(unreg)} 张：{unreg}")
        if bak:
            print(f"     （其中 {len(bak)} 张是 _bak_ 备份表，前端会自动归入「备份归档」组，可豁免）")
        if other:
            print(f"     ⚠️ 非备份表 {other} 建议补进 seed_table_meta.py 的 _META")
        print()

    print("=" * 78)
    if bad:
        print(f"结论：❌ 发现 {bad} 类不一致，需修复（见上）")
    else:
        print("结论：✅ 三方一致，无静默漏表风险")
    print("=" * 78)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
