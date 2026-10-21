#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
投资环境模块 · 国际指数技术面物化任务（overseas_tech_derive）

把「国际指数」页要展示的技术面指标**物化**到 env_overseas_tech_daily 表：
  · 趋势与动量：均线 MA60/250 及偏离、MACD/RSI、跨市场相对强弱（区间涨幅对比）
  · 波动率与风险：60 日年化波动率、下行波动率、最大回撤（1Y/3Y/全史）
  · 跨市场联动：滚动相关性（对 ACWI 全球基准）、Beta

数据来源（全部已有表，无新外部源）：
  overseas_index_daily     海外/港股指数日线（18 指数，AKSHARE + MSCI 两源）

设计要点：
  · 与国内指数 env_index_tech_daily 共用同一套计算引擎（env_metrics），
    只把源表从 dc_index_market 换成 overseas_index_daily、基准从沪深300 换成 ACWI。
  · 纯库内计算，幂等（env_overseas_tech_daily 全量重建）。
  · MSCI 四指数（ACWI/MSWORLD/MSEAFE/MSEM）只有收盘 LEVEL、无 OHLC/量，
    close 口径统一，量价列（vol_ma20/vol_ratio_20）对它们置 NULL。

⚠️ 复权口径：指数点位本身已连续（除数调整），跨日比值直接可用，无需 adj_factor。
⚠️ 时区：18 指数跨亚/欧/美三时区，各自交易日不同步。本表**按各自本币收盘序列独立计算**，
   不跨指数对齐交易日（对齐/前值填充是「跨市场对照」消费方的职责，见 env_service）。
"""
import logging

import numpy as np
import pandas as pd
import pymysql

from ..db import get_db_config
from ..services import env_metrics as M
from ._common import with_steps

logger = logging.getLogger(__name__)

RUN_STEPS = [
    {"no": 1, "name": "加载海外指数日线矩阵", "params": "overseas_index_daily 全史 close → 逐指数计算"},
    {"no": 2, "name": "计算技术面指标", "params": "均线/MACD/RSI/BOLL/波动率/回撤/相对强弱"},
    {"no": 3, "name": "附加跨市场 Beta/相关性", "params": "以 ACWI 为全球基准算 beta_60/corr_60"},
    {"no": 4, "name": "重建 env_overseas_tech_daily", "params": "DELETE + INSERT 全量"},
]

# 海外指数清单（index_code → 中文名），与 overseas_index_sync 的 OVERSEAS 一致
OVERSEAS_NAMES = {
    "HSI": "恒生指数", "DJI": "道琼斯工业平均", "SPX": "标普500",
    "IXIC": "纳斯达克综合", "NDX": "纳斯达克100", "HSCEI": "恒生中国企业指数",
    "DAX": "德国DAX30", "CAC": "法国CAC40", "UKX": "英国富时100",
    "SX5E": "欧洲斯托克50", "N225": "日经225", "KOSPI": "韩国综合指数",
    "IBOV": "巴西Bovespa", "SENSEX": "印度SENSEX",
    "ACWI": "MSCI ACWI", "MSWORLD": "MSCI World",
    "MSEAFE": "MSCI EAFE", "MSEM": "MSCI Emerging Markets",
}

# 跨市场相对强弱基准：ACWI 代表全球可投资权益集合
_RELATIVE_BENCH = "ACWI"

# 输出列
_COLS = [
    "index_code", "trade_date", "close", "change_pct",
    "ma60", "ma250", "close_over_ma60_pct", "close_over_ma250_pct",
    "macd_dif", "macd_dea", "macd_hist", "rsi14",
    "boll_upper", "boll_mid", "boll_lower",
    "high_250", "low_250", "high_20", "low_20",
    "max_dd_1y", "max_dd_3y", "max_dd_all",
    "ann_vol_60", "down_vol_60",
    "rel_strong_60", "rel_strong_250",   # 相对 ACWI 的区间超额（百分点）
    "beta_60", "corr_60",
]


def _f(v, nd: int = 4):
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return round(f, nd)


class OverseasTechDeriveCollector:
    """国际指数技术面物化（纯库内，无外部源）"""

    def __init__(self, lookback_years: int = 0):
        self.lookback_years = int(lookback_years or 0)

    @staticmethod
    def _ensure_tables(conn):
        ddl = """
        CREATE TABLE IF NOT EXISTS env_overseas_tech_daily (
          id BIGINT NOT NULL AUTO_INCREMENT,
          index_code VARCHAR(20) NOT NULL COMMENT '海外指数代码',
          trade_date DATE NOT NULL COMMENT '交易日（各市场本币口径）',
          close DECIMAL(12,4) DEFAULT NULL COMMENT '收盘点位',
          change_pct DECIMAL(8,4) DEFAULT NULL COMMENT '当日涨跌幅(%)',
          ma60 DECIMAL(12,4) DEFAULT NULL,
          ma250 DECIMAL(12,4) DEFAULT NULL,
          close_over_ma60_pct DECIMAL(8,4) DEFAULT NULL COMMENT '收盘相对MA60偏离(%)',
          close_over_ma250_pct DECIMAL(8,4) DEFAULT NULL COMMENT '收盘相对MA250偏离(%)',
          macd_dif DECIMAL(12,4) DEFAULT NULL,
          macd_dea DECIMAL(12,4) DEFAULT NULL,
          macd_hist DECIMAL(12,4) DEFAULT NULL,
          rsi14 DECIMAL(8,4) DEFAULT NULL,
          boll_upper DECIMAL(12,4) DEFAULT NULL,
          boll_mid DECIMAL(12,4) DEFAULT NULL,
          boll_lower DECIMAL(12,4) DEFAULT NULL,
          high_250 DECIMAL(12,4) DEFAULT NULL,
          low_250 DECIMAL(12,4) DEFAULT NULL,
          high_20 DECIMAL(12,4) DEFAULT NULL,
          low_20 DECIMAL(12,4) DEFAULT NULL,
          max_dd_1y DECIMAL(8,4) DEFAULT NULL COMMENT '近1年最大回撤(%)',
          max_dd_3y DECIMAL(8,4) DEFAULT NULL,
          max_dd_all DECIMAL(8,4) DEFAULT NULL COMMENT '全史最大回撤(%)',
          ann_vol_60 DECIMAL(8,4) DEFAULT NULL COMMENT '60日年化波动率(%)',
          down_vol_60 DECIMAL(8,4) DEFAULT NULL COMMENT '60日下行波动率(%)',
          rel_strong_60 DECIMAL(8,4) DEFAULT NULL COMMENT '近60日相对ACWI超额(%)',
          rel_strong_250 DECIMAL(8,4) DEFAULT NULL COMMENT '近250日相对ACWI超额(%)',
          beta_60 DECIMAL(8,4) DEFAULT NULL COMMENT '对ACWI的60日Beta',
          corr_60 DECIMAL(8,4) DEFAULT NULL COMMENT '对ACWI的60日滚动相关',
          update_time TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          data_source VARCHAR(100) DEFAULT NULL,
          PRIMARY KEY (id),
          UNIQUE KEY uk_code_date (index_code, trade_date),
          KEY idx_trade_date (trade_date)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        COMMENT='投资环境·国际指数技术面日频物化表（2026-10-21）'
        """
        with conn.cursor() as cur:
            cur.execute(ddl)
        conn.commit()

    def _load_close(self, conn) -> dict[str, pd.Series]:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT index_code, trade_date, close FROM overseas_index_daily "
                "WHERE close IS NOT NULL ORDER BY trade_date"
            )
            rows = cur.fetchall()
        df = pd.DataFrame(rows)
        df["close"] = pd.to_numeric(df["close"], errors="coerce")
        out: dict[str, pd.Series] = {}
        for code, g in df.groupby("index_code"):
            g = g.sort_values("trade_date")
            s = pd.Series(g["close"].values, index=pd.to_datetime(g["trade_date"]))
            out[code] = s.dropna()
        return out

    def _compute_tech(self, closes: dict[str, pd.Series]) -> pd.DataFrame:
        rows = []
        for code, close in closes.items():
            if len(close) < 60:
                continue
            close = close[~close.index.duplicated(keep="last")].sort_index()
            df = pd.DataFrame({"close": close})

            df["change_pct"] = close.pct_change() * 100
            # 均线
            df["ma60"] = close.rolling(60, min_periods=60).mean()
            df["ma250"] = close.rolling(250, min_periods=250).mean()
            df["close_over_ma60_pct"] = (close / df["ma60"] - 1) * 100
            df["close_over_ma250_pct"] = (close / df["ma250"] - 1) * 100
            # MACD/RSI/BOLL
            dif, dea, hist = M.macd(close)
            df["macd_dif"], df["macd_dea"], df["macd_hist"] = dif, dea, hist
            df["rsi14"] = M.rsi(close, 14)
            bu, bm, bl = M.boll(close)
            df["boll_upper"], df["boll_mid"], df["boll_lower"] = bu, bm, bl
            # 支撑阻力
            df["high_250"] = close.rolling(250, min_periods=250).max()
            df["low_250"] = close.rolling(250, min_periods=250).min()
            df["high_20"] = close.rolling(20, min_periods=20).max()
            df["low_20"] = close.rolling(20, min_periods=20).min()
            # 回撤
            cummax = close.cummax()
            dd = (close / cummax - 1) * 100
            df["max_dd_1y"] = dd.rolling(252, min_periods=1).min()
            df["max_dd_3y"] = dd.rolling(756, min_periods=1).min()
            df["max_dd_all"] = dd.cummin()
            # 波动
            df["ann_vol_60"] = M.annualized_vol(close, 60)
            df["down_vol_60"] = M.downside_vol(close, 60)

            df["index_code"] = code
            df["trade_date"] = close.index
            rows.append(df)

        if not rows:
            return pd.DataFrame()
        return pd.concat(rows, ignore_index=True)

    def _attach_relative_strength(self, tech: pd.DataFrame,
                                  closes: dict[str, pd.Series]) -> pd.DataFrame:
        """相对强弱：各指数近 60/250 日累计涨幅 − ACWI 同期累计涨幅（百分点）。"""
        tech["rel_strong_60"] = np.nan
        tech["rel_strong_250"] = np.nan
        bench = closes.get(_RELATIVE_BENCH)
        if bench is None or tech.empty:
            return tech
        b = bench[~bench.index.duplicated(keep="last")].sort_index()
        b60 = b.pct_change(60) * 100
        b250 = b.pct_change(250) * 100
        for code, g in tech.groupby("index_code"):
            close = closes.get(code)
            if close is None or len(close) < 60:
                continue
            s = close[~close.index.duplicated(keep="last")].sort_index()
            s60 = s.pct_change(60) * 100
            s250 = s.pct_change(250) * 100
            rel60 = (s60 - b60.reindex(s60.index)).to_dict()
            rel250 = (s250 - b250.reindex(s250.index)).to_dict()
            idxs = g.index
            tech.loc[idxs, "rel_strong_60"] = g["trade_date"].map(
                lambda d: rel60.get(d, np.nan))
            tech.loc[idxs, "rel_strong_250"] = g["trade_date"].map(
                lambda d: rel250.get(d, np.nan))
        return tech

    def _attach_beta_corr(self, tech: pd.DataFrame,
                          closes: dict[str, pd.Series]) -> pd.DataFrame:
        """Beta/相关性：以 ACWI 为全球基准。"""
        tech["beta_60"] = np.nan
        tech["corr_60"] = np.nan
        bench = closes.get(_RELATIVE_BENCH)
        if bench is None or tech.empty:
            return tech
        b = bench[~bench.index.duplicated(keep="last")].sort_index()
        bret = b.pct_change()
        for code, g in tech.groupby("index_code"):
            close = closes.get(code)
            if close is None or len(close) < 60:
                continue
            if code == _RELATIVE_BENCH:
                tech.loc[g.index, "beta_60"] = 1.0
                tech.loc[g.index, "corr_60"] = 1.0
                continue
            s = close[~close.index.duplicated(keep="last")].sort_index()
            sret = s.pct_change()
            common = sret.index.intersection(bret.index)
            if len(common) < 60:
                continue
            sr = sret.loc[common]
            br = bret.loc[common]
            cov = sr.rolling(60, min_periods=60).cov(br)
            var = br.rolling(60, min_periods=60).var(ddof=0)
            beta = cov / var.replace(0, np.nan)
            corr = sr.rolling(60, min_periods=60).corr(br)
            beta_map = beta.to_dict()
            corr_map = corr.to_dict()
            idxs = g.index
            tech.loc[idxs, "beta_60"] = g["trade_date"].map(
                lambda d: beta_map.get(d, np.nan))
            tech.loc[idxs, "corr_60"] = g["trade_date"].map(
                lambda d: corr_map.get(d, np.nan))
        return tech

    def _write(self, conn, tech: pd.DataFrame) -> int:
        tech = tech.copy()
        for c in _COLS:
            if c not in tech.columns:
                tech[c] = np.nan
        payload = []
        for _, r in tech.iterrows():
            td = r["trade_date"]
            if hasattr(td, "date"):
                td = td.date()
            payload.append(tuple(
                _f(r[c]) if c not in ("index_code", "trade_date") else
                (r[c] if c == "index_code" else td)
                for c in _COLS
            ))
        with conn.cursor() as cur:
            cur.execute("DELETE FROM env_overseas_tech_daily")
            if payload:
                cur.executemany(
                    f"INSERT INTO env_overseas_tech_daily ({', '.join(_COLS)}) "
                    f"VALUES ({', '.join(['%s'] * len(_COLS))})",
                    payload,
                )
        conn.commit()
        return len(payload)

    def run(self) -> dict:
        conn = pymysql.connect(**get_db_config().to_dict(),
                               cursorclass=pymysql.cursors.DictCursor)
        try:
            self._ensure_tables(conn)
            closes = self._load_close(conn)
            n_idx = len(closes)
            tech = self._compute_tech(closes)
            tech = self._attach_relative_strength(tech, closes)
            tech = self._attach_beta_corr(tech, closes)
            written = self._write(conn, tech)

            with conn.cursor() as cur:
                cur.execute("SELECT index_code, COUNT(*) AS n, MAX(trade_date) AS d "
                            "FROM env_overseas_tech_daily GROUP BY index_code ORDER BY index_code")
                stat = cur.fetchall()
        finally:
            conn.close()

        msg = f"国际指数技术面物化 {written} 行（{len(closes)} 指数）"
        logger.info("✅ %s", msg)
        return with_steps(
            {"records_written": written, "error_count": 0, "errors": [],
             "note": msg},
            RUN_STEPS,
            {
                1: f"overseas_index_daily 全史 → {n_idx} 指数",
                2: f"均线/MACD/RSI/BOLL/波动率/回撤/相对强弱",
                3: f"以 {_RELATIVE_BENCH} 为基准算 beta_60/corr_60",
                4: "；".join(f"{r['index_code']} {r['n']}行至{r['d']}" for r in stat),
            },
        )
