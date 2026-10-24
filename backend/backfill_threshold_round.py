# -*- coding: utf-8 -*-
"""回填 env_threshold 大跌阈值（取整到 0.5 后的全量手定值，幂等可重放）。

用途：E4 阈值定稿后，将 21 个指数阈值全部固化为 MANUAL_THRESHOLD 中的取整值，
     来源统一标 manual。可重复执行，结果一致（UPSERT）。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.collectors.env_derive import MANUAL_THRESHOLD
from app.db import execute_write, query_all

def main():
    prof = {r["index_code"]: r["index_name"] for r in query_all(
        "SELECT index_code, index_name FROM index_profile")}
    n = 0
    for code, thr in MANUAL_THRESHOLD.items():
        name = prof.get(code)
        if not name:
            print(f"[skip] {code} 不在 index_profile")
            continue
        execute_write(
            "INSERT INTO env_threshold (index_code, index_name, drop_threshold, "
            "threshold_source, threshold_note, update_time) "
            "VALUES (%s, %s, %s, 'manual', %s, NOW()) "
            "ON DUPLICATE KEY UPDATE index_name=VALUES(index_name), "
            "drop_threshold=VALUES(drop_threshold), threshold_source=VALUES(threshold_source), "
            "threshold_note=VALUES(threshold_note), update_time=NOW()",
            (code, name, thr, "手定阈值（近3年日跌幅95分位四舍五入到0.5）"),
        )
        n += 1
    print(f"[done] 回填 {n} 个指数阈值")

if __name__ == "__main__":
    main()
