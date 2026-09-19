#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""InvestBuddy 采集任务配置全量播种（幂等，可重复执行）—— task_config 的唯一事实来源。

用法：python scripts/seed_task_config.py            # 只读预览（打印差异 + 中文调度语义）
      python scripts/seed_task_config.py --apply    # 实际写入

为什么要有这个脚本（2026-09-14 立）：
  此前 task_config 只有「死表恢复批次」有种子脚本（seed_recovered_tasks.py），
  最早那批任务（stock_daily_incr / concept_market_sync / trade_calendar_sync …）
  只存在于数据库里 —— 与项目既定纪律「种子脚本是唯一事实来源，直改 DB 必须回写脚本」
  相矛盾。后果已经发生：`futures_sync` 的 cron 语义写错（`1-5` 被 APScheduler 解释为
  周二~周六）无人可从脚本侧发现，`concept_market_sync` 缺晚班次也无处登记。
  本脚本把 29 个任务全部固化，之后所有排期调整都改这里再跑一次。

⚠️ 星期语义（务必先读）：cron 由 APScheduler `CronTrigger.from_crontab` 解析，
   **day_of_week 为 0=周一、6=周日**——与 Unix crontab（0=周日）**相反**。
   写「工作日」要写 `0-4`，写 `1-5` 实际是「周二~周六」。
   写入前本脚本会打印 `cron_human()` 的中文语义，请逐条核对。

⚠️ 维护纪律：直改数据库（含前端「作业监控」热改）只能应急，
   必须回写本文件，否则下次跑 seed 会被静默覆盖。
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pymysql  # noqa: E402

from app.db import get_db_config  # noqa: E402
from app.scheduler import cron_human  # noqa: E402

# (task_name, enabled, cron, params)
#   cron 为 '手动' 表示不自动调度（仅 API/命令行触发）
#   注释里的中文语义由 cron_human() 复算，改 cron 后请一并更新注释
# ============================================================================
# ⚠️ 排期窗口契约（2026-09-17 重排，务必遵守）
#
# 本机实际在线窗口：**工作日 19:00–23:00（4 h）、周末 08:00–23:00（15 h）**，
# 节假日可能连续数天离线。原排期跨度 3:00–23:00（20 h）隐含假设「机器常开」，
# 实测凌晨 3 点档与上午 8:30–10 点档**命中率 0%**、17 点档仅 21%。
#
# 故此后所有排期的硬约束：
#   1. 时刻必须落在 19:00–23:00（周末任务可放宽到 08:00 起）；
#   2. 「日线之后」的任务不靠固定时刻，改由 scheduler.CHAIN_NEXT 链式触发，
#      下表时刻仅作兜底（当日已有 success 则自动跳过）；
#   3. 新增任务前先问：这个时刻机器开着吗？
# 依据与完整推演见 docs/调度方案v2_习惯驱动重排_2026-09-17.md
# ============================================================================
TASKS = [
    # ---------- 前置链（19:00–19:20，必须早于日线；日线候选池取自 stock_info） ----------
    ("stock_info_sync", 1, "0 19 * * *", {}),                       # 每天 19:00（日线前置，不可后移）
    ("stock_status_sync", 1, "5 19 * * *", {}),                     # 每天 19:05
    ("index_market_sync", 1, "5 19 * * 0-4", {}),                   # 每周一至周五 19:05（物化表需要指数收盘价）
    ("stock_company_sync", 1, "10 19 * * *",                        # 每天 19:10
     {"max_count": 200, "refresh_days": 30}),
    ("finance_calendar_sync", 1, "10 19 * * *", {}),                # 每天 19:10
    ("bond_profit_sync", 1, "15 19 * * 0-4", {}),                   # 每周一至周五 19:15
    # ---------- ⚠️ 日线闸门：以下任务以「个股日线」为输入，主触发=链式，固定时刻仅兜底 ----------
    # 实测 stock_daily_incr 常态耗时 15~52 分钟；2026-09-16 事故（快照写入半量 2820/5119 行，
    # 连锁导致 stock_info_sync 名单护栏连续 6 个班次失败）与 09-17 空转（物化表写 5254 行
    # 实为上一交易日数据）都是「下游跑在日线前面」造成的。固定时刻永远赌不准，故改链式。
    ("stock_daily_incr", 1, "20 19 * * 0-4",                        # 每周一至周五 19:20（后移 20 min 让前置链跑完）
     {"adjust": "qfq", "days_back": 15, "max_stocks": 0}),
    ("market_style_sync", 1, "30 21 * * 0-4", {}),                  # 每周一至周五 21:30（兜底；主触发=链式）
    ("market_current_sync", 1, "40 21 * * 0-4", {}),                # 每周一至周五 21:40（兜底；主触发=链式）
    # ---------- 概念（同花顺分批发布，主班 + 补班） ----------
    ("concept_market_sync", 1, "0 21,22 * * 0-4",                   # 每周一至周五 21:00、22:00
     {"days_back": 15}),
    ("concept_sync", 1, "0 21 * * 0", {}),                          # 每周一 21:00
    # ---------- 蓝图 P2/P3 六采集器（2026-09-19 落地，见 docs/市场风向数据蓝图落地审计_2026-09-19.md） ----------
    # 全部是**纯外部取数**，不依赖个股日线，故用固定时刻（无需链式）；
    # 时刻刻意避开 19:20 日线启动后的写库高峰，且彼此错峰 10~15 分钟防止并发风暴。
    ("index_valuation_sync", 1, "25 19 * * 0-4",                    # 每周一至周五 19:25（P2 估值→ERP）
     {"timeout_sec": 60, "lookback_days": 0}),
    ("interbank_rate_sync", 1, "35 19 * * 0-4",                     # 每周一至周五 19:35（蓝图A 钱贵不贵）
     {"timeout_sec": 90}),
    ("overseas_index_sync", 1, "50 19 * * *",                       # 每天 19:50（蓝图E 恒生+美股，美股为前一夜收盘）
     {"timeout_sec": 60}),
    ("currency_boc_sync", 1, "5 20 * * *",                          # 每天 20:05（蓝图E 人民币中间价）
     {"timeout_sec": 60, "first_lookback_days": 1825, "from_date": None}),
    ("fund_new_issue_sync", 1, "20 20 * * *",                       # 每天 20:20（蓝图E 发行冰点）
     {"timeout_sec": 120}),
    ("stock_repurchase_sync", 1, "35 20 * * *",                     # 每天 20:35（蓝图D 产业资本回购）
     {"timeout_sec": 240}),
    # ---------- 资讯（高频，靠窗口内自然触发；已支持回看补采） ----------
    ("news_fetch", 1, "*/30 * * * *",                               # 每 30 分钟
     {"sources": ["em", "cls"], "max_pages": 3}),
    # ---------- 日历（周末窗口内） ----------
    ("trade_calendar_sync", 1, "0 9 * * 6", {}),                    # 每周日 09:00
    # ---------- 资金 / 财务 / 基本面（逐股型，20:00 后错峰） ----------
    ("margin_sync", 1, "0 20 * * *",                                # 每天 20:00（T+1；原 09:00 命中率 0%）
     {"max_days": 0, "sleep_sec": 0.25, "timeout_sec": 60, "bse_retry": 2}),
    ("stock_shares_sync", 1, "45 21 * * *",                         # 每天 21:45（原 22:40 贴边，提前留重试余量）
     {"max_stocks": 1000, "refresh_days": 30, "sleep_sec": 0.12,
      "timeout_sec": 30, "full_sweep": False}),
    ("jgdy_sync", 1, "0 22 * * *",                                  # 每天 22:00
     {"overlap_days": 30, "first_lookback_days": 365, "timeout_sec": 60}),
    ("financial_abstract_sync", 1, "15 22 * * *",                   # 每天 22:15（原 23:00 贴边，无重试机会）
     {"max_stocks": 800, "sleep_sec": 0.12, "timeout_sec": 30, "full_sweep": False,
      "retry": 1, "retry_backoff": 2.0}),
    ("futures_sync", 1, "20 22 * * 0-4",                            # 每周一至周五 22:20（0-4=周一~周五！）
     {"chunk_days": 30, "max_days": 0, "sleep_sec": 0.5,
      "timeout_sec": 180, "first_lookback_days": 365, "from_date": None}),
    ("capital_flow_sync", 0, "15 22 * * *",                         # 每天 22:15（禁用）
     {"max_stocks": 400, "refresh_days": 7, "sleep_sec": 0.12,
      "timeout_sec": 30, "full_sweep": False}),
    # ---------- 月度（统一 21:00 后：无论 1 日是否周末都在线，原凌晨档命中率 0%） ----------
    ("sw_industry_sync", 1, "0 21 1 * *",                           # 每月1日 21:00
     {"sleep_sec": 0.15, "timeout_sec": 30, "min_coverage": 0.85}),
    ("ths_dividend_sync", 1, "30 21 1,15 * *",                      # 每月1、15日 21:30（最长 130 min，允许跨界续跑）
     {"max_stocks": 0, "sleep_sec": 0.12, "retry": 1, "timeout_sec": 30}),
    ("fund_info_sync", 1, "30 20 1 * *", {}),                       # 每月1日 20:30（避开 21:50 体检尖峰）
    ("index_cons_sync", 1, "30 21 15 * *",                          # 每月15日 21:30
     {"note": "指数成分股月度快照（每月15日 21:30，覆盖月度调样）"}),
    # ---------- 分析侧派生物化（2026-09-19） ----------
    # xcheck_sync：把「交叉印证背离数」逐日记入 market_xcheck_daily，供 market_wind 卡片
    # 计算「当前背离数相对自己的历史算不算多」（此前 4/7 是常态却每天当结论报）。
    # ⚠️ 必须晚于概念补班（22:00）：概念源分批发布，21:30 只有 ~190/375 个概念，
    #    用缺样本的口径算出的背离数会被写死进历史。故**不挂链式上游**（21:00 那次补班会带上它）。
    ("xcheck_sync", 1, "30 22 * * 0-4", {}),                        # 每周一至周五 22:30
    # ---------- 数据质量体检 ----------
    ("data_quality_check", 1, "50 21 * * *",                        # 每天 21:50（兜底；主触发=链式）
     {"groups": ["daily"]}),
    ("data_quality_check_weekly", 1, "30 21 * * 0",                 # 每周一 21:30
     {"groups": ["weekly"]}),
    # ---------- L2 外部对账 ----------
    ("daily_recon_window", 1, "15 21 * * 0-4",                      # 每周一至周五 21:15（需日线就绪，46~69 min）
     {"days": 5, "max_stocks": 0, "tolerance_pct": 0.5, "adjust": "qfq"}),
    ("daily_recon_sample", 1, "0 10 * * 6",                         # 每周日 10:00
     {"sample_size": 50, "seed": 42, "adjust": "qfq"}),
    # ---------- 手动任务 ----------
    ("ai_concept_analysis", 0, "手动",                                # 手动触发
     {"limit": 20, "days_back": 30}),
    ("daily_backfill", 1, "手动",                                    # 手动触发
     {"source": "dq_gap", "risk": "all", "max_codes": 0, "adjust": "qfq"}),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="实际写入（默认只读预览）")
    args = ap.parse_args()

    conn = pymysql.connect(**get_db_config().to_dict())
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT task_name, enabled, cron, params FROM task_config")
            cur_rows = {r[0]: (r[1], r[2], r[3]) for r in cur.fetchall()}

        print(f"本脚本任务 {len(TASKS)} 个；数据库现有 {len(cur_rows)} 个")
        extra = sorted(set(cur_rows) - {t[0] for t in TASKS})
        if extra:
            print(f"⚠️ 数据库中存在本脚本未覆盖的任务（不会被删除，请补进本脚本）：{extra}")

        changed = []
        print()
        print(f"{'任务':<28}{'启用':<5}{'cron':<18}{'中文语义':<24}状态")
        print("-" * 96)
        for name, enabled, cron, params in TASKS:
            pjson = json.dumps(params, ensure_ascii=False, sort_keys=True)
            cur_v = cur_rows.get(name)
            if cur_v is None:
                state = "新增"
            else:
                same = (int(cur_v[0]) == int(enabled)
                        and (cur_v[1] or "") == cron
                        and json.dumps(json.loads(cur_v[2]) if isinstance(cur_v[2], str) and cur_v[2] else (cur_v[2] or {}),
                                       ensure_ascii=False, sort_keys=True) == pjson)
                state = "一致" if same else "变更"
            if state != "一致":
                changed.append(name)
            print(f"{name:<28}{enabled:<5}{cron:<18}{cron_human(cron):<24}{state}")

        if not args.apply:
            print()
            print(f"[只读预览] 需变更 {len(changed)} 项：{changed}")
            print("加 --apply 才会写入。")
            return 0

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
        print()
        print(f"✅ 已写入 {len(TASKS)} 个任务（变更 {len(changed)} 项：{changed}）")
        print("   调度器会在 PUT /api/jobs/tasks 或重启后热同步。")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
