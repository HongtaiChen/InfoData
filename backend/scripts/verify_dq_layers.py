#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
DQ 三层体系验收脚本（只读）

用途：一次性打印 L1 自洽 / L2 外部对账 / L3 回补闭环的落库现状，
      用于交付验收与日常巡检。

用法：
    cd backend && python scripts/verify_dq_layers.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db import query_all  # noqa: E402


def show(title):
    print("\n" + "=" * 74)
    print(title)
    print("=" * 74)


show("L1/L2 最近报告（dq_report，倒序 12 条）")
for r in query_all(
    "SELECT id, check_type, rule_name, status, LEFT(COALESCE(message,''),62) AS msg, "
    "run_at FROM dq_report ORDER BY id DESC LIMIT 12"
):
    print(f"  #{r['id']:<5} {str(r['run_at'])[5:16]} | {r['check_type']:<14} | "
          f"{str(r['rule_name'])[:26]:<26} | {r['status']:<7} | {r['msg']}")

show("L1 缺口明细汇总（dq_gap_detail）")
row = query_all(
    "SELECT COUNT(*) total, SUM(risk='high') high_risk, SUM(risk='mid') mid_risk, "
    "SUM(status='open') open_cnt, SUM(status='fixed') fixed_cnt FROM dq_gap_detail"
)[0]
print(f"  总计 {row['total']} 段 | 高危 {row['high_risk']} | 中危 {row['mid_risk']} | "
      f"待修 {row['open_cnt']} | 已修 {row['fixed_cnt']}")
for r in query_all(
    "SELECT stock_code, prev_date, next_date, gap_days, risk, status FROM dq_gap_detail "
    "ORDER BY gap_days DESC LIMIT 5"
):
    print(f"  Top: {r['stock_code']} {r['prev_date']}→{r['next_date']} "
          f"{r['gap_days']}天 [{r['risk']}/{r['status']}]")

show("L2 外部对账明细汇总（dq_recon_detail，仅记差异）")
rows = query_all(
    "SELECT mode, COUNT(*) total, SUM(diff_type='value_diff') vdiff, "
    "SUM(diff_type='missing_local') mloc, SUM(diff_type='missing_remote') mrem, "
    "SUM(diff_type IN ('count_diff','range_diff')) summ "
    "FROM dq_recon_detail GROUP BY mode"
)
if not rows:
    print("  无差异明细（对账全部一致 —— 符合预期）")
for r in rows:
    print(f"  {r['mode']}: 共 {r['total']} | 数值差 {r['vdiff']} | 本地缺 {r['mloc']} | "
          f"远端缺 {r['mrem']} | 摘要差 {r['summ']}")

show("规则分组（dq_rules）")
for r in query_all(
    "SELECT rule_group, COUNT(*) cnt, SUM(enabled=1) enabled_cnt FROM dq_rules GROUP BY rule_group"
):
    print(f"  {r['rule_group']:<8} 共 {r['cnt']} 条，启用 {r['enabled_cnt']} 条")

show("调度配置（task_config）")
for r in query_all(
    "SELECT task_name, cron, enabled, LEFT(COALESCE(params,''),46) AS p FROM task_config "
    "WHERE task_name LIKE '%quality%' OR task_name LIKE '%recon%' OR task_name LIKE '%backfill%' "
    "ORDER BY task_name"
):
    print(f"  {r['task_name']:<28} cron={r['cron']:<14} enabled={r['enabled']}  {r['p']}")

print()
