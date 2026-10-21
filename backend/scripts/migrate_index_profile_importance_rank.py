#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""为 index_profile 新增「代表指数的重要程度 importance_rank」一列（幂等迁移）。

用法：python scripts/migrate_index_profile_importance_rank.py            # 只读预览
      python scripts/migrate_index_profile_importance_rank.py --apply    # 实际 ALTER

设计（见 docs/design/指数重要程度列_增量补丁_v5.0_2026-10-18.md）：
  importance_rank TINYINT UNSIGNED NULL
    COMMENT '代表指数的重要程度：1=上证 ... 14=中证全指房地产（按用户清单出现先后）；99=用户未确认排序的默认最小值'
  纯 ADD COLUMN，不动任何现有列；回滚 = 1 条 DROP COLUMN importance_rank。

幂等性：
  - 列不存在 → ADD COLUMN；
  - 列已存在但 COMMENT 是旧口径（NULL=用户未表态）→ MODIFY 更新 COMMENT；
  - 均满足则跳过。可安全重复执行。
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pymysql  # noqa: E402

from app.db import get_db_config  # noqa: E402

COL = ("importance_rank",
       "TINYINT UNSIGNED NULL",
       "代表指数的重要程度：1=上证 ... 14=中证全指房地产（按用户清单出现先后）；99=用户未确认排序的默认最小值")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="实际 ALTER（默认只读预览）")
    args = ap.parse_args()

    name, dtype, comment = COL

    conn = pymysql.connect(**get_db_config().to_dict(), cursorclass=pymysql.cursors.DictCursor)
    try:
        with conn.cursor() as cur:
            cur.execute("SHOW FULL COLUMNS FROM index_profile WHERE Field=%s", (name,))
            row = cur.fetchone()

        if row is None:
            print(f"{name:<18} 🟡 列不存在，待添加")
            todo_add, todo_comment = True, False
        elif row["Comment"] != comment:
            print(f"{name:<18} 🟡 列已存在但 COMMENT 为旧口径，待更新")
            print(f"    旧：{row['Comment']}")
            todo_add, todo_comment = False, True
        else:
            print(f"{name:<18} ✅ 已存在且 COMMENT 为最新口径，跳过")
            todo_add, todo_comment = False, False

        if not args.apply:
            print()
            if not (todo_add or todo_comment):
                print("无需变更。")
            else:
                act = "ADD COLUMN" if todo_add else "MODIFY COLUMN（更新 COMMENT）"
                print(f"[只读预览] 将 {act} {name} {dtype}，加 --apply 才会执行。")
            return 0

        with conn.cursor() as cur:
            if todo_add:
                cur.execute(f"ALTER TABLE index_profile ADD COLUMN {name} {dtype} COMMENT %s", (comment,))
                print(f"  ✅ ADD COLUMN {name}")
            elif todo_comment:
                cur.execute(f"ALTER TABLE index_profile MODIFY COLUMN {name} {dtype} COMMENT %s", (comment,))
                print(f"  ✅ MODIFY COLUMN {name}（COMMENT 已更新）")
        conn.commit()

        with conn.cursor() as cur:
            cur.execute("SHOW COLUMNS FROM index_profile")
            after = {r["Field"] for r in cur.fetchall()}
        assert name in after, f"迁移后仍缺列：{name}"
        print(f"✅ 完成，index_profile 现共 {len(after)} 列")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())