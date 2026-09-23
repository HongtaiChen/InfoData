#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
InvestBuddy 全库股票日线口径重建（stock_market_daily · 实际价 + 后复权因子列）

为什么要重建（2026-09-20 实测立）
================================================================================
库内 stock_market_daily 1,815 万行 / 5,787 只，存的**不是实际成交价**，而是
「每只票按自己最后一次写入日做基准的前复权价」—— 基准因股而异、写入后再不更新，
等价于一批**基准不统一的冻结复权数**。三类铁证：

1. **末端是实际价、历史不是** —— 245 只北交所旧段票的末日 VWAP **240/240** 全部
   落在 [low, high] 内（实际成交价的自洽判据，复权价会与 amount/volume 脱钩）；
   而库内 600519 于 2016-01-04 的 `low = -35.78` —— 减法式前复权把早期价复成了负数。
2. **与独立双源的实际价不符** —— 600519 于 2024-01-02 库内 1602.65，腾讯与新浪
   两个独立源的实际价一致为 1685.01，差额恰为之后累计分红。
3. **每只票的偏离倍数各不相同** —— 002594 库内/实际价：2011 年 0.2094、
   2019 年 0.2772、2022 年 0.3229、2026-09-18 恰好 **1.0000**。
   「末端=1、历史被逐段压低」正是「过期前复权」的指纹。

同时暴露两个既存量纲缺陷（本次一并修正）：

- **volume 单位混用**：判据「(amount/close) ÷ volume」，全库 5,135 只中 4,721 只为「股」、
  **413 只为「手」**、另 1 只异常。根因是采集器逐批漂移（TENCENT 源 4,720 股 + 412 手、
  EASTMONEY 源 851 只全为手），同一张表并存两种量纲。**本次统一为「股」**
  （源原生口径 + 库内多数派，无需二次换算）。
- **turnover_ratio 口径混用**：2025-09 中旬由「百分数」切换为「小数」（相差 100 倍）。
  **本次统一为「百分数 %」**，与列注释一致；下游 market_style_sync 的自适应阈值
  （中位数 < 0.3 判为小数）× 100 在统一后自动失效不再触发，行为不变。

本次口径（2026-09-20 用户决策）
================================================================================
- **价格 = 不复权实际价**（新浪 `adjust=''`）。实际成交价**永不随时间改变**，
  写入一次永久有效，且与 amount/volume 自洽。
- **复权因子入列 `adj_factor`**（= hfq_close / raw_close）。选后复权因子而非前复权，
  是因为**后复权因子的历史值永不变**（基准是第一日），新增数据与分红都不会回改历史。
- 由此三种口径**全部可派生且都不过期**：
    实际价   = `close`
    后复权价 = `close * adj_factor`
    前复权价 = `close * adj_factor / latest(adj_factor)`   ← 分母永远取最新日，分红后自动跟上
    收益率   = `(c2*f2)/(c1*f1) - 1`                       ← 因子整体缩放不影响结果

为什么**不能**直接存前复权价
--------------------------------------------------------------------------------
前复权的定义里含「以最新日为基准」—— 它的值**每次分红都会全史变化**。本项目是
「增量 append 后冻结」模式、没有全量重算机制，存进去的 qfq 在下一个分红日就会退化成
「又一批基准不统一的冻结数」，即上述三类症状的复现。
**前复权是「视图」而不是「数据」**：做成派生公式它永远鲜活，固化成数值必然过期。

写表
================================================================================
- `stock_market_daily`：UPSERT 重建后的 OHLCV + 派生列 + `adj_factor`。
  **只 UPSERT、绝不 DELETE** —— 新浪未返回的库内独有日期（实测占比约 0.13%）原样保留。
- 不写任何其他表。

与其他采集器的边界（勿越界）
================================================================================
- `stock_daily_incr` ：沪深日更增量（东财→腾讯→新浪→Tushare 四级降级；硬编码跳过北交所）
- `bj_stock_sync`    ：北交所名册（代码 / 行业），不碰行情
- `index_market_sync`：指数行情（index_market_daily / dc_index_market），与个股日线是两套表
本采集器是**一次性重建工具**，不进入日常排期（日常增量仍由 stock_daily_incr 负责）。

幂等与可重跑
================================================================================
按唯一键 (stock_code, trade_date) UPSERT，重跑只刷新值、不产生重复行。
分批断点由调用方通过 `codes` 参数控制（把待处理代码切块传入即可续跑）。
"""
import logging
import os
import random
import socket
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import pymysql

import akshare as ak

from ..db import get_db_config
from ._common import call_with_retry, is_source_missing, with_steps

# 口径内核（与日常增量 stock_daily_incr **共用同一份转换逻辑**，
# 否则重建刚统一好的口径会在次日被增量重新写歪 —— 本表的 volume / turnover
# 双口径病史就是这么来的）。详见 stock_daily_core 模块头。
from .stock_daily_core import (
    FACTOR_TOL,                                  # noqa: F401  保留导出（阈值仍在 core 定义）
    INSERT_SQL as _CORE_INSERT_SQL,
    canon,
    code_to_symbol as _core_code_to_symbol,
    derive_rows,
    js_engine_ready,
    num as _num,
    warmup_js_engine,
    with_update_time,
)

# 国内数据源不走代理（本机若配置 HTTP 代理，访问新浪等国内站点会 ProxyError）
os.environ["NO_PROXY"] = "*"
os.environ["no_proxy"] = "*"

# requests 默认无超时，遇退市/停牌股会卡很久；socket 级兜底
socket.setdefaulttimeout(20)

logger = logging.getLogger(__name__)

SOURCE = "SINA"
# 退市股兜底源（新浪整只无返回时启用，见 _fetch 上方源阶梯注释）
SOURCE_TENCENT = "TENCENT"
TABLE = "stock_market_daily"

# 因子阶梯化阈值 FACTOR_TOL 与「单位自适应判定」「派生列口径」均已收敛到
# `stock_daily_core`（与日常增量共用），此处不再重复定义 —— 阈值取 0.3% 的理由
# （真实除权跳变 0.1%~2% vs 2 位小数舍入噪声 ~0.05%）见该模块头。

TIMEOUT_PER_CALL = 40
REQUEST_DELAY_MIN = 0.15
REQUEST_DELAY_MAX = 0.5
DEFAULT_WORKERS = 3
# 每累积这么多行提交一批（单票最多可达约 8,000 行，故按**行数**而非票数控制批大小）
DEFAULT_FLUSH_ROWS = 5000

RUN_STEPS = [
    {"no": 1, "name": "读候选名单",
     "params": f"库内 {TABLE} 已有全部股票（含退市，非 stock_info 在册）——重建目标是「库内全量口径统一」"},
    {"no": 2, "name": "抓新浪双口径",
     "params": "ak.stock_zh_a_daily(symbol, adjust='') 取实际价 + adjust='hfq' 取后复权价；每只 2 次请求"},
    {"no": 3, "name": "反推复权因子",
     "params": f"hfq_close/raw_close → 按「相对变化 > {FACTOR_TOL:.1%}」分段、每段取中位数（去 2 位小数舍入噪声）"},
    {"no": 4, "name": "重算派生列",
     "params": "涨跌幅取 hfq 比值（含除权调整的真实收益）→ 反推 pre_close；volume 归「股」/ turnover 归「%」"},
    {"no": 5, "name": "UPSERT 写库",
     "params": f"唯一键 (stock_code, trade_date)，每累积 {DEFAULT_FLUSH_ROWS:,} 行提交一批；只更新不删除"},
    {"no": 6, "name": "失败重试",
     "params": "首轮失败票再跑一轮（源侧瞬态抖动）；仍失败记 error 并列出代码"},
    {"no": 7, "name": "结果巡检",
     "params": "覆盖只数 / 写入行数 / 因子跳变次数 / 异常值计数（close<=0、volume<0、|涨跌幅|>31%）"},
]


def _code_to_symbol(code: str) -> str:
    """6 位代码 → 带交易所前缀（sh600519 / sz000001 / bj920599）

    实现已收敛到 `stock_daily_core.code_to_symbol`（重建与增量共用一份）。
    该实现比早期版本多覆盖三段，缺任一个都会退化成裸码、取数必然失败：
      ① 「92」段 —— 北交所 2025-10-09 切换后的独立代码段；
      ② 「302」段 —— 深交所 2025 年新启用的创业板段（库内实测已有 302132）；
      ③ B 股段（900→sh / 200、201→sz）。
    """
    return _core_code_to_symbol(code)


class StockDailyRebuildCollector:
    """全库日线口径重建（实际价 raw + 后复权因子 adj_factor）"""

    def __init__(self, workers: int = DEFAULT_WORKERS, codes: list[str] | None = None,
                 flush_rows: int = DEFAULT_FLUSH_ROWS, max_stocks: int = 0,
                 retry_rounds: int = 1):
        self.workers = max(1, int(workers))
        self.codes = [str(c).strip() for c in codes] if codes else None
        self.flush_rows = max(1, int(flush_rows))
        self.max_stocks = int(max_stocks)
        self.retry_rounds = max(0, int(retry_rounds))
        # 实际并发：V8 预热成功才用 self.workers，否则 run() 会降为 1
        self._effective_workers = self.workers
        # 批量写入失败时逐行降级所发现的坏行：(code, date, 原因)。
        # 见 _flush —— 存在的意义是「不让一行坏值连坐整批 300 只」，
        # 同时**不掩盖**：坏行仍进 error_count 与 steps 明细。
        self._bad_rows: list[tuple] = []

    # ---------------------------------------------------------------- 名单
    def _load_codes(self, conn) -> list[str]:
        """候选名单 = 库内 stock_market_daily 已有的全部股票（含退市）。

        为什么不用 stock_info 在册名单：重建的目标是「库内已有数据的口径统一」，
        而库内 5,787 只比当前在册更多（含已退市）。只按在册取，会让退市股的历史
        永远停在旧口径上。与 _common.load_universe 的并集思路一致，但这里
        以「库内全量」为准更彻底。
        """
        with conn.cursor() as cur:
            cur.execute(f"SELECT DISTINCT stock_code FROM {TABLE} ORDER BY stock_code")
            codes = [str(r[0]) for r in cur.fetchall() if r[0]]
        if self.codes:
            want = set(self.codes)
            codes = [c for c in codes if c in want]
        if self.max_stocks:
            codes = codes[: self.max_stocks]
        return codes

    # ------------------------------------------------------------ 抓数与转换
    # 源阶梯：新浪为主（因子基准统一），**腾讯为退市股兜底**
    #
    # 为什么必须有腾讯这一档（2026-09-20 实测立，见 .workbuddy/tmp/diag_delisted_src2.py）：
    # 新浪对退市 / 已作废代码是**整只无返回**，不是「缺几天」——
    #   000003 / 000005 / 000013 / 000015 / 000018 / 000023 六只全抛
    #   `akshare.utils.demjson.JSONDecodeError('No value to decode')`。
    # 而库内这批退市股的历史仍是**过期的前复权价**（实测 000001 早期 close = -2.490，
    # 且 vwap 与 close 相差三个数量级），不重建就等于把负价与基准漂移永久留在表里。
    # 腾讯对同一批代码**有完整数据**（实测 000005 7,222 行 / 1991-01-02~2024-03-05、
    # 000003 2,465 行 / 1991-07-03~2002-04-26），是它们唯一可达的源；
    # 东财实测 `RemoteDisconnected`（被风控），Tushare 需付费 token，都不可依赖。
    #
    # ⚠️ 与增量采集器的一处**有意不同**：stock_daily_incr 命中非新浪源时把 adj_factor
    # 置 NULL，因为那里是「在既有新浪因子序列上补 15 天」，跨源写会把两个基准拼接成
    # 断阶。而这里整只票**只有一个源**、全史一次性重写，故**倾向于写因子**，
    # 否则退市股就白白失去了后复权能力。
    #
    # 🔴 勘误（2026-09-22，实锤）：上面这层「整只票单源 ⇒ 因子内部自洽」的推理
    # **对腾讯源不成立**。实测 akshare 1.18.94 的 `stock_zh_a_hist_tx(adjust="hfq")`
    # 在退市/低价票上返回的序列会递减到**负**（600811：04-01=2.31 → 04-08=0.42
    # → 04-09=-0.01 → 04-14=-1.06），而同一接口 `adjust="qfq"` 与 `adjust=""`
    # 返回**逐值相同**（即复权参数未生效）。用它推 hfq/raw 因子等于把「减法式
    # 前复权的漂移」当成「除权阶梯」，既污染 adj_factor 也污染涨跌幅。
    # 现行处置见 `stock_daily_core.derive_rows` 的「hfq 口径体检」——
    # 只要 hfq 序列含非正价格即**整只票弃用 hfq**（降级不复权口径 + adj_factor 全 NULL）。
    # 详见 docs/昨收与涨跌幅列失真取证_2026-09-22.md。
    def _fetch_sina(self, code: str):
        """新浪：raw 与 hfq 成对取回。返回 (raw, hfq) 或 (None, None) 表示源无此票。"""
        sym = _code_to_symbol(code)
        try:
            raw = call_with_retry(ak.stock_zh_a_daily, TIMEOUT_PER_CALL, 3, 1.5,
                                  symbol=sym, adjust="")
        except Exception as e:                      # noqa: BLE001
            if is_source_missing(e):
                return None, None                   # 源无此票（退市/已作废代码）
            raise
        if raw is None or raw.empty or "close" not in raw.columns:
            return None, None
        hfq = None
        try:
            hfq = call_with_retry(ak.stock_zh_a_daily, TIMEOUT_PER_CALL, 2, 1.5,
                                  symbol=sym, adjust="hfq")
        except Exception as e:                      # noqa: BLE001
            if is_source_missing(e):
                hfq = None                          # 有 raw 无 hfq → 涨跌幅降级
            else:
                logger.warning("%s hfq 抓取失败，涨跌幅降级为不复权口径: %s", code, repr(e)[:120])
        return raw, hfq

    def _fetch_tencent(self, code: str):
        """腾讯：退市股兜底源。raw 与 hfq 成对取回（同源保证因子基准一致）。"""
        sym = _code_to_symbol(code)
        start = "19900101"
        end = time.strftime("%Y%m%d")
        try:
            raw = call_with_retry(ak.stock_zh_a_hist_tx, TIMEOUT_PER_CALL, 2, 1.5,
                                  symbol=sym, start_date=start, end_date=end, adjust="")
        except Exception as e:                      # noqa: BLE001
            logger.warning("%s 腾讯 raw 抓取失败: %s", code, repr(e)[:120])
            return None, None
        if raw is None or raw.empty:
            return None, None
        hfq = None
        try:
            hfq = call_with_retry(ak.stock_zh_a_hist_tx, TIMEOUT_PER_CALL, 2, 1.5,
                                  symbol=sym, start_date=start, end_date=end, adjust="hfq")
        except Exception as e:                      # noqa: BLE001
            logger.warning("%s 腾讯 hfq 抓取失败（涨跌幅降级）: %s", code, repr(e)[:120])
        return raw, hfq

    def _fetch(self, code: str):
        """按源阶梯取 (raw, hfq, 源名)；全部源都无此票时返回 (None, None, None)。

        「源无此票」的判定走 `_common.is_source_missing`（按类名 + 消息），
        **不能**写 `except json.JSONDecodeError`：akshare 抛的是自带 demjson 实现的
        同名类 `akshare.utils.demjson.JSONDecodeError`，它不是 stdlib 的子类，
        按类型捕获永不命中（2026-09-20 实测，含退市股的 300 只批次里 63 只因此
        被误判为「失败」、白跑一整轮重试，而 `源无数据` 恒为 0）。
        """
        raw, hfq = self._fetch_sina(code)
        src = SOURCE
        if raw is None:
            # 新浪整只无返回 → 退市股兜底走腾讯（见上方源阶梯注释）
            raw, hfq = self._fetch_tencent(code)
            src = SOURCE_TENCENT
            if raw is not None and not raw.empty:
                logger.info("↩️ %s 新浪无数据，退市股兜底改用腾讯源", code)
        time.sleep(random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX))
        if raw is None or raw.empty:
            return None, None, None
        return raw, hfq, src

    def _to_rows(self, code: str, raw: pd.DataFrame, hfq: pd.DataFrame | None, source: str):
        """源 DataFrame → (写库元组列表, 因子跳变次数)

        实现收敛到 `stock_daily_core.derive_rows`（与日常增量共用），本方法只负责
        把源原始列名送进 `canon()` 归一化。**等价性已用黄金样本验证**：
        600519/002594/000001/302132/920599 共 23,469 行逐值比对 0 差异
        （见 .workbuddy/tmp/golden_capture.py → golden_verify.py）。
        """
        rows, n_cut, _degraded = derive_rows(code, canon(raw, source.lower()),
                                             canon(hfq, source.lower()) if hfq is not None else None,
                                             source)
        return rows, n_cut

    def _work(self, code: str):
        raw, hfq, src = self._fetch(code)
        if raw is None:
            return None, 0, False, None             # 全源无此票（退市太久等）
        rows, n_cut = self._to_rows(code, raw, hfq, src)
        degraded = hfq is None or hfq.empty         # hfq 缺失 → 涨跌幅降级为不复权口径
        return rows, n_cut, degraded, src

    # ---------------------------------------------------------------- 写库
    # 与增量共用同一条 UPSERT（列序一致，避免「两个采集器写同一张表却列序不同」）
    _SQL = _CORE_INSERT_SQL

    def _flush(self, cur, buf: list[tuple]) -> int:
        """写库。批量失败时**降级逐行**：健康行照写、坏行登记。

        为什么需要（2026-09-20 实测事故）
        ------------------------------------------------------------------
        stage4 批次 13/20（600600~600899）整批 300 只白跑，根因只是**一行**
        `turnover_ratio` 超出 `decimal(8,4)` 上限：
            DataError(1264, "Out of range value for column 'turnover_ratio' at row 335")
        executemany 是「单条多行 INSERT」的原子往返 —— 一行坏值即整批（约 30 万行）
        全丢；而执行器对该批走 `continue`、**连完成标记都不落**，于是这 300 只票
        悄悄停在旧前复权口径上（rebuild_done.txt 缺的正是 600600~600899）。

        处置原则＝「不连坐、也不掩盖」：
          - 健康行照常落库（不再因个别坏行白跑整批）；
          - 坏行进 self._bad_rows → error_count → steps 明细，**明确暴露**；
          - **不做数值钳制**。把越界值截成上限只是把确定的数据问题伪装成
            「写入成功」，用「看起来能跑」换掉「真的对」。
        """
        if not buf:
            return 0
        # executemany + 多行 VALUES（pymysql 只在「全占位符」时才会合并，见 core 注释）；
        # ON DUPLICATE KEY UPDATE 的受影响行数：更新计 2、插入计 1
        payload = with_update_time(buf)
        try:
            cur.executemany(self._SQL, payload)
            return len(buf)
        except pymysql.err.DataError as e:
            conn = getattr(cur, "connection", None)
            if conn is not None:
                conn.rollback()                     # 清掉失败的 executemany（单条语句，无部分写入）
            bad_before = len(self._bad_rows)
            ok = 0
            for row in payload:
                try:
                    cur.execute(self._SQL, row)
                    ok += 1
                except Exception as e2:             # noqa: BLE001 - 逐行定位坏行
                    # ⚠️ 这里**绝不能** rollback。实测教训（2026-09-20，600654 单票验证）：
                    # 首版在此 rollback，把**同一事务中坏行之前**已 execute 的 334 行一并
                    # 作废 —— 巡检显示「已重建 7,795 / 合计 8,129」，差的正是那 334 行，
                    # 而写入计数却是 8,129，两个数字打架才暴露出来。
                    # MySQL 的 DataError 是**语句级**错误，事务仍然可用，继续执行即可，
                    # 最后由调用方统一 commit。
                    self._bad_rows.append((row[0], row[1], repr(e2)[:120]))
            logger.warning("批量写入失败（%s），已降级逐行：健康行 %s / 本批坏行 %s；样例 %s",
                           repr(e)[:90], ok, len(self._bad_rows) - bad_before,
                           self._bad_rows[bad_before:bad_before + 3])
            return ok

    # ---------------------------------------------------------------- 主流程
    def run(self) -> dict:
        t0 = time.time()
        conn = pymysql.connect(**get_db_config().to_dict())
        codes = self._load_codes(conn)

        # ⚠️ 必须在建线程池之前、在主线程里预热 V8：新浪接口内部用 py_mini_racer，
        # 其 PartitionAlloc 禁止同进程并发初始化，未预热就开线程池会让整个进程硬崩溃
        # （实测，见 stock_daily_core.warmup_js_engine 的注释与最小实验）。
        # 预热失败则不硬闯 → 退回单线程，慢但不会崩。
        warm_msg = warmup_js_engine()
        workers = self.workers if js_engine_ready() else 1
        logger.info("%s；实际并发 %s 线程", warm_msg, workers)
        self._effective_workers = workers
        logger.info("日线重建候选 %s 只（workers=%s）", len(codes), workers)

        done = skipped = failed = written = cut_total = 0
        degraded: list[str] = []                    # hfq 缺失、降级为不复权算收益的票
        errors: list[str] = []
        by_src: dict[str, int] = {}                 # 各源实际出票数（新浪 / 腾讯兜底）
        pending: list[str] = list(codes)

        try:
            cur = conn.cursor()
            for rnd in range(self.retry_rounds + 1):
                if not pending:
                    break
                if rnd:
                    logger.info("第 %s 轮重试 %s 只", rnd, len(pending))
                buf: list[tuple] = []
                still: list[str] = []
                with ThreadPoolExecutor(max_workers=self._effective_workers) as ex:
                    futs = {ex.submit(self._work, c): c for c in pending}
                    for k, fut in enumerate(as_completed(futs), 1):
                        code = futs[fut]
                        try:
                            rows, n_cut, deg, src = fut.result()
                        except Exception as e:            # noqa: BLE001
                            if rnd >= self.retry_rounds:
                                failed += 1
                                errors.append(f"{code}: {repr(e)[:150]}")
                            else:
                                still.append(code)
                            continue
                        if rows is None:
                            skipped += 1
                            continue
                        buf.extend(rows)
                        done += 1
                        cut_total += n_cut
                        by_src[src] = by_src.get(src, 0) + 1
                        if deg:
                            degraded.append(code)
                        if len(buf) >= self.flush_rows:
                            written += self._flush(cur, buf)
                            conn.commit()
                            buf.clear()
                            logger.info("  进度 %s/%s 只，已写 %s 行", k, len(pending), written)
                written += self._flush(cur, buf)
                conn.commit()
                pending = still

            # 巡检：异常值计数。
            # ⚠️ 必须**限定在本次处理的代码范围**内（2026-09-20 实测教训）：
            # 首版写成全表统计，而 `SUM(data_source = 'SINA')` / `COUNT(*)` 在 1,815 万行
            # 上没有任何可用索引 → 每调用一次 run() 就要全表扫一遍（实测单次 140s+）。
            # 调用方（stage4 runner）按 300 只一批循环调 run() → 20 批就是 45 分钟白耗 IO。
            # 限定 stock_code IN (...) 后走唯一索引，毫秒级；语义也更准（只看本批处理的票）。
            if codes and len(codes) <= 2000:
                ph = ", ".join(["%s"] * len(codes))
                scope_sql = f"WHERE stock_code IN ({ph})"
                scope_args = list(codes)
            else:
                # 代码数过多（整库一次跑）时不拼 IN 列表，退回全表统计并接受其代价
                scope_sql, scope_args = "", []
            cur.execute(f"""SELECT
                  SUM(close IS NULL OR close <= 0) bad_close,
                  SUM(volume IS NOT NULL AND volume < 0) bad_vol,
                  SUM(change_pct IS NOT NULL AND ABS(change_pct) > 31) bad_pct,
                  SUM(adj_factor IS NULL AND data_source IN (%s, %s)) no_factor,
                  SUM(data_source IN (%s, %s)) rebuilt
                FROM {TABLE} {scope_sql}""",
                        tuple([SOURCE, SOURCE_TENCENT, SOURCE, SOURCE_TENCENT] + scope_args))
            r = cur.fetchone()
            bad_close, bad_vol, bad_pct = r[0] or 0, r[1] or 0, r[2] or 0
            no_factor, rebuilt = r[3] or 0, r[4] or 0
            cur.execute(f"SELECT COUNT(*) FROM {TABLE} {scope_sql}", tuple(scope_args))
            total_rows = cur.fetchone()[0]
            cur.close()
        finally:
            conn.close()

        elapsed = time.time() - t0
        src_desc = " / ".join(f"{k} {v} 只" for k, v in sorted(by_src.items())) or "无"
        msg = (f"日线口径重建：处理 {done}/{len(codes)} 只（源无数据 {skipped} / 失败 {failed}），"
               f"写入 {written:,} 行，因子跳变 {cut_total:,} 次，耗时 {elapsed:.0f}s")
        logger.info("✅ %s", msg)
        logger.info("   出票源构成：%s", src_desc)

        # 坏行（越界 / 类型不符）不阻塞整批，但**必须计入 error 并曝光**，
        # 否则「写入成功」会把数据问题盖住 —— 见 _flush 的处置原则。
        bad_note = [f"{c}@{d}: {r}" for c, d, r in self._bad_rows[:20]]
        if self._bad_rows:
            logger.warning("⚠️ 本批有 %s 行因超范围/类型不符被跳过（已计入 error）",
                           len(self._bad_rows))

        return with_steps(
            {"records_written": written, "error_count": len(errors) + len(self._bad_rows),
             "errors": (errors + bad_note)[:50], "note": msg},
            RUN_STEPS,
            {
                1: f"候选 {len(codes)} 只（库内全量，含退市）",
                2: f"成功抓取 {done} 只 · 源无数据 {skipped} 只 · 失败 {failed} 只 · 出票源 {src_desc}",
                3: f"因子跳变 {cut_total:,} 次（≈ 疑似除权事件数）",
                4: (f"降级为不复权口径算涨跌幅 {len(degraded)} 只（如 {', '.join(degraded[:5])}）"
                    if degraded else "全部按 hfq 口径算涨跌幅"),
                5: f"写入 {written:,} 行 · 本批已重建 {rebuilt:,} / 本批合计 {total_rows:,}",
                6: f"重试后仍失败 {failed} 只" if failed else "无失败",
                7: f"异常：close≤0 {bad_close} · volume<0 {bad_vol} · |涨跌幅|>31% {bad_pct} "
                   f"· 本批已重建行中 adj_factor 空 {no_factor}"
                   + (f" · 坏行跳过 {len(self._bad_rows)}" if self._bad_rows else ""),
            },
        )
