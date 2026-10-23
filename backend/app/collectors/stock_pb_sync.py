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
    {"no": 1, "name": "拉全市场个股 PB 快照", "params": "stock_zh_a_spot_em()（东财 ~5500 只，含市净率 f23）；三通道降级（akshare→push2 直连→腾讯行情 qt.gtimg.cn）"},
    {"no": 2, "name": "清洗", "params": "市净率/最新价转 float；NaN/-- → NULL"},
    {"no": 3, "name": "upsert 当日行", "params": "PRIMARY KEY(trade_date, stock_code) + ON DUPLICATE KEY UPDATE"},
]

_INSERT_COLS = ["trade_date", "stock_code", "pb", "close", "update_time", "data_source"]
_UPDATE_COLS = ["pb", "close", "update_time"]

# push2 直连降级：字段映射（f12=代码 f14=名称 f2=最新价 f23=市净率）
_PUSH2_URL = "https://82.push2.eastmoney.com/api/qt/clist/get"
_PUSH2_UT = "bd1d9ddb04089700cf9c27f6f7426281"
_PUSH2_FS = "m:0 t:6,m:0 t:80,m:1 t:2,m:1 t:23,m:0 t:81 s:2048"

# 腾讯行情兜底路径（qt.gtimg.cn）：2026-10-23 实测东财 push2 对本机 IP 触发 WAF 封禁
# （TCP 可建连、GET / 返回 404，但 /api/qt/* 精确 RST，且封禁随重试扩大），
# 腾讯行情不受影响，含 PB（f[46]）与收盘价（f[3]），作为第三通道。
_TENCENT_URL = "https://qt.gtimg.cn/q="
_TENCENT_BATCH = 60
# qt.gtimg.cn 返回以 ~ 分隔（0 基）：3=最新价 39=PE-TTM 44=流通市值(亿) 45=总市值(亿) 46=市净率
_TencentIdxClose, _TencentIdxPB = 3, 46


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


def _fetch_tencent() -> list[dict]:
    """腾讯行情兜底（第三通道）：按 stock_info 票池批量拉 PB/收盘。

    票池来自库内 stock_info（list_status 在市），交易所前缀 SH→sh / SZ→sz / BJ→bj。
    每批 _TENCENT_BATCH 只，0.2s 间隔，控制在对端友好量级（~6000 只 ≈ 100 请求）。
    """
    conn = pymysql.connect(**get_db_config().to_dict(),
                           cursorclass=pymysql.cursors.DictCursor)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT stock_code, exchange FROM stock_info "
                "WHERE list_status = '上市' OR list_status IS NULL"
            )
            pairs = cur.fetchall()
    finally:
        conn.close()

    codes = []
    for r in pairs:
        ex = (r["exchange"] or "").upper()
        pre = {"SH": "sh", "SZ": "sz", "BJ": "bj"}.get(ex)
        if pre:
            codes.append(pre + str(r["stock_code"]).zfill(6))

    out: list[dict] = []
    sess = requests.Session()
    sess.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    for i in range(0, len(codes), _TENCENT_BATCH):
        batch = codes[i:i + _TENCENT_BATCH]
        try:
            resp = sess.get(_TENCENT_URL + ",".join(batch), timeout=10)
            resp.encoding = "gbk"
        except Exception as e:  # noqa: BLE001
            logger.warning("腾讯批次 %d 失败（%s），跳过 %d 只", i // _TENCENT_BATCH + 1,
                           type(e).__name__, len(batch))
            continue
        for line in resp.text.splitlines():
            if "~" not in line:
                continue  # 无效代码返回 v_pv_none="1"，跳过
            f = line.split("~")
            if len(f) <= max(_TencentIdxClose, _TencentIdxPB):
                continue
            code = f[2]
            out.append({
                "stock_code": str(code).zfill(6),
                "pb": _f(f[_TencentIdxPB]),
                "close": _f(f[_TencentIdxClose]),
            })
        time.sleep(0.2)
    return out


class StockPbSyncCollector:
    """个股 PB 日频快照同步（东财实时行情，双通道）"""

    def __init__(self, timeout_sec: float = 120):
        self.timeout_sec = float(timeout_sec)

    def _fetch(self) -> tuple[list[dict], str]:
        """主路径 akshare → 降级 push2 直连 → 兜底腾讯行情。返回 (rows, data_source)。"""
        try:
            df = call_with_timeout(ak.stock_zh_a_spot_em, self.timeout_sec)
            if df is not None and not df.empty and "市净率" in df.columns:
                return [
                    {"stock_code": str(r.get("代码", "")).zfill(6),
                     "pb": _f(r.get("市净率")),
                     "close": _f(r.get("最新价"))}
                    for _, r in df.iterrows()
                ], "AKSHARE"
        except Exception as e:  # noqa: BLE001
            logger.warning("akshare 主路径失败（%s: %s），降级 push2 直连",
                           type(e).__name__, str(e)[:80])
        try:
            rows = _fetch_push2()
            if rows:
                return rows, "PUSH2_DIRECT"
        except Exception as e:  # noqa: BLE001
            logger.warning("push2 直连失败（%s: %s），兜底腾讯行情",
                           type(e).__name__, str(e)[:80])
        return _fetch_tencent(), "TENCENT"

    def run(self) -> dict:
        rows, data_source = self._fetch()
        if not rows:
            raise RuntimeError("个股 PB 快照获取失败（akshare / push2 直连 / 腾讯行情均不可用）")

        today = date.today()
        conn = pymysql.connect(**get_db_config().to_dict())
        try:
            with conn.cursor() as cur:
                cur.executemany(
                    f"INSERT INTO stock_pb_daily ({', '.join(_INSERT_COLS)}) "
                    f"VALUES ({', '.join(['%s'] * len(_INSERT_COLS))}) "
                    "ON DUPLICATE KEY UPDATE "
                    + ", ".join(f"{c}=VALUES({c})" for c in _UPDATE_COLS),
                    [(today, r["stock_code"], r["pb"], r["close"], None, data_source)
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
