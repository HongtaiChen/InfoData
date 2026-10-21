#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
InvestBuddy 新增开户采集器（stock_account_monthly）—— 「杠杆与资金行为」三指标之一

数据源（双口径拼接，2026-10-20 调研落地）：
  1. 上交所官方（主口径，2023 至今）：query.sse.com.cn/commonQuery.do
     sqlId=COMMON_SSE_TZZ_M_STOCK_ACCT_C，返回「股票账户新开户状况表」，
     列 = PERSON/ORG/A_PERSON/A_ORG/B_PERSON/B_ORG（单位：万户）+ TERM（月份/累计总户数）。
     实测 2026-10-20：返回 14 行 = 当年 1-9 月 + 累计，MDATE=202609。
  2. 东财历史（背景趋势，2015-04~2023-08）：ak.stock_account_statistics_em()
     列 = 新增投资者-数量 / 期末投资者-A股账户 等（全市场口径，单位：万户）。

⚠️ 口径差异（务必写进 source_note，严禁直接续接同一序列）：
  - 上交所口径 = 仅上交所单所；东财口径 = 全市场（沪深合计）。
  - 两者数值不同（如上交所 2023-08 个人 A 股新开户约 91 万户 vs 东财全市场 99.59 万户）。
  - 处理：主口径用上交所（2023 至今逐月积累），历史背景用东财（2015-04~2022-12），
    分两段展示，UI 标注口径切换点。

⚠️ 接口只返回「当前月报的当年数据」（忽略日期参数，固定返回最新一期），
  故历史必须**逐月采集积累**：每轮只新增「本地没有的新月份」，无法回溯补历史。
  ⇒ 2023~今的官方数据只能从「采集器上线当月起」往后逐月攒；
    上线前的官方历史月（2023-01 ~ 上线前月）用东财数据兜底（标 EM 口径）。

幂等：PRIMARY KEY(stat_month) + ON DUPLICATE KEY UPDATE。
  上交所接口每轮返回当年全部月份 + 累计行（TERM='累计总户数'），
  累计行不进主表（只存 total_* 列到当月行？否——累计是全局值，单独逻辑处理）。
  ⚠️ 累计总户数行：TERM='累计总户数'，其 A_PERSON 是「截至最新月的累计总户数」，
    属于全局快照，不适合塞进某个月的行里。本采集器**只采月度新增行**（TERM 形如 YYYY.MM），
    累计行单独记 latest_total（存进当月行的 total_* 列，仅最新月有值）。
"""
import logging
import re
import time
from datetime import date, datetime

import pymysql
import requests

from ..db import get_db_config
from ._common import with_steps, call_with_timeout, parse_cn_month

logger = logging.getLogger(__name__)

RUN_STEPS = [
    {"no": 1, "name": "拉上交所官方开户", "params": "query.sse.com.cn commonQuery.do sqlId=COMMON_SSE_TZZ_M_STOCK_ACCT_C（60s 超时）"},
    {"no": 2, "name": "拉东财历史开户", "params": "ak.stock_account_statistics_em()（2015-04~2023-08 全市场口径，仅首次回填）"},
    {"no": 3, "name": "口径标注与清洗", "params": "上交所=SSE / 东财=EM，分两段；单位万户；累计行单独处理"},
    {"no": 4, "name": "批量 upsert", "params": "PRIMARY KEY(stat_month) + ON DUPLICATE KEY UPDATE"},
]

_SSE_URL = "https://query.sse.com.cn/commonQuery.do"
_SSE_HEADERS = {
    "Referer": "https://www.sse.com.cn/aboutus/publication/monthly/investor/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
}


def _fetch_sse() -> list[dict]:
    """拉上交所官方开户数据，返回月度新增行列表（已解析数值）。"""
    import json
    params = {
        "jsonCallBack": "jsonpCallback",
        "sqlId": "COMMON_SSE_TZZ_M_STOCK_ACCT_C",
    }
    r = call_with_timeout(
        requests.get, 60, _SSE_URL, params=params, headers=_SSE_HEADERS
    )
    r.encoding = "utf-8"
    m = re.search(r"jsonpCallback\((.*)\)\s*$", r.text, re.S)
    if not m:
        raise RuntimeError("上交所开户接口返回非 jsonp 格式")
    data = json.loads(m.group(1))
    result = data.get("result")
    if not result:
        raise RuntimeError("上交所开户接口 result 为空（接口变更或风控）")

    rows = []
    for item in result:
        term = str(item.get("TERM", "")).strip()
        # 跳过「累计总户数」与「YYYY年合计」行（累计值，非月度新增）
        if "累计" in term or "合计" in term:
            continue
        # TERM 形如 2026.01（当月）；⚠️ 不能用 MDATE（所有行都等于最新月 202609）
        ym = parse_cn_month(term)
        if not ym:
            continue
        year, month = ym
        a_person = _f(item.get("A_PERSON"))
        person = _f(item.get("PERSON"))
        # 跳过未来月份（源预填 0，如当前 10-12 月 A_PERSON=0）：0 且无个人开户视为无数据
        if a_person == 0 and person == 0:
            continue
        rows.append({
            "stat_month": date(year, month, 1),
            "person": person,
            "org": _f(item.get("ORG")),
            "a_person": a_person,
            "a_org": _f(item.get("A_ORG")),
            "b_person": _f(item.get("B_PERSON")),
            "b_org": _f(item.get("B_ORG")),
        })
    return rows


def _fetch_em() -> list[dict]:
    """拉东财历史开户（2015-04~2023-08 全市场口径）。"""
    import akshare as ak
    df = call_with_timeout(ak.stock_account_statistics_em, 90)
    if df is None or df.empty:
        return []
    rows = []
    for _, r in df.iterrows():
        ym = parse_cn_month(r.get("数据日期"))
        if not ym:
            continue
        year, month = ym
        # 只采 2022-12 及之前（2023 起交给上交所官方口径）
        if year > 2022:
            continue
        # 东财无个人/机构拆分，只有「新增投资者-数量」（全市场）
        rows.append({
            "stat_month": date(year, month, 1),
            "person": _f(r.get("新增投资者-数量")),
            "org": None,
            "a_person": _f(r.get("新增投资者-数量")),  # 东财口径近似映射：无拆分，主列存总量
            "a_org": None,
            "b_person": None,
            "b_org": None,
            "_source_note": "EM=东财全市场口径（2015~2022），仅作背景趋势",
        })
    return rows


def _f(v):
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f


class AccountOpenSyncCollector:
    """新增开户（上交所官方 + 东财历史）增量同步"""

    def __init__(self, timeout_sec: float = 90, backfill_em: bool = True):
        self.timeout_sec = float(timeout_sec)
        self.backfill_em = bool(backfill_em)

    def run(self) -> dict:
        # 1. 上交所官方（主口径）
        sse_rows = _fetch_sse()
        if not sse_rows:
            raise RuntimeError("上交所开户接口解析后无月度行")

        # 2. 东财历史（仅首次回填：本地无任何 EM 记录时）
        em_rows: list[dict] = []
        conn = pymysql.connect(**get_db_config().to_dict(),
                               cursorclass=pymysql.cursors.DictCursor)
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS n FROM stock_account_monthly WHERE source_note LIKE 'EM%'")
                em_exists = cur.fetchone()["n"] > 0
                cur.execute("SELECT MAX(stat_month) AS d FROM stock_account_monthly")
                max_month = cur.fetchone()["d"]

            if self.backfill_em and not em_exists:
                em_rows = _fetch_em()

            # 3. 组装 upsert（SSE 主口径 + EM 背景）
            rows = []
            for r in sse_rows:
                rows.append((
                    r["stat_month"], r["person"], r["org"], r["a_person"], r["a_org"],
                    r["b_person"], r["b_org"], None, None,
                    "SSE=上交所官方（单所口径）", "SSE",
                ))
            for r in em_rows:
                rows.append((
                    r["stat_month"], r["person"], r["org"], r["a_person"], r["a_org"],
                    r["b_person"], r["b_org"], None, None,
                    r.get("_source_note", "EM=东财全市场口径"), "EM",
                ))

            if not rows:
                return with_steps(
                    {"records_written": 0, "error_count": 0, "errors": [], "note": "无可写数据"},
                    RUN_STEPS, {1: f"SSE {len(sse_rows)} 月", 2: "跳过", 3: "跳过", 4: "0 行"},
                )

            _cols = ["stat_month", "person", "org", "a_person", "a_org",
                     "b_person", "b_org", "total_person", "total_a_person",
                     "source_note", "data_source"]
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS n FROM stock_account_monthly")
                before = cur.fetchone()["n"]
                sql = (
                    f"INSERT INTO stock_account_monthly ({', '.join(_cols)}, update_time) "
                    f"VALUES ({', '.join(['%s'] * len(_cols))}, NOW()) "
                    "ON DUPLICATE KEY UPDATE "
                    "person=VALUES(person), org=VALUES(org), a_person=VALUES(a_person), "
                    "a_org=VALUES(a_org), b_person=VALUES(b_person), b_org=VALUES(b_org), "
                    "source_note=VALUES(source_note), data_source=VALUES(data_source), update_time=NOW()"
                )
                cur.executemany(sql, rows)
            conn.commit()
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS n, MAX(stat_month) AS d FROM stock_account_monthly")
                r = cur.fetchone()
            new_rows = max(0, r["n"] - before)
        finally:
            conn.close()

        msg = (f"开户 upsert {len(rows)} 行（新增 {new_rows}）｜最新 {r['d']}｜"
               f"SSE {len(sse_rows)} 月 + EM {len(em_rows)} 月")
        logger.info("✅ %s", msg)
        return with_steps(
            {"records_written": new_rows if new_rows else len(rows),
             "error_count": 0, "errors": [], "note": msg},
            RUN_STEPS,
            {
                1: f"上交所 {len(sse_rows)} 月（含 2026 当年）",
                2: f"东财 {len(em_rows)} 月（首次回填）" if em_rows else "已有 EM 历史，跳过",
                3: f"SSE 主口径 + EM 背景，单位万户",
                4: f"表内 {r['n']} 月，最新 {r['d']}",
            },
        )
