#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
InvestBuddy 中国央行资产负债表采集器（cn_cb_balance_monthly）

蓝图的哪一块：**货币流动性 · 中国层「数量维度」的核心**（设计文档 §2.3 C11）。

⭐ 为什么这张表最有价值：
  中国没有官方「QE 规模」公告。要回答「央行在扩表还是缩表」，唯一办法是从
  **资产负债表倒推**——「对其他存款性公司债权」这个科目就是央行通过 MLF/逆回购/PSL
  投放给银行的资金余额：**它扩张 = 央行在放水，收缩 = 在收水**。
  实测 2026-08 该科目 21.34 万亿元。

两条口径纪律：

1. **纯源值镜像，不存派生列**（与同批的 cn_liquidity_monthly 刻意不同：后者存了
   m1_m2_gap 派生列）。理由：本表的派生信号（总资产同比 / 「对其他存款性公司债权」环比变化）
   都是**跨期**计算，且 356 期的窗口口径由分析层决定；在采集器里固化会与源值不同步。
   同批表 m1_m2_gap 是**同行内**两列相减，无窗口、不会不同步，故可存。

2. **stat_month 由 '2026.8' 解析**（源格式是 `年.月`，月不补零），统一取当月 1 日。
   源最早到 1993.3，且部分科目为**历史口径**（如「对非货币金融机构债权」「活期存款」）
   近端为 NULL —— 这不是缺失，是口径变更，DQ 规则不应把它们判为「断供」。

幂等：PRIMARY KEY(stat_month) + 全量 upsert（表仅 356 行）。
"""
import logging
import re
from datetime import date, datetime

import pymysql
import akshare as ak

from ..db import get_db_config
from ._common import with_steps, call_with_timeout

logger = logging.getLogger(__name__)

RUN_STEPS = [
    {"no": 1, "name": "拉取央行资产负债表", "params": "macro_china_central_bank_balance（28 科目，356 期）"},
    {"no": 2, "name": "解析统计期", "params": "源格式 '2026.8'（年.月，月不补零）→ 当月 1 日"},
    {"no": 3, "name": "全量 Upsert", "params": "PRIMARY KEY(stat_month)；纯源值镜像，不存派生列"},
]

# 源列名 → 表列名（唯一权威；源列名含半角冒号与中文括号，逐字对齐）
COL_MAP: list[tuple[str, str]] = [
    ("国外资产", "foreign_assets"),
    ("外汇", "fx_reserve"),
    ("货币黄金", "monetary_gold"),
    ("其他国外资产", "other_foreign_assets"),
    ("对政府债权", "claims_gov"),
    ("其中:中央政府", "claims_central_gov"),
    ("对其他存款性公司债权", "claims_other_dep_banks"),
    ("对其他金融性公司债权", "claims_other_fin_cos"),
    ("对非货币金融机构债权", "claims_non_monetary"),
    ("对非金融性公司债权", "claims_non_fin_cos"),
    ("其他资产", "other_assets"),
    ("总资产", "total_assets"),
    ("储备货币", "reserve_money"),
    ("发行货币", "currency_issue"),
    ("金融性公司存款", "fin_cos_deposit"),
    ("其他存款性公司", "other_dep_banks_dep"),
    ("其他金融性公司", "other_fin_cos_dep"),
    ("对金融机构负债", "fin_liab"),
    ("准备金存款", "reserve_deposit"),
    ("非金融性公司存款", "non_fin_cos_dep"),
    ("活期存款", "demand_deposit"),
    ("债券", "bonds"),
    ("国外负债", "foreign_liab"),
    ("政府存款", "gov_deposit"),
    ("自有资金", "own_capital"),
    ("其他负债", "other_liab"),
    ("总负债", "total_liab"),
]

_TGT = ["stat_month"] + [t for _, t in COL_MAP]

# 源格式 '2026.8' / '1993.3'
_PERIOD = re.compile(r"^(\d{4})\.(\d{1,2})$")


def _period(v) -> date | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return date(v.year, v.month, 1)
    if isinstance(v, date):
        return date(v.year, v.month, 1)
    s = str(v).strip()
    m = _PERIOD.match(s)
    if m:
        return date(int(m.group(1)), int(m.group(2)), 1)
    m = re.match(r"^(\d{4})[-/年](\d{1,2})", s)
    if m:
        return date(int(m.group(1)), int(m.group(2)), 1)
    return None


def _f(v, nd: int = 2):
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else round(f, nd)


class CnCbBalanceSyncCollector:
    """中国人民银行资产负债表月度同步"""

    def __init__(self, timeout_sec: float = 90):
        self.timeout_sec = float(timeout_sec)

    def run(self) -> dict:
        errors: list[str] = []
        try:
            df = call_with_timeout(ak.macro_china_central_bank_balance, self.timeout_sec)
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"央行资产负债表拉取失败：{type(e).__name__} {str(e)[:120]}") from e
        if df is None or df.empty:
            raise RuntimeError("央行资产负债表返回为空")

        # 源列名逐字校验：拼错不会报错、只会静默写 NULL（本项目既有教训）
        missing = [src for src, _ in COL_MAP if src not in df.columns]
        if missing:
            raise RuntimeError(f"源列名不匹配（疑似 akshare 升级改了列名）：缺 {missing}")

        rows, skipped = [], 0
        for _, r in df.iterrows():
            m = _period(r.get("统计时间"))
            if m is None:
                skipped += 1
                continue
            rows.append((m, *[_f(r.get(src), 2) for src, _ in COL_MAP]))
        if not rows:
            raise RuntimeError(f"解析后无有效行（跳过 {skipped} 行日期不可解析）")

        conn = pymysql.connect(**get_db_config().to_dict(),
                               cursorclass=pymysql.cursors.DictCursor)
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS n FROM cn_cb_balance_monthly")
                before = cur.fetchone()["n"]
                sql = (
                    f"INSERT INTO cn_cb_balance_monthly ({', '.join(_TGT)}, update_time, data_source) "
                    f"VALUES ({', '.join(['%s'] * len(_TGT))}, NOW(), 'AKSHARE') "
                    "ON DUPLICATE KEY UPDATE "
                    + ", ".join(f"{c}=COALESCE(VALUES({c}), {c})" for c in _TGT if c != "stat_month")
                    + ", update_time=NOW()"
                )
                cur.executemany(sql, rows)
            conn.commit()
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) AS n, MIN(stat_month) AS d0, MAX(stat_month) AS d1, "
                    "MAX(CASE WHEN claims_other_dep_banks IS NOT NULL THEN stat_month END) AS d_omc, "
                    "MAX(CASE WHEN total_assets IS NOT NULL THEN stat_month END) AS d_ta "
                    "FROM cn_cb_balance_monthly")
                r = cur.fetchone()
            new_rows = max(0, r["n"] - before)
        finally:
            conn.close()

        msg = (f"央行资产负债表 upsert {len(rows)} 期（新增 {new_rows}）｜"
               f"{r['d0']} ~ {r['d1']}｜对其他存款性公司债权 至 {r['d_omc']} · 总资产 至 {r['d_ta']}")
        logger.info("✅ %s", msg)
        return with_steps(
            {"records_written": new_rows if new_rows else len(rows),
             "error_count": len(errors), "errors": errors, "note": msg},
            RUN_STEPS,
            {
                1: f"{len(df)} 行 · {len(COL_MAP)} 个科目",
                2: f"{len(rows)} 期（{r['d0']} ~ {r['d1']}），跳过 {skipped} 行",
                3: f"upsert {len(rows)} 行（表内 {r['n']} 行）",
            },
        )
