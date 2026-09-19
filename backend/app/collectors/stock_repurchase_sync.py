#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
InvestBuddy 股票回购采集器（stock_repurchase）—— 蓝图 D「机构与产业资本」

信号读法（报告 §6 蓝图 D）：
  **低位 + 回购潮 + 调研升温 + 解禁压力小 = 底部特征；高位 + 解禁潮 = 风险。**
  回购是产业资本用真金白银投票——比任何表态都硬。它必须与另外两腿合看：
  机构调研（★库内 stock_jgdy_detail）× 限售解禁（★库内 stock_shares 的「限售股份上市」）
  ——三者方向一致才有结论，单看回购会误读（回购常被用于市值管理，动机不纯）。

数据源：`ak.stock_repurchase_em()`（东财数据中心域，实测 5.3s，5,500+ 行 / 12 页）
  含计划回购价格区间、金额区间、占总股本比例、实施进度、已回购金额/数量、最新公告日期。

⚠️ 幂等键取 (stock_code, start_date) 而非源「序号」：
  序号是源的展示序号，随新增记录整体位移，用它做键会让整表重复。
  同一家公司同一天启动两单回购的概率极低，故 (代码, 起始日) 是稳定自然键。
  「实施进度 / 已回购金额」会随时间更新，故用 ON DUPLICATE KEY UPDATE 覆盖。

⚠️ 单位：金额列源为**元**（实测 1.44e8 = 1.44 亿元），不做换算，保持原值入库。
"""
import logging
from datetime import date, datetime

import pymysql
import akshare as ak

from ..db import get_db_config
from ._common import with_steps, call_with_timeout

logger = logging.getLogger(__name__)

RUN_STEPS = [
    {"no": 1, "name": "拉取回购列表", "params": "stock_repurchase_em()（东财数据中心域，~5,500 行 / 12 页，实测 5.3s）"},
    {"no": 2, "name": "字段清洗", "params": "日期列转 DATE；金额/比例转数值；无代码或无起始日的行剔除"},
    {"no": 3, "name": "全量 Upsert", "params": "uk_code_start(stock_code, start_date)；进度与已回购金额会更新，故每轮全量重写"},
]

_TGT = ["stock_code", "stock_name", "start_date", "announce_date", "progress",
        "plan_price_low", "plan_price_high", "plan_amount_low", "plan_amount_high",
        "plan_pct_low", "plan_pct_high", "done_amount", "done_shares",
        "done_price_low", "done_price_high"]


def _d(v) -> date | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()[:10]
    if s in ("", "nan", "None", "--", "NaT"):
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _f(v, nd: int = 4):
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else round(f, nd)


def _s(v, limit: int):
    if v is None:
        return None
    s = str(v).strip()
    if s in ("", "nan", "None", "--"):
        return None
    return s[:limit]


class StockRepurchaseSyncCollector:
    """股票回购全量同步"""

    def __init__(self, timeout_sec: float = 240):
        self.timeout_sec = float(timeout_sec)

    def run(self) -> dict:
        df = call_with_timeout(ak.stock_repurchase_em, self.timeout_sec)
        if df is None or df.empty:
            raise RuntimeError("stock_repurchase_em 返回为空（接口风控或变更）")

        rows, skipped = [], 0
        for _, r in df.iterrows():
            code = _s(r.get("股票代码"), 10)
            start = _d(r.get("回购起始时间"))
            if not code or start is None:
                skipped += 1
                continue
            rows.append((
                code,
                _s(r.get("股票简称"), 50),
                start,
                _d(r.get("最新公告日期")),
                _s(r.get("实施进度"), 20),
                _f(r.get("计划回购价格区间")),          # 源为「区间」单列（多数只给一个值）
                _f(r.get("计划回购价格区间")),
                _f(r.get("计划回购金额区间-下限"), 2),
                _f(r.get("计划回购金额区间-上限"), 2),
                _f(r.get("占公告前一日总股本比例-下限"), 6),
                _f(r.get("占公告前一日总股本比例-上限"), 6),
                _f(r.get("已回购金额"), 2),
                _f(r.get("已回购股份数量"), 2),
                _f(r.get("已回购股份价格区间-下限")),
                _f(r.get("已回购股份价格区间-上限")),
            ))
        if not rows:
            raise RuntimeError("stock_repurchase_em 解析后无有效行（列名可能变更）")

        # 源内可能同一 (code, start) 出现多行（多次进度公告）→ 保留最新公告日那条
        best: dict[tuple, tuple] = {}
        for row in rows:
            k = (row[0], row[2])
            cur = best.get(k)
            if cur is None or (row[3] is not None and (cur[3] is None or row[3] >= cur[3])):
                best[k] = row
        rows = list(best.values())

        conn = pymysql.connect(**get_db_config().to_dict(),
                               cursorclass=pymysql.cursors.DictCursor)
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS n FROM stock_repurchase")
                before = cur.fetchone()["n"]
                sql = (
                    f"INSERT INTO stock_repurchase ({', '.join(_TGT)}, update_time, data_source) "
                    f"VALUES ({', '.join(['%s'] * len(_TGT))}, NOW(), 'AKSHARE') "
                    "ON DUPLICATE KEY UPDATE "
                    + ", ".join(f"{c}=VALUES({c})" for c in _TGT if c not in
                                ("stock_code", "start_date"))
                    + ", update_time=NOW()"
                )
                cur.executemany(sql, rows)
            conn.commit()
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS n, MAX(announce_date) AS d, "
                            "SUM(announce_date >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)) AS n30 "
                            "FROM stock_repurchase")
                r = cur.fetchone()
            new_rows = max(0, r["n"] - before)
        finally:
            conn.close()

        msg = (f"回购 upsert {len(rows)} 单（新增 {new_rows}）｜最新公告 {r['d']}｜"
               f"近 30 日新公告 {r['n30']} 单")
        logger.info("✅ %s", msg)
        return with_steps(
            {"records_written": new_rows if new_rows else len(rows),
             "error_count": 0, "errors": [], "note": msg},
            RUN_STEPS,
            {
                1: f"源 {len(df)} 行",
                2: f"清洗去重后 {len(rows)} 单（剔除无代码/无起始日 {skipped}）",
                3: f"表内 {r['n']} 单，最新公告 {r['d']}，近 30 日 {r['n30']} 单",
            },
        )
