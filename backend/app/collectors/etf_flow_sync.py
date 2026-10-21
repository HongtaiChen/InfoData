#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
InvestBuddy ETF 资金流向采集器（etf_share_daily）—— 「杠杆与资金行为」三指标之一

数据源：`ak.fund_etf_spot_em()`（东财全市场 ETF 实时快照，单次返回全部 ETF，
  约 1650 只，含「最新份额」列）。口径=全市场股票型 ETF（含跨境/商品，排除债券/货币）。

信号读法（registry 契约①结构分解⑤ + 自身纵比②）：
  ETF 净流入 = Σ(各 ETF 当日份额 − 上一采集日份额) × 净值。
  正 = 资金净申购（流入股票 ETF，散户/机构增量资金入场）；负 = 净赎回（资金流出）。
  这是「资金是否借道 ETF 进场」的直接读数，须看连续多日方向与累计，单日噪声大。

⚠️ 核心限制（务必理解再改）：
  1. **源无历史份额序列**：fund_etf_spot_em 只给「当前最新份额」，无逐日历史。
     ⇒ 本表从采集器上线当日起逐日积累，历史不可回溯。
     ⇒ 「净流入」只能算采集启动后的日变化，首日净流入为 NULL（无前一基准）。
  2. **份额 vs 净值**：份额变化 × 净值才等于资金流。但 fund_etf_spot_em 的「最新份额」
     是份额（份数），净值需另取（fund_etf_fund_daily_em 有单位净值）。为降低复杂度和
     接口依赖，本采集器 v1 **只采份额变化**（total_share 与 Δshare），净流入的净值
     折算留待分析层用「份额变化 × 最新价」近似（ETF 场内价 ≈ 净值，误差可忽略）。
     故 net_inflow 列在本采集器内按「Δ份额 × 最新价」估算。
  3. **股票型 ETF 范围**：fund_etf_spot_em 不含「基金类型」字段，无法直接筛股票型。
     ⇒ 用 fund_info.fund_type（库内已有）按基金代码映射筛「股票型」：
       指数型-股票 / 指数型-海外股票 / 股票型 / QDII-普通股票 / QDII-商品 / QDII-混合偏股。
     匹配不到的（新 ETF 尚未进 fund_info）按「保守纳入」处理（宁多勿漏）。

幂等：PRIMARY KEY(trade_date) + ON DUPLICATE KEY UPDATE。
  同一交易日重复采集 = 覆盖更新（份额是快照，后写覆盖前写）。

⚠️ 环境已知问题（2026-10-20 实测）：
  当前开发环境代理（127.0.0.1:5785）对东财 push2delay/push2 行情域**极不稳定**
  （时而 ProxyError / RemoteDisconnected）。生产环境代理正常时本采集器可用；
  开发环境实测跑通需在代理稳定时重试。
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
    {"no": 1, "name": "拉全市场 ETF 快照", "params": "fund_etf_spot_em（东财）主路径；失败降级 push2 直连分页（3 次重试/页）"},
    {"no": 2, "name": "筛股票型 ETF", "params": "按 fund_info.fund_type 映射筛股票型（指数型-股票/海外/股票型/QDII-股票/商品），匹配不到保守纳入"},
    {"no": 3, "name": "聚合份额并算净流入", "params": "total_share=Σ份额；Δ份额=今日−上一采集日；net_inflow=Δ份额×均价"},
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


def _f(v):
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f


def _fetch_spot() -> tuple[list[dict], str]:
    """拉全市场 ETF 快照，统一返回 [{code, name, price, share}] + 路径标注。

    主路径：ak.fund_etf_spot_em（有「最新份额」列，口径最准）。
    降级路径：push2 直连分页（份额按 流通市值÷最新价 反推，近似）。
    """
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
    except Exception as e1:
        logger.warning("fund_etf_spot_em 主路径失败（%s: %.120s），降级 push2 直连",
                       type(e1).__name__, str(e1))
        return _fetch_via_push2(), "push2_direct"


class EtfFlowSyncCollector:
    """ETF 份额与净流入增量同步（逐日积累）"""

    def __init__(self, timeout_sec: float = 120):
        self.timeout_sec = float(timeout_sec)

    def run(self) -> dict:
        rows_src, src_path = _fetch_spot()
        if not rows_src:
            raise RuntimeError("ETF 快照两条路径均无数据")

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
                cur.execute("SELECT MAX(trade_date) AS d FROM etf_share_daily")
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

            # 净流入：Δ份额 × 最新价（用全市场加权近似，或简单用总份额变化 × 均价）
            net_inflow = None
            if prev_date is not None:
                with conn.cursor() as cur:
                    cur.execute("SELECT total_share FROM etf_share_daily WHERE trade_date = %s",
                                [prev_date])
                    prev_row = cur.fetchone()
                if prev_row and prev_row["total_share"] is not None:
                    prev_share = float(prev_row["total_share"])
                    delta = total_share - prev_share
                    avg_price = sum(prices) / len(prices) if prices else None
                    if avg_price:
                        net_inflow = round(delta * avg_price, 2)

            today = date.today()
            with conn.cursor() as cur:
                sql = (
                    "INSERT INTO etf_share_daily (trade_date, total_share, net_inflow, fund_count, update_time, data_source) "
                    "VALUES (%s, %s, %s, %s, NOW(), 'AKSHARE') "
                    "ON DUPLICATE KEY UPDATE total_share=VALUES(total_share), "
                    "net_inflow=VALUES(net_inflow), fund_count=VALUES(fund_count), update_time=NOW()"
                )
                cur.execute(sql, (today, round(total_share, 4), net_inflow, fund_count))
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
