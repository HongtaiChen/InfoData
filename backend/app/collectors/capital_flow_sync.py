#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
InvestBuddy 个股资金流向采集器（stock_capital_flow，东方财富源经 akshare，恢复采集）

背景（2026-09-13）：该表为历史导入遗留，代码层无任何采集器，水位停于 2025-09-19
（本地 74.8 万行 / 5,548 只）。本次按调研报告第 4 批（P3）实现。

- 源：ak.stock_individual_fund_flow(stock, market) —— 东财单只全历史资金流向
- 写表：stock_capital_flow
- 策略：**按「数据滞后 ∪ 久未刷新」逐股滚动补**
  floor = 本地全局 MAX(trade_date)；入选 = MAX(trade_date) < floor 或 MAX(update_time) 超过 refresh_days
  → 数据滞后判增量、刷新时限兜全量，两条规则取并集，任一条触发都会自愈
- 幂等：DB 侧 UNIQUE(stock_code, trade_date) + ON DUPLICATE KEY UPDATE
- 容错：单只失败记 error 不中断；每 200 只分批提交 → 断点续传；
        若本轮「扫描 ≥ 20 只且 100% 失败」→ 抛错让任务记 failed（防止源整体不可达被当成成功）

⚠️ 字段口径（依据本地既有数据反解，**尚未在真实环境与源逐字段比对**）：
  本地存在恒等式 `main_net_inflow = max_net_inflow + lg_net_inflow`，
  实测 748,019 行中成立 90~93.8%（分 data_source：ADATA 93.8% / AUDATA 90.0%），
  未成立的部分疑为原导入器的计算口径差异。据此对应关系：
    主力净流入-净额 → main_net_inflow（主力）
    超大单净流入-净额 → max_net_inflow（超大单）
    大单净流入-净额 → lg_net_inflow（大单，与超大单合计即主力，互相印证）
    中单净流入-净额 → mid_net_inflow（中单）
    小单净流入-净额 → sm_net_inflow（小单）

  ⚠️ **本任务在开发沙箱内无法验收**：东财行情域名 push2his.eastmoney.com 的出口被风控
  （裸 TCP 可连、TLS 成功，但服务端主动断开；实测 ConnectionError:
  RemoteDisconnected('Remote end closed connection without response')）。
  而生产环境其他东财接口（datacenter-web）正常，故按生产直连实现，
  **但任务默认 enabled=0，需在部署环境首跑并人工核对字段后再启用**。
"""
import logging
import time
from datetime import datetime, date

import pymysql

import akshare as ak

from ..db import get_db_config
from ._common import with_steps, call_with_timeout, load_refresh_targets

logger = logging.getLogger(__name__)

RUN_STEPS = [
    {"no": 1, "name": "构建待刷新名单", "params": "候选 = 本地已有股票 ∪ stock_info 在册 A 股；入选 = MAX(trade_date)<全局MAX 或 update_time 超过 refresh_days；最久未刷新排最前"},
    {"no": 2, "name": "逐股拉资金流向", "params": "ak.stock_individual_fund_flow(stock, market) 单只全历史；market 由代码前缀判定(sh/sz/bj)；限流 0.12s；单只 30s 超时"},
    {"no": 3, "name": "字段映射", "params": "日期→trade_date；主力/超大单/大单/中单/小单净额 → main/max/lg/mid/sm_net_inflow"},
    {"no": 4, "name": "批量 upsert", "params": "ON DUPLICATE KEY UPDATE；每 200 只提交，具备断点续传；100% 失败则抛错记 failed"},
]

INSERT_COLS = ["stock_code", "short_name", "trade_date", "main_net_inflow", "max_net_inflow",
               "lg_net_inflow", "mid_net_inflow", "sm_net_inflow", "update_time", "data_source"]
UPDATE_COLS = ["short_name", "main_net_inflow", "max_net_inflow", "lg_net_inflow",
               "mid_net_inflow", "sm_net_inflow", "update_time"]

# 源列 → 目标列
_COL_MAP = {
    "主力净流入-净额": "main_net_inflow",
    "超大单净流入-净额": "max_net_inflow",
    "大单净流入-净额": "lg_net_inflow",
    "中单净流入-净额": "mid_net_inflow",
    "小单净流入-净额": "sm_net_inflow",
}


def market_of(code: str) -> str | None:
    """A 股代码 → 东财 market 参数（sh / sz / bj）"""
    c = str(code)
    if c[:2] in ("60", "68", "90", "11", "13"):
        return "sh"
    if c[:2] in ("00", "30", "20", "12"):
        return "sz"
    if c[:2] in ("43", "82", "83", "87", "88", "89", "92"):
        return "bj"
    if c[0] in ("6", "9"):
        return "sh"
    if c[0] in ("0", "2", "3"):
        return "sz"
    return None


def _to_date(v) -> date | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        import pandas as pd
        if pd.isna(v):
            return None
        if isinstance(v, pd.Timestamp):
            return v.date()
    except (TypeError, ValueError):
        pass
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


def _to_num(v):
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, str):
        v = v.replace(",", "").replace("%", "").strip()
        if v in ("", "--", "-"):
            return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f


class CapitalFlowSyncCollector:
    """个股资金流向逐股滚动同步（东财 stock_individual_fund_flow）"""

    TABLE = "stock_capital_flow"

    def __init__(self, max_stocks: int = 400, refresh_days: int = 7,
                 sleep_sec: float = 0.12, timeout_sec: float = 30,
                 full_sweep: bool = False):
        self.max_stocks = int(max_stocks or 0)
        self.refresh_days = int(refresh_days or 0)
        self.sleep_sec = float(sleep_sec)
        self.timeout_sec = float(timeout_sec)
        self.full_sweep = bool(full_sweep)

    # ---------- 数据源 ----------

    def _fetch(self, code: str):
        mk = market_of(code)
        if mk is None:
            raise RuntimeError(f"无法判定市场：{code}")
        return call_with_timeout(ak.stock_individual_fund_flow, self.timeout_sec,
                                 stock=code, market=mk)

    # ---------- 主流程 ----------

    def run(self) -> dict:
        conn = pymysql.connect(**get_db_config().to_dict())
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT MAX(trade_date) FROM %s" % self.TABLE)
                row = cur.fetchone()
            local_max = row[0] if row and row[0] else None
            targets, total = load_refresh_targets(
                conn, self.TABLE, data_col="trade_date", floor_date=local_max,
                refresh_days=0 if self.full_sweep else self.refresh_days,
                name_col="short_name",
                max_stocks=0 if self.full_sweep else self.max_stocks,
                skip_delisted=True,
            )
        finally:
            conn.close()

        if not targets:
            msg = f"资金流向无需刷新（候选 {total} 只，均已到最新交易日 {local_max}）"
            logger.info("✅ %s", msg)
            return with_steps(
                {"records_written": 0, "error_count": 0, "errors": [], "note": msg},
                RUN_STEPS, {1: f"候选 {total} 只 · 待刷新 0 只（全局最新 {local_max}）"},
            )
        logger.info("资金流向同步：候选 %s 只，本轮待刷新 %s 只（全局最新 %s）",
                    total, len(targets), local_max)

        conn = pymysql.connect(**get_db_config().to_dict())
        written, scanned, errors, empty = 0, 0, [], 0
        placeholder = ", ".join(["%s"] * len(INSERT_COLS))
        upsert_sql = (f"INSERT INTO {self.TABLE} ({', '.join(INSERT_COLS)}) VALUES ({placeholder}) "
                      f"ON DUPLICATE KEY UPDATE "
                      + ", ".join("`%s`=VALUES(`%s`)" % (c, c) for c in UPDATE_COLS))
        now = datetime.now()
        try:
            with conn.cursor() as cur:
                for code, name in targets:
                    scanned += 1
                    try:
                        df = self._fetch(code)
                    except Exception as e:  # noqa: BLE001 - 单只失败不中断
                        errors.append(f"{code}: {type(e).__name__} {str(e)[:70]}")
                        continue
                    if df is None or df.empty or "日期" not in df.columns:
                        empty += 1
                        continue
                    rows = []
                    for _, r in df.iterrows():
                        td = _to_date(r.get("日期"))
                        if td is None:
                            continue
                        vals = [_to_num(r.get(src)) for src in _COL_MAP]
                        if all(v is None for v in vals):
                            continue
                        rows.append((code, name or code, td, vals[0], vals[1], vals[2],
                                     vals[3], vals[4], now, "AKSHARE"))
                    if rows:
                        cur.executemany(upsert_sql, rows)
                        written += len(rows)
                    else:
                        empty += 1
                    if scanned % 200 == 0:
                        conn.commit()
                        logger.info("  已扫描 %s 只，upsert %s 条…", scanned, written)
                    if self.sleep_sec > 0:
                        time.sleep(self.sleep_sec)
                conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

        # 源整体不可达保护：不允许「全失败」被静默当成成功
        if scanned >= 20 and len(errors) >= scanned:
            raise RuntimeError(
                f"全部 {scanned} 只均失败（疑似东财源不可达）：{errors[0] if errors else ''}")

        msg = (f"资金流向：扫描 {scanned} 只，upsert {written} 条，失败 {len(errors)}，"
               f"空数据 {empty} 只")
        logger.info("✅ %s", msg)
        return with_steps(
            {"records_written": written, "error_count": len(errors), "errors": errors[:50], "note": msg},
            RUN_STEPS,
            {
                1: f"候选 {total} 只 · 本轮待刷新 {len(targets)} 只（全局最新 {local_max}）",
                2: f"请求 {scanned} 只 · 失败 {len(errors)} · 空数据 {empty}",
                3: "5 个净额字段映射完成",
                4: f"upsert {written} 条",
            },
        )
