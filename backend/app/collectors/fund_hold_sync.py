#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""stock_hold_by_fund 采集器 —— 基金持仓（按报告期）

为什么需要这个采集器
====================
`stock_hold_by_fund` 有 **113,147 行 / 38 个报告期（2016-03-31 ~ 2025-06-30）**，
但 `table_meta.writers=[]` —— 没有写入方，停更后无法补齐，空库也无法重建。

口径（2026-10-17 实测对齐）
==========================
数据源：`ak.stock_report_fund_hold(symbol='基金持仓', date='YYYYMMDD')`
（东方财富「主力持仓-基金持仓」，一次返回**全市场**该报告期数据）

| 列 | 来源 | 实测依据 |
|---|---|---|
| stock_code | 股票代码（zfill 6） | — |
| short_name | 股票简称 | — |
| report_date | 请求的 date | 季度末 03-31/06-30/09-30/12-31 |
| hldfund_cnt | 持有基金家数 | — |
| total_amount | 持股总数（股） | — |
| total_asset | **持股市值 ÷ 10000** | ⚠️ 本表单位是**万元**，接口给**元**。实测 300750 于 2025-06-30 接口 1.497e11 元 ÷ 10000 ≈ 本表 1.4e7 万元 |
| data_source | 固定 'AKSHARE' | ⚠️ 历史值是 **'AKSAHRE'（拼写错误）**，见下方说明 |

⚠️ 为什么默认 `INSERT IGNORE` 而不是 UPSERT
============================================
基金季报**会被持续补充和修订**：同一个 2025-06-30，本地存的是披露早期快照
（2,943 行 / 300750 基金家数 1,752），而接口现在给的是完整版
（5,216 行 / 2,598 家）—— **行数和数值都变了**。

若用 UPSERT，一次「重建历史」就会把当年存下的快照静默改写成今天的修订值，
历史结论无法复原。故默认只补空缺、不覆盖已有行；确需刷新时显式传 `overwrite=True`。

⚠️ data_source 拼写
====================
历史 113,147 行全部是 `'AKSAHRE'`（AKSHARE 的错拼）。本采集器写**正确拼写**
`'AKSHARE'`，不擅自 UPDATE 历史行（保留痕迹）。代价是按 data_source 分组会出现两组，
若日后要统一，需单独做一次带备份的订正。

可重建范围
==========
接口实测可回溯到 **2012-03-31**（1,540 行），比本地最早的 2016-03-31 更早
⇒ 重建不仅可复原，还能**向前补齐 2012~2015 共 16 个报告期**。
"""
import logging
import os
import socket
import time
from datetime import date, datetime

import pymysql

from ..db import get_db_config
from ._common import with_steps

os.environ["NO_PROXY"] = "*"
os.environ["no_proxy"] = "*"
socket.setdefaulttimeout(30)

logger = logging.getLogger(__name__)

# 本地历史最早报告期；接口可回溯更早，故重建时可用 2012-03-31
DEFAULT_START_PERIOD = "20120331"
FETCH_TIMEOUT = 60
RETRY_TIMES = 2
# 季度结束后多久才采（天）：等季报披露完整，避免存下半量快照后不再修正
DISCLOSURE_BUFFER_DAYS = 45

INSERT_COLS = [
    "stock_code", "short_name", "report_date", "hldfund_cnt",
    "total_amount", "total_asset", "update_time", "data_source",
]

_INSERT_IGNORE_SQL = f"""
    INSERT IGNORE INTO stock_hold_by_fund
        ({", ".join(INSERT_COLS)})
    VALUES ({", ".join(["%s"] * len(INSERT_COLS))})
"""

# overwrite=True 时才用的覆盖版（会把历史快照改写成当前修订值，慎用）
_INSERT_UPSERT_SQL = f"""
    INSERT INTO stock_hold_by_fund
        ({", ".join(INSERT_COLS)})
    VALUES ({", ".join(["%s"] * len(INSERT_COLS))})
    ON DUPLICATE KEY UPDATE
        short_name = VALUES(short_name), hldfund_cnt = VALUES(hldfund_cnt),
        total_amount = VALUES(total_amount), total_asset = VALUES(total_asset),
        data_source = VALUES(data_source), update_time = VALUES(update_time)
"""

RUN_STEPS = [
    {"no": 1, "name": "生成报告期", "params": f"季度末序列，默认 {DEFAULT_START_PERIOD} → 最近已结束季度"},
    {"no": 2, "name": "逐期拉全市场", "params": f"stock_report_fund_hold(symbol='基金持仓')，单期 {FETCH_TIMEOUT}s 超时 + {RETRY_TIMES} 次退避"},
    {"no": 3, "name": "口径转换", "params": "持股市值 ÷10000 转万元；代码补前导 0；data_source 写 'AKSHARE'"},
    {"no": 4, "name": "写库", "params": "默认 INSERT IGNORE（保护历史快照）；overwrite=True 时改 UPSERT"},
]

# 接口里的中文列名
COL_CODE = "股票代码"
COL_NAME = "股票简称"
COL_CNT = "持有基金家数"
COL_AMT = "持股总数"
COL_ASSET = "持股市值"


def quarter_ends(start: str, end: str | None = None) -> list[str]:
    """生成 [start, end] 之间的季度末（YYYYMMDD），如 20120331/20120630/..."""
    d0 = datetime.strptime(start, "%Y%m%d").date()
    d1 = datetime.strptime(end, "%Y%m%d").date() if end else date.today()

    out: list[str] = []
    year, q = d0.year, (d0.month - 1) // 3 + 1
    while True:
        m = q * 3
        day = 31 if m in (3, 12) else 30
        cand = date(year, m, day)
        if cand > d1:
            break
        if cand >= d0:
            out.append(cand.strftime("%Y%m%d"))
        q += 1
        if q > 4:
            q, year = 1, year + 1
    return out


class FundHoldCollector:
    """基金持仓采集器（按报告期，全市场一次拉齐）"""

    def __init__(self, periods: list[str] | None = None,
                 start_period: str | None = None, end_period: str | None = None,
                 overwrite: bool = False):
        self.periods = periods or None
        self.start_period = start_period or DEFAULT_START_PERIOD
        self.end_period = end_period
        self.overwrite = bool(overwrite)
        self.db = get_db_config().to_dict()
        self._written = 0
        self._errors: list[str] = []
        self._empty: list[str] = []

    def _fetch(self, period: str):
        """单期拉取（带硬超时与退避重试）"""
        from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
        import akshare as ak

        def _call():
            return ak.stock_report_fund_hold(symbol="基金持仓", date=period)

        last = None
        for i in range(RETRY_TIMES + 1):
            ex = ThreadPoolExecutor(max_workers=1)
            try:
                fut = ex.submit(_call)
                return fut.result(timeout=FETCH_TIMEOUT)
            except FutureTimeout:
                last = RuntimeError(f"{period} 拉取超时(>{FETCH_TIMEOUT}s)")
            except Exception as e:  # noqa: BLE001
                last = e
            finally:
                ex.shutdown(wait=False)
            if i < RETRY_TIMES:
                time.sleep(2 * (i + 1))
        raise last  # type: ignore[misc]

    def _to_rows(self, period: str, df) -> list[tuple]:
        import pandas as pd

        if df is None or len(df) == 0:
            return []
        need = [COL_CODE, COL_NAME, COL_CNT, COL_AMT, COL_ASSET]
        missing = [c for c in need if c not in df.columns]
        if missing:
            raise ValueError(f"接口列变化，缺 {missing}；实际列 {list(df.columns)}")

        rd = datetime.strptime(period, "%Y%m%d").date()
        now = datetime.now()
        rows = []
        for _, r in df.iterrows():
            code = str(r[COL_CODE]).strip().zfill(6)
            if not code.isdigit() or len(code) != 6:
                continue

            def _num(v, nd=None):
                if v is None or (isinstance(v, float) and pd.isna(v)):
                    return None
                try:
                    f = float(v)
                except (TypeError, ValueError):
                    return None
                return int(f) if nd is None else round(f, nd)

            cnt = _num(r[COL_CNT])
            amt = _num(r[COL_AMT])
            asset = _num(r[COL_ASSET], 2)
            # 接口给「元」，本表存「万元」
            asset = None if asset is None else round(asset / 10000, 2)

            rows.append((
                code, str(r[COL_NAME]).strip() if r[COL_NAME] is not None else None,
                rd, cnt, amt, asset, now, "AKSHARE",
            ))
        return rows

    def run(self) -> dict:
        t0 = time.time()
        periods = self.periods or quarter_ends(self.start_period, self.end_period)

        # ⚠️ 排除「刚结束、披露未完成」的季度：季报在季度结束后约 15 个工作日内陆续披露，
        # 此时拉取只能拿到半量快照。而默认又是 INSERT IGNORE（保护历史），一旦写入
        # 半量数据就再也不会被修正 —— 这正是历史表 2025-06-30 只有 2,943 行
        # （接口现在 5,216 行）的成因。故留 45 天缓冲，过了才采。
        today = date.today()
        periods = [
            p for p in periods
            if (today - datetime.strptime(p, "%Y%m%d").date()).days >= DISCLOSURE_BUFFER_DAYS
        ]
        logger.info(f"报告期 {len(periods)} 个：{periods[:2]} … {periods[-2:] if len(periods) > 1 else ''}")

        sql = _INSERT_UPSERT_SQL if self.overwrite else _INSERT_IGNORE_SQL
        conn = self._connect()
        try:
            for i, period in enumerate(periods, 1):
                try:
                    df = self._fetch(period)
                except Exception as e:  # noqa: BLE001
                    msg = f"{period}: {type(e).__name__}: {str(e)[:70]}"
                    self._errors.append(msg)
                    logger.warning(f"✗ {msg}")
                    continue

                rows = self._to_rows(period, df)
                if not rows:
                    self._empty.append(period)
                    logger.info(f"  {period}: 接口无数据，跳过")
                    continue

                with conn.cursor() as cur:
                    cur.executemany(sql, rows)
                conn.commit()  # ⚠️ 必须显式 commit（pymysql autocommit 不可信）
                self._written += len(rows)
                logger.info(f"  [{i}/{len(periods)}] {period}: 接口 {len(df)} 行 → 写入尝试 {len(rows)} 行")
        finally:
            conn.close()

        elapsed = round(time.time() - t0, 1)
        result = {
            "records_written": self._written,
            "error_count": len(self._errors),
            "errors": self._errors[:10],
            "periods": len(periods),
            "empty_periods": self._empty,
            "overwrite": self.overwrite,
            "elapsed_sec": elapsed,
        }
        logger.info(f"完成：{len(periods)} 期，尝试写入 {self._written:,} 行，"
                    f"失败 {len(self._errors)} 期，耗时 {elapsed}s")
        return with_steps(result, RUN_STEPS, {
            1: f"{len(periods)} 个报告期",
            2: f"失败 {len(self._errors)} 期",
            3: "市值 ÷10000 → 万元",
            4: f"写入 {self._written:,} 行（{'UPSERT' if self.overwrite else 'INSERT IGNORE'}）",
        })

    def _connect(self):
        return pymysql.connect(**self.db)
