#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
InvestBuddy 股票日线增量采集器（实际价 + 后复权因子口径）

口径（2026-09-20 与全库重建 stock_daily_rebuild 对齐，两者共用 stock_daily_core）
================================================================================
- open/high/low/close = **不复权实际成交价**（新浪 adjust=''）
- adj_factor          = 后复权累计因子 = hfq_close / close（历史永不变）
      实际价   = close
      后复权价 = close * adj_factor
      前复权价 = close * adj_factor / latest(adj_factor)     ← 永不失效
      收益率   = (c2*f2)/(c1*f1) - 1
- volume              = 股（单位自适应判定，见 core.canon）
- turnover_ratio      = 百分数 %（单位自适应判定）

**为什么增量必须一起改**：本表此前就是「重建/增量两套口径」写歪的 —— volume
（股 vs 手）与 turnover（百分数 vs 小数）两列都出现过同一列混存两种量纲。
所以增量与重建现在共用 `stock_daily_core` 的同一份转换逻辑，不再各写各的。

数据源阶梯（2026-09-20 重排）
================================================================================
    新浪 → 腾讯 → 东财 → Tushare
**raw 与 hfq 必须来自同一个源**。

为什么把新浪从第 3 提到第 1：
1. **实测可用性**：本机东财 push2 全线 `RemoteDisconnected`；腾讯 `stock_zh_a_hist_tx`
   可用但 volume 单位**按股票而变**（sh600519 是股、sz000001 是手，实测），
   换手率也时而缺失；只有新浪 raw/hfq/turnover/volume 四项齐全且口径一致。
2. **因子口径基准**：各源 hfq 的基准不同 —— 实测 2026-09-18 的 600519，
   新浪 hfq/raw = 8.8826 而腾讯 = 7.0625。全库重建用新浪，增量若换源会把
   adj_factor 写成两段互不衔接的阶梯。故 `FACTOR_SOURCE = "sina"` 作为
   因子口径基准源，命中其他源时**不写因子**（置 NULL + 计入 factor_missing）。

⚠️ V8 预热（必须）
================================================================================
新浪接口内部用 py_mini_racer(V8) 解 JS，V8 的 PartitionAlloc 禁止同进程并发初始化，
未预热直接开线程池会让**整个进程硬崩溃**（FATAL, 非异常）。故 run() 在建线程池前
先在主线程预热一次；预热失败则退回单线程。详见 stock_daily_core.warmup_js_engine。

去重
================================================================================
写库用 INSERT IGNORE + 唯一键 (stock_code, trade_date)。
**刻意不用 UPSERT**：增量窗口的第一行是「上一交易日」（用于给后续行提供前收盘基准），
它的 pre_close/涨跌幅按定义为空，UPSERT 会把库内已有的正确值冲成 NULL。
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
from .stock_daily_core import (
    INSERT_ALL_COLS,
    canon,
    code_to_symbol as _core_code_to_symbol,
    derive_rows,
    js_engine_ready,
    warmup_js_engine,
    with_update_time,
)

# 国内数据源不走代理（本机若配置了 HTTP 代理，访问东财等国内站点会 ProxyError）
os.environ["NO_PROXY"] = "*"
os.environ["no_proxy"] = "*"

# 关键：requests 默认无超时，遇退市股/停牌股网络重试会卡死很久。
# 设置 socket 级默认超时，任何请求最多等 N 秒即失败降级。
socket.setdefaulttimeout(15)

logger = logging.getLogger(__name__)

# 默认增量窗口（天）：覆盖停牌/长假等缺口
DEFAULT_DAYS_BACK = 15
# 单只股票请求间隔基准（秒）——实际用随机延时 [MIN, MAX]，模拟人工节奏，降低被风控概率
REQUEST_DELAY_MIN = 0.3
REQUEST_DELAY_MAX = 1.2
# 每个数据源重试次数
RETRY_TIMES = 2
# 疑似退市/长期停牌判定：最后数据日期距今超过该天数则默认跳过
STALE_DAYS = 730  # 2 年
# 因子口径基准源：命中其他源时不写 adj_factor（各源 hfq 基准不同，混写会断阶）
FACTOR_SOURCE = "sina"

# 写库列序与 stock_daily_core.INSERT_COLS 100% 同源（重建/增量共用一份列定义）。
# ⚠️ update_time 用 %s 占位而非 SQL 的 NOW()：pymysql 仅在「VALUES 全为占位符」时才把
# 多行合并成一条 INSERT，夹了 NOW() 会静默退化成逐行往返（2026-09-20 实测，吞吐差一个数量级）。
_INSERT_SQL = f"""
    INSERT IGNORE INTO stock_market_daily
        ({", ".join(INSERT_ALL_COLS)})
    VALUES ({", ".join(["%s"] * len(INSERT_ALL_COLS))})
"""

# 运行步骤链模板（供前端「数据流·整链拓扑」展示运行逻辑）。
# 约定：每步 {no, name, params(可选说明/关键参数)}；
# run() 结束时把模板 + 当轮实录合并为 run_detail 写入 task_runs.run_detail，
# 代码变更后下一轮运行即携带最新模板，无需人工维护元数据。
RUN_STEPS = [
    {"no": 1, "name": "读候选名单", "params": "stock_info 在市 A 股，含北交所（920 段）"},
    {"no": 2, "name": "定位增量窗口", "params": f"有本地数据→从最新日+1 增量；无本地数据→从 list_date（上市日）拉全史；"
                                                f"连上市日也缺才回退 {DEFAULT_DAYS_BACK} 交易日窗口"},
    {"no": 3, "name": "逐只增量判定", "params": f"最后日期 ≥ 判定线即跳过；STALE_DAYS={STALE_DAYS} 疑似退市软跳过"},
    {"no": 4, "name": "并发抓取 × 源阶梯", "params": "4 线程（建池前先预热 V8）；新浪→腾讯→东财→Tushare，raw/hfq 成对同源"},
    {"no": 5, "name": "归一 + 派生", "params": "实际价 + 后复权因子；volume 自适应归股、换手率自适应归 %；涨跌幅按 hfq 计真实收益"},
    {"no": 6, "name": "INSERT IGNORE 写库", "params": "唯一键 (stock_code, trade_date)；刻意非 UPSERT（保护窗口首行已有前收盘）"},
]


def code_to_symbol(code: str) -> str:
    """6位代码 -> 带交易所前缀（sh600519 / sz000001 / bj920599）

    实现已收敛到 `stock_daily_core.code_to_symbol`（重建与增量共用一份）。
    该实现覆盖 302 段（深交所新创业板段，库内实测已有 302132）、92 段（北交所
    2025-10-09 切换后的新码段）与 B 股段 —— 缺任一个都会退化成裸码、取数必然失败。
    """
    return _core_code_to_symbol(code)


class StockDailyIncrementalCollector:
    """股票日线增量采集器（实际价 + 后复权因子）"""

    def __init__(self, days_back: int = DEFAULT_DAYS_BACK, adjust: str = "",
                 max_stocks: int = 0, include_stale: bool = False, include_bj: bool = True,
                 backfill_ranges: list | None = None):
        self.days_back = days_back
        # adjust 参数**已废弃**：口径固定为「实际价 + 后复权因子列」，不再按调用方切换
        # 复权。保留参数仅为兼容既有调用方（daily_recon / tasks.run）不破坏签名。
        self.adjust = adjust
        self.max_stocks = max_stocks  # 0 = 不限（全量）
        self.include_stale = include_stale  # True = 也采集疑似退市/长期停牌股
        # True = 也采集北交所。2026-09-20 由 False 改为 True —— 原先跳过的理由是
        # 「四级源均不支持北交所」，实测该结论**已不成立**：新浪 stock_zh_a_daily
        # 对 bj920xxx 完全可用（20/20，含全史），且全库重建已把北交所历史补齐。
        # 若继续跳过，北交所行情会从重建日起再次停更。
        self.include_bj = include_bj
        # 定向回补（L3 修复闭环）：[(code, start_yyyymmdd, end_yyyymmdd), ...]
        # 提供时只按显式区间回补，绕过增量定位、500 天截断与陈旧软跳过
        self.backfill_ranges = backfill_ranges or []
        self.db = get_db_config().to_dict()
        self._written = 0
        self._errors: list[str] = []
        self._factor_missing: list[str] = []
        self._degraded: list[str] = []
        self._effective_workers = 4

    # ---------- 数据库工具 ----------
    def _connect(self):
        return pymysql.connect(**self.db)

    def get_last_trade_date(self, conn) -> str | None:
        """存量数据最新交易日（无数据返回 None）"""
        with conn.cursor() as cur:
            cur.execute("SELECT MAX(trade_date) FROM stock_market_daily")
            row = cur.fetchone()
            return row[0].strftime("%Y%m%d") if row and row[0] else None

    def _last_trading_day(self, conn, end_date: str) -> str:
        """最近已收盘交易日（<= end_date）。

        以 trade_calendar 为准（is_trading_day=1 且 <= 当天）；日历缺失/为空时
        用星期启发兜底（周一回退 3 天到上周五，周日回退 2 天到周五，其余回退 1 天）。

        该日期是"每只股票应推进到的目标日"——用它替代"全局 MAX(trade_date)"做
        增量判定线，否则一旦全市场齐到某天，之后每晚所有股票都会被误判为
        "已最新"而跳过，日线永远停在原地（2026-09 数据停更根因）。
        """
        with conn.cursor() as cur:
            cur.execute(
                "SELECT MAX(trade_date) FROM trade_calendar "
                "WHERE is_trading_day = 1 AND trade_date <= %s",
                (end_date,),
            )
            row = cur.fetchone()
        if row and row[0]:
            return row[0].strftime("%Y%m%d")
        # 兜底：星期启发（不依赖日历表）
        d = datetime.strptime(end_date, "%Y%m%d")
        back = {0: 3, 6: 2}.get(d.weekday(), 1)  # 周一回看 3 天(上周五)；周日回看 2 天(周五)
        return (d - timedelta(days=back)).strftime("%Y%m%d")

    def get_stock_list(self, conn, include_bj: bool = True) -> tuple[list[tuple], int]:
        """获取股票代码列表，返回 (列表, 北交所数量)。

        过滤规则：
        - 排除 B 股（200%/900% 前缀）——数据源不支持
        - 排除名称含"退"或以"PT"开头的已退市股——数据源已无数据，逐个重试耗时巨大
        - include_bj=True（默认）时包含北交所 4/8/92 段；False 则剔除并返回只数
        排序规则：疑似退市（无数据或最后数据极旧）排最后，先处理活跃缺口股。

        返回 4 元组 (code, short_name, last_date, list_date)，日期均为 'YYYYMMDD' 或 None。

        ⚠️ list_date（2026-09-24 新增，P0 修复）：**从无本地数据的股票（多为新股）
        必须用「上市日」而不是「近 N 天窗口」作为起始日**。原实现统一用
        `cutoff = 目标日 - days_back(15)`，导致新股首次入库只能补最近 15 天、
        上市初期的历史永久空洞（实测 688836 缺 08-19~09-08、001399 缺 06-26~09-08）。
        背景：89 只在市股曾因名册漏收而零日线，2026-09-24 换源后首次进入候选池。
        """
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT a.stock_code, a.short_name,
                       (SELECT MAX(b.trade_date) FROM stock_market_daily b
                        WHERE b.stock_code = a.stock_code) AS last_date,
                       a.list_date
                FROM stock_info a
                WHERE a.stock_code NOT LIKE '200%'      -- 深B（老代码段）
                  AND a.stock_code NOT LIKE '201%'      -- 深B（新代码段，如 201872 招港B）
                  AND a.stock_code NOT LIKE '900%'      -- 沪B
                  AND a.short_name NOT LIKE '%退%'      -- 退市股
                  AND a.short_name NOT LIKE 'PT%'       -- PT 退市股
                ORDER BY
                    (last_date IS NULL OR last_date < DATE_SUB(CURDATE(), INTERVAL 370 DAY)) ASC,  -- 活跃优先
                    a.stock_code ASC
                """
            )
            rows = [
                (
                    r[0], r[1],
                    r[2].strftime("%Y%m%d") if r[2] else None,   # last_date
                    r[3].strftime("%Y%m%d") if r[3] else None,   # list_date
                )
                for r in cur.fetchall()
            ]
        if not include_bj:
            bj = [r for r in rows if r[0].startswith(("4", "8", "920"))]
            rows = [r for r in rows if not r[0].startswith(("4", "8", "920"))]
            return rows, len(bj)
        return rows, 0

    # ---------- 数据源（成对降级：raw 与 hfq 必须同源） ----------
    # 为什么成对：各源 hfq 的基准不同（实测 600519 于 2026-09-18：
    # 新浪 hfq/raw = 8.8826、腾讯 = 7.0625）。raw 与 hfq 若跨源配对，
    # 反推出的因子就是两个基准的混合体，会污染 adj_factor 序列。
    def _fetch_pair_sina(self, code: str, start: str, end: str) -> tuple:
        import akshare as ak
        symbol = code_to_symbol(code)
        return (
            ak.stock_zh_a_daily(symbol=symbol, start_date=start, end_date=end, adjust=""),
            ak.stock_zh_a_daily(symbol=symbol, start_date=start, end_date=end, adjust="hfq"),
        )

    def _fetch_pair_tencent(self, code: str, start: str, end: str) -> tuple:
        import akshare as ak
        symbol = code_to_symbol(code)
        return (
            ak.stock_zh_a_hist_tx(symbol=symbol, start_date=start, end_date=end, adjust=""),
            ak.stock_zh_a_hist_tx(symbol=symbol, start_date=start, end_date=end, adjust="hfq"),
        )

    def _fetch_pair_eastmoney(self, code: str, start: str, end: str) -> tuple:
        import akshare as ak
        return (
            ak.stock_zh_a_hist(symbol=code, period="daily", start_date=start, end_date=end, adjust=""),
            ak.stock_zh_a_hist(symbol=code, period="daily", start_date=start, end_date=end, adjust="hfq"),
        )

    def _fetch_pair_tushare(self, code: str, start: str, end: str) -> tuple:
        token = os.getenv("TUSHARE_TOKEN", "")
        if not token:
            raise RuntimeError("Tushare 备源未配置（缺少环境变量 TUSHARE_TOKEN），跳过")
        import tushare as ts
        ts.set_token(token)
        symbol = code + (".SH" if code.startswith("6") else ".SZ" if code.startswith(("0", "3")) else ".BJ")
        return (
            ts.pro_bar(ts_code=symbol, start_date=start, end_date=end, adj=None),
            ts.pro_bar(ts_code=symbol, start_date=start, end_date=end, adj="hfq"),
        )

    @staticmethod
    def _fetch_with_timeout(fetcher, code: str, start: str, end: str, timeout: int = 25) -> tuple:
        """给单个数据源请求加硬超时。

        akshare 部分接口（如腾讯源分页循环）不受 socket.setdefaulttimeout 约束，
        遇异常股票可能永久挂起。用独立线程 + future.result(timeout) 兜底：
        超时即放弃该源（线程泄漏仅占内存，不阻塞主流程）。
        """
        from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout

        ex = ThreadPoolExecutor(max_workers=1)
        try:
            fut = ex.submit(fetcher, code, start, end)
            return fut.result(timeout=timeout)
        except FutureTimeout:
            raise RuntimeError(f"{code} 请求超时(>{timeout}s)，已放弃该源")
        finally:
            ex.shutdown(wait=False)  # 不等挂死的线程，让其泄漏

    def fetch_with_retry(self, code: str, start: str, end: str) -> tuple:
        """源阶梯：新浪→腾讯→东财→Tushare，每源返回 (raw_df, hfq_df)。

        返回 (raw_df, hfq_df, source_name)。东财近年频繁被风控（返回 HTTP 000 /
        RemoteDisconnected），故被风控时快速降级：东财只重试 1 次，其余源 RETRY_TIMES 次。
        每源带 25s 硬超时，杜绝单只股票挂死拖垮整个任务。
        """
        sources = [
            ("sina", self._fetch_pair_sina),
            ("tencent", self._fetch_pair_tencent),
            ("eastmoney", self._fetch_pair_eastmoney),
            ("tushare", self._fetch_pair_tushare),
        ]
        errors = []
        for name, fetcher in sources:
            times = 1 if name in ("eastmoney", "sina") else RETRY_TIMES
            for _attempt in range(times):
                try:
                    raw, hfq = self._fetch_with_timeout(fetcher, code, start, end)
                    if raw is not None and not raw.empty:
                        if name != sources[0][0]:
                            logger.info(f"↩️ {code} 降级使用 {name} 源")
                        return raw, hfq, name
                    raise RuntimeError(f"{name} 返回空数据")
                except Exception as e:                     # noqa: BLE001
                    errors.append(f"{name}:{e}")
                    time.sleep(0.8)
        raise RuntimeError("全部数据源失败: " + "; ".join(errors[-6:]))

    # ---------- 写库 ----------
    def to_rows(self, code: str, raw, hfq, source: str) -> tuple:
        """源 DataFrame → (入库元组列表, 因子跳变次数, 因子是否缺失)

        因子口径守卫：命中非 FACTOR_SOURCE（新浪）时把 adj_factor 置 NULL 并回报 ——
        各源 hfq 基准不同，硬写会把因子序列写成两段互不衔接的阶梯，
        比留空更糟（留空可由 DQ 规则与修复脚本显式发现）。
        """
        rows, n_cut, degraded = derive_rows(
            code,
            canon(raw, source),
            canon(hfq, source) if hfq is not None and not hfq.empty else None,
            source.upper(),
        )
        factor_missing = False
        if rows and source != FACTOR_SOURCE:
            rows = [r[:12] + (None,) + r[13:] for r in rows]
            factor_missing = True
        return rows, n_cut, (degraded or factor_missing)

    def insert_rows(self, conn, rows: list[tuple]) -> int:
        """批量写入（INSERT IGNORE 去重），返回尝试写入行数"""
        if not rows:
            return 0
        with conn.cursor() as cur:
            cur.executemany(_INSERT_SQL, with_update_time(rows))
        conn.commit()
        return len(rows)

    # ---------- 主流程 ----------
    def _process_one(self, code: str, name: str, stock_last: str | None,
                     list_date: str | None, cutoff: str, end_date: str) -> tuple:
        """处理单只股票（worker 内独立 DB 连接，避免 pymysql 连接跨线程复用），
        返回 (code, name, written, source, error)

        起始日三级判定（2026-09-24 P0 修复，list_date 为新增参数）：
          1. 本地已有数据 → 从「本地最新日 - 1」增量补齐（原行为）；
          2. 本地无数据但有上市日 → **从上市日拉全史**。这是新增分支：原实现落到
             第 3 档的 15 天窗口，会让新股上市初期的历史永久空洞。
          3. 连上市日也没有 → 回退近 days_back 天窗口（保守兜底）。
        """
        conn = self._connect()
        try:
            if stock_last:
                start_date = (datetime.strptime(stock_last, "%Y%m%d") - timedelta(days=1)).strftime("%Y%m%d")
            elif list_date:
                start_date = list_date
            else:
                start_date = cutoff
            gap_days = (datetime.strptime(end_date, "%Y%m%d") - datetime.strptime(start_date, "%Y%m%d")).days
            if gap_days > 500:
                start_date = (datetime.strptime(end_date, "%Y%m%d") - timedelta(days=500)).strftime("%Y%m%d")
                logger.warning(f"⚠️ {code} {name} 缺口 {gap_days} 天，限制拉取最近 500 天")
            raw, hfq, src = self.fetch_with_retry(code, start_date, end_date)
            rows, _n_cut, degraded = self.to_rows(code, raw, hfq, src)
            n = self.insert_rows(conn, rows)
            if degraded:
                self._degraded.append(code)
            if src != FACTOR_SOURCE:
                self._factor_missing.append(code)
            time.sleep(random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX))
            return (code, name, n, src, None)
        except Exception as e:                              # noqa: BLE001
            return (code, name, 0, None, f"{code} {name}: {e}")
        finally:
            conn.close()

    def _process_one_explicit(self, code: str, name: str, start: str, end: str) -> tuple:
        """显式区间回补（L3 修复闭环）：不做增量定位、不做 500 天截断、不做陈旧跳过"""
        conn = self._connect()
        try:
            raw, hfq, src = self.fetch_with_retry(code, start, end)
            rows, _n_cut, degraded = self.to_rows(code, raw, hfq, src)
            n = self.insert_rows(conn, rows)
            if degraded:
                self._degraded.append(code)
            if src != FACTOR_SOURCE:
                self._factor_missing.append(code)
            time.sleep(random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX))
            return (code, name, n, src, None)
        except Exception as e:                              # noqa: BLE001
            return (code, name, 0, None, f"{code} {name}: {e}")
        finally:
            conn.close()

    def _run_backfill(self) -> dict:
        """按 backfill_ranges 定向回补（L3）：缺口清单 → 单票区间拉取 → INSERT IGNORE 补行

        口径：与主表一致（实际价 + 后复权因子），因为走的是同一套 to_rows()。
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        start_ts = datetime.now()
        names: dict[str, str] = {}
        conn = self._connect()
        try:
            codes = sorted({r[0] for r in self.backfill_ranges})
            if codes:
                fmt = ",".join(["%s"] * len(codes))
                with conn.cursor() as cur:
                    cur.execute(
                        f"SELECT stock_code, short_name FROM stock_info WHERE stock_code IN ({fmt})",
                        codes,
                    )
                    names = {r[0]: r[1] for r in cur.fetchall()}
        finally:
            conn.close()

        todo = [(c, names.get(c, ""), s, e) for c, s, e in self.backfill_ranges]
        if self.max_stocks > 0:
            todo = todo[: self.max_stocks]
        logger.info(f"🔧 定向回补 {len(todo)} 个区间（涉及 {len({t[0] for t in todo})} 只股票）")

        warm_msg = warmup_js_engine()
        workers = 4 if js_engine_ready() else 1
        logger.info("%s；回补并发 %s 线程", warm_msg, workers)

        source_stats: dict = {}
        ok_codes: list[str] = []
        ok_ranges: list[tuple] = []  # 成功补到数据的区间（供 L3 精确标记 fixed）
        self._written = 0
        self._errors = []
        self._factor_missing = []
        self._degraded = []
        done = 0
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = {
                ex.submit(self._process_one_explicit, code, name, s, e): (code, s, e)
                for code, name, s, e in todo
            }
            for fut in as_completed(futures):
                fcode, fstart, fend = futures[fut]
                code, name, n, src, err = fut.result()
                done += 1
                if err:
                    self._errors.append(err)
                    logger.warning(f"❌ 回补失败 {err}")
                else:
                    self._written += n
                    source_stats[src] = source_stats.get(src, 0) + 1
                    if n > 0:
                        ok_codes.append(code)
                        ok_ranges.append((fcode, fstart, fend))
                    logger.info(f"✅ 回补 {code} {name} 写入 {n} 行（源 {src}）")
                if done % 200 == 0:
                    logger.info(f"进度 {done}/{len(todo)}，已写 {self._written} 行")

        duration = datetime.now() - start_ts
        result = {
            "task_name": "daily_backfill",
            "status": "success" if not self._errors else "partial",
            "records_written": self._written,
            "duration": str(duration),
            "ranges": len(todo),
            "codes": len({t[0] for t in todo}),
            "source_stats": source_stats,
            "ok_codes": sorted(set(ok_codes)),
            "ok_ranges": ok_ranges,
            "errors": self._errors[:10],
            "error_count": len(self._errors),
            "factor_missing": sorted(set(self._factor_missing)),
            "degraded": sorted(set(self._degraded)),
        }
        logger.info(
            f"✅ 定向回补完成：{len(todo)} 区间，写入 {self._written} 行，失败 {len(self._errors)}，耗时 {duration}"
        )
        return result

    def run(self) -> dict:
        """执行增量采集，返回统计信息（并发采集，默认 4 线程）

        提供 backfill_ranges 时走定向回补分支（L3 修复闭环），否则走常规增量。
        """
        if self.backfill_ranges:
            return self._run_backfill()
        from concurrent.futures import ThreadPoolExecutor, as_completed

        start_ts = datetime.now()
        conn = self._connect()
        try:
            global_last = self.get_last_trade_date(conn)
            end_date = datetime.now().strftime("%Y%m%d")

            if global_last is None:
                global_last = (datetime.now() - timedelta(days=365)).strftime("%Y%m%d")
                logger.info("无存量数据，回退拉取近 1 年")

            # 最近已收盘交易日（交易日历为准）：每只股票本轮应推进到的目标日。
            # 判定线必须基于它，而不是"全局 MAX(trade_date)"——否则全市场齐到某天后，
            # 每晚任务都会把所有股票判为已最新而跳过，日线永久停更。
            target_day = self._last_trading_day(conn, end_date)
            # 新股/首次拉取的起始窗口：目标日回看 days_back 天
            cutoff = (datetime.strptime(target_day, "%Y%m%d") - timedelta(days=self.days_back)).strftime("%Y%m%d")
            logger.info(
                f"最近已收盘交易日: {target_day}，增量目标(≥此日期算已最新): {target_day}，"
                f"新股回看窗口起始: {cutoff}（存量最新 {global_last}）"
            )

            stocks, bj_count = self.get_stock_list(conn, include_bj=self.include_bj)
            if bj_count:
                logger.info(f"跳过北交所 {bj_count} 只（include_bj=False）")
            else:
                bj_in = len([s for s in stocks if s[0].startswith(("4", "8", "920"))])
                logger.info(f"北交所已纳入候选 {bj_in} 只")
            # 软跳过：最后数据日期距今超过 STALE_DAYS（2 年）的，视为疑似退市/长期停牌，
            # 默认不采集（数据源基本已不支持，逐个重试成本极高），可用 include_stale=True 放开。
            if not self.include_stale:
                stale_cutoff = (datetime.now() - timedelta(days=STALE_DAYS)).strftime("%Y%m%d")
                active, stale = [], []
                for s in stocks:
                    # 无数据（NULL）可能是新股/首次采集，必须尝试；只有"有数据但过于久远"才算疑似退市
                    if s[2] is None or s[2] >= stale_cutoff:
                        active.append(s)
                    else:
                        stale.append(s)
                stocks = active
                skipped_stale = len(stale)
                logger.info(f"跳过疑似退市/长期停牌 {skipped_stale} 只（最后数据早于 {stale_cutoff}）")
            else:
                skipped_stale = 0
            if self.max_stocks > 0:
                stocks = stocks[: self.max_stocks]
            logger.info(f"共 {len(stocks)} 只股票待检查")

            skipped = 0
            todo = []
            for code, name, stock_last, stock_list_date in stocks:
                # 增量判定：该股数据是否已推进到最近已收盘交易日？是 → 跳过；否 → 补缺口
                if stock_last and stock_last >= target_day:
                    skipped += 1
                    continue
                todo.append((code, name, stock_last, stock_list_date))
            logger.info(f"实际待采集 {len(todo)} 只（已推进到 {target_day} 跳过 {skipped} 只）")

            # ⚠️ 建线程池前必须先在主线程预热 V8（否则新浪接口并发调用会让进程硬崩溃）
            warm_msg = warmup_js_engine()
            workers = 4 if js_engine_ready() else 1
            logger.info("%s；实际并发 %s 线程", warm_msg, workers)

            source_stats = {}
            self._written = 0
            self._errors = []
            self._factor_missing = []
            self._degraded = []
            done = 0
            with ThreadPoolExecutor(max_workers=workers) as ex:
                futures = {
                    ex.submit(self._process_one, code, name, stock_last, stock_list_date,
                              cutoff, end_date): (code, name)
                    for code, name, stock_last, stock_list_date in todo
                }
                for fut in as_completed(futures):
                    code, name, n, src, err = fut.result()
                    done += 1
                    if err:
                        self._errors.append(err)
                        logger.warning(f"❌ {code} {name} 采集失败: {err}")
                    else:
                        self._written += n
                        source_stats[src] = source_stats.get(src, 0) + 1
                        if n == 0:
                            logger.info(f"⏭️ {code} {name} 无新增数据")
                    if done % 200 == 0:
                        logger.info(f"进度 {done}/{len(todo)}，已写 {self._written} 行")

            duration = datetime.now() - start_ts
            missing = sorted(set(self._factor_missing))
            degraded = sorted(set(self._degraded))
            if missing:
                logger.warning(f"⚠️ {len(missing)} 只的 adj_factor 未写入（源非新浪，因子基准不同，"
                               f"留空待修复）如 {missing[:5]}")
            result = {
                "task_name": "stock_daily_incr",
                "status": "success" if not self._errors else "partial",
                "records_written": self._written,
                "duration": str(duration),
                "skipped": skipped,
                "skipped_stale": skipped_stale,
                "skipped_bj": bj_count,
                "source_stats": source_stats,
                "errors": self._errors[:10],
                "error_count": len(self._errors),
                "factor_missing": missing,
                "degraded": degraded,
                # 运行步骤链：模板 + 当轮实录（供前端整链拓扑/运行逻辑展示）
                "run_steps": [
                    {
                        "no": s["no"],
                        "name": s["name"],
                        "params": s.get("params"),
                        "value": {
                            1: f"候选 {len(stocks)} 只（含北交所）",
                            2: f"判定线 {target_day}（回看窗口起 {cutoff}）",
                            3: f"已最新跳过 {skipped} · 疑似退市跳过 {skipped_stale} · 北交所跳过 {bj_count}",
                            4: f"命中 {source_stats or '无（本轮全部失败）'} · 并发 {workers}",
                            5: f"因子缺失 {len(missing)} 只 · 涨跌幅降级 {len(degraded)} 只",
                            6: f"写入 {self._written} 行",
                        }.get(s["no"]),
                    }
                    for s in RUN_STEPS
                ],
                "cutoff": cutoff,
                "stale_cutoff": locals().get("stale_cutoff"),
            }
            logger.info(
                f"✅ 完成: 写入 {self._written} 行, 跳过 {skipped} 只, 疑似退市跳过 {skipped_stale} 只, "
                f"源分布 {source_stats}, 失败 {len(self._errors)} 只, 因子缺失 {len(missing)} 只, "
                f"耗时 {duration}"
            )
            return result
        finally:
            conn.close()


def _f(v) -> float | None:
    """安全转 float"""
    import pandas as pd
    if v is None or pd.isna(v):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _i(v) -> int | None:
    """安全转 int"""
    import pandas as pd
    if v is None or pd.isna(v):
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None
