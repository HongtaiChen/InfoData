#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
投资环境模块 · 国内货币「价格指标」—— DR001/DR007 质押式回购利率采集器（dr_repo_sync）

补「国内货币」章节价格指标里最关键、也最缺的两项：DR001 / DR007（存款类金融机构
质押式回购利率，即央行 2025 年起替代 DR 序列作为核心观测的短端资金「温度计」）。

数据源与口径：
  · `ak.repo_rate_hist(start_date, end_date)` → 中国货币网质押式回购行情。
    返回列含 FDR001 / FDR007（存款类机构定盘口径，即市场通常所称 DR001/DR007）
    与 FR001 / FR007（全市场回购定盘口径）。
  · 本表只取 **FDR001 / FDR007**（存款类机构 DR 口径），另保留 FR007 作对照。
    列语义 = 该交易日「加权平均利率」的官方定盘值。

⚠️ 一个源侧硬限制（必须分段拉取）：
   `repo_rate_hist` 单次请求最长约 300 天（约 10 个月），超过约 1 年会返回空
   `frValueMap` 报 KeyError。故采集器按 **300 天一段**从 2025-01-01 分段回溯拉取，
   合并后全量 upsert（表量级约几百行，重跑无成本）。

幂等：主键 trade_date + 全量 upsert；同一日期多次拉取 COALESCE 覆盖。
"""
import logging
from datetime import date, datetime, timedelta

import pymysql
import akshare as ak

from ..db import get_db_config
from ._common import with_steps, call_with_timeout

logger = logging.getLogger(__name__)

RUN_STEPS = [
    {"no": 1, "name": "分段拉取 FDR001/FDR007", "params": "repo_rate_hist，300 天/段，自 2025-01-01 回溯"},
    {"no": 2, "name": "合并去重", "params": "日期并集，同日期 COALESCE 覆盖"},
    {"no": 3, "name": "全量 Upsert", "params": "PRIMARY KEY(trade_date)，表量级几百行"},
]

_TGT = ["trade_date", "dr001", "dr007", "fr007"]
_START = date(2025, 1, 1)
_SEG_DAYS = 300


def _d(v) -> date | None:
    if v is None:
        return None
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


def _f(v):
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else round(f, 4)


class DrRepoSyncCollector:
    """DR001/DR007 存款类机构质押式回购利率日频同步"""

    def __init__(self, timeout_sec: float = 60):
        self.timeout_sec = float(timeout_sec)

    def _pull_segments(self, errors: list[str]) -> dict[date, tuple]:
        """分段拉取 FDR001/FDR007/FR007，返回 {date: (dr001, dr007, fr007)}"""
        merged: dict[date, tuple] = {}
        end = date.today()
        seg_start = _START
        while seg_start <= end:
            seg_end = min(seg_start + timedelta(days=_SEG_DAYS), end)
            try:
                df = call_with_timeout(
                    ak.repo_rate_hist, self.timeout_sec,
                    start_date=seg_start.strftime("%Y%m%d"),
                    end_date=seg_end.strftime("%Y%m%d"))
            except Exception as e:  # noqa: BLE001
                errors.append(f"段 {seg_start}~{seg_end}: {type(e).__name__} {str(e)[:60]}")
                seg_start = seg_end + timedelta(days=1)
                continue
            if df is not None and not df.empty:
                for _, r in df.iterrows():
                    d = _d(r.get("date"))
                    if d is None:
                        continue
                    dr001 = _f(r.get("FDR001"))
                    dr007 = _f(r.get("FDR007"))
                    fr007 = _f(r.get("FR007"))
                    # 同日期覆盖（后拉段覆盖前段，取最新）
                    merged[d] = (dr001, dr007, fr007)
            seg_start = seg_end + timedelta(days=1)
        return merged

    def run(self) -> dict:
        errors: list[str] = []
        merged = self._pull_segments(errors)

        if not merged:
            raise RuntimeError("DR 回购利率各段均未取到数据：" + "; ".join(errors[:4]))

        rows = [(d, v[0], v[1], v[2]) for d, v in sorted(merged.items())
                if v[0] is not None or v[1] is not None]

        if not rows:
            raise RuntimeError("DR 回购利率组装后无有效行")

        conn = pymysql.connect(**get_db_config().to_dict(),
                               cursorclass=pymysql.cursors.DictCursor)
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS n FROM dr_repo_daily")
                before = cur.fetchone()["n"]
                sql = (
                    f"INSERT INTO dr_repo_daily ({', '.join(_TGT)}, update_time, data_source) "
                    f"VALUES ({', '.join(['%s'] * len(_TGT))}, NOW(), 'CHINAMONEY') "
                    "ON DUPLICATE KEY UPDATE "
                    + ", ".join(f"{c}=COALESCE(VALUES({c}), {c})" for c in _TGT if c != "trade_date")
                    + ", update_time=NOW()"
                )
                cur.executemany(sql, rows)
            conn.commit()
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS n, MAX(trade_date) AS d, "
                            "COUNT(dr001) AS n1, COUNT(dr007) AS n7 FROM dr_repo_daily")
                r = cur.fetchone()
            new_rows = max(0, r["n"] - before)
        finally:
            conn.close()

        msg = (f"DR 回购 upsert {len(rows)} 天（新增 {new_rows}）｜"
               f"DR001 覆盖 {r['n1']} 天 · DR007 覆盖 {r['n7']} 天 · 最新 {r['d']}")
        logger.info("✅ %s", msg)
        return with_steps(
            {"records_written": new_rows if new_rows else len(rows),
             "error_count": len(errors), "errors": errors[:20], "note": msg},
            RUN_STEPS,
            {
                1: f"合并 {len(merged)} 天（自 {_START} 起分段回溯）",
                2: f"有效行 {len(rows)} 天",
                3: f"upsert {len(rows)} 行（表内 {r['n']} 行，最新 {r['d']}）",
            },
        )
