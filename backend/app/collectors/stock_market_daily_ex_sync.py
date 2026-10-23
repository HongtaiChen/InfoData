#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""stock_market_daily_ex 采集器 —— 归档扩展行情（不复权原始价）

为什么需要这个采集器
====================
`stock_market_daily_ex` 有 **16,891,313 行**（占全库 43.9%），但 `table_meta.writers=[]`
—— **此前没有任何代码能写它**，纯粹是历史快照。一旦本机损毁又无备份，
这 1690 万行就永久消失（这也是「只靠 git clone 只能恢复 53.8%」的主因）。
本采集器补上写入方后，clone → 重建可覆盖其中 **98.52%**。

口径（2026-10-17 与历史值逐字段实测对齐，不是照文档推演）
========================================================
| 列 | 来源 / 算法 | 实测依据 |
|---|---|---|
| open/high/low/close | 新浪 raw 原值 | 5 只样本（000001/000002/600519/300750/601318）单日对比 **0 差异** |
| volume | 接口 volume **÷ 100** | 历史值单位是「手」，接口给「股」；5 样本比值恒为 100.0000 |
| amount | 接口 amount | 一致（历史值更精确到分，接口到元） |
| turnover_ratio | 接口 turnover **× 100** | 接口给小数（0.003675）→ 本表存百分数（0.3675） |
| pre_close | 前一交易日 close | 历史值 **100% 为 NULL**（16,891,313 行全空），故不依赖接口 |
| change_amount | close − pre_close | 历史值 = 0.02 与 10.15−10.13 吻合 |
| change_pct | (close−pre_close)/pre_close×100，**保留 2 位** | 历史值 0.2000（2 位），实测连续 3 日全部吻合 |
| data_source | 固定 'AKSHARE' | 全表唯一取值就是它 |

⚠️ **三处与主表 `stock_market_daily` 的差异 —— 绝不可混用写入逻辑**
1. **volume 单位不同**：本表 = 手，主表 = 股（主表列注释写「手」是错的，
   实测同日 000001 本表 918090 / 主表 91808992）。
2. 本表**没有** `adj_factor`（不做复权）。
3. 本表 `change_pct` 是**名义涨跌幅**（2 位，未复权），主表是复权后的真实收益（4 位）。
   ⇒ 直接把主表的 `stock_daily_core.derive_rows` 搬过来会写错这两列。

已知不可重建域（如实标注，不假装能全量）
========================================
- **老北交所代码**（430 / 83x / 87x）：新浪 `bj430047`、`bj830799`、`bj871981`
  实测均返回 `JSONDecodeError`（空响应）。这部分 **245 只 / 249,999 行** 无法重建。
- `920` 段（北交所 2025-10 启用的新代码段）**可用**：`bj920819` 实测返回 22 行。
- 沪深部分 **5534 只 / 16,641,314 行 = 98.52%** 可重建。

票池
====
`stock_info` 全量，排除 B 股（200/201/900 段）与名称含「退」/「PT」的退市股。
老北交所代码本就不在 `stock_info` 中（实测 240 只 ex 独有股票在 `stock_info` 里 0 命中），
故票池天然不含它们，无需额外排除。

幂等与断点
==========
- 写库 `ON DUPLICATE KEY UPDATE` + 唯一键 `(stock_code, trade_date)`，重复跑不产生重复行。
- 每只股票独立定位窗口：库内 `MAX(trade_date)+1` → 今天；库内无数据 → `list_date` → 今天。
  ⇒ 中断后重跑自动跳过已完成部分，可分批（`max_codes`）推进。

⚠️ V8 预热（必须）
==================
新浪接口内部用 py_mini_racer(V8) 解 JS，V8 的 PartitionAlloc 禁止同进程并发初始化。
未预热直接开线程池会让**整个进程硬崩溃**（FATAL，非异常）。故 run() 在建池前先在主线程预热，
失败则退回单线程。与 `stock_daily_incr` 共用 `stock_daily_core.warmup_js_engine`。
"""
import logging
import os
import random
import socket
import time
from datetime import datetime, timedelta

import pymysql

from ..db import get_db_config
from ._common import with_steps
from .stock_daily_core import code_to_symbol, warmup_js_engine

# 国内数据源不走代理（本机若配了 HTTP 代理，访问新浪会 ProxyError）
os.environ["NO_PROXY"] = "*"
os.environ["no_proxy"] = "*"

# requests 默认无超时，遇退市/停牌股重试会卡死很久
socket.setdefaulttimeout(20)

logger = logging.getLogger(__name__)

DEFAULT_WORKERS = 8
# 单票请求间隔（秒）：并发下取较短区间，模拟人工节奏降低风控概率
REQUEST_DELAY_MIN = 0.15
REQUEST_DELAY_MAX = 0.45
FETCH_TIMEOUT = 25
# 重试 1 次即可：实测源侧缺失的票（如 sz000004）任何区间都返回空 JSON，
# 多试只会白等。2 次重试 × 25s 硬超时 = 单只最坏 50s，不足以拖垮整批。
RETRY_TIMES = 1
# 为算第一行的 pre_close，往前多取的**自然日**（覆盖长假最多 8 天 + 余量）
PRE_LOOKBACK_DAYS = 20

# 长期无新数据判定（天）：库内最后一条早于该阈值 ⇒ 视为退市/长期停牌。
# 与 stock_daily_incr.STALE_DAYS 取值一致。实测新浪对这类票（002002/002013/
# 000150/002118 等）在任何区间都返回空 JSON，重试纯属浪费。
STALE_DAYS = 730

INSERT_COLS = [
    "stock_code", "trade_date", "open", "high", "low", "close", "pre_close",
    "change_amount", "change_pct", "volume", "amount", "turnover_ratio",
    "update_time", "data_source",
]

# ⚠️ update_time 用 %s 占位而非 SQL 的 NOW()：pymysql 仅在「VALUES 全为占位符」时
# 才会把多行合并成一条 INSERT，夹了 NOW() 会静默退化成逐行往返（差一个数量级）
_INSERT_SQL = f"""
    INSERT INTO stock_market_daily_ex
        ({", ".join(INSERT_COLS)})
    VALUES ({", ".join(["%s"] * len(INSERT_COLS))})
    ON DUPLICATE KEY UPDATE
        open = VALUES(open), high = VALUES(high), low = VALUES(low),
        close = VALUES(close), pre_close = VALUES(pre_close),
        change_amount = VALUES(change_amount), change_pct = VALUES(change_pct),
        volume = VALUES(volume), amount = VALUES(amount),
        turnover_ratio = VALUES(turnover_ratio),
        data_source = VALUES(data_source), update_time = VALUES(update_time)
"""

RUN_STEPS = [
    {"no": 1, "name": "读票池", "params": "stock_info 在市 A 股；排除 B 股（200/201/900）与退市（名称含退/PT）"},
    {"no": 2, "name": "逐票定位窗口", "params": "库内 MAX(trade_date)+1 → 今天；无数据 → list_date（上市日）→ 今天"},
    {"no": 3, "name": "预热 V8", "params": "主线程预热 py_mini_racer，失败退回单线程（否则线程池并发初始化会 FATAL 崩溃）"},
    {"no": 4, "name": "并发拉取 raw", "params": f"{DEFAULT_WORKERS} 线程 stock_zh_a_daily(adjust='')，单票 {FETCH_TIMEOUT}s 硬超时 + {RETRY_TIMES} 次退避重试"},
    {"no": 5, "name": "对齐历史口径", "params": "volume ÷100 转「手」；turnover ×100 转百分数；pre_close 取前一交易日 close；change_pct 保留 2 位"},
    {"no": 6, "name": "UPSERT 写库", "params": "唯一键 (stock_code, trade_date)，ON DUPLICATE KEY UPDATE 保证幂等"},
]

# 老北交所代码段：新浪不支持（实测 bj430047/bj830799/bj871981 均空响应）
_OLD_BJ_PREFIX = ("430", "83", "87")


def is_old_bj(code: str) -> bool:
    """老北交所/老三板代码（新浪不可用）。920 段不算，它是可用的新段。"""
    return code.startswith(_OLD_BJ_PREFIX)


class StockDailyExCollector:
    """stock_market_daily_ex 采集器（归档扩展行情 / 不复权原始价）"""

    def __init__(self, max_codes: int = 0, ranges: list | None = None,
                 start_date: str | None = None, end_date: str | None = None,
                 workers: int = DEFAULT_WORKERS, full_sweep: bool = False):
        """
        max_codes : 0 = 不限；>0 = 本轮只处理前 N 只（分批推进用）
        ranges    : 显式区间 [(code, startYYYYMMDD, endYYYYMMDD), ...]，给定时忽略自动定位
        start_date: 无本地数据时的回补起点（默认用 list_date）
        end_date  : 截止日（默认今天）
        full_sweep: True = 无视库内水位线，一律从 list_date 起全量重拉（source=all）
        """
        self.max_codes = int(max_codes or 0)
        self.ranges = ranges or []
        self.start_date = start_date
        self.end_date = end_date or datetime.now().strftime("%Y%m%d")
        self.workers = max(1, int(workers))
        self.full_sweep = bool(full_sweep)
        self.db = get_db_config().to_dict()
        self._written = 0
        self._errors: list[str] = []
        self._skipped_old_bj = 0
        self._skipped_stale = 0
        self._no_data: list[str] = []

    # ---------- DB ----------
    def _connect(self):
        return pymysql.connect(**self.db)

    def _stock_list(self, conn) -> list[tuple]:
        """票池：stock_info 中排除 B 股与退市股 → [(code, name, list_date_yyyymmdd)]"""
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT stock_code, short_name, list_date
                FROM stock_info
                WHERE stock_code NOT LIKE '200%'   -- 深B 老段
                  AND stock_code NOT LIKE '201%'   -- 深B 新段（如 201872 招港B）
                  AND stock_code NOT LIKE '900%'   -- 沪B
                  AND (short_name IS NULL OR short_name NOT LIKE '%退%')
                  AND (short_name IS NULL OR short_name NOT LIKE 'PT%')
                ORDER BY stock_code
                """
            )
            return [
                (r[0], r[1], r[2].strftime("%Y%m%d") if r[2] else None)
                for r in cur.fetchall()
            ]

    def _existing_last(self, conn) -> dict[str, str]:
        """各票库内最新交易日 {code: yyyymmdd}（一次聚合，避免逐票查询）"""
        with conn.cursor() as cur:
            cur.execute(
                "SELECT stock_code, MAX(trade_date) FROM stock_market_daily_ex "
                "GROUP BY stock_code"
            )
            return {
                r[0]: r[1].strftime("%Y%m%d")
                for r in cur.fetchall() if r[1]
            }

    # ---------- 抓取 ----------
    @staticmethod
    def _fetch_raw(code: str, start: str, end: str):
        """新浪不复权原始日线（单源；本表无复权口径，故不拉 hfq）"""
        import akshare as ak
        return ak.stock_zh_a_daily(
            symbol=code_to_symbol(code), start_date=start, end_date=end, adjust=""
        )

    @staticmethod
    def _fetch_with_timeout(code: str, start: str, end: str) -> object:
        """独立线程 + future.result(timeout) 给单次请求加硬超时。

        akshare 部分路径不受 socket.setdefaulttimeout 约束，遇异常票会永久挂起。
        超时即放弃该票（泄漏线程仅占内存，不阻塞主流程）。
        """
        from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout

        ex = ThreadPoolExecutor(max_workers=1)
        try:
            fut = ex.submit(StockDailyExCollector._fetch_raw, code, start, end)
            return fut.result(timeout=FETCH_TIMEOUT)
        except FutureTimeout:
            raise RuntimeError(f"{code} 拉取超时(>{FETCH_TIMEOUT}s)")
        finally:
            ex.shutdown(wait=False)

    def _fetch_retry(self, code: str, start: str, end: str):
        last = None
        for i in range(RETRY_TIMES + 1):
            try:
                return self._fetch_with_timeout(code, start, end)
            except Exception as e:  # noqa: BLE001 —— 网络异常种类多，统一重试
                last = e
                if i < RETRY_TIMES:
                    time.sleep(min(8, 1.5 * (2 ** i)) + random.uniform(0, 1))
        raise last  # type: ignore[misc]

    # ---------- 口径转换 ----------
    @staticmethod
    def _to_rows(code: str, df, eff_start: str, end: str) -> list[tuple]:
        """原始 DataFrame → 入库元组（严格对齐历史口径，见模块 docstring）"""
        import pandas as pd
        from decimal import Decimal, ROUND_HALF_UP

        def half_up(x: float, nd: int) -> Decimal:
            """四舍五入（历史值用 ROUND_HALF_UP，不是 Python 的银行家舍入）。

            实测差异来源：600519 于 2024-06-12 接口 3317050 股 → 33170.5 手，
            Python round() 得 33170（银行家舍入到偶数），历史值 33171。
            """
            return Decimal(str(x)).quantize(
                Decimal(1).scaleb(-nd), rounding=ROUND_HALF_UP
            )

        if df is None or len(df) == 0:
            return []
        d = df.copy()
        d["date"] = pd.to_datetime(d["date"])
        d = d.sort_values("date").reset_index(drop=True)

        # pre_close 取「前一交易日收盘」；批次首行靠 PRE_LOOKBACK_DAYS 多取的前一条兜住，
        # 仍无前值（新股首日）则留空 —— 历史表 pre_close 本就 100% 为 NULL，不影响一致性。
        d["pre_close"] = d["close"].shift(1)

        eff = pd.to_datetime(eff_start)
        d = d[(d["date"] >= eff) & (d["date"] <= pd.to_datetime(end))]
        if len(d) == 0:
            return []

        now = datetime.now()
        rows = []
        for _, r in d.iterrows():
            close = float(r["close"])
            pre = r["pre_close"]
            pre = None if pd.isna(pre) else float(pre)

            # ⚠️ 必须用 Decimal 做减法：float 下 (7.99-8.00)/8.00*100 会得到
            # -0.12499999999999999，ROUND_HALF_UP 后变成 -0.12，
            # 而历史值（当年按十进制算）= -0.13（实测 000002 于 2024-04-09）。
            if pre is None:
                chg_amt, chg_pct = None, None
            else:
                dc, dp = Decimal(str(close)), Decimal(str(pre))
                chg_amt = float(half_up(float(dc - dp), 3))
                chg_pct = (None if dp == 0
                           else float(half_up(float((dc - dp) / dp * 100), 2)))

            vol = r.get("volume")
            # 接口给「股」，本表存「手」：实测 5 样本比值恒为 100.0000
            vol = None if pd.isna(vol) else int(half_up(float(vol) / 100, 0))

            amt = r.get("amount")
            # ⚠️ 接口 amount 精度只到「元」，历史值精确到「分」
            # ⇒ 本列与历史值存在 ≤0.5 元的舍入差，不可修复（非算法问题）
            amt = None if pd.isna(amt) else float(half_up(float(amt), 2))

            tr = r.get("turnover")
            # 接口给小数（0.003675）→ 本表存百分数，且历史值只有 **2 位**小数（0.68 / 0.51）。
            # ⚠️ 该值基于**当前**流通股本，股本变动会让它与历史值有微小漂移，属可接受差异。
            tr = None if pd.isna(tr) else float(half_up(float(tr) * 100, 2))

            rows.append((
                code, r["date"].date(), float(r["open"]), float(r["high"]),
                float(r["low"]), close, pre, chg_amt, chg_pct,
                vol, amt, tr, now, "AKSHARE",
            ))
        return rows

    # ---------- 单票 ----------
    def _process_one(self, code: str, start: str, end: str) -> int:
        delay = random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX)
        time.sleep(delay)

        # 多取 PRE_LOOKBACK_DAYS 自然日，保证首行能算 pre_close
        fetch_start = (datetime.strptime(start, "%Y%m%d")
                       - timedelta(days=PRE_LOOKBACK_DAYS)).strftime("%Y%m%d")
        df = self._fetch_retry(code, fetch_start, end)
        rows = self._to_rows(code, df, start, end)
        if not rows:
            return 0

        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.executemany(_INSERT_SQL, rows)
            conn.commit()  # ⚠️ 必须显式 commit（pymysql autocommit 不可信）
        finally:
            conn.close()
        return len(rows)

    # ---------- 主流程 ----------
    def run(self) -> dict:
        t0 = time.time()
        conn = self._connect()
        try:
            # 显式区间模式不依赖 stock_info（也便于在空库/测试库中直接验证）
            if self.ranges:
                stocks: list[tuple] = []
                last_map: dict[str, str] = {}
            else:
                stocks = self._stock_list(conn)
                last_map = self._existing_last(conn)
        finally:
            conn.close()

        # 组装任务清单
        tasks: list[tuple[str, str, str]] = []
        if self.ranges:
            for code, s, e in self.ranges:
                tasks.append((code, s, e))
        else:
            for code, name, list_date in stocks:
                if is_old_bj(code):
                    self._skipped_old_bj += 1
                    continue
                last = None if self.full_sweep else last_map.get(code)
                if last:
                    # 库内已有数据但早已断更 ⇒ 退市/长期停牌，源侧无数据，跳过省时
                    gap = (datetime.now() - datetime.strptime(last, "%Y%m%d")).days
                    if gap > STALE_DAYS:
                        self._skipped_stale += 1
                        continue
                    start = (datetime.strptime(last, "%Y%m%d")
                             + timedelta(days=1)).strftime("%Y%m%d")
                else:
                    start = self.start_date or list_date
                if not start:
                    # 连上市日都缺 → 回退 3 年（防呆，不阻塞）
                    start = (datetime.now() - timedelta(days=1095)).strftime("%Y%m%d")
                if start > self.end_date:
                    continue  # 已最新
                tasks.append((code, start, self.end_date))

        if self.max_codes > 0:
            tasks = tasks[: self.max_codes]

        if not tasks:
            logger.info("无待采任务（票池已最新，或 ranges 为空）")
            return with_steps(
                {"records_written": 0, "error_count": 0, "errors": [],
                 "stocks_total": len(stocks), "tasks": 0,
                 "skipped_old_bj": self._skipped_old_bj,
                 "elapsed_sec": round(time.time() - t0, 1)},
                RUN_STEPS,
                {1: f"票池 {len(stocks)} 只", 2: "无待采", 6: "写入 0 行"},
            )

        logger.info(f"待采 {len(tasks)} 只（票池 {len(stocks)} 只，"
                    f"跳过老北交所 {self._skipped_old_bj} 只）")

        # V8 预热：必须在建池前、主线程内完成，否则并发初始化会 FATAL 崩溃
        warm = warmup_js_engine()
        warm_ok = warm.startswith("✅")
        self._effective_workers = self.workers if warm_ok else 1
        if not warm_ok:
            logger.warning(f"V8 预热失败（{warm}），无法并发，退回单线程")

        # 并发执行
        from concurrent.futures import ThreadPoolExecutor, as_completed

        done = 0
        with ThreadPoolExecutor(max_workers=self._effective_workers) as pool:
            fut_map = {
                pool.submit(self._process_one, c, s, e): c for c, s, e in tasks
            }
            for fut in as_completed(fut_map):
                code = fut_map[fut]
                done += 1
                try:
                    n = fut.result()
                    if n == 0:
                        self._no_data.append(code)
                    self._written += n
                except Exception as e:  # noqa: BLE001
                    msg = f"{code}: {type(e).__name__}: {str(e)[:80]}"
                    self._errors.append(msg)
                    logger.warning(f"✗ {msg}")
                if done % 50 == 0 or done == len(tasks):
                    logger.info(f"进度 {done}/{len(tasks)}，累计写入 {self._written:,} 行，"
                                f"失败 {len(self._errors)}")

        elapsed = round(time.time() - t0, 1)
        result = {
            "records_written": self._written,
            "error_count": len(self._errors),
            "errors": self._errors[:20],
            "stocks_total": len(stocks),
            "tasks": len(tasks),
            "no_data_count": len(self._no_data),
            "skipped_old_bj": self._skipped_old_bj,
            "skipped_stale": self._skipped_stale,
            "workers": self._effective_workers,
            "elapsed_sec": elapsed,
        }
        logger.info(f"完成：写入 {self._written:,} 行 / {len(tasks)} 只，"
                    f"失败 {len(self._errors)}，无数据 {len(self._no_data)}，耗时 {elapsed}s")
        return with_steps(result, RUN_STEPS, {
            1: f"票池 {len(stocks)} 只",
            2: f"待采 {len(tasks)} 只（跳过老北交所 {self._skipped_old_bj}）",
            3: warm,
            4: f"{self._effective_workers} 线程，{len(self._errors)} 只失败",
            5: f"写入 {self._written:,} 行",
            6: f"{len(tasks)} 只完成",
        })
