#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
历史死表恢复采集 · 任务配置播种（幂等，可重复执行）
用法：python scripts/seed_recovered_tasks.py

背景（2026-09-13）：10 张历史导入表经调研确认可低成本恢复采集，分三批落地：
  · 第 1 批 P0/P1 —— 分红送配 / 融资融券 / 机构调研
  · 第 3 批 P2    —— 期货现货价格与基差 / 申万行业分类
  · 第 4 批 P3    —— 财务关键指标 / 股本变动 / 资金流向（资金流向默认禁用）
对应采集器已在 app/collectors/ 落地，本脚本把任务写入 task_config 供调度器自动装载。

⚠️ 维护纪律（与 seed_dq_rules.py 同理）：**task_config 的这些记录以本脚本为事实来源**。
   前端「作业监控」热改 cron/params 后如需长期生效，请同步回写本文件，
   否则下次跑 seed 会把热改值静默覆盖（同 dq_rules 2026-09-13 踩过的坑）。

排期设计（避开现有 17:00~21:30 的盘后任务密集区，故集中在 22:00 之后 / 凌晨）：
- margin_sync               每日 09:00        —— 沪深两融为 T+1 发布，次日早间补齐最稳
- jgdy_sync                 每日 22:00        —— 调研公告盘后/次日发布（单次调用即拉全量增量）
- futures_sync              工作日 22:20      —— 现货/基差为日频，单次调用 60~165s，盘后取最稳
- stock_shares_sync         每日 22:40        —— 事件型；refresh_days=30 全市场约 6 天轮一遍后空跑
- financial_abstract_sync   每日 23:00        —— 滞后股票 ~800/日，财报季约 7 天收敛全市场
- ths_dividend_sync         每月 1/15 日 03:00 —— 逐股型（约 5,100 次请求），避免高频空跑
- sw_industry_sync          每月 1 日 03:30    —— 申万分类调样频率低，月度快照足够
- capital_flow_sync         每日 23:00（禁用） —— 东财域沙箱不可达 + 口径待复核，先关闭
"""
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.db import get_db_config  # noqa: E402
import pymysql  # noqa: E402

# (task_name, enabled, cron, params)
TASKS = [
    # ---- 第 1 批 P0/P1 ----
    ("margin_sync", 1, "0 9 * * *",
     {"max_days": 0, "sleep_sec": 0.25, "timeout_sec": 60, "bse_retry": 2}),
    ("jgdy_sync", 1, "0 22 * * *",
     {"overlap_days": 30, "first_lookback_days": 365, "timeout_sec": 60}),
    ("ths_dividend_sync", 1, "0 3 1,15 * *",
     {"max_stocks": 0, "sleep_sec": 0.12, "retry": 1, "timeout_sec": 30}),
    # ---- 第 3 批 P2 ----
    ("futures_sync", 1, "20 22 * * 1-5",
     {"chunk_days": 30, "max_days": 0, "sleep_sec": 0.5,
      "timeout_sec": 180, "first_lookback_days": 365, "from_date": None}),
    ("sw_industry_sync", 1, "30 3 1 * *",
     {"sleep_sec": 0.15, "timeout_sec": 30, "min_coverage": 0.85}),
    # ---- 第 4 批 P3 ----
    ("financial_abstract_sync", 1, "0 23 * * *",
     {"max_stocks": 800, "sleep_sec": 0.12, "timeout_sec": 30, "full_sweep": False}),
    ("stock_shares_sync", 1, "40 22 * * *",
     {"max_stocks": 1000, "refresh_days": 30, "sleep_sec": 0.12,
      "timeout_sec": 30, "full_sweep": False}),
    # ⚠️ 资金流向：东财域名本机沙箱不可达；且「主力净流入=超大单+大单」仅 90~93.8% 成立，
    #    源口径未与本地表完全对齐 → 先实现不启用，待网络放行 + 口径复核后再置 enabled=1
    ("capital_flow_sync", 0, "0 23 * * *",
     {"max_stocks": 400, "refresh_days": 7, "sleep_sec": 0.12,
      "timeout_sec": 30, "full_sweep": False}),
]


def main():
    conn = pymysql.connect(**get_db_config().to_dict())
    try:
        with conn.cursor() as cur:
            for name, enabled, cron, params in TASKS:
                cur.execute(
                    """INSERT INTO task_config (task_name, enabled, cron, params, update_time)
                       VALUES (%s, %s, %s, %s, NOW())
                       ON DUPLICATE KEY UPDATE enabled=VALUES(enabled), cron=VALUES(cron),
                       params=VALUES(params), update_time=NOW()""",
                    (name, enabled, cron, json.dumps(params, ensure_ascii=False)),
                )
        conn.commit()
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM task_config")
            total = cur.fetchone()[0]
            names = tuple(t[0] for t in TASKS)
            cur.execute(
                "SELECT task_name, enabled, cron FROM task_config WHERE task_name IN (%s)"
                % ", ".join(["%s"] * len(names)),
                names,
            )
            rows = cur.fetchall()
        print(f"seed 完成，task_config 共 {total} 条任务；本次 {len(TASKS)} 条：")
        for r in rows:
            print(f"  {r[0]:<24} enabled={r[1]}  cron={r[2]}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
