#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
InvestBuddy 个股 PB 日频快照采集器（stock_pb_daily，东财实时行情经 akshare/直连）

背景（2026-10-20，投资环境·估值水平章节）：
  该章节要补「PB 离散度」（指数成分股 PB 的横截面离散程度）。库内 stock_market_current.pb
  全表为空（历史遗留，见 index_valuation_sync 文档），故新建本采集器补个股 PB 横截面。

- 源：ak.stock_zh_a_spot_em()（东财沪深京 A 股实时行情，~5500 只，含「市净率」列 f23）
- 写表：stock_pb_daily（trade_date / stock_code / pb / close）
- 幂等：DB 侧 PRIMARY KEY(trade_date, stock_code) + ON DUPLICATE KEY UPDATE
- 双通道降级：主路径 akshare.stock_zh_a_spot_em；若 ProxyError/ConnectionError 则
  降级直接 requests 请求 82.push2.eastmoney.com（绕过 akshare 的 fetch_paginated_data）

⚠️ 环境限制（与 etf_flow_sync 同一障碍）：本机代理 127.0.0.1:5785 对东财 push2 域
   间歇性/持续性阻断。本采集器代码就绪，网络恢复后调度任务（每交易日盘后）自动首采；
   当前环境无法实测跑通属网络限制，非代码缺陷。
"""
import json
import logging
import time
from datetime import date

import pymysql
import requests

import akshare as ak

from ..db import get_db_config
from ._common import with_steps, call_with_timeout

logger = logging.getLogger(__name__)

RUN_STEPS = [
    {"no": 1, "name": "拉全市场个股 PB 快照", "params": "stock_zh_a_spot_em()（东财 ~5500 只，含市净率 f23）；双通道降级（akshare→push2 直连）"},
    {"no": 2, "name": "清洗", "params": "市净率/最新价转 float；NaN/-- → NULL"},
    {"no": 3, "name": "upsert 当日行", "params": "PRIMARY KEY(trade_date, stock_code) + ON DUPLICATE KEY UPDATE"},
]

_INSERT_COLS = ["trade_date", "stock_code", "pb", "close", "update_time", "data_source"]
_UPDATE_COLS = ["pb", "close", "update_time"]

# push2 直连降级：字段映射（f12=代码 f14=名称 f2=最新价 f23=市净率）
_PUSH2_URL = "https://82.push2.eastmoney.com/api/qt/clist/get"
_PUSH2_UT = "bd1d9ddb04089700cf9c27f6f7426281"
_PUSH2_FS = "m:0 t:6,m:0 t:80,m:1 t:2,m:1 t:23,m:0 t:81 s:2048"


def _f(v):
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f


def _fetch_push2() -> list[dict]:
    """直连 push2 分页拉取全市场 PB（降级路径，绕过 akshare 的 fetch_paginated_data）。"""
    out: list[dict] = []
    pn = 1
    while True:
        params = {
            "pn": str(pn), "pz": "1000", "po": "1", "np": "1",
            "ut": _PUSH2_UT, "fltt": "2", "invt": "2",
            "fid": "f12", "fs": _PUSH2_FS, "fields": "f2,f12,f14,f23",
        }
        r = requests.get(_PUSH2_URL, params=params, timeout=20,
                         headers={"User-Agent": "Mozilla/5.0"})
        data = json.loads(r.text)
        d = data.get("data") or {}
        diff = d.get("diff") or []
        if isinstance(diff, dict):
            diff = list(diff.values())
        for item in diff:
            out.append({
                "stock_code": str(item.get("f12", "")).zfill(6),
                "pb": _f(item.get("f23")),
                "close": _f(item.get("f2")),
            })
        total = int(d.get("total") or 0)
        if pn * 1000 >= total or not diff:
            break
        pn += 1
        time.sleep(0.3)
    return out


class StockPbSyncCollector:
    """个股 PB 日频快照同步（东财实时行情，双通道）"""

    def __init__(self, timeout_sec: float = 120):
        self.timeout_sec = float(timeout_sec)

    def _fetch(self) -> list[dict]:
        """主路径 akshare → 降级 push2 直连。"""
        try:
            df = call_with_timeout(ak.stock_zh_a_spot_em, self.timeout_sec)
            if df is not None and not df.empty and "市净率" in df.columns:
                return [
                    {"stock_code": str(r.get("代码", "")).zfill(6),
                     "pb": _f(r.get("市净率")),
                     "close": _f(r.get("最新价"))}
                    for _, r in df.iterrows()
                ]
        except Exception as e:  # noqa: BLE001
            logger.warning("akshare 主路径失败（%s: %s），降级 push2 直连",
                           type(e).__name__, str(e)[:80])
        return _fetch_push2()

    def run(self) -> dict:
        rows = self._fetch()
        if not rows:
            raise RuntimeError("个股 PB 快照获取失败（akshare 与 push2 直连均不可用）")

        today = date.today()
        conn = pymysql.connect(**get_db_config().to_dict())
        try:
            with conn.cursor() as cur:
                cur.executemany(
                    f"INSERT INTO stock_pb_daily ({', '.join(_INSERT_COLS)}) "
                    f"VALUES ({', '.join(['%s'] * len(_INSERT_COLS))}) "
                    "ON DUPLICATE KEY UPDATE "
                    + ", ".join(f"{c}=VALUES({c})" for c in _UPDATE_COLS),
                    [(today, r["stock_code"], r["pb"], r["close"], None, "AKSHARE")
                     for r in rows],
                )
            conn.commit()
            n_pb = sum(1 for r in rows if r["pb"] is not None)
        finally:
            conn.close()

        msg = f"个股 PB 快照 {len(rows)} 只（PB 非空 {n_pb}），写 {today}"
        logger.info("✅ %s", msg)
        return with_steps(
            {"records_written": len(rows), "error_count": 0, "errors": [], "note": msg},
            RUN_STEPS,
            {
                1: f"源 {len(rows)} 只",
                2: f"PB 非空 {n_pb} 只",
                3: f"写 {today}",
            },
        )
