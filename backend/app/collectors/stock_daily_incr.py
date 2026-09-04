#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
InfoData 股票日线增量采集器
从存量最新交易日开始，增量补齐股票日 K 线到最新交易日。
- 数据源四级降级：东财 stock_zh_a_hist → 腾讯 stock_zh_a_hist_tx → 新浪 stock_zh_a_daily → Tushare
- 去重：INSERT IGNORE + 唯一键 (stock_code, trade_date)
- 记录：task_runs 作业记录 + 失败重试
"""
import logging
import os
import random
import socket
import time
from datetime import datetime, timedelta

import pymysql
import pandas as pd

from ..db import get_db_config

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


def code_to_symbol(code: str) -> str:
    """6位代码 -> 带交易所前缀（sh600519 / sz000001 / bj830799）"""
    code = code.strip()
    if code.startswith(("600", "601", "603", "605", "688", "689")):
        return "sh" + code
    if code.startswith(("000", "001", "002", "003", "300", "301")):
        return "sz" + code
    if code.startswith(("4", "8", "920")):
        return "bj" + code
    return code


def normalize_df(df: pd.DataFrame, source: str) -> pd.DataFrame:
    """将不同数据源的列名归一化为统一中文列名，并补充昨收/涨跌幅"""
    if df is None or df.empty:
        return pd.DataFrame(columns=["日期", "开盘", "最高", "最低", "收盘", "昨收", "涨跌额", "涨跌幅", "成交量", "成交额", "换手率"])
    d = df.copy()
    if source == "eastmoney":
        # 东财: 日期/开盘/收盘/最高/最低/成交量/成交额/振幅/涨跌幅/涨跌额/换手率
        d = d.rename(columns={
            "日期": "日期", "开盘": "开盘", "收盘": "收盘", "最高": "最高", "最低": "最低",
            "成交量": "成交量", "成交额": "成交额", "涨跌幅": "涨跌幅", "涨跌额": "涨跌额",
            "换手率": "换手率",
        })
    elif source == "tencent":
        # 腾讯: date/open/close/high/low/volume/turnover/amount
        d = d.rename(columns={
            "date": "日期", "open": "开盘", "close": "收盘", "high": "最高", "low": "最低",
            "volume": "成交量", "amount": "成交额", "turnover": "换手率",
        })
    elif source == "sina":
        # 新浪: date/open/high/low/close/volume/amount/outstanding_share/turnover
        d = d.rename(columns={
            "date": "日期", "open": "开盘", "high": "最高", "low": "最低", "close": "收盘",
            "volume": "成交量", "amount": "成交额", "turnover": "换手率",
        })
    elif source == "tushare":
        # Tushare: trade_date/open/high/low/close/pre_close/change/pct_chg/vol/amount
        d = d.rename(columns={
            "trade_date": "日期", "open": "开盘", "high": "最高", "low": "最低", "close": "收盘",
            "pre_close": "昨收", "change": "涨跌额", "pct_chg": "涨跌幅",
            "vol": "成交量", "amount": "成交额",
        })
    # 统一列集合，缺失列补 NaN
    cols = ["日期", "开盘", "最高", "最低", "收盘", "昨收", "涨跌额", "涨跌幅", "成交量", "成交额", "换手率"]
    for c in cols:
        if c not in d.columns:
            d[c] = None
    d = d[cols]
    # 按日期升序，推算缺失的昨收/涨跌幅/涨跌额
    d = d.sort_values("日期").reset_index(drop=True)
    closes = d["收盘"].astype(float)
    if "昨收" in d and d["昨收"].isna().all():
        d["昨收"] = closes.shift(1)  # 首行昨收为 NaN
    for i in range(len(d)):
        close, pre = d.at[i, "收盘"], d.at[i, "昨收"]
        if close is None or pd.isna(close) or pre is None or pd.isna(pre):
            continue
        close, pre = float(close), float(pre)
        if (d.at[i, "涨跌幅"] is None or pd.isna(d.at[i, "涨跌幅"])) and pre:
            d.at[i, "涨跌幅"] = round((close - pre) / pre * 100, 4)
        if (d.at[i, "涨跌额"] is None or pd.isna(d.at[i, "涨跌额"])):
            d.at[i, "涨跌额"] = round(close - pre, 3)
    return d


class StockDailyIncrementalCollector:
    """股票日线增量采集器"""

    def __init__(self, days_back: int = DEFAULT_DAYS_BACK, adjust: str = "qfq", max_stocks: int = 0,
                 include_stale: bool = False, include_bj: bool = False):
        self.days_back = days_back
        self.adjust = adjust
        self.max_stocks = max_stocks  # 0 = 不限（全量）
        self.include_stale = include_stale  # True = 也采集疑似退市/长期停牌股
        self.include_bj = include_bj  # True = 也采集北交所（当前数据源不支持，默认跳过）
        self.db = get_db_config().to_dict()
        self._written = 0
        self._errors: list[str] = []

    # ---------- 数据库工具 ----------
    def _connect(self):
        return pymysql.connect(**self.db)

    def get_last_trade_date(self, conn) -> str | None:
        """存量数据最新交易日（无数据返回 None）"""
        with conn.cursor() as cur:
            cur.execute("SELECT MAX(trade_date) FROM stock_market_daily")
            row = cur.fetchone()
            return row[0].strftime("%Y%m%d") if row and row[0] else None

    def get_stock_list(self, conn, include_bj: bool = False) -> tuple[list[tuple], int]:
        """获取股票代码列表，返回 (列表, 北交所数量)。

        过滤规则：
        - 排除 B 股（200%/900% 前缀）——数据源不支持
        - 排除名称含"退"或以"PT"开头的已退市股——数据源已无数据，逐个重试耗时巨大
        - 默认排除北交所（4/8/92 开头）：当前四级源（东财被风控、腾讯/新浪不支持北交所、
          Tushare 未配置）均无法采集，硬拉纯浪费时间；留待专用北交所任务。
        排序规则：疑似退市（无数据或最后数据极旧）排最后，先处理活跃缺口股。
        """
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT a.stock_code, a.short_name,
                       (SELECT MAX(b.trade_date) FROM stock_market_daily b
                        WHERE b.stock_code = a.stock_code) AS last_date
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
            rows = [(r[0], r[1], r[2].strftime("%Y%m%d") if r[2] else None) for r in cur.fetchall()]
        if not include_bj:
            bj = [r for r in rows if r[0].startswith(("4", "8", "920"))]
            rows = [r for r in rows if not r[0].startswith(("4", "8", "920"))]
            return rows, len(bj)
        return rows, 0

    # ---------- 数据源（四级降级） ----------
    def _fetch_eastmoney(self, code: str, start: str, end: str) -> pd.DataFrame:
        import akshare as ak
        return normalize_df(
            ak.stock_zh_a_hist(symbol=code, period="daily", start_date=start, end_date=end, adjust=self.adjust),
            "eastmoney",
        )

    def _fetch_tencent(self, code: str, start: str, end: str) -> pd.DataFrame:
        import akshare as ak
        symbol = code_to_symbol(code)
        return normalize_df(
            ak.stock_zh_a_hist_tx(symbol=symbol, start_date=start, end_date=end, adjust=self.adjust),
            "tencent",
        )

    def _fetch_sina(self, code: str, start: str, end: str) -> pd.DataFrame:
        import akshare as ak
        symbol = code_to_symbol(code)
        return normalize_df(
            ak.stock_zh_a_daily(symbol=symbol, start_date=start, end_date=end, adjust=self.adjust),
            "sina",
        )

    def _fetch_tushare(self, code: str, start: str, end: str) -> pd.DataFrame:
        token = os.getenv("TUSHARE_TOKEN", "")
        if not token:
            raise RuntimeError("Tushare 备源未配置（缺少环境变量 TUSHARE_TOKEN），跳过")
        import tushare as ts
        ts.set_token(token)
        symbol = code + (".SH" if code.startswith("6") else ".SZ" if code.startswith(("0", "3")) else ".BJ")
        df = ts.pro_api().daily(ts_code=symbol, start_date=start, end_date=end)
        return normalize_df(df, "tushare")

    @staticmethod
    def _fetch_with_timeout(fetcher, code: str, start: str, end: str, timeout: int = 25) -> pd.DataFrame:
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

    def fetch_with_retry(self, code: str, start: str, end: str) -> tuple[pd.DataFrame, str]:
        """四级数据源降级：东财→腾讯→新浪→Tushare，返回 (数据, 实际使用的数据源)。

        东财被风控时（如返回 HTTP 000）快速降级：首源只重试 1 次，其余源保持 RETRY_TIMES 次。
        每源带 25s 硬超时，杜绝单只股票挂死拖垮整个任务。
        """
        sources = [
            ("eastmoney", self._fetch_eastmoney),
            ("tencent", self._fetch_tencent),
            ("sina", self._fetch_sina),
            ("tushare", self._fetch_tushare),
        ]
        errors = []
        for i, (name, fetcher) in enumerate(sources):
            # 首个源（东财）只试 1 次，被风控/失败即快速降级，避免全量采集时大量时间空耗
            times = 1 if i == 0 else RETRY_TIMES
            for attempt in range(times):
                try:
                    df = self._fetch_with_timeout(fetcher, code, start, end)
                    if df is not None and not df.empty:
                        if name != "eastmoney":
                            logger.info(f"↩️ {code} 使用 {name} 源成功")
                        return df, name
                    raise RuntimeError(f"{name} 返回空数据")
                except Exception as e:
                    errors.append(f"{name}:{e}")
                    time.sleep(0.8)
        raise RuntimeError("全部数据源失败: " + "; ".join(errors[-6:]))

    # ---------- 写库 ----------
    def insert_rows(self, conn, code: str, df: pd.DataFrame, source: str) -> int:
        """批量插入（INSERT IGNORE 去重），返回实际写入行数"""
        if df is None or df.empty:
            return 0
        insert_sql = """
            INSERT IGNORE INTO stock_market_daily
            (stock_code, trade_date, open, high, low, close, pre_close,
             change_amount, change_pct, volume, amount, turnover_ratio, update_time, data_source)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        rows = []
        for _, row in df.iterrows():
            trade_date = row.get("日期")
            if trade_date is None or pd.isna(trade_date):
                continue
            rows.append((
                code,
                str(trade_date),
                _f(row.get("开盘")), _f(row.get("最高")),
                _f(row.get("最低")), _f(row.get("收盘")),
                _f(row.get("昨收")),
                _f(row.get("涨跌额")), _f(row.get("涨跌幅")),
                _i(row.get("成交量")), _f(row.get("成交额")),
                _f(row.get("换手率")),
                datetime.now(), source.upper(),
            ))
        if not rows:
            return 0
        with conn.cursor() as cur:
            cur.executemany(insert_sql, rows)
        conn.commit()
        return len(rows)

    # ---------- 主流程 ----------
    def _process_one(self, code: str, name: str, stock_last: str | None, cutoff: str, end_date: str) -> tuple:
        """处理单只股票（worker 内独立 DB 连接，避免 pymysql 连接跨线程复用），
        返回 (code, name, written, source, error)"""
        conn = self._connect()
        try:
            if stock_last:
                start_date = (datetime.strptime(stock_last, "%Y%m%d") - timedelta(days=1)).strftime("%Y%m%d")
            else:
                start_date = cutoff
            gap_days = (datetime.strptime(end_date, "%Y%m%d") - datetime.strptime(start_date, "%Y%m%d")).days
            if gap_days > 500:
                start_date = (datetime.strptime(end_date, "%Y%m%d") - timedelta(days=500)).strftime("%Y%m%d")
                logger.warning(f"⚠️ {code} {name} 缺口 {gap_days} 天，限制拉取最近 500 天")
            df, src = self.fetch_with_retry(code, start_date, end_date)
            n = self.insert_rows(conn, code, df, src)
            time.sleep(random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX))
            return (code, name, n, src, None)
        except Exception as e:
            return (code, name, 0, None, f"{code} {name}: {e}")
        finally:
            conn.close()

    def run(self) -> dict:
        """执行增量采集，返回统计信息（并发采集，默认 4 线程）"""
        from concurrent.futures import ThreadPoolExecutor, as_completed

        start_ts = datetime.now()
        conn = self._connect()
        try:
            global_last = self.get_last_trade_date(conn)
            end_date = datetime.now().strftime("%Y%m%d")

            if global_last is None:
                global_last = (datetime.now() - timedelta(days=365)).strftime("%Y%m%d")
                logger.info("无存量数据，回退拉取近 1 年")

            cutoff = (datetime.strptime(global_last, "%Y%m%d") - timedelta(days=self.days_back)).strftime("%Y%m%d")
            logger.info(f"全局最新交易日: {global_last}，增量判定线(≥此日期算已最新): {cutoff}")

            stocks, bj_count = self.get_stock_list(conn, include_bj=self.include_bj)
            if bj_count:
                logger.info(f"跳过北交所 {bj_count} 只（数据源暂不支持，待专用任务采集）")
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
            for code, name, stock_last in stocks:
                if stock_last and stock_last >= cutoff:
                    skipped += 1
                    continue
                todo.append((code, name, stock_last))
            logger.info(f"实际待采集 {len(todo)} 只（已最新跳过 {skipped} 只）")

            source_stats = {}
            self._written = 0
            self._errors = []
            done = 0
            workers = 4
            with ThreadPoolExecutor(max_workers=workers) as ex:
                futures = {
                    ex.submit(self._process_one, code, name, stock_last, cutoff, end_date): (code, name)
                    for code, name, stock_last in todo
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
            }
            logger.info(
                f"✅ 完成: 写入 {self._written} 行, 跳过 {skipped} 只, 疑似退市跳过 {skipped_stale} 只, "
                f"源分布 {source_stats}, 失败 {len(self._errors)} 只, 耗时 {duration}"
            )
            return result
        finally:
            conn.close()


def _f(v) -> float | None:
    """安全转 float"""
    if v is None or pd.isna(v):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _i(v) -> int | None:
    """安全转 int"""
    if v is None or pd.isna(v):
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None
