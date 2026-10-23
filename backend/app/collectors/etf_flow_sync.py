#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
InvestBuddy ETF 资金流向采集器（etf_share_daily）—— 「杠杆与资金行为」三指标之一

数据源（2026-10-23 改造，主源切换为交易所官方，可回溯历史）：
  · 主源① 上交所官方 `ak.fund_etf_scale_sse(date)` —— 指定任意历史交易日，返回当日
    全沪市 ETF 收盘清算后基金总份额（份）。✅ 可回溯到 2019 年甚至更早。
  · 主源② 深交所官方 `ak.fund_scale_daily_szse(start,end)` —— 日频历史，单次 ≤6 个月，
    返回沪深(深)ETF 每日基金总份额（份）。✅ 可回溯历史，分段循环可铺满全史。
  · 兜底  东财 `fund_etf_spot_em` / push2 直连 / 腾讯行情 qt.gtimg.cn（份额=市值÷价格反推）。

  口径=全市场股票型 ETF（含跨境/商品，排除债券/货币）。
  ⚠️ 交易所官方域名（sse.com.cn / szse.cn）不受东财 push2 的 IP 级 WAF 封禁影响，
    且份额为「收盘清算后」口径，比腾讯「市值÷价格反推」更权威、更稳定。

信号读法（registry 契约①结构分解⑤ + 自身纵比②）：
  ETF 净流入 = Σ(各 ETF 当日份额 − 上一交易日份额) × 净值。
  正 = 资金净申购（流入股票 ETF，散户/机构增量资金入场）；负 = 净赎回（资金流出）。
  这是「资金是否借道 ETF 进场」的直接读数，须看连续多日方向与累计，单日噪声大。

⚠️ 核心口径（务必理解再改）：
  1. **历史可回溯**（2026-10-23 起）：官方源提供逐日历史份额，历史由一次性回补脚本
     `backend/scripts/backfill_etf_share.py` 铺满（深交所 ≤6 月/段循环，上交所逐日）。
     增量采集器仍逐日积累最新一日。
  2. **净流入**：本采集器只采「份额」（total_share），净流入按「Δ份额 × 最新价」估算。
     ETF 场内价 ≈ 净值，误差可忽略。Δ份额 = 当日份额 − 上一交易日份额。
  3. **股票型 ETF 范围**：官方源**无「基金类型」字段**，无法直接筛股票型。
     ⇒ 用 fund_info.fund_type（库内已有）按基金代码映射筛「股票型」：
       指数型-股票 / 指数型-海外股票 / 股票型 / QDII-普通股票 / QDII-商品 / QDII-混合偏股。
     匹配不到的（新 ETF 尚未进 fund_info）按「保守纳入」处理（宁多勿漏）。
  4. **同源口径守卫**（铁律，勿删）：净流入跨日相减必须「同一数据源族」，否则会因
     份额口径差异（官方「收盘清算份额」 vs 腾讯「市值÷价格反推」）出假值，实测差万亿级。
     不同源/非法基线 → net_inflow=NULL（诚实缺一天，不造假）。

幂等：PRIMARY KEY(trade_date) + ON DUPLICATE KEY UPDATE。
  同一交易日重复采集 = 覆盖更新（份额是快照，后写覆盖前写）。
"""
import logging
import json
import time
from datetime import date

import pymysql
import requests
import akshare as ak

from ..db import get_db_config
from ._common import with_steps, call_with_timeout

logger = logging.getLogger(__name__)

RUN_STEPS = [
    {"no": 1, "name": "拉沪深 ETF 份额", "params": "主路径=交易所官方双源：上交所 fund_etf_scale_sse(date)+深交所 fund_scale_daily_szse(start,end)（收盘清算份额，可回溯历史）；失败降级东财 fund_etf_spot_em → push2 直连 → 腾讯 qt.gtimg.cn（份额=市值÷价格反推）"},
    {"no": 2, "name": "筛股票型 ETF", "params": "按 fund_info.fund_type 映射筛股票型（指数型-股票/海外/股票型/QDII-股票/商品），匹配不到保守纳入"},
    {"no": 3, "name": "聚合份额并算净流入", "params": "total_share=Σ份额；Δ份额=今日−上一交易日；net_inflow=Δ份额×均价"},
    {"no": 4, "name": "upsert 当日行", "params": "PRIMARY KEY(trade_date) + ON DUPLICATE KEY UPDATE"},
]

# 股票型 ETF 的 fund_type 前缀集合（库内 fund_info 口径）
STOCK_ETF_TYPES = (
    "指数型-股票", "指数型-海外股票", "股票型",
    "QDII-普通股票", "QDII-商品", "QDII-混合偏股",
)

# push2 直连降级路径参数（akshare 走 push2delay 域，部分网络环境该域被阻断；
# push2 域为实时行情域，与 push2delay 同数据不同接入点，2026-10-20 实测可行）
_P2_URL = "https://push2.eastmoney.com/api/qt/clist/get"
_P2_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

# 腾讯行情兜底（第三通道）：2026-10-23 实测东财 push2 对本机 IP 触发 WAF 封禁
# （TCP 可建连、GET / 返回 404，但 /api/qt/* 精确 RST，且封禁随重试扩大），
# 腾讯 qt.gtimg.cn 不受影响。份额=总市值(f[45]，亿)÷价格(f[3]) 反推，与 push2 降级同口径。
_TENCENT_URL = "https://qt.gtimg.cn/q="
_TENCENT_BATCH = 60
_TencentIdxPrice, _TencentIdxTotalMv = 3, 45


def _fetch_via_push2(max_retry: int = 3) -> list[dict]:
    """push2 域直连分页拉全市场 ETF（降级路径）。

    返回 [{code, name, price, share}]，share 可能为 None（部分 ETF 无份额数据）。
    """
    rows: list[dict] = []
    pn = 1
    total = None
    while True:
        params = {
            "pn": str(pn), "pz": "100", "po": "1", "np": "1",
            "ut": "bd1d9ddb04089700cf9c27f6f7426281", "fltt": "2", "invt": "2",
            "fid": "f12", "fs": "b:MK0021,b:MK0022,b:MK0023,b:MK0024",
            "fields": "f12,f14,f2,f24",
        }
        diff = None
        for attempt in range(max_retry):
            try:
                r = requests.get(_P2_URL, params=params, headers=_P2_HEADERS, timeout=10)
                d = json.loads(r.text)
                diff = (d.get("data") or {}).get("diff") or []
                total = (d.get("data") or {}).get("total")
                break
            except Exception:
                time.sleep(2)
        if diff is None:
            raise RuntimeError(f"push2 直连第 {pn} 页 {max_retry} 次均失败（代理阻断或网络故障）")
        for it in diff:
            rows.append({
                "code": str(it.get("f12", "")).zfill(6),
                "name": it.get("f14"),
                "price": _f(it.get("f2")),
                # f24=流通市值；份额在 clist 接口无直接字段 ⇒ 降级路径下份额用 市值/价格 反推
                "share": (_f(it.get("f24")) / _f(it.get("f2"))
                          if _f(it.get("f24")) and _f(it.get("f2")) else None),
            })
        if total is not None and len(rows) >= total:
            break
        if not diff:
            break
        pn += 1
    return rows


def _fetch_via_tencent() -> list[dict]:
    """腾讯行情兜底（第三通道）。

    ETF 代码清单来自库内 fund_info（股票型 fund_type 集合；该集合在 run() 主流程
    也用于筛选，作为清单与筛选同源，新基金未入 fund_info 时会滞后纳入）。
    份额 = 总市值(亿)×1e8 ÷ 价格（近似，与 push2 降级路径同口径）。
    """
    conn = pymysql.connect(**get_db_config().to_dict(),
                           cursorclass=pymysql.cursors.DictCursor)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT fund_code FROM fund_info "
                "WHERE fund_type IN (%s)" % ",".join(["%s"] * len(STOCK_ETF_TYPES)),
                list(STOCK_ETF_TYPES),
            )
            codes = [f"{r['fund_code']}" for r in cur.fetchall()]
    finally:
        conn.close()

    # 交易所前缀：51/56/58/50→sh，15/16→sz（ETF 场内代码段）
    def _pre(c: str) -> str | None:
        if c.startswith(("51", "56", "58", "50")):
            return "sh" + c
        if c.startswith(("15", "16")):
            return "sz" + c
        return None

    pairs = [p for p in (_pre(c) for c in codes) if p]
    out: list[dict] = []
    sess = requests.Session()
    sess.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    for i in range(0, len(pairs), _TENCENT_BATCH):
        batch = pairs[i:i + _TENCENT_BATCH]
        try:
            resp = sess.get(_TENCENT_URL + ",".join(batch), timeout=10)
            resp.encoding = "gbk"
        except Exception as e:  # noqa: BLE001
            logger.warning("腾讯批次 %d 失败（%s），跳过 %d 只", i // _TENCENT_BATCH + 1,
                           type(e).__name__, len(batch))
            continue
        for line in resp.text.splitlines():
            if "~" not in line:
                continue
            f = line.split("~")
            if len(f) <= max(_TencentIdxPrice, _TencentIdxTotalMv):
                continue
            price, mv_yi = _f(f[_TencentIdxPrice]), _f(f[_TencentIdxTotalMv])
            out.append({
                "code": f[2],
                "name": f[1],
                "price": price,
                "share": (mv_yi * 1e8 / price) if (mv_yi and price) else None,
            })
        time.sleep(0.2)
    return out


def _f(v):
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f


def _fetch_via_exchange(target_date: date) -> list[dict]:
    """交易所官方双源：拉指定交易日的沪深两市 ETF 收盘清算份额（份）。

    上交所 `fund_etf_scale_sse(date)`：单日全沪 ETF，列「基金代码」「基金份额」。
    深交所 `fund_scale_daily_szse(start,end)`：日频，列「基金代码」「基金份额」；
      单次查询 ≤6 个月，这里只查 target_date 当天（start=end）。

    返回 [{code, name, share}]（share=份）。⚠️ 官方源无「最新价」，price=None；
    调用方算净流入时如需均价，用 _fetch_prices_tencent 单独补（仅作乘数，不参与份额相减）。
    """
    rows: dict[str, dict] = {}

    # 上交所
    d = target_date.strftime("%Y%m%d")
    df_sse = call_with_timeout(ak.fund_etf_scale_sse, 60, date=d)
    if df_sse is None or df_sse.empty:
        logger.warning("上交所 fund_etf_scale_sse(%s) 返回为空", d)
    else:
        code_col = next((c for c in df_sse.columns if "代码" in str(c)), None)
        share_col = next((c for c in df_sse.columns if "份额" in str(c)), None)
        name_col = next((c for c in df_sse.columns if "简称" in str(c)), None)
        if not code_col or not share_col:
            raise RuntimeError(f"fund_etf_scale_sse 列名变更：{list(df_sse.columns)}")
        for _, r in df_sse.iterrows():
            code = str(r.get(code_col)).zfill(6)
            sh = _f(r.get(share_col))
            rows[code] = {
                "code": code,
                "name": str(r.get(name_col)) if name_col else None,
                "share": sh,
            }

    # 深交所（start=end=当天）
    df_szse = call_with_timeout(ak.fund_scale_daily_szse, 60,
                                start_date=d, end_date=d, symbol="ETF")
    if df_szse is None or df_szse.empty:
        logger.warning("深交所 fund_scale_daily_szse(%s) 返回为空", d)
    else:
        code_col = next((c for c in df_szse.columns if "代码" in str(c)), None)
        share_col = next((c for c in df_szse.columns if "份额" in str(c)), None)
        name_col = next((c for c in df_szse.columns if "简称" in str(c)), None)
        if not code_col or not share_col:
            raise RuntimeError(f"fund_scale_daily_szse 列名变更：{list(df_szse.columns)}")
        for _, r in df_szse.iterrows():
            code = str(r.get(code_col)).zfill(6)
            sh = _f(r.get(share_col))
            if code in rows:
                # 理论上沪深代码不重叠，防御性处理：份额取并集（不覆盖）
                continue
            rows[code] = {
                "code": code,
                "name": str(r.get(name_col)) if name_col else None,
                "share": sh,
            }

    return list(rows.values())


def _fetch_prices_tencent(codes: list[str]) -> dict[str, float]:
    """腾讯行情批量取 ETF 场内价，返回 {code: price}。

    仅作「净流入 = Δ份额 × 均价」里的价格乘数（不参与份额相减，不触同源守卫）。
    复用 _fetch_via_tencent 的腾讯域与字段解析逻辑，但只回价格。
    """
    if not codes:
        return {}

    def _pre(c: str) -> str | None:
        if c.startswith(("51", "56", "58", "50")):
            return "sh" + c
        if c.startswith(("15", "16")):
            return "sz" + c
        return None

    pairs = [p for p in (_pre(c) for c in codes) if p]
    out: dict[str, float] = {}
    sess = requests.Session()
    sess.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    for i in range(0, len(pairs), _TENCENT_BATCH):
        batch = pairs[i:i + _TENCENT_BATCH]
        try:
            resp = sess.get(_TENCENT_URL + ",".join(batch), timeout=10)
            resp.encoding = "gbk"
        except Exception as e:  # noqa: BLE001
            logger.warning("腾讯价格批次 %d 失败（%s），跳过 %d 只",
                           i // _TENCENT_BATCH + 1, type(e).__name__, len(batch))
            continue
        for line in resp.text.splitlines():
            if "~" not in line:
                continue
            f = line.split("~")
            if len(f) <= max(_TencentIdxPrice, _TencentIdxTotalMv):
                continue
            code = f[2]
            price = _f(f[_TencentIdxPrice])
            if price:
                out[code] = price
        time.sleep(0.2)
    return out


def _fetch_spot() -> tuple[list[dict], str]:
    """拉当日沪深 ETF 份额，统一返回 [{code, name, price, share}] + 路径标注。

    主路径（2026-10-23 起）：交易所官方双源 _fetch_via_exchange（收盘清算份额，最权威）。
    降级路径：东财 fund_etf_spot_em（有「最新份额」列）→ push2 直连分页 → 腾讯行情兜底。

    ⚠️ 官方源无「最新价」，price=None；净流入计算时均价由调用方处理（官方源当日
    若缺价，net_inflow 退化为仅份额差，分析层用场内价近似时另行取价）。
    """
    today = date.today()
    try:
        rows = _fetch_via_exchange(today)
        if not rows:
            raise RuntimeError("交易所官方双源均返回空")
        return rows, "exchange"
    except Exception as e1:
        logger.warning("交易所官方源失败（%s: %.120s），降级东财 fund_etf_spot_em",
                       type(e1).__name__, str(e1))
        try:
            df = call_with_timeout(ak.fund_etf_spot_em, 120)
            if df is None or df.empty:
                raise RuntimeError("fund_etf_spot_em 返回为空")
            code_col = next((c for c in df.columns if str(c) == "代码"), None)
            share_col = next((c for c in df.columns if "份额" in str(c)), None)
            price_col = next((c for c in df.columns if str(c) == "最新价"), None)
            if not code_col or not share_col:
                raise RuntimeError(f"fund_etf_spot_em 列名变更：{list(df.columns)}")
            rows = []
            for _, r in df.iterrows():
                rows.append({
                    "code": str(r.get(code_col)).zfill(6),
                    "name": r.get("名称"),
                    "price": _f(r.get(price_col)) if price_col else None,
                    "share": _f(r.get(share_col)),
                })
            return rows, "akshare(fund_etf_spot_em)"
        except Exception as e2:
            logger.warning("fund_etf_spot_em 降级失败（%s: %.120s），再降级 push2 直连",
                           type(e2).__name__, str(e2))
            try:
                return _fetch_via_push2(), "push2_direct"
            except Exception as e3:
                logger.warning("push2 直连失败（%s: %.120s），兜底腾讯行情",
                               type(e3).__name__, str(e3))
                return _fetch_via_tencent(), "tencent"


class EtfFlowSyncCollector:
    """ETF 份额与净流入增量同步（逐日积累）"""

    def __init__(self, timeout_sec: float = 120):
        self.timeout_sec = float(timeout_sec)

    def run(self) -> dict:
        rows_src, src_path = _fetch_spot()
        if not rows_src:
            raise RuntimeError("ETF 快照两条路径均无数据")

        # 官方源无价格，用腾讯批量补「价格乘数」（不参与份额相减，不触同源守卫）
        if src_path == "exchange":
            codes = [r["code"] for r in rows_src]
            price_map = _fetch_prices_tencent(codes)
            for r in rows_src:
                r["price"] = price_map.get(r["code"])

        # 股票型 ETF 名单（从 fund_info 映射）
        conn = pymysql.connect(**get_db_config().to_dict(),
                               cursorclass=pymysql.cursors.DictCursor)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT fund_code, fund_type FROM fund_info "
                    "WHERE fund_type IN (%s)" % ",".join(["%s"] * len(STOCK_ETF_TYPES)),
                    list(STOCK_ETF_TYPES),
                )
                stock_etf_codes = {str(r["fund_code"]) for r in cur.fetchall()}
                # 基线取「严格早于今天」的最近一日：同日重跑时不能拿今天自己的行当基线
                cur.execute("SELECT MAX(trade_date) AS d FROM etf_share_daily "
                            "WHERE trade_date < %s", [date.today()])
                prev_date = cur.fetchone()["d"]

            # 聚合（rows_src 已统一为 [{code,name,price,share}]）
            total_share = 0.0
            fund_count = 0
            prices: list[float] = []
            for r in rows_src:
                code = r["code"]
                # 股票型筛选：在名单内，或名单为空（fund_info 尚未覆盖）时保守纳入
                if stock_etf_codes and code not in stock_etf_codes:
                    continue
                sh = r.get("share")
                if sh is not None:
                    total_share += sh
                    fund_count += 1
                if r.get("price") is not None:
                    prices.append(r["price"])

            # 净流入：Δ份额 × 最新价。⚠️ 口径守卫（2026-10-23）：
            #   1) 基线与当日的 data_source 必须同源（akshare「最新份额」列 vs 市值÷价格反推，
            #      两者口径有系统性差异，跨源相减会出假值，实测差出万亿级）；
            #   2) 基线 total_share 必须为正（2026-10-21 akshare 基线解析事故，sum=-12236）。
            #   不同源/非法基线 → net_inflow=NULL（诚实缺一天，不造假）。
            net_inflow = None
            if prev_date is not None:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT total_share, data_source FROM etf_share_daily "
                        "WHERE trade_date = %s", [prev_date])
                    prev_row = cur.fetchone()
                prev_share = float(prev_row["total_share"]) if prev_row and prev_row["total_share"] is not None else None
                src_family = "AKSHARE" if src_path.startswith("akshare") else src_path
                same_src = bool(prev_row and (prev_row.get("data_source") or "AKSHARE") == src_family)
                if prev_share is not None and prev_share > 0 and same_src:
                    delta = total_share - prev_share
                    avg_price = sum(prices) / len(prices) if prices else None
                    if avg_price:
                        net_inflow = round(delta * avg_price, 2)

            today = date.today()
            with conn.cursor() as cur:
                sql = (
                    "INSERT INTO etf_share_daily (trade_date, total_share, net_inflow, fund_count, update_time, data_source) "
                    "VALUES (%s, %s, %s, %s, NOW(), %s) "
                    "ON DUPLICATE KEY UPDATE total_share=VALUES(total_share), "
                    "net_inflow=VALUES(net_inflow), fund_count=VALUES(fund_count), update_time=NOW()"
                )
                cur.execute(sql, (today, round(total_share, 4), net_inflow, fund_count, src_path))
            conn.commit()
        finally:
            conn.close()

        msg = (f"ETF 份额 upsert {today}｜股票型 {fund_count} 只｜"
               f"总份额 {total_share:,.0f}｜净流入 {net_inflow if net_inflow is not None else '首日(无基准)'}")
        logger.info("✅ %s", msg)
        return with_steps(
            {"records_written": 1, "error_count": 0, "errors": [], "note": msg},
            RUN_STEPS,
            {
                1: f"源 {len(rows_src)} 只（{src_path}）",
                2: f"筛股票型后 {fund_count} 只",
                3: f"总份额 {total_share:,.0f}；净流入 {net_inflow if net_inflow is not None else '首日'}",
                4: f"写 {today}",
            },
        )
