#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""为 index_profile 新增「核心说明 core_note / 不足说明 limit_note」两列（幂等迁移）。

用法：python scripts/migrate_index_profile_two_columns.py            # 只读预览
      python scripts/migrate_index_profile_two_columns.py --apply    # 实际 ALTER

设计（见 docs/design/指数两字段落地方案_设计规范_v4.0_2026-10-18.md §1.2）：
  core_note  VARCHAR(255) NULL  COMMENT '核心说明（原话）：对「核心之处」的完整原话，多句用、连接，不改字；NULL=原话未提'
  limit_note VARCHAR(255) NULL  COMMENT '不足说明（原话）：对「不足之处」的完整原话；NULL=原话未提'
  纯 ADD COLUMN，不动任何现有列；回滚 = 2 条 DROP COLUMN。

幂等性：先 SHOW COLUMNS 检测，已存在则跳过，可安全重复执行。
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pymysql  # noqa: E402

from app.db import get_db_config  # noqa: E402

COLS = {
    "core_note": ("VARCHAR(255) NULL",
                  "核心说明（原话）：对「核心之处」的完整原话，多句用、连接，不改字；NULL=原话未提"),
    "limit_note": ("VARCHAR(255) NULL",
                   "不足说明（原话）：对「不足之处」的完整原话；NULL=原话未提"),
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="实际 ALTER（默认只读预览）")
    args = ap.parse_args()

    conn = pymysql.connect(**get_db_config().to_dict())
    try:
        with conn.cursor() as cur:
            cur.execute("SHOW COLUMNS FROM index_profile")
            existing = {r[0] for r in cur.fetchall()}

        todo = {k: v for k, v in COLS.items() if k not in existing}
        print(f"目标 2 列；已存在 {sorted(COLS) and [c for c in COLS if c in existing] or '无'}"
              f"；待加 {sorted(todo) or '无'}")
        for k in COLS:
            print(f"  {k:<12} {'✅ 已存在' if k in existing else '🟡 待添加'}")

        if not args.apply:
            print()
            if not todo:
                print("无需变更。")
            else:
                print(f"[只读预览] 将 ALTER 添加 {sorted(todo)}，加 --apply 才会执行。")
            return 0

        for k, (dtype, comment) in todo.items():
            sql = f"ALTER TABLE index_profile ADD COLUMN {k} {dtype} COMMENT %s"
            with conn.cursor() as cur:
                cur.execute(sql, (comment,))
            print(f"  ✅ ADD COLUMN {k}")
        conn.commit()

        # 复核
        with conn.cursor() as cur:
            cur.execute("SHOW COLUMNS FROM index_profile")
            after = {r[0] for r in cur.fetchall()}
        assert set(COLS) <= after, f"迁移后仍缺列：{set(COLS) - after}"
        print(f"✅ 完成，index_profile 现共 {len(after)} 列；新增 {sorted(todo) or '无'}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
