#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
InvestBuddy 日线口径共用内核（实际价 + 后复权因子）

为什么要有这个模块
================================================================================
「全库重建」（stock_daily_rebuild）与「日常增量」（stock_daily_incr）必须写出
**逐列一致**的行，否则重建刚统一好的口径会在次日被增量重新污染 —— 这正是本表
此前的病史（volume 单位、turnover 单位都是「两个采集器各写各的」漂移出来的）。
故把「源 DataFrame → 入库元组」这段唯一容易写歪的转换收敛到一处，两边共用。

口径（2026-09-20 定稿）
================================================================================
- open/high/low/close = **不复权实际成交价**（永不随时间改变，与 amount/volume 自洽）
- adj_factor          = 后复权累计因子 = hfq_close / close（历史永不变）
      实际价   = close
      后复权价 = close * adj_factor
      前复权价 = close * adj_factor / latest(adj_factor)
      收益率   = (c2*f2)/(c1*f1) - 1
- volume              = **股**（源为「手」则 ×100；源为「真实股数×100」则 ÷100）
- turnover_ratio      = **百分数 %**（源为比例则 ×100）

单位一律「自适应判定」而非「按源硬编码」
================================================================================
2026-09-20 实测：**同一个源的单位会按股票而变**。腾讯 `stock_zh_a_hist_tx`
对 sh600519 返回 volume=2,623,500（amount/volume=1260.88≈close → 股），
对 sz000001 却返回 949,626（amount/volume=1165.36≈close×100 → 手）。
按源写死换算系数必然错一半，故改用**判据自证**：

- volume：`vwap = amount / volume`，三种假设各算命中数、取多数：
    vwap≈close        → 已是股（不动）
    vwap≈close×100    → 源是「手」→ ×100
    vwap≈close/100    → 源是「真实股数×100」→ ÷100
    （三者相差 100 倍，判据天然无歧义；都命中不了则保持原值，由 DQ 的
    daily_price_vwap 规则暴露。2026-09-20 实测 689009 CDR 命中第三种、1,432 行）
- turnover：取当日该批的**中位数**，< 0.3 判为比例 → ×100，否则判为百分数。
  阈值与 `market_style_sync.TURNOVER_FRACTION_MAX` **同源**（实测余量：百分数口径下
  日最小中位数 0.47、比例口径下日最大中位数 0.04，两侧各留 1.5× / 7.5× 余量）。

为什么因子要「阶梯化」
================================================================================
hfq 与 raw 都只保留 2 位小数，逐日比值带 ~0.005/price 的舍入噪声。实测茅台
逐日 hfq/raw 有 5,238 个**不同**值，而真实除权只有 29 次 —— 直接落原始比值等于把
舍入噪声写成 1,800 万个「假精度因子」。故按「相对变化 > FACTOR_TOL」分段、段内取中位数。
"""
import math
import os
import threading
import time

import numpy as np
import pandas as pd

# 国内数据源不走代理（本机若配置 HTTP 代理，访问新浪等国内站点会 ProxyError）
os.environ["NO_PROXY"] = "*"
os.environ["no_proxy"] = "*"

# 因子阶梯化阈值（相对变化）。真实除权跳变 = 每股分红/股价 ≈ 0.1%~2%；
# 舍入噪声量级 ~0.005/price（10 元股约 0.05%）。取 0.3% 把两者分开。
FACTOR_TOL = 0.003

# turnover 单位判定阈值（与 market_style_sync.TURNOVER_FRACTION_MAX 同源，改动需两处同步）
TURNOVER_FRACTION_MAX = 0.3

# volume 单位判定容差：vwap 与 close 的相对偏差（含盘中均价与收盘价的天然差）
VWAP_TOL = 0.25

# 规范列
CANON = ["date", "open", "high", "low", "close", "volume", "amount", "turnover"]

# 各源列名 → 规范列名
SRC_COLMAP = {
    "sina": {"date": "date", "open": "open", "high": "high", "low": "low", "close": "close",
             "volume": "volume", "amount": "amount", "turnover": "turnover"},
    "eastmoney": {"date": "日期", "open": "开盘", "high": "最高", "low": "最低", "close": "收盘",
                  "volume": "成交量", "amount": "成交额", "turnover": "换手率"},
    "tencent": {"date": "date", "open": "open", "high": "high", "low": "low", "close": "close",
                "volume": "volume", "amount": "amount", "turnover": "turnover"},
    "tushare": {"date": "trade_date", "open": "open", "high": "high", "low": "low", "close": "close",
                "volume": "vol", "amount": "amount", "turnover": "turnover_rate"},
}


def code_to_symbol(code: str, *, bj: bool = True) -> str:
    """6 位代码 → 带交易所前缀（sh600519 / sz000001 / bj920599）

    `bj=False` 时不把 4/8/92 段映射到北交所（留给不支持该市场的调用方自行处理）。

    与早期实现的三处差异（缺任一个都会退化成裸码、取数必然失败）：
      ① 显式补「92」段 —— 北交所 2025-10-09 切换后的独立代码段；
      ② 显式补「302」段 —— 深交所 2025 年新启用的创业板段（库内实测已有 302132）；
      ③ 显式补 B 股段（900→sh / 200、201→sz）。
    """
    c = str(code).strip()
    if c.startswith(("600", "601", "603", "605", "688", "689", "900")):
        return "sh" + c
    if c.startswith(("000", "001", "002", "003", "300", "301", "302", "200", "201")):
        return "sz" + c
    if bj and c.startswith(("43", "83", "87", "92")):
        return "bj" + c
    if bj and c.startswith(("4", "8")):
        return "bj" + c
    return c


def num(v, nd: int | None = None):
    """安全转数值：NaN/inf/不可解析 → None（写库落 NULL 而非脏值）"""
    if v is None:
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(x):
        return None
    return round(x, nd) if nd is not None else x


# ---------------------------------------------------------------------------
# V8 / py_mini_racer 预热（多线程并发的**前置条件**，2026-09-20 实测）
#
# 新浪接口 `stock_zh_a_daily` 在 akshare 内部用 py_mini_racer(V8) 解密 JS。
# V8 的 PartitionAlloc **不允许在同一进程里被并发初始化**，直接开线程池调用
# 会让整个 Python 进程**硬崩溃**（不是抛异常，是 abort）：
#
#     [FATAL:partition_address_space.cc(243)] Check failed: !IsConfigurablePoolInitialized()
#     ... mini_racer.dll → libffi-8.dll → _ctypes.pyd
#
# 最小实验（.workbuddy/tmp/probe_thread_crash.py）：串行 4/4 成功；3 线程并发必崩。
# 规避办法：**先在主线程调用一次**，让 V8 完成初始化；此后线程池并发即安全。
# 压力验证（.workbuddy/tmp/stress_sina_mt.py）：主线程预热后 4 线程 × 6 轮
# = 576 次并发调用，0 异常、进程存活，吞吐 8.5 次/秒。
#
# 预热失败（网络不通等）时**不硬闯**：调用方应退回单线程（见各采集器 run()）。
# ---------------------------------------------------------------------------
_WARMED = False
_WARM_LOCK = threading.Lock()


def warmup_js_engine(symbol: str = "sh600519", attempts: int = 3) -> str:
    """在主线程预热 akshare 的 JS 引擎（V8）。返回人类可读结果字符串。

    必须在创建线程池**之前**、在**主线程**里调用；已预热过则直接返回。
    返回以「✅」开头表示预热成功（可安全并发），否则调用方应退回单线程。
    """
    global _WARMED
    with _WARM_LOCK:
        if _WARMED:
            return "✅ 已预热（跳过）"
        import akshare as ak

        last = ""
        for i in range(1, max(1, attempts) + 1):
            t0 = time.time()
            try:
                ak.stock_zh_a_daily(symbol=symbol, start_date="20260801",
                                    end_date="20260918", adjust="")
                _WARMED = True
                return f"✅ 预热成功（第 {i} 次，{time.time()-t0:.2f}s）"
            except Exception as e:                        # noqa: BLE001
                last = f"{type(e).__name__}: {e}"
                time.sleep(1.0)
        return f"⚠️ 预热失败（{attempts} 次）：{last[:120]}"


def js_engine_ready() -> bool:
    """V8 是否已预热（决定能否安全开线程池）"""
    return _WARMED


def canon(df: pd.DataFrame, source: str) -> pd.DataFrame:
    """源 DataFrame → 规范列 + 规范单位（volume=股 / turnover=%）

    只做列名归一与单位归一，不做排序去重（由 derive_rows 负责）。
    缺列以 NaN 补齐（hfq 帧通常只有 date/close）。
    """
    if df is None or len(df) == 0:
        return pd.DataFrame(columns=CANON)
    m = SRC_COLMAP.get(source)
    if m is None:
        raise ValueError(f"未知数据源：{source}")
    d = pd.DataFrame()
    for c in CANON:
        src_col = m.get(c)
        d[c] = df[src_col] if (src_col and src_col in df.columns) else np.nan
    d["date"] = pd.to_datetime(d["date"], errors="coerce").dt.date
    for c in CANON[1:]:
        d[c] = pd.to_numeric(d[c], errors="coerce")

    # ---- volume 单位自判（见模块头「单位一律自适应判定」）----
    c_arr = d["close"].to_numpy(dtype=float)
    v_arr = d["volume"].to_numpy(dtype=float)
    a_arr = d["amount"].to_numpy(dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        vwap = a_arr / v_arr
    ok = np.isfinite(vwap) & np.isfinite(c_arr) & (c_arr > 0) & (v_arr > 0)
    if ok.any():
        rel_share = np.abs(vwap[ok] - c_arr[ok]) / c_arr[ok]
        rel_hand = np.abs(vwap[ok] - c_arr[ok] * 100.0) / (c_arr[ok] * 100.0)
        rel_div = np.abs(vwap[ok] - c_arr[ok] / 100.0) / (c_arr[ok] / 100.0)
        n_share = int((rel_share <= VWAP_TOL).sum())
        n_hand = int((rel_hand <= VWAP_TOL).sum())
        n_div = int((rel_div <= VWAP_TOL).sum())
        # 少数服从多数：单只票的一批数据里单位应当一致
        if n_hand > n_share and n_hand >= n_div:
            d["volume"] = d["volume"] * 100.0
        elif n_div > n_share and n_div > n_hand:
            # 第三种单位情形（2026-09-20 立）：源给的 volume 是**真实股数的 100 倍**。
            # 实测 689009（九号公司 CDR，腾讯源）1,432 行全数命中 vwap≈close/100，
            # 而 000003/000005/000013/600565/600899 共 19,735 行 TENCENT 数据 0 命中 ——
            # 单位偏差是「按票」而非「按源」的，故只认判据、不认源。
            d["volume"] = d["volume"] / 100.0

    # ---- turnover 单位自判（阈值与 market_style_sync 同源）----
    tov = d["turnover"].to_numpy(dtype=float)
    fin = tov[np.isfinite(tov)]
    if fin.size:
        med = float(np.median(np.abs(fin)))
        if 0 < med < TURNOVER_FRACTION_MAX:
            d["turnover"] = d["turnover"] * 100.0
    return d


def step_factor(raw_close: np.ndarray, hfq_close: np.ndarray, tol: float = FACTOR_TOL):
    """把 hfq/raw 比值阶梯化为分段常数因子序列，返回 (factors, n_cut)。

    n_cut = 跳变（≈ 疑似除权）次数。后复权因子数学上是阶梯函数（只在除权日上跳），
    用「分段 + 段内中位数」还原：既吃掉舍入噪声，又不像逐日取值那样漂移。

    ⚠️ 阈值的分辨率上限（2026-09-20 实测，**不是 bug，是信息论层面的必然**）
    --------------------------------------------------------------------------
    源价只有 2 位小数，逐日比值 hfq/raw 的相对舍入噪声约 `0.01 / price`：

        10 元股 → 0.1%（远小于 tol=0.3%，分段干净）
         2 元股 → 0.5%（与 tol 同量级，开始误分段）
         0.66 元股 → 1.5%（**远大于 tol**，每天都判成跳变）

    实测印证：退市股 000005（价格区间 0.66~19.14）在 7,222 行上产生 3,335 次跳变，
    退市股 000003（1.66~50.9）在 2,465 行上产生 618 次 —— 即低价格段的因子
    「退化回逐日比值」。此时 `adj_factor` 数值上等于 hfq/raw 的当日比值，
    故派生结论仍然正确（后复权价 = close×factor 恰好还原源的 hfq 价），
    只是失去了「干净阶梯」这一形态。**无法通过调参解决**：源的 2 位小数里
    本就不含 0.5% 以下的分辨率。故此处不引入价格自适应容差 ——
    那只会把「确定做不到」包装成「看起来做到了」。
    """
    r = hfq_close / raw_close
    n = len(r)
    cuts = [0]
    for i in range(1, n):
        base = r[cuts[-1]]
        if base > 0 and abs(r[i] - base) / base > tol:
            cuts.append(i)
    cuts.append(n)
    f = np.empty(n, dtype=float)
    for k in range(len(cuts) - 1):
        a, b = cuts[k], cuts[k + 1]
        f[a:b] = float(np.median(r[a:b]))
    return f, len(cuts) - 2


def derive_rows(code: str, raw: pd.DataFrame, hfq: pd.DataFrame | None, source: str):
    """规范化的 (raw, hfq) → 入库元组列表。

    返回 (rows, n_cut, degraded)：
      rows    —— 与 _INSERT_COLS 同序的元组列表
      n_cut   —— 因子跳变（≈ 除权）次数
      degraded—— True 表示 hfq 缺失/不可用 → 涨跌幅退化成了不复权口径（除权日会有偏差）

    调用方须保证 raw / hfq 已过 `canon()`。
    """
    if raw is None or len(raw) == 0:
        return [], 0, False
    d = (raw.dropna(subset=["date"])
            .sort_values("date")
            .drop_duplicates("date", keep="last")
            .reset_index(drop=True))
    if d.empty:
        return [], 0, False

    n = len(d)
    dates = d["date"].tolist()
    o = d["open"].to_numpy(dtype=float)
    h = d["high"].to_numpy(dtype=float)
    low = d["low"].to_numpy(dtype=float)
    c = d["close"].to_numpy(dtype=float)
    vol = d["volume"].to_numpy(dtype=float)
    amt = d["amount"].to_numpy(dtype=float)
    tov = d["turnover"].to_numpy(dtype=float)

    # ---- 复权因子与真实收益 ----
    fac = np.full(n, np.nan)
    ret = np.full(n, np.nan)
    n_cut = 0
    hfq_ok = False
    if hfq is not None and len(hfq) > 0:
        hh = (hfq.dropna(subset=["date"])
                  .drop_duplicates("date", keep="last")[["date", "close"]]
                  .rename(columns={"close": "hfq_close"}))
        hc = pd.to_numeric(d[["date"]].merge(hh, on="date", how="left")["hfq_close"],
                           errors="coerce").to_numpy(dtype=float)
        good = np.isfinite(hc) & (hc > 0) & np.isfinite(c) & (c > 0)
        # 对齐率需够高才敢用 hfq 口径算收益（同源同接口，正常应接近 100%）
        if good.sum() >= 1 and good.sum() >= 0.95 * n:
            fs, n_cut = step_factor(c[good], hc[good], FACTOR_TOL)
            ser = pd.Series(np.nan, index=range(n))
            ser.loc[np.flatnonzero(good)] = fs
            fac = ser.ffill().bfill().to_numpy(dtype=float)
            # 停牌日 hfq 可能为 0 → 除零产生 inf，必须先清成 NaN 再参与派生计算
            with np.errstate(divide="ignore", invalid="ignore"):
                ret[1:] = hc[1:] / hc[:-1] - 1
            ret[~np.isfinite(ret)] = np.nan
            hfq_ok = True

    if not hfq_ok:
        # 降级：不复权口径算收益（除权日会有偏差，由巡检与降级计数暴露）
        with np.errstate(divide="ignore", invalid="ignore"):
            ret[1:] = c[1:] / c[:-1] - 1
        ret[~np.isfinite(ret)] = np.nan

    # ---- 派生列 ----
    with np.errstate(divide="ignore", invalid="ignore"):
        pre = np.where(np.isfinite(ret) & (ret > -1), c / (1.0 + ret), np.nan)
        pre[0] = np.nan
        chg = c - pre
        pct = np.where(np.isfinite(ret), ret * 100.0, np.nan)

    rows = []
    for i in range(n):
        rows.append((
            code, dates[i],
            num(o[i], 3), num(h[i], 3), num(low[i], 3), num(c[i], 3),
            num(pre[i], 3), num(chg[i], 3), num(pct[i], 4),
            int(vol[i]) if math.isfinite(vol[i]) else None,
            num(amt[i], 2), num(tov[i], 4),
            num(fac[i], 8), source,
        ))
    return rows, n_cut, (not hfq_ok)


# 入库列顺序（两个采集器的 INSERT 必须与此一致）
INSERT_COLS = ["stock_code", "trade_date", "open", "high", "low", "close", "pre_close",
               "change_amount", "change_pct", "volume", "amount", "turnover_ratio",
               "adj_factor", "data_source"]

# ⚠️ update_time 必须用 %s 占位、由 Python 传值，**不能**写成 SQL 的 NOW()。
# 原因（2026-09-20 实测性能事故）：pymysql 的 executemany 只在「VALUES 里全是占位符」时
# 才把多行合并成一条 INSERT（`RE_INSERT_VALUES` 正则要求括号内仅由 %s 组成）。
# 一旦夹了 NOW()，正则失配 → 静默退化为**逐行独立往返**。
# 实测后果：全库重建吞吐被压到约 1,000 行/秒，单只约 3.8s，全量需 6 小时（改前预期 1 小时）。
# 改回占位符后合并为多行 INSERT，写入吞吐提升一个数量级。
INSERT_ALL_COLS = INSERT_COLS + ["update_time"]

INSERT_SQL = f"""
    INSERT INTO stock_market_daily
        ({", ".join(INSERT_ALL_COLS)})
    VALUES ({", ".join(["%s"] * len(INSERT_ALL_COLS))})
    ON DUPLICATE KEY UPDATE
        open = VALUES(open), high = VALUES(high), low = VALUES(low), close = VALUES(close),
        pre_close = VALUES(pre_close), change_amount = VALUES(change_amount),
        change_pct = VALUES(change_pct), volume = VALUES(volume), amount = VALUES(amount),
        turnover_ratio = VALUES(turnover_ratio), adj_factor = VALUES(adj_factor),
        data_source = VALUES(data_source), update_time = VALUES(update_time)
"""


def with_update_time(rows: list[tuple], ts=None) -> list[tuple]:
    """给业务元组补上 update_time（配合 INSERT_ALL_COLS 的第 15 列）

    必须是「多行 VALUES 全占位符」的形态，见 INSERT_ALL_COLS 上方注释。
    """
    from datetime import datetime

    ts = ts or datetime.now()
    return [tuple(r) + (ts,) for r in rows]
