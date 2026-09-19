#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
InvestBuddy 行情快照聚合采集器
从 stock_market_daily（日线，采集器持续更新）最新交易日聚合生成 stock_market_current（行情看板快照）。
- 纯本地 SQL 计算，无外部数据源、无风控风险
- 解决快照表停更问题：行情看板表格永远展示"最近一个交易日的收盘快照"
- 幂等：TRUNCATE 后全量重建（约 5400 行，秒级）

🔴 为什么本表必须有 stock_code 唯一索引（2026-09-19 事故）
  「TRUNCATE + 全量重建」在**单次串行执行**下是幂等的，但**并发两份同时跑时不是**：
  两边各自 TRUNCATE 后交错 INSERT，结果整表每只股票恰好 2 份。
  实测 09-19：10,242 行 / 5,121 只 = **2.00x**，update_time 与 data_source 完全相同、
  只有 id 不同 —— 正是两次并发重建的指纹。
  触发条件是调度器 `_execute` 的 TOCTOU 竞态（链式线程与启动补跑线程同时进入，
  已于同日用进程内按任务锁修复），而当时本表**没有任何唯一键**兜底，
  且 `current_rows`(≥4500) 这类行数规则对「翻倍」天然无感（10,242 也 ≥4500）。
  现在 `uk_stock_code` 唯一索引是第二道结构性防线：并发再现时第二次 INSERT 直接报
  重复键失败（**响亮地失败**），而不是静默把表写成双份。
  ⚠️ 改本文件前先确认 `uk_stock_code` 仍在（`seed_indexes.py` 是其事实来源）。

⚠️ 8 列「有列无值」的处置（2026-09-19 复核 + 部分补齐）
  这 8 列原是全 NULL：`dynamic_pe / pb / volume_ratio / total_captital /
  float_captital / rise_speed / 5m_change_pct / 60d_change_pct`。
  成因是**口径不匹配**而非采集失败——它们是**东财实时行情专属字段**，
  本采集器是「日线聚合」口径（data_source=daily-agg），源里根本没有。
  接实时源需走东财 push2，而该子域对本机是**间歇性 RST 风控**（非硬不可达：实测首次
  直连可拿到含 f9/f23/f20/f21 的完整快照、连续请求即被拒，跨 4 分钟重试全败），
  不适合作为稳定依赖。

  故本轮**按「能否用本地数据精确派生」分两类处置，而不是一刀切删列**：
  ✅ 已补齐（本地精确派生，不依赖任何外部源，名单覆盖率实测 **100%**）——
     · `total_captital`  ← `stock_shares.total_shares`（总股本，单位：股）
     · `float_captital`  ← `stock_shares.list_a_shares`（A 股流通股，单位：股）
     取每只股票 `MAX(change_date)` 的最新一条股本（`uk_stock_date` 保证不放大）。
     量纲校验：`api/market.py` 的 `market_cap = new × total_captital` → 元，故此处存**股数**。
     实测交叉验证（收盘价 × 总股本）：工商银行 2.88 万亿、贵州茅台 1.57 万亿、
     宁德时代 1.40 万亿，A 股占比亦符合实际（比亚迪 38.2%、中芯国际 23.4%、工行 75.6%）。
     ★ 这同时修好了 `api/market.py` 里**静默失效**的「按市值排序」——
       原 `ORDER BY new * total_captital` 因列恒 NULL 而等于没排序，现已真正生效。
  ⛔ 仍为 NULL（本地不可派生，且外部源不可靠）——
     · `dynamic_pe` / `pb`：需外部估值，非本地可算
     · `rise_speed` / `5m_change_pct`：需盘中分时，日线口径天然没有
     · `volume_ratio`：东财「量比」有特定定义（当日均量/过去 5 日均量），
       本地近似值与源口径不一致，**宁缺勿错**故不填
     任何涉及 PE/PB 的判断都不要读本表这 6 列。

⚠️ 调度顺序契约（2026-09-12 建立，2026-09-16 修正）：
  stock_daily_incr（19:00 启动）必须先完成，本任务才能拿到完整 daily 最新日切片。
  历史①（09-12）：原 cron `45 18` 早于 `0 19` → 6 字头股票漏 2220 行被锁死。
  历史②（09-16）：改到 `30 19` 仍不够——实测 stock_daily_incr 常态耗时 **15~50 分钟**
  （19:00 起跑，最慢 19:50 才完成），故 19:30 依然早于日线完成 → 写入「半量快照」
  （2820 / 应为 5119 行），并沿依赖链毒害下游：stock_info_sync 的名单护栏拒绝残缺快照，
  连续 6 个班次失败，A 股在册名单停更。
  修正（两管齐下）：
    ① cron 后移至 `20 20`（20:10），给日线留足余量；
    ② 新增「相对上一交易日」充分性护栏（见下），即使时序再被打乱也不会写入半量数据。
  每次跑都重新聚合（TRUNCATE + INSERT 仅数秒），不做「今日已跑跳过」。
  双重护栏：
    · 绝对下限 MIN_ROWS —— 防日线表被清空的极端情形；
    · 相对充分性 PREV_RATIO —— 当日行数不足上一交易日的该比例即拒绝，
      防「日线增量未跑完」把半量数据写成快照。
  任一不满足即拒绝覆盖，**保留库内原有完整快照**（宁可数据晚一天，不可毒害下游）。
"""
import logging
from datetime import datetime

import pymysql

from ..db import get_db_config
from ._common import with_steps

logger = logging.getLogger(__name__)

# 护栏一：绝对下限。少于该值视为异常，拒绝覆盖（防止日线表不完整时把快照清空）
MIN_ROWS = 1000

# 护栏二：相对充分性。当日行数 < 上一交易日行数 × PREV_RATIO 时拒绝覆盖。
# 动机（2026-09-16）：MIN_ROWS 只防「表被清空」，拦不住「日线增量跑到一半」
# —— 2820 行也 > 1000，会静默把半量快照写进库，再连锁毒害下游名单类任务。
# A 股约 5400 只，交易日之间数量不会骤减 10%，故 0.9 是安全阈值。
PREV_RATIO = 0.9

# 运行步骤链模板（供前端「数据流·整链拓扑」展示运行逻辑）
RUN_STEPS = [
    {"no": 1, "name": "读最新交易日", "params": "stock_market_daily 取 MAX(trade_date)"},
    {"no": 2, "name": "聚合当日行情", "params": "LEFT JOIN stock_info 名称 + 年初至今涨幅 + stock_shares 最新股本"
                                              "（派生 total_captital/float_captital，修好按市值排序），全市场约 5121 行"},
    {"no": 3, "name": "行数护栏校验", "params": f"①少于 {MIN_ROWS} 行；②不足上一交易日的 {PREV_RATIO:.0%}——任一不满足即拒绝覆盖"},
    {"no": 4, "name": "全量重建写入", "params": "TRUNCATE 后单事务批量 INSERT（22 列, data_source=daily-agg）"},
]


class MarketCurrentSyncCollector:
    """行情快照聚合采集器"""

    def run(self) -> dict:
        conn = pymysql.connect(**get_db_config().to_dict(), cursorclass=pymysql.cursors.DictCursor)
        try:
            with conn.cursor() as cur:
                # 1. 最新交易日
                cur.execute("SELECT MAX(trade_date) AS d FROM stock_market_daily")
                latest = cur.fetchone()["d"]
                if not latest:
                    return with_steps(
                        {"records_written": 0, "error_count": 0, "errors": [], "note": "日线表为空，跳过"},
                        RUN_STEPS, {1: "stock_market_daily 为空，终止"},
                    )
                latest_date = str(latest)

                # 2. 聚合最新交易日数据（无日期幂等：每次都重建，详见模块顶部调度契约）
                year = datetime.now().year
                sql = """
                    SELECT d.stock_code,
                           IFNULL(i.short_name, d.stock_code) AS stock_name,
                           d.open, d.high, d.low, d.close AS new_price, d.pre_close,
                           d.change_amount, d.change_pct, d.volume, d.amount,
                           d.turnover_ratio,
                           ROUND((d.high - d.low) / NULLIF(d.pre_close, 0) * 100, 2) AS amplitude,
                           ROUND((d.close / y.close - 1) * 100, 2) AS ytd_change_pct,
                           sh.total_shares, sh.list_a_shares
                    FROM stock_market_daily d
                    LEFT JOIN stock_info i ON d.stock_code = i.stock_code
                    LEFT JOIN (
                        SELECT m.stock_code, m.close
                        FROM stock_market_daily m
                        JOIN (
                            SELECT stock_code, MIN(trade_date) AS md
                            FROM stock_market_daily
                            WHERE trade_date >= %s
                            GROUP BY stock_code
                        ) g ON m.stock_code = g.stock_code AND m.trade_date = g.md
                    ) y ON d.stock_code = y.stock_code
                    -- 股本：取每只股票最新一条变动记录（uk_stock_date 保证每股票至多一行，不放大行数）
                    LEFT JOIN (
                        SELECT s1.stock_code, s1.total_shares, s1.list_a_shares
                        FROM stock_shares s1
                        JOIN (
                            SELECT stock_code, MAX(change_date) AS md
                            FROM stock_shares WHERE total_shares > 0 GROUP BY stock_code
                        ) g2 ON s1.stock_code = g2.stock_code AND s1.change_date = g2.md
                        WHERE s1.total_shares > 0
                    ) sh ON d.stock_code = sh.stock_code
                    WHERE d.trade_date = %s
                """
                cur.execute(sql, [f"{year}-01-01", latest_date])
                rows = cur.fetchall()

                # 上一交易日行数（相对充分性护栏的基准）
                cur.execute(
                    "SELECT COUNT(*) AS c FROM stock_market_daily "
                    "WHERE trade_date = (SELECT MAX(trade_date) FROM stock_market_daily WHERE trade_date < %s)",
                    (latest_date,),
                )
                prev_cnt = cur.fetchone()["c"] or 0
                n = len(rows)

                # 双重护栏：任一不满足即拒绝覆盖，保留库内原有完整快照
                reject = None
                if n < MIN_ROWS:
                    reject = f"仅 {n} 行，小于绝对下限 {MIN_ROWS}"
                elif prev_cnt and n < prev_cnt * PREV_RATIO:
                    reject = (
                        f"仅 {n} 行，不足上一交易日 {prev_cnt} 行的 {PREV_RATIO:.0%}"
                        f"（{prev_cnt * PREV_RATIO:.0f} 行）——疑似 stock_daily_incr 尚未跑完"
                    )
                if reject:
                    logger.warning(
                        f"⛔ 快照护栏拒绝覆盖：最新交易日 {latest_date} {reject}；保留库内原有快照"
                    )
                    return with_steps(
                        {
                            "records_written": 0,
                            "error_count": 0,
                            # blocked=True 表达「数据未就绪，本次不执行」——这不是任务故障。
                            # run_task 据此写 status='blocked'（而非 failed），
                            # 否则每个交易日的 21:40 兜底班次都会产生一次假失败，污染 DQ 失败率。
                            "blocked": True,
                            "errors": [f"最新交易日 {latest_date} {reject}，拒绝覆盖（保留库内原有快照）"],
                            "note": "数据未就绪保护：疑似日线未跑完，本次不写（保留库内原有快照）；"
                                    "日线完成后的链式触发会自动重跑本任务",
                        },
                        RUN_STEPS,
                        {
                            1: f"最新交易日 {latest_date}",
                            2: f"聚合 {n} 行（上一交易日 {prev_cnt} 行）",
                            3: f"⛔ {reject}，拒绝覆盖",
                            4: "保留库内原有快照",
                        },
                    )

                # 3. TRUNCATE + 批量重建
                now = datetime.now()
                # 6 个 NULL 列：dynamic_pe/pb（需外部估值）、volume_ratio（源口径特殊）、
                # rise_speed/5m_change_pct（需盘中分时）——见模块顶部「8 列处置」说明。
                # total_captital / float_captital 改为本地派生填充（单位：股）。
                insert_sql = """
                    INSERT INTO stock_market_current
                    (stock_code, stock_name, `new`, change_pct, change_amount,
                     open, high, low, pre_close, volume, amount, turnover_ratio,
                     amplitude, ytd_change_pct, dynamic_pe, pb, volume_ratio,
                     rise_speed, 5m_change_pct, 60d_change_pct,
                     total_captital, float_captital, update_time, data_source)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                            NULL,NULL,NULL,NULL,NULL,NULL,%s,%s,%s,%s)
                """
                cur.execute("TRUNCATE TABLE stock_market_current")
                params = [
                    (
                        r["stock_code"], r["stock_name"], r["new_price"], r["change_pct"], r["change_amount"],
                        r["open"], r["high"], r["low"], r["pre_close"], r["volume"], r["amount"],
                        r["turnover_ratio"], r["amplitude"], r["ytd_change_pct"],
                        r["total_shares"], r["list_a_shares"],
                        now, "daily-agg",
                    )
                    for r in rows
                ]
                cur.executemany(insert_sql, params)
                conn.commit()
                logger.info(f"✅ 快照重建完成：{len(rows)} 行 @ {latest_date}")
                return with_steps(
                    {
                        "records_written": len(rows),
                        "error_count": 0,
                        "errors": [],
                        "note": f"快照已更新至 {latest_date}（{year} 年 ytd 已计算）",
                    },
                    RUN_STEPS,
                    {
                        1: f"最新交易日 {latest_date}",
                        2: f"聚合 {len(rows)} 行（含 {year} ytd）",
                        3: f"{len(rows)} ≥ {MIN_ROWS} 通过",
                        4: f"TRUNCATE 后写入 {len(rows)} 行",
                    },
                )
        finally:
            conn.close()
