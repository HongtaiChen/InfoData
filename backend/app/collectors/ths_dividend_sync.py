#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
InvestBuddy 分红送配采集器（ths_stock_dividend，同花顺源，恢复采集）

背景（2026-09-13）：该表为历史导入遗留，代码层无任何采集器，水位停于 2025-08-21。
它是 `app/analysis/dividend.py`（分红率分析）的唯一数据源，前端 AnalysisView
「股息率排行」因此长期展示一年前的数据。本次按调研结论恢复采集。

- 源：ak.stock_fhps_detail_ths(symbol) —— 同花顺分红送配明细，单只返回全部历史期
- 写表：ths_stock_dividend（11 个业务列与源列一一对应，调研阶段已逐字段实证一致）
- 策略：**逐股增量补齐** —— 先读本地 (stock_code, report_period) 存量集合，
  仅插入源中存在而本地缺失的报告期；已存在的报告期不重写（避免误改历史）
- 幂等：重复执行安全（缺失判定基于本地集合，非 INSERT IGNORE —— 该表无唯一索引）
- 容错：单只失败记 error 不中断，下轮自动重试；每 200 只分批提交
- 限流：默认 0.12s 间隔（同花顺反爬），如需提速可调 sleep_sec

⚠️ 已知遗留（不在本采集器职责内）：
  1. 该表存在大量历史重复行（138,826 行中约 9.5 万为重复），粒度待业务确认后
     另做去重专项 —— 本采集器只做「缺什么补什么」，不删不改既有行
  2. 方案进度演进（董事会预案 → 实施方案）不会回写既有报告期：
     若首次采集到的是预案态，后续转实施态不会更新。如需可开启 recent_update
"""
import logging
import time
from datetime import datetime, date

import pandas as pd
import pymysql

import akshare as ak

from ..db import get_db_config
from ._common import with_steps, call_with_timeout

logger = logging.getLogger(__name__)

# 运行步骤链模板（供前端「数据流·整链拓扑」展示运行逻辑）
RUN_STEPS = [
    {"no": 1, "name": "扫描候选股票", "params": "stock_info 在册 A 股（按代码排序），max_stocks 限单轮上限"},
    {"no": 2, "name": "读本地存量键", "params": "ths_stock_dividend 全部 (stock_code, report_period) 组合 → 内存集合"},
    {"no": 3, "name": "逐股拉同花顺分红", "params": "ak.stock_fhps_detail_ths(symbol) 单只返回全历史；限流 sleep 0.12s；单只 30s 超时；失败记 error 不中断"},
    {"no": 4, "name": "差量插入", "params": "仅插入源中有、本地缺的 report_period；每 200 只分批提交"},
]

# 源列 -> 目标列（11/11 一一对应，2026-09-13 调研阶段逐字段实证）
_COL_MAP = {
    "报告期": "report_period",
    "董事会日期": "board_date",
    "股东大会预案公告日期": "shareholders_meeting_date",
    "实施公告日": "implementation_date",
    "分红方案说明": "dividend_plan_desc",
    "A股股权登记日": "ashare_record_date",
    "A股除权除息日": "ashare_ex_date",
    "分红总额": "dividend_amount_total",
    "方案进度": "plan_progress",
    "股利支付率": "dividend_payout_ratio",
    "税前分红率": "pre_tax_dividend_ratio",
}
_DATE_COLS = {"board_date", "shareholders_meeting_date", "implementation_date",
              "ashare_record_date", "ashare_ex_date"}
# 源中表示「无值」的占位符（实测：'--' 出现在不分配/未实施的行）
_NULL_TOKENS = {"--", "-", "", "nan", "None", "NaT"}

_INSERT_COLS = ["stock_code", "short_name", "report_period", "board_date",
                "shareholders_meeting_date", "implementation_date", "dividend_plan_desc",
                "ashare_record_date", "ashare_ex_date", "dividend_amount_total",
                "plan_progress", "dividend_payout_ratio", "pre_tax_dividend_ratio",
                "update_time", "data_source"]


def _clean(v):
    """占位符/NaN/NaT -> None；日期对象归一为 date；其余字符串去空白

    注意顺序：pd.NaT 是 datetime 的子类，若放在 datetime 分支之后判断，
    会被 `isinstance(v, datetime)` 截胡并原样透传（.date() 仍返回 NaT），
    最终以 'NaT' 形式传入 date 列导致 1292 报错。故 NaN/NaT 必须最先拦截。
    """
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(v, pd.Timestamp):
        return v.date()
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()
    if s in _NULL_TOKENS:
        return None
    return s


def _to_date(v):
    """宽松转 date；失败返回 None"""
    c = _clean(v)
    if c is None:
        return None
    if isinstance(c, date):
        return c
    s = str(c)[:10]
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


class ThsDividendSyncCollector:
    """分红送配增量同步（同花顺 stock_fhps_detail_ths）"""

    def __init__(self, max_stocks: int = 0, sleep_sec: float = 0.12, retry: int = 1,
                 timeout_sec: float = 30):
        self.max_stocks = int(max_stocks or 0)
        self.sleep_sec = float(sleep_sec)
        self.retry = int(retry)
        self.timeout_sec = float(timeout_sec)

    # ---------- 候选与存量 ----------

    def _load_candidates(self, conn) -> list[tuple[str, str | None]]:
        """在册 A 股（沪深，排除 B 股/北交所 —— 同花顺分红接口覆盖范围）"""
        with conn.cursor() as cur:
            cur.execute(
                "SELECT stock_code, short_name FROM stock_info "
                "WHERE list_status = '上市' ORDER BY stock_code"
            )
            rows = cur.fetchall()
        out = []
        for code, name in rows:
            code = str(code)
            if code[:3] in ("200", "201", "900", "901"):   # B 股
                continue
            if code[:2] in ("43", "82", "83", "87", "88", "89", "92"):  # 北交所
                continue
            out.append((code, name))
        return out[: self.max_stocks] if self.max_stocks > 0 else out

    def _load_existing_keys(self, conn) -> set[tuple[str, str]]:
        """本地 (stock_code, report_period) 存量集合"""
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT stock_code, report_period FROM ths_stock_dividend")
            return {(str(c), str(p)) for c, p in cur.fetchall() if c is not None and p is not None}

    # ---------- 数据源 ----------

    def _fetch(self, code: str) -> pd.DataFrame | None:
        last_err = None
        for attempt in range(self.retry + 1):
            try:
                return call_with_timeout(ak.stock_fhps_detail_ths, self.timeout_sec, symbol=code)
            except Exception as e:  # noqa: BLE001 - 反爬/网络抖动/超时，重试一次
                last_err = e
                if attempt < self.retry:
                    time.sleep(0.8)
        raise RuntimeError(str(last_err)[:120])

    # ---------- 主流程 ----------

    def run(self) -> dict:
        # 注意：此处刻意使用默认 Cursor（返回 tuple）—— 下两个方法按位置解包，
        # 若改用 DictCursor 会把 dict 的键名解出来（2026-09-13 实测踩坑）
        conn = pymysql.connect(**get_db_config().to_dict())
        try:
            candidates = self._load_candidates(conn)
            existing = self._load_existing_keys(conn)
        finally:
            conn.close()

        if not candidates:
            return with_steps(
                {"records_written": 0, "error_count": 0, "errors": [], "note": "无候选股票（stock_info 在册为空）"},
                RUN_STEPS, {1: "候选 0 只，终止"},
            )
        logger.info("分红同步：候选 %s 只，本地存量键 %s 个", len(candidates), len(existing))

        conn = pymysql.connect(**get_db_config().to_dict())
        written, scanned, errors, partial_missing = 0, 0, [], []
        placeholder = ", ".join(["%s"] * len(_INSERT_COLS))
        insert_sql = f"INSERT INTO ths_stock_dividend ({', '.join(_INSERT_COLS)}) VALUES ({placeholder})"
        now = datetime.now()
        try:
            with conn.cursor() as cur:
                for code, name in candidates:
                    scanned += 1
                    try:
                        df = self._fetch(code)
                    except Exception as e:  # 单只失败不中断
                        errors.append(f"{code}: {str(e)[:70]}")
                        continue
                    if df is None or df.empty:
                        continue

                    # 源对「无分红/仅预案」的个股返回列结构可能不完整（实测 000002 缺「分红总额」），
                    # 故不做整列齐备校验：仅「报告期」为必需列，其余缺列按 NULL 写入
                    if "报告期" not in df.columns:
                        errors.append(f"{code}: 源无「报告期」列，实际列 {list(df.columns)[:6]}")
                        continue
                    partial = [c for c in _COL_MAP if c not in df.columns]
                    if partial:
                        partial_missing.append(f"{code}:{partial}")

                    rows = []
                    for _, r in df.iterrows():
                        period = _clean(r.get("报告期"))
                        if period is None:
                            continue
                        if (code, period) in existing:      # 已有该报告期 -> 跳过
                            continue
                        vals = []
                        for src_col, col in _COL_MAP.items():
                            v = _clean(r.get(src_col))      # 列不存在 -> None
                            vals.append(_to_date(v) if col in _DATE_COLS else v)
                        rows.append((code, name, period, vals[1], vals[2], vals[3], vals[4],
                                     vals[5], vals[6], vals[7], vals[8], vals[9], vals[10],
                                     now, "AKSHARE"))
                        existing.add((code, period))        # 本轮内去重

                    if rows:
                        cur.executemany(insert_sql, rows)
                        written += len(rows)

                    if scanned % 200 == 0:
                        conn.commit()
                        logger.info("  已扫描 %s 只，写入 %s 条…", scanned, written)
                    if self.sleep_sec > 0:
                        time.sleep(self.sleep_sec)
                conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

        msg = f"分红送配增量：扫描 {scanned} 只，写入 {written} 条（失败 {len(errors)}）"
        if partial_missing:
            msg += f"；{len(partial_missing)} 只源列不全已按 NULL 写入"
        logger.info("✅ %s", msg)
        return with_steps(
            {"records_written": written, "error_count": len(errors), "errors": errors[:50], "note": msg},
            RUN_STEPS,
            {
                1: f"候选 {len(candidates)} 只",
                2: f"本地存量键 {len(existing)} 个",
                3: f"请求 {scanned} 只 · 失败 {len(errors)} · 列不全 {len(partial_missing)}",
                4: f"差量插入 {written} 条",
            },
        )
