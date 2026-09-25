#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
InvestBuddy 海外/港股指数日线采集器（overseas_index_daily）—— 蓝图 E「跨市场与外部情绪」

为什么需要（报告 §1.1 / §6 蓝图 E）：
  市场风向现有 35 列全部是「A 股内部比」，没有任何外部参照物。跨市场是最便宜的外部参照：
  外盘大跌而 A 股抗跌 = 韧性；外盘领涨而 A 股不跟 = 内部资金问题。
  **只有两边都有，才谈得上「抗跌」这个判断**——单看 A 股跌幅说明不了任何事。

两只源（均为新浪，实测 0.1~0.3s，全量历史）：
  · 恒生指数      stock_hk_index_daily_sina(symbol='HSI')        ~3,220 行
  · 美股三大指数  index_us_stock_sina(symbol='.DJI/.INX/.IXIC')  ~5,716 行

2026-09-25（货币流动性批次 2C）新增 6 个环球指数，补「日韩欧层」——这一层是
**对美元周期最敏感的开放经济体**，跨市场对照原先只有港美、看不出美元周期在非美市场的传导：
  · 德国 DAX30 / 法国 CAC40 / 英国富时100 / 欧洲斯托克50 / 日经225 / 韩国 KOSPI
  · 走**新浪环球指数直连**（`gi.finance.sina.com.cn/hq/daily`）—— 见 OVERSEAS 上方长注释
    说明为何不能用 akshare 的 index_global_hist_* 系列（东财 push2his 域名整体不通）。
  · ⚠️ 该接口只给约 1,000 行（≈4 年）、且**不给成交额**（amount 恒 NULL），
    部分指数不给成交量（v='0' → 转 NULL）。这些限制已写进 table_meta 与 DQ 规则口径。

幂等：uk_code_date(index_code, trade_date) + 增量（起点 = 各指数本地 MAX(trade_date) 的次日）。
首次运行会全量补齐（约 2 万行 + 环球 6 千行），之后每日只写增量。
"""
import logging
from datetime import date, datetime, timedelta

import pymysql
import akshare as ak

from ..db import get_db_config
from ._common import with_steps, call_with_timeout

logger = logging.getLogger(__name__)

RUN_STEPS = [
    {"no": 1, "name": "读取本地水位", "params": "逐指数 MAX(trade_date)（首次无数据则回扫 first_lookback_days 天，但源会返回全量）"},
    {"no": 2, "name": "拉取海外指数", "params": "恒生 stock_hk_index_daily_sina(HSI) + 美股 index_us_stock_sina(.DJI/.INX/.IXIC)"},
    {"no": 3, "name": "增量过滤", "params": "仅保留 trade_date > 本地 MAX 的行；剔除非交易日空行"},
    {"no": 4, "name": "批量 Upsert", "params": "uk_code_date(index_code, trade_date) 幂等"},
]

# (index_code, 中文名, 源类型, 源 symbol)
#
# 源类型三种：
#   "hk" —— akshare stock_hk_index_daily_sina（港股）
#   "us" —— akshare index_us_stock_sina（美股）
#   "gi" —— **新浪环球指数直连** `gi.finance.sina.com.cn/hq/daily`（2026-09-25 新增，日韩欧）
#
# ⚠️ 为什么日韩欧必须走 "gi" 直连、不能用 akshare 函数（2026-09-25 实测）：
#   · `ak.index_global_hist_em`（东财）—— 底层打 push2his 域名，该域名**整体不通**
#     （kline 路径被拦），报 ConnectionError/RemoteDisconnected。加 UA、换 push2/push2delay
#     都不行，是域名级封锁，不是参数问题。
#   · `ak.index_global_hist_sina` —— 其符号映射表的 key 与调用侧对不上，实测 KeyError。
#   · 直连 `gi.finance.sina.com.cn/hq/daily?symbol=DAX` —— **可用**，返回 JSON，
#     字段 {d 日期, o 开, h 高, l 低, c 收, v 量}，无 amount。
#   ⚠️ 该接口 `num=10000` 实测**只返 1,000 行（约 4 年）**——够算近一年分位与 20/60 日变化，
#     但**不够做长周期历史类比**（设计文档 D3 已定「v1 不做历史类比」，与此无冲突）。
#   ⚠️ `v`（成交量）对多数欧洲指数返回 '0'（源不提供）→ 入库时转 NULL，避免被读成"零成交"。
OVERSEAS = [
    ("HSI", "恒生指数", "hk", "HSI"),
    ("DJI", "道琼斯工业平均", "us", ".DJI"),
    ("SPX", "标普500", "us", ".INX"),
    ("IXIC", "纳斯达克综合", "us", ".IXIC"),
    # ---- 2026-09-25 货币流动性批次 2C：日韩欧层（对美元周期最敏感的开放经济体）----
    ("DAX", "德国DAX30", "gi", "DAX"),
    ("CAC", "法国CAC40", "gi", "CAC"),
    ("UKX", "英国富时100", "gi", "UKX"),
    ("SX5E", "欧洲斯托克50", "gi", "SX5E"),
    ("N225", "日经225", "gi", "NKY"),
    ("KOSPI", "韩国综合指数", "gi", "KOSPI"),
]
_TGT = ["index_code", "index_name", "trade_date",
        "open", "high", "low", "close", "volume", "amount"]

# 新浪环球指数接口（直连，非 akshare 函数）
_GI_URL = "https://gi.finance.sina.com.cn/hq/daily"
_GI_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120",
               "Referer": "https://finance.sina.com.cn/"}


def _d(v) -> date | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()[:10]
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _f(v, nd: int = 4):
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else round(f, nd)


class OverseasIndexSyncCollector:
    """海外/港股指数日线增量同步"""

    def __init__(self, timeout_sec: float = 60):
        self.timeout_sec = float(timeout_sec)

    @staticmethod
    def _fetch(kind: str, symbol: str, timeout: float):
        if kind == "gi":
            return OverseasIndexSyncCollector._fetch_gi(symbol, timeout)
        fn = ak.stock_hk_index_daily_sina if kind == "hk" else ak.index_us_stock_sina
        return call_with_timeout(fn, timeout, symbol=symbol)

    @staticmethod
    def _fetch_gi(symbol: str, timeout: float = 20.0):
        """新浪环球指数日线 → 归一成与另两条源相同的列名（date/open/high/low/close/volume）

        归一化的意义：下游过滤/组装逻辑对三条源**完全共用**，不需要按源分叉——
        分叉是「同一件事写两遍」的开始，本项目已有教训。

        ⚠️ 成交量 v<=0 一律转 None：欧洲指数源返回 '0' 表示"源不提供"，
           直接存 0 会被下游读成"零成交"（这是把缺失伪装成数据的典型形态）。
        """
        import requests
        import pandas as pd

        r = requests.get(_GI_URL, params={"symbol": symbol, "num": "10000"},
                         headers=_GI_HEADERS, timeout=timeout)
        r.raise_for_status()
        js = r.json()
        data = (js.get("result") or {}).get("data") or []
        if not data:
            raise RuntimeError(f"gi 接口返回空：symbol={symbol} code={js.get('code')} "
                               f"msg={str(js.get('message'))[:60]}")
        df = pd.DataFrame(data)
        df = df.rename(columns={"d": "date", "o": "open", "h": "high",
                                "l": "low", "c": "close", "v": "volume"})
        df["volume"] = pd.to_numeric(df.get("volume"), errors="coerce")
        df.loc[df["volume"] <= 0, "volume"] = None
        for c in ("open", "high", "low", "close"):
            df[c] = pd.to_numeric(df.get(c), errors="coerce")
        return df

    def run(self) -> dict:
        conn = pymysql.connect(**get_db_config().to_dict(),
                               cursorclass=pymysql.cursors.DictCursor)
        written, errors, per = 0, [], {}
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT index_code, MAX(trade_date) AS d FROM overseas_index_daily "
                            "GROUP BY index_code")
                wm = {r["index_code"]: r["d"] for r in cur.fetchall()}

                insert_sql = (
                    f"INSERT INTO overseas_index_daily ({', '.join(_TGT)}, update_time, data_source) "
                    f"VALUES ({', '.join(['%s'] * len(_TGT))}, NOW(), 'AKSHARE') "
                    "ON DUPLICATE KEY UPDATE "
                    + ", ".join(f"{c}=VALUES({c})" for c in _TGT if c not in
                                ("index_code", "trade_date"))
                    + ", update_time=NOW()"
                )

                for code, name, kind, symbol in OVERSEAS:
                    try:
                        df = self._fetch(kind, symbol, self.timeout_sec)
                    except Exception as e:              # noqa: BLE001
                        errors.append(f"{code} {name}: {type(e).__name__} {str(e)[:60]}")
                        continue
                    if df is None or df.empty:
                        errors.append(f"{code} {name}: 返回为空")
                        continue

                    last = wm.get(code)
                    rows = []
                    for _, r in df.iterrows():
                        d = _d(r.get("date"))
                        if d is None or (last is not None and d <= last):
                            continue
                        c = _f(r.get("close"), 4)
                        if c is None:
                            continue          # 无收盘价的行（停牌/占位）不入库
                        rows.append((code, name, d,
                                     _f(r.get("open"), 4), _f(r.get("high"), 4),
                                     _f(r.get("low"), 4), c,
                                     _f(r.get("volume"), 2), _f(r.get("amount"), 2)))
                    if rows:
                        cur.executemany(insert_sql, rows)
                        conn.commit()
                    written += len(rows)
                    per[code] = len(rows)
                    logger.info("  %s %s：源 %s 行 → 增量 %s 行（本地至 %s）",
                                code, name, len(df), len(rows), last)

            with conn.cursor() as cur:
                cur.execute("SELECT index_code, COUNT(*) AS n, MAX(trade_date) AS d "
                            "FROM overseas_index_daily GROUP BY index_code ORDER BY index_code")
                stat = cur.fetchall()
        finally:
            conn.close()

        msg = f"海外指数新增 {written} 行（{len(per)}/{len(OVERSEAS)} 个指数）"
        if errors:
            msg += f"；异常 {len(errors)} 项"
        logger.info("✅ %s", msg)
        return with_steps(
            {"records_written": written, "error_count": len(errors), "errors": errors[:20], "note": msg},
            RUN_STEPS,
            {
                1: " · ".join(f"{c}至{wm.get(c) or '无'}" for c, _, _, _ in OVERSEAS),
                2: f"拉取 {len(per)}/{len(OVERSEAS)} 个",
                3: " · ".join(f"{c} +{per[c]}" for c in per) or "无增量",
                4: "；".join(f"{r['index_code']} {r['n']}行至{r['d']}" for r in stat),
            },
        )
