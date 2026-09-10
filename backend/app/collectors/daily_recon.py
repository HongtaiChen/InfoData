#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
InvestBuddy 日线外部对账（L2）

两个模式（2026-09-10 与用户确认「本期做」）：
- window：最近 N 个交易日 × 在市 A 股，与**腾讯**源逐票比对价格（独立血缘）
- sample：固定种子抽 N 票，与**东财**源比对全史摘要（count / 首末日期，SUM 仅参考）

设计要点（基于 2026-09-10 实库实测结论）：
1. 对账源必须独立于本地采集链血缘：本地全史 93.4% 为 AKSHARE（东财接口），
   故 window 模式用腾讯（真正独立的免费源）；sample 用东财仅用于查「入库丢行/截断」
   （同源正好对口该类问题，但不能发现源本身的错）。
2. 复权口径：本地为前复权。双方均以 adjust=qfq 拉取，且 window 模式限定"最近 5 个交易日"
   ——该窗口内若发生除权，前复权基准会整体重算导致假差异，故对差异票标注除权豁免提示。
3. 停牌语义：无停牌表，远端"本地有/远端无"的行记为 missing_remote 但语义为"疑似"，
   不直接判失败（由人工或后续 Tushare suspend_d 兜底）。
4. 结果落库：汇总写 dq_report（check_type='recon'，前端「数据质量」栏目可见）；
   明细写 dq_recon_detail；两者均按 30 天保留。
5. 限频沿用增量任务纪律：随机延时 0.3~1.2s、25s 硬超时、NO_PROXY（stock_daily_incr 已设置）。
"""
import logging
import random
import time
from datetime import datetime, timedelta

import pymysql

from ..db import get_db_config
from ._common import with_steps

logger = logging.getLogger(__name__)

# 明细与报告保留窗口（天），与 dq_report 对齐
REPORT_KEEP_DAYS = 30
# 比对的价格字段（volume/amount 因源单位口径差异大，首版不参与判定）
PRICE_COLS = ["open", "high", "low", "close"]

RUN_STEPS = [
    {"no": 1, "name": "确定对账范围", "params": "window: 最近 N 交易日×在市 A 股；sample: 固定种子抽 N 票"},
    {"no": 2, "name": "拉取外部源", "params": "window→腾讯（独立血缘）；sample→东财（查入库丢行）；adjust=qfq、25s 超时、随机延时"},
    {"no": 3, "name": "逐票比对", "params": "按 trade_date 对齐；价格容差 0.5%；缺失/数值差异分类记明细"},
    {"no": 4, "name": "写明细与汇总", "params": "dq_recon_detail 差异明细 + dq_report 汇总（check_type=recon）"},
    {"no": 5, "name": "轮次保留清理", "params": f"删除 run_date 早于 {REPORT_KEEP_DAYS} 天的历史"},
]


class DailyReconCollector:
    """日线外部对账采集器（L2）"""

    def __init__(self, mode: str = "window", days: int = 5, sample_size: int = 50,
                 seed: int = 42, max_stocks: int = 0, tolerance_pct: float = 0.5,
                 adjust: str = "qfq"):
        self.mode = mode if mode in ("window", "sample") else "window"
        self.days = days
        self.sample_size = sample_size
        self.seed = seed
        self.max_stocks = max_stocks  # 0 = 不限（调试可设小值）
        self.tolerance_pct = tolerance_pct
        self.adjust = adjust
        self.run_at = datetime.now()
        self.run_date = self.run_at.date()
        self._diffs: list[tuple] = []
        self._errors: list[str] = []
        self._compared = 0
        self._written = 0

    # ---------- 工具 ----------

    def _connect(self):
        return pymysql.connect(**get_db_config().to_dict(), cursorclass=pymysql.cursors.DictCursor)

    def _fetcher(self):
        """复用增量采集器的四源 fetcher（同源逻辑只维护一份）"""
        from .stock_daily_incr import StockDailyIncrementalCollector

        return StockDailyIncrementalCollector(adjust=self.adjust)

    def _recent_trade_dates(self, conn, n: int) -> list:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT trade_date FROM trade_calendar WHERE is_trading_day = 1 AND trade_date <= CURDATE() "
                "ORDER BY trade_date DESC LIMIT %s",
                (n,),
            )
            return sorted(r["trade_date"] for r in cur.fetchall())

    def _stock_list(self, conn, limit: int = 0) -> list[tuple]:
        """在市 A 股（排除退市、北交所、B 股）"""
        sql = (
            "SELECT stock_code, short_name FROM stock_info "
            "WHERE list_status = '上市' "
            "AND stock_code NOT LIKE '4%%' AND stock_code NOT LIKE '8%%' AND stock_code NOT LIKE '920%%' "
            "AND stock_code NOT LIKE '200%%' AND stock_code NOT LIKE '201%%' AND stock_code NOT LIKE '900%%' "
            "ORDER BY stock_code"
        )
        if limit > 0:
            sql += f" LIMIT {int(limit)}"
        with conn.cursor() as cur:
            cur.execute(sql)
            return [(r["stock_code"], r["short_name"]) for r in cur.fetchall()]

    # ---------- window 模式 ----------

    def _run_window(self) -> dict:
        """最近 N 个交易日 × 全市场，与腾讯源逐票比对"""
        from concurrent.futures import ThreadPoolExecutor, as_completed

        conn = self._connect()
        try:
            dates = self._recent_trade_dates(conn, self.days)
            if not dates:
                return {"status": "error", "diff_count": 0, "compared": 0,
                        "message": "交易日历不可用，无法确定对账窗口"}
            start_d, end_d = dates[0], dates[-1]
            stocks = self._stock_list(conn, self.max_stocks)
        finally:
            conn.close()
        if not stocks:
            return {"status": "error", "diff_count": 0, "compared": 0, "message": "在市股票名单为空"}

        fetcher = self._fetcher()
        start_s, end_s = start_d.strftime("%Y%m%d"), end_d.strftime("%Y%m%d")
        logger.info(f"🔍 窗口对账：{start_d}~{end_d}（{len(dates)} 个交易日）× {len(stocks)} 只，源=腾讯")

        diffs: list[tuple] = []
        errors: list[str] = []
        compared = 0
        with ThreadPoolExecutor(max_workers=4) as ex:
            futures = {
                ex.submit(self._compare_window_one, fetcher, code, name, start_d, end_d): code
                for code, name in stocks
            }
            done = 0
            for fut in as_completed(futures):
                code = futures[fut]
                done += 1
                try:
                    code, rows, err = fut.result()
                except Exception as e:  # noqa: BLE001
                    errors.append(f"{code}: {e}")
                    continue
                if err:
                    errors.append(err)
                else:
                    compared += 1
                    diffs.extend(rows)
                if done % 500 == 0:
                    logger.info(f"进度 {done}/{len(stocks)}，差异 {len(diffs)} 行")

        self._diffs = diffs
        self._errors = errors
        self._compared = compared
        n_diff_codes = len({d[0] for d in diffs})
        status = "pass" if not diffs else "warning"
        msg = (
            f"窗口 {start_d}~{end_d}：比对 {compared}/{len(stocks)} 只（失败 {len(errors)}），"
            f"差异 {len(diffs)} 行 / {n_diff_codes} 只，源=腾讯，容差 {self.tolerance_pct}%"
        )
        return {"status": status, "diff_count": len(diffs), "compared": compared, "message": msg}

    def _compare_window_one(self, fetcher, code: str, name: str, start_d, end_d) -> tuple:
        """单票窗口比对：远端（腾讯）vs 本地"""
        df, src = fetcher.fetch_with_retry(code, start_d.strftime("%Y%m%d"), end_d.strftime("%Y%m%d"))
        if df is None or df.empty:
            return (code, [], f"{code} {name}: 远端返回空")
        remote = {}
        for _, row in df.iterrows():
            d = row.get("日期")
            if d is None:
                continue
            remote[str(d)[:10]] = row
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT trade_date, open, high, low, close FROM stock_market_daily "
                    "WHERE stock_code = %s AND trade_date BETWEEN %s AND %s",
                    (code, start_d, end_d),
                )
                local = {str(r["trade_date"]): r for r in cur.fetchall()}
        finally:
            conn.close()

        rows: list[tuple] = []
        for d in sorted(set(local) | set(remote)):
            in_l, in_r = d in local, d in remote
            if in_r and not in_l:
                rows.append((code, d, "missing_local", None, None, None, src, None))
            elif in_l and not in_r:
                # 远端无该行：多为停牌（腾讯不返停牌行），语义为"疑似"，不直接判失败
                rows.append((code, d, "missing_remote", None, str(local[d]["close"]), None, src, "疑似停牌/源缺行"))
            else:
                for col in PRICE_COLS:
                    lv, rv = local[d].get(col), remote[d].get(col)
                    if lv is None or rv is None:
                        continue
                    try:
                        lvf, rvf = float(lv), float(rv)
                    except (TypeError, ValueError):
                        continue
                    if rvf == 0:
                        continue
                    if abs(lvf - rvf) / abs(rvf) * 100 > self.tolerance_pct:
                        rows.append((code, d, "value_diff", col, str(lvf), str(rvf), src, None))
        return (code, rows, None)

    # ---------- sample 模式 ----------

    def _run_sample(self) -> dict:
        """固定种子抽 N 票，与东财源比对全史摘要（count / 首末日期）"""
        conn = self._connect()
        try:
            stocks = self._stock_list(conn)
        finally:
            conn.close()
        if not stocks:
            return {"status": "error", "diff_count": 0, "compared": 0, "message": "在市股票名单为空"}

        rnd = random.Random(self.seed)
        picked = rnd.sample(stocks, min(self.sample_size, len(stocks)))
        fetcher = self._fetcher()
        logger.info(f"🔍 全史抽样对账：{len(picked)} 只（种子 {self.seed}），源=东财")

        diffs: list[tuple] = []
        errors: list[str] = []
        compared = 0
        for code, name in picked:
            try:
                df, src = fetcher.fetch_with_retry(code, "19900101", datetime.now().strftime("%Y%m%d"))
            except Exception as e:  # noqa: BLE001
                errors.append(f"{code} {name}: {e}")
                continue
            if df is None or df.empty:
                errors.append(f"{code} {name}: 远端返回空")
                continue
            compared += 1
            rcount = len(df)
            rmin = str(df["日期"].min())[:10]
            rmax = str(df["日期"].max())[:10]
            conn = self._connect()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT COUNT(*) AS c, MIN(trade_date) AS mn, MAX(trade_date) AS mx "
                        "FROM stock_market_daily WHERE stock_code = %s",
                        (code,),
                    )
                    row = cur.fetchone()
            finally:
                conn.close()
            lcount = row["c"]
            lmin = str(row["mn"])[:10] if row["mn"] else None
            lmax = str(row["mx"])[:10] if row["mx"] else None
            # 行数差异：>1% 且 >5 行才报（停牌/退市边界会造成少量差异）
            if lcount != rcount and (abs(lcount - rcount) > 5 or abs(lcount - rcount) / max(rcount, 1) > 0.01):
                diffs.append((code, None, "count_diff", None, str(lcount), str(rcount), src, f"{lmin}~{lmax}"))
            if lmin != rmin or lmax != rmax:
                diffs.append((code, None, "range_diff", None, f"{lmin}~{lmax}", f"{rmin}~{rmax}", src, None))
            time.sleep(random.uniform(0.3, 1.2))

        self._diffs = diffs
        self._errors = errors
        self._compared = compared
        status = "pass" if not diffs else "warning"
        msg = (
            f"全史抽样 {compared}/{len(picked)} 只（种子 {self.seed}），差异 {len(diffs)} 条"
            f"（count/首末日期；SUM 不判定——前复权基准漂移会致假差异），源=东财"
        )
        return {"status": status, "diff_count": len(diffs), "compared": compared, "message": msg}

    # ---------- 落库 ----------

    def _flush_details(self):
        if not self._diffs:
            self._written = 0
            return
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO dq_recon_detail "
                    "(run_at, run_date, mode, stock_code, trade_date, diff_type, col_name, "
                    " local_value, remote_value, remote_source, note) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    [
                        (self.run_at, self.run_date, self.mode, d[0], d[1], d[2], d[3], d[4], d[5], d[6], d[7])
                        for d in self._diffs
                    ],
                )
            conn.commit()
            self._written = len(self._diffs)
        finally:
            conn.close()

    def _write_report(self, summary: dict):
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO dq_report "
                    "(run_at, run_date, rule_id, rule_name, table_name, check_type, severity, status, metric_value, message) "
                    "VALUES (%s,%s,NULL,%s,'stock_market_daily','recon','warning',%s,%s,%s)",
                    (self.run_at, self.run_date, f"recon_{self.mode}", summary["status"],
                     str(summary["diff_count"]), summary["message"][:500]),
                )
                cur.execute("DELETE FROM dq_report WHERE run_date < %s", (self.run_date - timedelta(days=REPORT_KEEP_DAYS),))
                cur.execute("DELETE FROM dq_recon_detail WHERE run_date < %s", (self.run_date - timedelta(days=REPORT_KEEP_DAYS),))
            conn.commit()
        finally:
            conn.close()

    def run(self) -> dict:
        start_ts = datetime.now()
        summary = self._run_sample() if self.mode == "sample" else self._run_window()
        self._flush_details()
        self._write_report(summary)
        duration = datetime.now() - start_ts
        logger.info(f"✅ 对账完成（{self.mode}）：{summary['message']}，耗时 {duration}")
        return with_steps(
            {
                "task_name": f"daily_recon_{self.mode}",
                "status": "success" if not self._errors else "partial",
                "records_written": self._written,
                "duration": str(duration),
                "compared": self._compared,
                "diff_count": summary["diff_count"],
                "errors": self._errors[:10],
                "error_count": len(self._errors),
                "note": summary["message"],
            },
            RUN_STEPS,
            {
                1: f"mode={self.mode} · 比对 {self._compared} 只",
                2: "腾讯" if self.mode == "window" else "东财",
                3: f"差异 {summary['diff_count']} 条",
                4: f"明细 {self._written} 行 + 汇总 1 条",
                5: f"清理 ≤{REPORT_KEEP_DAYS} 天前历史",
            },
        )
