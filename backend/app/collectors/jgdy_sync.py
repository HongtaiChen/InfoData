#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
InvestBuddy 机构调研采集器（stock_jgdy_detail，东财源，恢复采集）

背景（2026-09-13）：该表为历史导入遗留，代码层无采集器，水位停于 2025-09-19。
（设计文档 §「机构调研热度」分析依赖本表，恢复后方可支撑该场景。）

- 源：ak.stock_jgdy_tj_em(date) —— 东财「机构调研统计」
- 写表：stock_jgdy_detail

⚠️ 接口语义（2026-09-13 实测确认，与直觉相反，务必留意）：
  参数 date **不是「接待日期」筛选，而是「公告日期起点」**——
  单次调用即返回「公告日期 >= date 的全部记录」（akshare 内部自动翻页）。
  实测：date='20260101' → 14,773 行，公告日期 2026-01-02 ~ 2026-09-12；
        date='20250910' → 20,847 行新增，公告日期 2025-09-16 ~ 2026-09-12。
  **因此无需逐日遍历**：一次调用即可补齐全部缺口（380 次 → 1 次）。

- 策略：起点 = 本地 MAX(announcement_date) - overlap_days（默认 30，容忍公告回填/补录），
  单次调用拉回增量 → 判重后插入
- 幂等：按 (stock_code, receptionist_date, received_method, received_institution_count)
  四元组判重（同股同日多次接待为合法业务场景，实测本地存在 12 组此类记录，故不能只按日期判重）
- 注：另一接口 stock_jgdy_detail_em 为逐股明细且分页量极大（数千页），不采用
"""
import logging
from datetime import datetime, date, timedelta

import pandas as pd
import pymysql

import akshare as ak

from ..db import get_db_config
from ._common import with_steps, call_with_timeout

logger = logging.getLogger(__name__)

RUN_STEPS = [
    {"no": 1, "name": "确定公告起点", "params": "本地 MAX(announcement_date) 回溯 overlap_days(30)，容忍公告回填/补录"},
    {"no": 2, "name": "单次拉取增量", "params": "ak.stock_jgdy_tj_em(date=起点) —— 返回公告日期 ≥ 起点的全部记录（akshare 内部自动翻页）；60s 超时兜底"},
    {"no": 3, "name": "四元组判重", "params": "(stock_code, receptionist_date, received_method, received_institution_count) 已存在则跳过"},
    {"no": 4, "name": "批量写入", "params": "executemany 一次提交（data_source=AKSHARE）"},
]

# 源列 -> 目标列（10/10 一一对应，2026-09-13 调研阶段实证）
_COL_MAP = {
    "代码": "stock_code",
    "名称": "stock_name",
    "最新价": "new",
    "涨跌幅": "change_pct",
    "接待机构数量": "received_institution_count",
    "接待方式": "received_method",
    "接待人员": "receptionist_name",
    "接待地点": "receptionist_place",
    "接待日期": "receptionist_date",
    "公告日期": "announcement_date",
}
_DATE_COLS = {"receptionist_date", "announcement_date"}

_INSERT_COLS = ["stock_code", "stock_name", "new", "change_pct", "received_institution_count",
                "received_method", "receptionist_name", "receptionist_place",
                "receptionist_date", "announcement_date", "update_time", "data_source"]


def _clean(v):
    if v is None:
        return None
    if isinstance(v, float) and pd.isna(v):
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    s = str(v).strip()
    if s in ("", "-", "--", "nan", "None", "NaT"):
        return None
    return s


def _to_date(v):
    c = _clean(v)          # _clean 统一返回 str | None，日期对象已被 str() 归一
    if c is None:
        return None
    s = str(c)[:10]
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _to_num(v):
    c = _clean(v)
    if c is None:
        return None
    try:
        return float(str(c).replace("%", "").replace(",", ""))
    except ValueError:
        return None


class JgdySyncCollector:
    """机构调研明细增量同步（东财 stock_jgdy_tj_em，按公告日期起点单次拉取）"""

    def __init__(self, overlap_days: int = 30, first_lookback_days: int = 365,
                 timeout_sec: float = 60):
        self.overlap_days = int(overlap_days or 0)
        self.first_lookback_days = int(first_lookback_days or 365)
        self.timeout_sec = float(timeout_sec)

    # ---------- 存量 ----------

    def _existing_keys(self, conn) -> set[tuple]:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT stock_code, receptionist_date, received_method, received_institution_count "
                "FROM stock_jgdy_detail"
            )
            return {(str(c), d, (m or ""), cnt) for c, d, m, cnt in cur.fetchall()}

    def _max_announce_date(self, conn) -> date | None:
        with conn.cursor() as cur:
            cur.execute("SELECT MAX(announcement_date) FROM stock_jgdy_detail")
            return cur.fetchone()[0]

    # ---------- 主流程 ----------

    def run(self) -> dict:
        conn = pymysql.connect(**get_db_config().to_dict())
        try:
            max_ann = self._max_announce_date(conn)
            existing = self._existing_keys(conn)
        finally:
            conn.close()

        if max_ann is None:
            start = date.today() - timedelta(days=self.first_lookback_days)
        else:
            start = max_ann - timedelta(days=self.overlap_days)
        ds = start.strftime("%Y%m%d")
        logger.info("机构调研：公告起点 %s（本地 MAX(announcement_date)=%s，回溯 %s 天），存量键 %s 个",
                    ds, max_ann, self.overlap_days, len(existing))

        try:
            df = call_with_timeout(ak.stock_jgdy_tj_em, self.timeout_sec, date=ds)
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"stock_jgdy_tj_em({ds}) 失败: {str(e)[:150]}")

        if df is None or df.empty:
            return with_steps(
                {"records_written": 0, "error_count": 0, "errors": [], "note": "源无返回，已是最新"},
                RUN_STEPS, {1: f"起点 {ds}", 2: "源返回空"},
            )

        missing = [c for c in _COL_MAP if c not in df.columns]
        if missing:
            raise RuntimeError(f"stock_jgdy_tj_em 列缺失: {missing}，实际列 {list(df.columns)}")

        rows, skipped = [], 0
        now = datetime.now()
        for _, r in df.iterrows():
            code = _clean(r.get("代码"))
            rdate = _to_date(r.get("接待日期"))
            if not code or rdate is None:
                continue
            method = _clean(r.get("接待方式"))
            cnt = _to_num(r.get("接待机构数量"))
            key = (code, rdate, method or "", (int(cnt) if cnt is not None else None))
            if key in existing:
                skipped += 1
                continue
            existing.add(key)
            rows.append((
                code,
                _clean(r.get("名称")),
                _to_num(r.get("最新价")),
                _to_num(r.get("涨跌幅")),
                (int(cnt) if cnt is not None else None),
                method,
                _clean(r.get("接待人员")),
                _clean(r.get("接待地点")),
                rdate,
                _to_date(r.get("公告日期")),
                now, "AKSHARE",
            ))

        written = 0
        if rows:
            conn = pymysql.connect(**get_db_config().to_dict())
            try:
                insert_sql = (f"INSERT INTO stock_jgdy_detail ({', '.join(_INSERT_COLS)}) "
                              f"VALUES ({', '.join(['%s'] * len(_INSERT_COLS))})")
                with conn.cursor() as cur:
                    cur.executemany(insert_sql, rows)
                conn.commit()
                written = len(rows)
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

        msg = f"机构调研增量：源 {len(df)} 行 → 新增 {written} 条（判重跳过 {skipped}）"
        logger.info("✅ %s", msg)
        return with_steps(
            {"records_written": written, "error_count": 0, "errors": [], "note": msg},
            RUN_STEPS,
            {
                1: f"公告起点 {ds}",
                2: f"源返回 {len(df)} 行",
                3: f"判重跳过 {skipped} 行",
                4: f"写入 {written} 条",
            },
        )
