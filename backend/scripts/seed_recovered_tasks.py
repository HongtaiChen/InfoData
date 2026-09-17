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

⚠️ 本脚本已被 seed_task_config.py 取代（2026-09-17）：
   全量 29 个任务的唯一事实来源是 **seed_task_config.py**（它已完整固化下面这些任务）。
   本文件保留作历史记录，**非必要不要执行**；若执行，其 cron 已同步为 19:00–23:00 窗口口径，
   但仍可能与 seed_task_config.py 的后续调整脱节。改排期请一律改 seed_task_config.py。

排期口径（2026-09-17 起）：全部收进本机在线窗口 19:00–23:00（原跨度 3:00–23:00）。
- margin_sync               每日 20:00        —— 两融 T+1，晚间可取到前一日数据（原 09:00 命中率 0%）
- jgdy_sync                 每日 22:00        —— 调研公告盘后/次日发布（单次调用即拉全量增量）
- futures_sync              工作日 22:20      —— 现货/基差为日频，单次调用 60~165s，盘后取最稳
- stock_shares_sync         每日 21:45        —— 事件型；refresh_days=30 全市场约 6 天轮一遍后空跑
- financial_abstract_sync   每日 22:15        —— 滞后股票 ~800/日，财报季约 7 天收敛全市场
                                                 （同花顺长跑会间歇限流，已加单股退避重试 retry=1）
- ths_dividend_sync         每月 1/15 日 21:30 —— 逐股型（约 5,100 次请求），允许跨界续跑
- sw_industry_sync          每月 1 日 21:00    —— 申万分类调样频率低，月度快照足够
- capital_flow_sync         每日 22:15（禁用） —— 东财域沙箱不可达 + 口径待复核，先关闭
"""
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.db import get_db_config  # noqa: E402
import pymysql  # noqa: E402

# (task_name, enabled, cron, params)
TASKS = [
    # ⚠️ 排期与 seed_task_config.py 保持一致（2026-09-17 全部收进 19:00–23:00 在线窗口）；
    #    本脚本已被 seed_task_config.py 取代，正常维护只改后者。
    # ---- 第 1 批 P0/P1 ----
    ("margin_sync", 1, "0 20 * * *",
     {"max_days": 0, "sleep_sec": 0.25, "timeout_sec": 60, "bse_retry": 2}),
    ("jgdy_sync", 1, "0 22 * * *",
     {"overlap_days": 30, "first_lookback_days": 365, "timeout_sec": 60}),
    ("ths_dividend_sync", 1, "30 21 1,15 * *",
     {"max_stocks": 0, "sleep_sec": 0.12, "retry": 1, "timeout_sec": 30}),
    # ---- 第 3 批 P2 ----
    # ⚠️ `0-4` = 周一~周五（APScheduler 0=周一）；写成 `1-5` 会变成周二~周六（2026-09-14 修正）
    ("futures_sync", 1, "20 22 * * 0-4",
     {"chunk_days": 30, "max_days": 0, "sleep_sec": 0.5,
      "timeout_sec": 180, "first_lookback_days": 365, "from_date": None}),
    ("sw_industry_sync", 1, "0 21 1 * *",
     {"sleep_sec": 0.15, "timeout_sec": 30, "min_coverage": 0.85}),
    # ---- 第 4 批 P3 ----
    ("financial_abstract_sync", 1, "15 22 * * *",
     {"max_stocks": 800, "sleep_sec": 0.12, "timeout_sec": 30, "full_sweep": False,
      "retry": 1, "retry_backoff": 2.0}),
    ("stock_shares_sync", 1, "45 21 * * *",
     {"max_stocks": 1000, "refresh_days": 30, "sleep_sec": 0.12,
      "timeout_sec": 30, "full_sweep": False}),
    # ⚠️ 资金流向：东财域名本机沙箱不可达；且「主力净流入=超大单+大单」仅 90~93.8% 成立，
    #    源口径未与本地表完全对齐 → 先实现不启用，待网络放行 + 口径复核后再置 enabled=1
    ("capital_flow_sync", 0, "15 22 * * *",
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
