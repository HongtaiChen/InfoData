#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
InvestBuddy 新基金发行采集器（fund_new_issue）—— 蓝图 E「跨市场与外部情绪」

信号读法（报告 §6 蓝图 E）：**基金发行冰点 = 反向底部信号**。
  这是散户情绪的极值读数：发行火热时往往已在顶部区域，发行冰点（募集失败、延长募集、
  发行份额骤降）往往对应底部区域。它必须跟历史比才有意义——
  「本周发行 12 只」本身不是信息，「本周 12 只 vs 近三年周均 45 只」才是。

数据源：`ak.fund_new_found_em()`（东财数据中心域，实测 3.0s，6,800+ 行）
  列 = 基金代码 / 基金简称 / 发行公司 / 基金类型 / 集中认购期 / 募集份额 / 成立日期 /
       成立来涨幅 / 基金经理 / 申购状态 / 优惠费率

⚠️ 关键列是 `成立日期` + `募集份额`：
  本表按「成立日」聚合即可得到「每周/每月新成立基金数与募集份额总额」——
  这是发行冰点判据的原料。集中认购期只是文本，仅作留档（源格式 `22/10/17～23/01/13`，
  两位数年份，解析易错，故不解析成日期列，避免引入假精度）。

幂等：PRIMARY KEY(fund_code) + 全表 upsert（6,800 行，每轮全量重写无成本，
且源会给同一只基金更新「成立来涨幅/申购状态」，全量 upsert 才能跟上）。
"""
import logging
from datetime import date, datetime

import pymysql
import akshare as ak

from ..db import get_db_config
from ._common import with_steps, call_with_timeout

logger = logging.getLogger(__name__)

RUN_STEPS = [
    {"no": 1, "name": "拉取新基金列表", "params": "fund_new_found_em()（东财，全量 ~6,800 只，实测 3s）"},
    {"no": 2, "name": "字段清洗", "params": "成立日期/募集份额转数值；无基金代码行剔除（集中认购期仅留档不解析）"},
    {"no": 3, "name": "全量 Upsert", "params": "PRIMARY KEY(fund_code)；源会更新「成立来涨幅/申购状态」，故每轮全量重写"},
]

_TGT = ["fund_code", "fund_name", "company", "fund_type", "subs_period",
        "raise_share", "establish_date", "manager", "purchase_status"]


def _d(v) -> date | None:
    """解析成立日期（源为 'YYYY-MM-DD' 字符串；未成立基金为空/'-'）"""
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()[:10]
    if s in ("", "nan", "None", "--", "NaT"):
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
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


class FundNewIssueSyncCollector:
    """新基金发行全量同步"""

    def __init__(self, timeout_sec: float = 120):
        self.timeout_sec = float(timeout_sec)

    def run(self) -> dict:
        df = call_with_timeout(ak.fund_new_found_em, self.timeout_sec)
        if df is None or df.empty:
            raise RuntimeError("fund_new_found_em 返回为空（接口风控或变更）")

        rows, skipped = [], 0
        for _, r in df.iterrows():
            code = _s(r.get("基金代码"), 12)
            if not code:
                skipped += 1
                continue
            rows.append((
                code,
                _s(r.get("基金简称"), 100),
                _s(r.get("发行公司"), 60),
                _s(r.get("基金类型"), 40),
                _s(r.get("集中认购期"), 40),
                _f(r.get("募集份额")),
                _d(r.get("成立日期")),
                _s(r.get("基金经理"), 80),
                _s(r.get("申购状态"), 20),
            ))
        if not rows:
            raise RuntimeError("fund_new_found_em 解析后无有效行（列名可能变更）")

        conn = pymysql.connect(**get_db_config().to_dict(),
                               cursorclass=pymysql.cursors.DictCursor)
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS n FROM fund_new_issue")
                before = cur.fetchone()["n"]
                sql = (
                    f"INSERT INTO fund_new_issue ({', '.join(_TGT)}, update_time, data_source) "
                    f"VALUES ({', '.join(['%s'] * len(_TGT))}, NOW(), 'AKSHARE') "
                    "ON DUPLICATE KEY UPDATE "
                    + ", ".join(f"{c}=VALUES({c})" for c in _TGT if c != "fund_code")
                    + ", update_time=NOW()"
                )
                cur.executemany(sql, rows)
            conn.commit()
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS n, MAX(establish_date) AS d, "
                            "SUM(establish_date IS NULL) AS nd FROM fund_new_issue")
                r = cur.fetchone()
            new_rows = max(0, r["n"] - before)
        finally:
            conn.close()

        msg = (f"新基金 upsert {len(rows)} 只（新增 {new_rows}）｜"
               f"最新成立日 {r['d']}｜未成立（仅发行中）{r['nd']} 只")
        logger.info("✅ %s", msg)
        return with_steps(
            {"records_written": new_rows if new_rows else len(rows),
             "error_count": 0, "errors": [], "note": msg},
            RUN_STEPS,
            {
                1: f"源 {len(df)} 行",
                2: f"清洗后 {len(rows)} 行（剔除无代码 {skipped}）",
                3: f"表内 {r['n']} 只，最新成立日 {r['d']}",
            },
        )
