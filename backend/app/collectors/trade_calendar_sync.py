#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
InvestBuddy 交易日历采集器（2026-10-19 起升级为「全量日历」写法）

背景（2026-10-19 假日假 K 线根治时发现）：
  旧版只用 `ak.tool_trade_date_hist_sina()`（**只返回交易日**）写 is_trading_day=1 的行，
  **从不写休市日**。导致 trade_calendar 里 2026 全年 is_trading_day=0 的行数为 0 ——
  国庆 10-01~10-07、中秋 09-25 等休市日「根本不存在于表里」。
  这带来两个连锁问题：
    1) 交易日闸门（index_market_sync._filter_trading_days）只能用「不在交易日集合」来
       判假日，无法区分「休市日」与「日历缺失日」，两件事被混为一谈；
    2) 用户担心的「某天突然停市」无法被日历表显式感知 —— 临时休市在表里表现为
       「一个本该是交易日的日期缺失」，而非「is_trading_day=0 的显式休市」。

升级做法（本地推导休市日，无需额外数据源）：
  - 数据源仍是新浪交易日（唯一可用源），只提供「哪些天开市」；
  - 对目标年份，**逐日遍历**：
      is_trading_day = 1  若该日 ∈ 交易日集合
      is_trading_day = 0  若该日 ∉ 集合（含周末 + 法定节假日休市）
  - 这样休市日（尤其工作日遇节假日）被显式落成 is_trading_day=0，日历表完整，
    临时休市也能在下一轮同步时自然被推导成 0。
  - 写库用 upsert（ON DUPLICATE KEY UPDATE），修掉旧版 INSERT IGNORE 无法
    「翻转」已存行标记的缺陷（某天从交易日被改为休市，旧版会静默不改）。

⚠️ 边界：新浪接口只覆盖到「次年」甚至更早（实测 2026-10 运行时最新到 2026-12-31，
  2027 日历未发布）。因此「明年」的推导区间**取决于接口实际返回的边界**，接口没给
  到的年份不做推导（宁缺，不臆造未来休市日）。日历完整性由 DQ 规则 date_floor 守护。
"""
import logging
from datetime import datetime, date, timedelta

import pymysql
import akshare as ak

from ..db import get_db_config
from ._common import with_steps

logger = logging.getLogger(__name__)

# 运行步骤链模板（供前端「数据流·整链拓扑」展示运行逻辑）
RUN_STEPS = [
    {"no": 1, "name": "拉取全量交易日", "params": "ak.tool_trade_date_hist_sina()（只返回交易日）"},
    {"no": 2, "name": "本地推导全量日历", "params": "目标年份逐日遍历：∈交易日=1，∉=0（含周末/节假日休市）"},
    {"no": 3, "name": "upsert 入库", "params": "ON DUPLICATE KEY UPDATE（可翻转标记），计数 新增/更新"},
]


class TradeCalendarSyncCollector:
    """交易日历采集器（全量日历：交易日 + 休市日 + 周末）"""

    def __init__(self, years: list[int] | None = None):
        # 默认补当年 + 次年（如 2026-10 运行 → 2026 + 2027）
        now_year = datetime.now().year
        self.years = years or [now_year, now_year + 1]

    def run(self) -> dict:
        df = ak.tool_trade_date_hist_sina()
        if df is None or df.empty or "trade_date" not in df.columns:
            raise RuntimeError("交易日历接口返回为空，可能被风控或接口变更")

        # 1) 归一化交易日集合
        trading: set[date] = set()
        for _, row in df.iterrows():
            d = row["trade_date"]
            if isinstance(d, str):
                d = datetime.strptime(d[:10], "%Y-%m-%d").date()
            elif isinstance(d, datetime):
                d = d.date()
            trading.add(d)

        # 2) 接口实际覆盖的年份边界（只推导接口给到的年份，不臆造未来）
        covered_years = sorted({d.year for d in trading})
        target_years = [y for y in self.years if y in covered_years]
        if not target_years:
            raise RuntimeError(f"接口未覆盖目标年份 {self.years}（实际覆盖 {covered_years}）")

        # 3) 逐日推导全量日历
        inserted = updated = 0
        conn = pymysql.connect(**get_db_config().to_dict())
        try:
            with conn.cursor() as cur:
                for y in target_years:
                    d = date(y, 1, 1)
                    while d.year == y:
                        is_td = 1 if d in trading else 0
                        cur.execute(
                            "INSERT INTO trade_calendar "
                            "(trade_date, is_trading_day, year, month, day, weekday, update_time) "
                            "VALUES (%s, %s, %s, %s, %s, %s, NOW()) "
                            "ON DUPLICATE KEY UPDATE is_trading_day=VALUES(is_trading_day), "
                            "weekday=VALUES(weekday), update_time=NOW()",
                            (d, is_td, d.year, d.month, d.day, d.weekday()),
                        )
                        if cur.rowcount == 1:
                            inserted += 1
                        elif cur.rowcount == 2:
                            updated += 1
                        d += timedelta(days=1)
            conn.commit()
        finally:
            conn.close()

        msg = (f"交易日历补齐至 {target_years}：新增 {inserted} · 更新标记 {updated} "
               f"（交易日 {sum(1 for d in trading if d.year in target_years)} · 休市日已显式推导）")
        logger.info("✅ %s", msg)
        return with_steps(
            {
                "records_written": inserted,
                "error_count": 0,
                "errors": [],
                "note": msg,
            },
            RUN_STEPS,
            {
                1: f"接口返回 {len(df)} 行",
                2: f"推导年份 {target_years}（接口覆盖 {covered_years}）",
                3: f"新增 {inserted} · 更新 {updated}",
            },
        )
