#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
投资环境模块 · 每日盘后物化任务（env_derive）

把「国内指数」页要展示的技术面 / 基本面指标**物化**到三张表：
  env_index_tech_daily     技术面日频（12 窗口涨跌幅、均线、量价、MACD/RSI/BOLL、
                           支撑阻力、回撤/波动率/Beta/相关性）
  env_fundamental_daily    基本面日频（估值分位 / ERP / 盈利截面）
  env_fundamental_quarter  基本面季频（成分股财务聚合，近 40 季度趋势）
  env_threshold            大幅下跌阈值（手定 + 近 3 年 95 分位建议）

数据来源（全部已有表，**无新外部源**，宏观另见 env_macro_sync）：
  dc_index_market          指数日线（close/volume/amount，后复权由 change_pct 保比值）
  index_valuation_daily    估值长历史（legu / legu_pb / csindex）
  index_constituents       成分股名单
  stock_financial_abstract_ths  成分股财务摘要
  bond_profit_daily        10Y 国债收益率（ERP 分母）

设计要点：
  - 纯库内计算，幂等（env_index_tech_daily / env_fundamental_daily 全量重建；
    env_fundamental_quarter / env_threshold 按 uk upsert）。
  - 结论由观看者判断 → 只存**客观数值 + 分位**，不存定性词。
  - 分位在物化时算好（env_fundamental_daily 的 *_pct 列），前端只读。
  - 挂 market_current_sync 之后链式触发（技术面依赖当日指数日线）。

⚠️ 复权口径：dc_index_market 的 close 是**实际点位**（指数通常不除权，但成分调样/分红
   会通过除数调整，点位本身已连续），跨日比值直接可用，无需 adj_factor（与个股不同）。
"""
import logging
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pymysql

from ..db import get_db_config
from ..services import env_metrics as M
from ._common import with_steps

logger = logging.getLogger(__name__)

RUN_STEPS = [
    {"no": 1, "name": "加载指数日线矩阵", "params": "dc_index_market 全史 close/volume → 逐指数计算"},
    {"no": 2, "name": "计算技术面指标", "params": "12窗口/均线/量价/MACD/RSI/BOLL/支撑阻力/回撤/波动/Beta/相关性"},
    {"no": 3, "name": "重建 env_index_tech_daily", "params": "DELETE + INSERT 全量"},
    {"no": 4, "name": "计算基本面（估值分位+盈利聚合）", "params": "估值长历史分位/ERP + 成分股财务聚合"},
    {"no": 5, "name": "落地基本面三表 + 阈值", "params": "env_fundamental_daily/quarter upsert + env_threshold 建议值"},
]

# 大跌阈值（全量 21 指数，E4 定稿）：按「近3年日跌幅95分位」实测后四舍五入到最近 0.5 便于分辨，
# 全部固化为手定值（来源 manual），不再走运行期 95 分位建议——保证换机重建可复现、阈值稳定可解释。
MANUAL_THRESHOLD = {
    "000001": 1.0,    # 上证指数
    "000300": 1.5,    # 沪深300
    "000016": 1.5,    # 上证50
    "000922": 2.0,    # 中证红利
    "399006": 2.0,    # 创业板指
    "000688": 2.0,    # 科创50
    "399673": 2.0,    # 创业板50
    "000698": 2.0,    # 科创100
    "000832": 1.5,    # 中证转债
    "399986": 2.0,    # 中证银行
    "000985": 2.5,    # 中证全指
    "399975": 2.5,    # 证券公司
    "399997": 3.0,    # 中证白酒
    "399330": 3.0,    # 深证100
    "000905": 3.0,    # 中证500
    "399001": 3.0,    # 深证成指
    "931775": 3.5,    # 中证全指房地产指数
    "000852": 3.5,    # 中证1000
    "932000": 4.0,    # 中证2000
    "899050": 4.5,    # 北证50
    "980017": 5.0,    # 国证芯片
}

# 财务聚合列 → stock_financial_abstract_ths 源列映射（注意：该表数值列为 varchar）
FIN_MAP = {
    "roe": "roe",
    "net_profit_yoy": "net_profit_yoy_gr",
    "revenue_yoy": "total_operating_yoy_gr",
    "net_profit_sum": "net_profit",
    "revenue_sum": "total_operating_revenue",
}


def _f(v, nd: int = 4):
    if v is None or (isinstance(v, float) and v != v):
        return None
    try:
        return round(float(v), nd)
    except (TypeError, ValueError):
        return None


# 中文财务字符串 → float：处理 `16.75%` / `-1.95%` / `445.17亿` / `922.78亿` / `1.2万` 等
_CN_UNIT = {"亿": 1e8, "万": 1e4, "千": 1e3}


def _parse_cn_value(v):
    """把 stock_financial_abstract_ths 的 varchar 值（如 '16.75%'、'445.17亿'）解析为 float。

    该表 roe/net_profit_yoy_gr/... 全列是**带格式的字符串**（百分号/中文单位），
    pd.to_numeric(errors='coerce') 对 '%' 与 '亿' 会直接转 NaN——故必须手动解析。
    """
    if v is None or (isinstance(v, float) and v != v):
        return None
    s = str(v).strip()
    if not s or s in ("--", "-", "None", "nan"):
        return None
    neg = s.startswith("-")
    if neg:
        s = s[1:]
    is_pct = s.endswith("%")
    if is_pct:
        s = s[:-1]
    mult = 1.0
    for unit, m in _CN_UNIT.items():
        if s.endswith(unit):
            mult = m
            s = s[:-1]
            break
    try:
        val = float(s) * mult
    except ValueError:
        return None
    return round(-val if neg else val, 4)



class EnvDeriveCollector:
    """投资环境物化（纯库内，无外部源）"""

    def __init__(self, tech_days: int = 0, lookback_years: int = 3):
        self.tech_days = int(tech_days or 0)
        self.lookback_years = int(lookback_years)

    # ---------- 表结构 ----------

    @staticmethod
    def _ensure_tables(conn):
        """三张表由 setup_v3_tables.py 的 DDL 保证存在；此处仅兜底空表判断。"""
        pass

    # ---------- 技术面 ----------

    def _load_index_close(self, conn) -> dict[str, pd.Series]:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT index_code, trade_date, close, volume FROM dc_index_market "
                "WHERE close IS NOT NULL ORDER BY trade_date"
            )
            rows = cur.fetchall()
        df = pd.DataFrame(rows, columns=["code", "date", "close", "volume"])
        df["close"] = pd.to_numeric(df["close"], errors="coerce")
        df["volume"] = pd.to_numeric(df["volume"], errors="coerce")
        out: dict[str, pd.Series] = {}
        for code, g in df.groupby("code"):
            g = g.sort_values("date")
            s = pd.Series(g["close"].values, index=pd.to_datetime(g["date"]))
            out[code] = s.dropna()
        return out

    def _compute_tech(self, closes: dict[str, pd.Series]) -> pd.DataFrame:
        """逐指数算全部技术指标，输出长表 DataFrame（index_code, trade_date, ...）。"""
        rows = []
        for code, close in closes.items():
            if len(close) < 60:
                continue
            close = close[~close.index.duplicated(keep="last")].sort_index()
            df = pd.DataFrame({"close": close})

            # 当日涨跌幅
            df["change_pct"] = close.pct_change() * 100
            # 12 窗口
            for name, n in M.WINDOW_DAYS:
                df[name] = M.window_return(close, n)
            df["win_ytd"] = M.period_return(close, "year")
            df["win_qtd"] = M.period_return(close, "quarter")
            df["win_mtd"] = M.period_return(close, "month")
            df["win_wtd"] = M.period_return(close, "week")
            # 均线
            df["ma60"] = close.rolling(60, min_periods=60).mean()
            df["ma250"] = close.rolling(250, min_periods=250).mean()
            df["close_over_ma60_pct"] = (close / df["ma60"] - 1) * 100
            df["close_over_ma250_pct"] = (close / df["ma250"] - 1) * 100
            # 量价（volume 需从 dc_index_market 单独取，这里用 close 的 index 对齐）
            # 技术指标
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
            # 回撤（滚动窗口内最大回撤：近1Y/3Y/全史，用 rolling max 近似「截至当日」）
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
        out = pd.concat(rows, ignore_index=True)
        # Beta/相关性：需要沪深300基准，跨指数算，单独处理
        return out

    def _attach_beta_corr(self, tech: pd.DataFrame, closes: dict[str, pd.Series]) -> pd.DataFrame:
        """附加 beta_60 / corr_300_120（需沪深300基准日线）。"""
        bench = closes.get("000300")
        tech["beta_60"] = np.nan
        tech["corr_300_120"] = np.nan
        if bench is None or tech.empty:
            return tech
        b = bench[~bench.index.duplicated(keep="last")].sort_index()
        bret = b.pct_change()
        for code, g in tech.groupby("index_code"):
            if code == "000300":
                # 沪深300 对自身 Beta=1，相关=1（按定义），避免 NaN
                tech.loc[g.index, "beta_60"] = 1.0
                tech.loc[g.index, "corr_300_120"] = 1.0
                continue
            close = closes.get(code)
            if close is None or len(close) < 60:
                continue
            s = close[~close.index.duplicated(keep="last")].sort_index()
            sret = s.pct_change()
            # 对齐到共同交易日
            common = sret.index.intersection(bret.index)
            if len(common) < 60:
                continue
            sr = sret.loc[common]
            br = bret.loc[common]
            cov = sr.rolling(60, min_periods=60).cov(br)
            var = br.rolling(60, min_periods=60).var(ddof=0)
            beta = cov / var.replace(0, np.nan)
            corr = sr.rolling(120, min_periods=120).corr(br)
            beta_map = beta.to_dict()
            corr_map = corr.to_dict()
            idxs = g.index
            tech.loc[idxs, "beta_60"] = g["trade_date"].map(
                lambda d: beta_map.get(d, np.nan))
            tech.loc[idxs, "corr_300_120"] = g["trade_date"].map(
                lambda d: corr_map.get(d, np.nan))
        return tech

    def _attach_volume(self, tech: pd.DataFrame, conn) -> pd.DataFrame:
        """补量价列（volume/amount 取自 dc_index_market，20 日均量 ±30% 区间）。"""
        with conn.cursor() as cur:
            cur.execute("SELECT index_code, trade_date, volume, amount FROM dc_index_market "
                        "WHERE volume IS NOT NULL ORDER BY trade_date")
            rows = cur.fetchall()
        if not rows:
            tech["vol_ma20"] = np.nan
            tech["vol_ratio_20"] = np.nan
            return tech
        vdf = pd.DataFrame(rows, columns=["index_code", "trade_date", "volume", "amount"])
        vdf["volume"] = pd.to_numeric(vdf["volume"], errors="coerce")
        vdf["trade_date"] = pd.to_datetime(vdf["trade_date"])
        vol_map: dict[tuple, float] = {}
        ratio_map: dict[tuple, float] = {}
        for code, g in vdf.groupby("index_code"):
            g = g.sort_values("trade_date")
            ma20 = g["volume"].rolling(20, min_periods=20).mean()
            ratio = g["volume"] / ma20 * 100
            for d, v, r in zip(g["trade_date"], g["volume"], ratio):
                vol_map[(code, d)] = v
                ratio_map[(code, d)] = r
        tech["vol_ma20"] = [vol_map.get((c, d), np.nan)
                            for c, d in zip(tech["index_code"], tech["trade_date"])]
        tech["vol_ratio_20"] = [ratio_map.get((c, d), np.nan)
                                for c, d in zip(tech["index_code"], tech["trade_date"])]
        # 20 日均量列：直接存当日 20 日均量（vol_ma20 列名语义 = 当日对应的 20 日均量）
        # 这里改用 rolling 在 group 内算，避免上面 vol_map 存的是当日量而非均量
        tech = tech.drop(columns=["vol_ma20"])
        ma_map: dict[tuple, float] = {}
        for code, g in vdf.groupby("index_code"):
            g = g.sort_values("trade_date")
            ma20 = g["volume"].rolling(20, min_periods=20).mean()
            for d, v in zip(g["trade_date"], ma20):
                ma_map[(code, d)] = v
        tech["vol_ma20"] = [ma_map.get((c, d), np.nan)
                            for c, d in zip(tech["index_code"], tech["trade_date"])]
        return tech

    def _write_tech(self, conn, tech: pd.DataFrame) -> int:
        cols = [
            "index_code", "trade_date", "close", "change_pct",
            "win_halfm", "win_1m", "win_15m", "win_2m", "win_25m", "win_3m",
            "win_6m", "win_1y", "win_ytd", "win_qtd", "win_mtd", "win_wtd",
            "ma60", "ma250", "close_over_ma60_pct", "close_over_ma250_pct",
            "vol_ma20", "vol_ratio_20",
            "macd_dif", "macd_dea", "macd_hist", "rsi14",
            "boll_upper", "boll_mid", "boll_lower",
            "high_250", "low_250", "high_20", "low_20",
            "max_dd_1y", "max_dd_3y", "max_dd_all",
            "ann_vol_60", "down_vol_60", "beta_60", "corr_300_120",
        ]
        tech = tech.copy()
        for c in cols:
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
                for c in cols
            ))
        with conn.cursor() as cur:
            cur.execute("DELETE FROM env_index_tech_daily")
            if payload:
                cur.executemany(
                    f"INSERT INTO env_index_tech_daily ({', '.join(cols)}) "
                    f"VALUES ({', '.join(['%s'] * len(cols))})",
                    payload,
                )
        conn.commit()
        return len(payload)

    # ---------- 基本面 ----------

    def _load_valuation(self, conn) -> dict[str, pd.DataFrame]:
        """读 index_valuation_daily：pe(legu)、pb(legu_pb)、股息率(csindex)。"""
        with conn.cursor() as cur:
            cur.execute(
                "SELECT index_code, trade_date, source, pe_ttm, pb, dividend_yield "
                "FROM index_valuation_daily ORDER BY trade_date"
            )
            rows = cur.fetchall()
        df = pd.DataFrame(rows, columns=["index_code", "trade_date", "source",
                                         "pe_ttm", "pb", "dividend_yield"])
        for c in ("pe_ttm", "pb", "dividend_yield"):
            df[c] = pd.to_numeric(df[c], errors="coerce")
        out = {}
        for code, g in df.groupby("index_code"):
            out[code] = g
        return out

    def _load_bond10y(self, conn) -> pd.Series | None:
        with conn.cursor() as cur:
            cur.execute("SELECT trade_date, cn_bond_10y FROM bond_profit_daily "
                        "WHERE cn_bond_10y IS NOT NULL ORDER BY trade_date")
            rows = cur.fetchall()
        # 列名探测：bond_profit_daily 的 10Y 列名可能是 us/cn 前缀
        if not rows:
            # 兜底：直接查表结构找 10y 列
            cur.execute("SHOW COLUMNS FROM bond_profit_daily")
            cols = [r[0] for r in cur.fetchall()]
            cn10 = next((c for c in cols if "10" in c and "cn" in c.lower()), None)
            us10 = next((c for c in cols if "10" in c and "us" in c.lower()), None)
            col = cn10 or us10
            if not col:
                return None
            cur.execute(f"SELECT trade_date, `{col}` AS y FROM bond_profit_daily "
                        f"WHERE `{col}` IS NOT NULL ORDER BY trade_date")
            rows = cur.fetchall()
            if not rows:
                return None
            return pd.Series([float(r[1]) for r in rows],
                             index=pd.to_datetime([r[0] for r in rows]))
        return pd.Series([float(r[1]) for r in rows],
                         index=pd.to_datetime([r[0] for r in rows]))

    def _compute_fundamental(self, conn, val: dict[str, pd.DataFrame],
                             bond10y: pd.Series | None) -> pd.DataFrame:
        """逐指数算估值分位 + ERP，输出长表。"""
        rows = []
        for code, g in val.items():
            g = g.sort_values("trade_date")
            # PE 长历史（legu 源优先）
            pe = g[g["source"] == "legu"]
            if pe.empty:
                pe = g[g["source"].isin(["legu", "csindex"])]
            pb = g[g["source"] == "legu_pb"]
            if pb.empty:
                pb = g[g["source"].isin(["legu_pb", "legu"])]
            div = g[g["dividend_yield"].notna()]
            # 合并到交易日
            dates = sorted(set(g["trade_date"].dropna()))
            out_rows = []
            pe_hist = pe["pe_ttm"].dropna().values
            pb_hist = pb["pb"].dropna().values
            div_hist = div["dividend_yield"].dropna().values
            for d in dates:
                pe_v = pe[pe["trade_date"] == d]["pe_ttm"]
                pe_v = pe_v.dropna()
                pb_v = pb[pb["trade_date"] == d]["pb"].dropna()
                div_v = div[div["trade_date"] == d]["dividend_yield"].dropna()
                pe_cur = float(pe_v.iloc[-1]) if len(pe_v) else None
                pb_cur = float(pb_v.iloc[-1]) if len(pb_v) else None
                div_cur = float(div_v.iloc[-1]) if len(div_v) else None
                # 分位（全史）
                pe_pct = M.pct_rank(pe_cur, pe_hist) if pe_cur is not None else None
                pb_pct = M.pct_rank(pb_cur, pb_hist) if pb_cur is not None else None
                div_pct = M.pct_rank(div_cur, div_hist) if div_cur is not None else None
                # ERP = 1/PE - 10Y（PE 为倍数，1/PE 为收益率）
                erp = None
                if pe_cur and pe_cur > 0 and bond10y is not None and len(bond10y):
                    r = bond10y.asof(pd.Timestamp(d))
                    if r is not None and not pd.isna(r):
                        erp = (100.0 / pe_cur) - float(r)
                out_rows.append({
                    "index_code": code, "trade_date": d,
                    "pe_ttm": pe_cur, "pe_ttm_pct": pe_pct,
                    "pb": pb_cur, "pb_pct": pb_pct,
                    "dividend_yield": div_cur, "dividend_yield_pct": div_pct,
                    "erp": erp,
                })
            rows.extend(out_rows)
        if not rows:
            return pd.DataFrame()
        return pd.DataFrame(rows)

    def _compute_fin_quarter(self, conn) -> pd.DataFrame:
        """成分股财务聚合（近 40 季度）：逐指数、逐报告期聚合 ROE/净利增速/营收增速 + 现金流。"""
        with conn.cursor() as cur:
            cur.execute("SELECT index_code, stock_code FROM index_constituents")
            cons = cur.fetchall()
        if not cons:
            return pd.DataFrame()
        cons_map: dict[str, set[str]] = {}
        for code, sc in cons:
            cons_map.setdefault(code, set()).add(sc)

        with conn.cursor() as cur:
            cur.execute(
                "SELECT stock_code, report_date, roe, net_profit_yoy_gr, "
                "total_operating_yoy_gr, net_profit, total_operating_revenue "
                "FROM stock_financial_abstract_ths ORDER BY report_date"
            )
            rows = cur.fetchall()
        if not rows:
            return pd.DataFrame()
        fin = pd.DataFrame(rows, columns=["stock_code", "report_date", "roe",
                                          "net_profit_yoy_gr", "total_operating_yoy_gr",
                                          "net_profit", "total_operating_revenue"])
        # 该表数值列是「带格式字符串」（'16.75%' / '445.17亿'），须手动解析而非 to_numeric
        for c in ("roe", "net_profit_yoy_gr", "total_operating_yoy_gr",
                  "net_profit", "total_operating_revenue"):
            fin[c] = fin[c].map(_parse_cn_value)

        # 现金流（stock_cash_flow_ths，逐成分股经营活动现金流量净额，累计值）
        ocf = pd.DataFrame()
        with conn.cursor() as cur:
            cur.execute("SELECT stock_code, report_date, operating_cash_flow "
                        "FROM stock_cash_flow_ths ORDER BY report_date")
            orows = cur.fetchall()
        if orows:
            ocf = pd.DataFrame(orows, columns=["stock_code", "report_date", "operating_cash_flow"])
            ocf["operating_cash_flow"] = pd.to_numeric(ocf["operating_cash_flow"], errors="coerce")

        out_rows = []
        for code, stks in cons_map.items():
            sub = fin[fin["stock_code"].isin(stks)]
            if sub.empty:
                continue
            osub = ocf[ocf["stock_code"].isin(stks)] if not ocf.empty else pd.DataFrame()
            for rp, g in sub.groupby("report_date"):
                cnt = len(g)
                def _med(col):
                    v = g[col].replace([np.inf, -np.inf], np.nan).dropna()
                    return round(float(v.median()), 4) if len(v) else None
                def _sum(col):
                    v = g[col].replace([np.inf, -np.inf], np.nan).dropna()
                    return round(float(v.sum()), 2) if len(v) else None
                np_sum = _sum("net_profit")
                # 现金流聚合：同报告期成分股现金流净额之和 + 含金量比率
                ocf_sum = None
                ocf_ratio = None
                if not osub.empty:
                    og = osub[osub["report_date"] == rp]["operating_cash_flow"]
                    og = og.replace([np.inf, -np.inf], np.nan).dropna()
                    if len(og):
                        ocf_sum = round(float(og.sum()), 2)
                        if np_sum not in (None, 0):
                            ocf_ratio = round(float(ocf_sum) / float(np_sum), 4)
                out_rows.append({
                    "index_code": code, "report_date": rp,
                    "roe": _med("roe"),
                    "net_profit_yoy": _med("net_profit_yoy_gr"),
                    "revenue_yoy": _med("total_operating_yoy_gr"),
                    "net_profit_sum": np_sum,
                    "revenue_sum": _sum("total_operating_revenue"),
                    "ocf_sum": ocf_sum,
                    "ocf_ratio": ocf_ratio,
                    "constituent_count": cnt,
                })
        return pd.DataFrame(out_rows)

    def _write_fundamental(self, conn, fund: pd.DataFrame) -> int:
        cols = ["index_code", "trade_date", "pe_ttm", "pe_ttm_pct", "pb", "pb_pct",
                "dividend_yield", "dividend_yield_pct", "erp", "erp_pct"]
        if fund.empty:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM env_fundamental_daily")
            conn.commit()
            return 0
        payload = []
        for _, r in fund.iterrows():
            td = r["trade_date"]
            if hasattr(td, "date"):
                td = td.date()
            payload.append((
                r["index_code"], td, _f(r["pe_ttm"]), _f(r["pe_ttm_pct"]),
                _f(r["pb"]), _f(r["pb_pct"]), _f(r["dividend_yield"]),
                _f(r["dividend_yield_pct"]), _f(r["erp"]), None,
            ))
        with conn.cursor() as cur:
            cur.execute("DELETE FROM env_fundamental_daily")
            cur.executemany(
                f"INSERT INTO env_fundamental_daily ({', '.join(cols)}) "
                f"VALUES ({', '.join(['%s'] * len(cols))})", payload)
        conn.commit()
        return len(payload)

    def _write_quarter(self, conn, q: pd.DataFrame) -> int:
        if q.empty:
            return 0
        cols = ["index_code", "report_date", "roe", "net_profit_yoy", "revenue_yoy",
                "net_profit_sum", "revenue_sum", "ocf_sum", "ocf_ratio",
                "constituent_count"]
        payload = []
        for _, r in q.iterrows():
            rd = r["report_date"]
            if hasattr(rd, "date"):
                rd = rd.date()
            payload.append((
                r["index_code"], rd, _f(r["roe"]), _f(r["net_profit_yoy"]),
                _f(r["revenue_yoy"]), _f(r["net_profit_sum"]),
                _f(r["revenue_sum"]), _f(r["ocf_sum"]), _f(r["ocf_ratio"]),
                int(r["constituent_count"]) if r["constituent_count"] else None,
            ))
        with conn.cursor() as cur:
            cur.executemany(
                f"INSERT INTO env_fundamental_quarter ({', '.join(cols)}) "
                f"VALUES ({', '.join(['%s'] * len(cols))}) "
                "ON DUPLICATE KEY UPDATE "
                + ", ".join(f"{c}=VALUES({c})" for c in cols if c not in ("index_code", "report_date")),
                payload)
        conn.commit()
        return len(payload)

    def _write_threshold(self, conn, closes: dict[str, pd.Series]) -> int:
        """落 env_threshold：手定值 + 其余指数近3年95分位跌幅建议值。"""
        with conn.cursor() as cur:
            cur.execute("SELECT index_code, index_name FROM index_profile")
            prof = {r[0]: r[1] for r in cur.fetchall()}
        payload = []
        for code, close in closes.items():
            if code in MANUAL_THRESHOLD:
                thr, src, note = MANUAL_THRESHOLD[code], "manual", "手定阈值（按指数波动特征设定）"
            else:
                s = M.quantile_suggested_threshold(close, self.lookback_years, 0.95)
                if s is None:
                    continue
                thr, src = s, "suggested"
                note = f"按近{self.lookback_years}年日跌幅95分位统计的建议值，仅供参考"
            payload.append((code, prof.get(code), thr, src, note))
        if not payload:
            return 0
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO env_threshold (index_code, index_name, drop_threshold, "
                "threshold_source, threshold_note, update_time) "
                "VALUES (%s, %s, %s, %s, %s, NOW()) "
                "ON DUPLICATE KEY UPDATE index_name=VALUES(index_name), "
                "drop_threshold=VALUES(drop_threshold), threshold_source=VALUES(threshold_source), "
                "threshold_note=VALUES(threshold_note), update_time=NOW()",
                payload)
        conn.commit()
        return len(payload)

    def _compute_pb_dispersion(self, conn) -> pd.DataFrame:
        """成分股 PB 离散度（横截面标准差），逐指数逐交易日。

        数据源 stock_pb_daily（个股 PB 日频快照）× index_constituents（成分股名单）。
        离散度 = 成分股 PB 的横截面标准差（抗极端值可改中位绝对差，先按标准差）。
        返回长表 DataFrame（index_code, trade_date, pb_dispersion）。
        """
        with conn.cursor() as cur:
            cur.execute("SELECT index_code, stock_code FROM index_constituents")
            cons = cur.fetchall()
        if not cons:
            return pd.DataFrame()
        cons_map: dict[str, set[str]] = {}
        for code, sc in cons:
            cons_map.setdefault(code, set()).add(sc)

        with conn.cursor() as cur:
            cur.execute("SELECT trade_date, stock_code, pb FROM stock_pb_daily "
                        "WHERE pb IS NOT NULL ORDER BY trade_date")
            rows = cur.fetchall()
        if not rows:
            return pd.DataFrame()
        pb = pd.DataFrame(rows, columns=["trade_date", "stock_code", "pb"])
        pb["pb"] = pd.to_numeric(pb["pb"], errors="coerce")

        out_rows = []
        for code, stks in cons_map.items():
            sub = pb[pb["stock_code"].isin(stks)]
            if sub.empty:
                continue
            for td, g in sub.groupby("trade_date"):
                v = g["pb"].replace([np.inf, -np.inf], np.nan).dropna()
                if len(v) < 5:   # 样本太少离散度无意义
                    continue
                out_rows.append({
                    "index_code": code, "trade_date": td,
                    "pb_dispersion": round(float(v.std()), 4),
                })
        return pd.DataFrame(out_rows)

    def _write_pb_dispersion(self, conn, disp: pd.DataFrame) -> int:
        """把 PB 离散度写回 env_fundamental_daily（按 index_code + trade_date 对齐）。"""
        if disp.empty:
            return 0
        with conn.cursor() as cur:
            n = 0
            for _, r in disp.iterrows():
                td = r["trade_date"]
                if hasattr(td, "date"):
                    td = td.date()
                cur.execute(
                    "UPDATE env_fundamental_daily SET pb_dispersion=%s "
                    "WHERE index_code=%s AND trade_date=%s",
                    (_f(r["pb_dispersion"]), r["index_code"], td),
                )
                n += cur.rowcount
        conn.commit()
        return n

    # ---------- 主流程 ----------

    def run(self) -> dict:
        conn = pymysql.connect(**get_db_config().to_dict())
        try:
            # 1) 指数日线
            closes = self._load_index_close(conn)
            if not closes:
                raise RuntimeError("dc_index_market 无数据，无法物化投资环境")

            # 2) 技术面
            tech = self._compute_tech(closes)
            tech = self._attach_beta_corr(tech, closes)
            tech = self._attach_volume(tech, conn)
            n_tech = self._write_tech(conn, tech)

            # 3) 基本面
            val = self._load_valuation(conn)
            bond10y = self._load_bond10y(conn)
            fund = self._compute_fundamental(conn, val, bond10y)
            n_fund = self._write_fundamental(conn, fund)

            q = self._compute_fin_quarter(conn)
            n_q = self._write_quarter(conn, q)

            # PB 离散度（成分股 PB 横截面标准差，来自 stock_pb_daily；无数据则跳过）
            disp = self._compute_pb_dispersion(conn)
            n_disp = self._write_pb_dispersion(conn, disp)

            n_thr = self._write_threshold(conn, closes)

            msg = (f"投资环境物化：技术面 {n_tech} 行 / 基本面 {n_fund} 行 / "
                   f"季频 {n_q} 行 / PB离散度 {n_disp} 行 / 阈值 {n_thr} 个")
            logger.info("✅ %s", msg)
            return with_steps(
                {"records_written": n_tech + n_fund + n_q + n_thr + n_disp,
                 "error_count": 0, "errors": [], "note": msg},
                RUN_STEPS,
                {
                    1: f"{len(closes)} 个指数日线",
                    2: "12窗口/均线/量价/MACD/RSI/BOLL/支撑阻力/回撤/波动/Beta/相关性",
                    3: f"{n_tech} 行",
                    4: f"估值分位 {n_fund} 行 + 财务聚合 {n_q} 行",
                    5: f"基本面 {n_fund} 行 + 季频 {n_q} 行 + PB离散度 {n_disp} + 阈值 {n_thr} 个",
                },
            )
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
