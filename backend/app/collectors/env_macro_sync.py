#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
投资环境模块 · 宏观月度/季度序列采集器（env_macro_monthly）—— E6 定稿纳入的宏观组

背景（docs/design/投资环境模块_设计方案_v0.2_2026-10-18.md §5.3）：
  基本面 C 组「宏观与政策环境」需要 GDP/PMI/CPI/PPI 读数 + 历史曲线，
  但库内**没有任何月度宏观序列表**（finance_calendar 只有事件型，无读数序列）。
  本采集器用 akshare macro_china 系列免密钥源补上，落 env_macro_monthly。

四指标口径（源列名 → 落库语义，**刻意不跨指标混比**）：
  gdp  macro_china_gdp  季度累计值：value=国内生产总值-绝对值(亿元,累计) · yoy=累计同比(%)
       ⚠️ 是**季度**不是月度；「季度」字段形如 `2026年第1季度` / `2026年第1-2季度` /
       `2025年第1-4季度`（即全年）。stat_month 用季度末月表示：Q1→03、Q1-2→06、
       Q1-3→09、Q1-4(全年)→12。
  pmi  macro_china_pmi  月度：value=制造业-指数(PMI 读数，50 为荣枯线) · yoy=制造业-同比增长
       ⚠️ 源列名「同比增长」实为 PMI 的环比变化（PMI 本身就是环比扩散指数，无同比语义），
       仅照搬列名、note 里注明，展示端以 value 曲线为主。
  cpi  macro_china_cpi  月度：value=全国-当月(上年同月=100 定基指数) · yoy=全国-同比增长(当月同比%)
  ppi  macro_china_ppi  月度：value=当月(上年同月=100 定基指数) · yoy=当月同比增长

⚠️ 排序纪律（复用 _common.parse_cn_month 的既有教训）：akshare 各接口返回 df 本身就是
   **倒序**（最新在前），且「月份」是中文字符串（`2026年09月份`）——字符串序 ≠ 时间序。
   故必须先解析成 (year, month) 再统一按时间正序落库，**绝不在月份列上做字符串排序**。

幂等：uk_month_ind(stat_month, indicator) upsert，可重复执行。
"""
import logging
import re

import pymysql
import akshare as ak

from ..db import get_db_config
from ._common import with_steps, call_with_timeout, parse_cn_month

logger = logging.getLogger(__name__)

RUN_STEPS = [
    {"no": 1, "name": "拉取宏观读数", "params": "akshare macro_china_gdp/pmi/cpi/ppi × 4 指标"},
    {"no": 2, "name": "解析月份/季度", "params": "中文月份→(year,month)；GDP 季度→季度末月"},
    {"no": 3, "name": "批量 Upsert", "params": "uk_month_ind(stat_month,indicator) 幂等"},
]

# 目标列（与 env_macro_monthly 对齐）
_TGT = ["stat_month", "indicator", "value", "yoy", "note", "data_source"]

# GDP 季度字符串 → (末月, 说明)。「第1-4季度」= 全年 = 12 月。
_GDP_Q_RE = re.compile(r"(\d{4})年第(\d)(?:-(\d))?季度")


def _gdp_month(q: str) -> tuple[str, str] | None:
    """`2026年第1季度` → ('2026-03','Q1')；`2026年第1-2季度` → ('2026-06','H1')；
    `2025年第1-4季度` → ('2025-12','FY')。无法识别返回 None。"""
    m = _GDP_Q_RE.match(q.strip())
    if not m:
        return None
    y = int(m.group(1))
    q1 = int(m.group(2))
    q2 = int(m.group(3)) if m.group(3) is not None else None
    if q2 is None:
        end = q1
        note = f"Q{q1}"
    else:
        end = q2
        note = "FY" if (q1 == 1 and q2 == 4) else f"H{1 if q2 <= 2 else 2}"
    mon = {1: 3, 2: 6, 3: 9, 4: 12}[end]
    return (f"{y}-{mon:02d}", note)


def _f(v, nd: int = 4):
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f:
        return None
    return round(f, nd)


class EnvMacroSyncCollector:
    """宏观月度/季度序列采集（akshare macro_china 系列，纯外部取数）"""

    def __init__(self, timeout_sec: float = 60):
        self.timeout_sec = float(timeout_sec)

    # ---------- 四个源 ----------

    def _fetch_all(self, out: list[dict], errors: list[str]) -> int:
        n = 0

        def _gdp():
            df = call_with_timeout(ak.macro_china_gdp, self.timeout_sec)
            for _, r in df.iterrows():
                parsed = _gdp_month(str(r.get("季度")))
                if parsed is None:
                    continue
                out.append({
                    "stat_month": parsed[0], "indicator": "gdp",
                    "value": _f(r.get("国内生产总值-绝对值")),
                    "yoy": _f(r.get("国内生产总值-同比增长")),
                    "note": f"累计{parsed[1]}·亿元", "data_source": "AKSHARE",
                })

        def _pmi():
            df = call_with_timeout(ak.macro_china_pmi, self.timeout_sec)
            for _, r in df.iterrows():
                ym = parse_cn_month(r.get("月份"))
                if ym is None:
                    continue
                out.append({
                    "stat_month": f"{ym[0]}-{ym[1]:02d}", "indicator": "pmi",
                    "value": _f(r.get("制造业-指数")),
                    "yoy": _f(r.get("制造业-同比增长")),
                    "note": "制造业PMI·50为荣枯线", "data_source": "AKSHARE",
                })

        def _cpi():
            df = call_with_timeout(ak.macro_china_cpi, self.timeout_sec)
            for _, r in df.iterrows():
                ym = parse_cn_month(r.get("月份"))
                if ym is None:
                    continue
                out.append({
                    "stat_month": f"{ym[0]}-{ym[1]:02d}", "indicator": "cpi",
                    "value": _f(r.get("全国-当月")),
                    "yoy": _f(r.get("全国-同比增长")),
                    "note": "全国CPI·当月指数(上年同月=100)", "data_source": "AKSHARE",
                })

        def _ppi():
            df = call_with_timeout(ak.macro_china_ppi, self.timeout_sec)
            for _, r in df.iterrows():
                ym = parse_cn_month(r.get("月份"))
                if ym is None:
                    continue
                out.append({
                    "stat_month": f"{ym[0]}-{ym[1]:02d}", "indicator": "ppi",
                    "value": _f(r.get("当月")),
                    "yoy": _f(r.get("当月同比增长")),
                    "note": "PPI·当月指数(上年同月=100)", "data_source": "AKSHARE",
                })

        for name, fn in (("gdp", _gdp), ("pmi", _pmi), ("cpi", _cpi), ("ppi", _ppi)):
            before = len(out)
            try:
                fn()
                got = len(out) - before
                logger.info("  %s：%s 行", name, got)
                n += got
            except Exception as e:  # noqa: BLE001
                errors.append(f"{name}: {type(e).__name__} {str(e)[:60]}")
        return n

    # ---------- 主流程 ----------

    def run(self) -> dict:
        out: list[dict] = []
        errors: list[str] = []
        n = self._fetch_all(out, errors)

        if not out:
            raise RuntimeError("宏观各源全部失败：" + "; ".join(errors[:5]))

        # 同一 (stat_month, indicator) 去重（后者覆盖前者）
        uniq: dict[tuple, dict] = {}
        for r in out:
            uniq[(r["stat_month"], r["indicator"])] = r
        rows = list(uniq.values())

        conn = pymysql.connect(**get_db_config().to_dict(),
                               cursorclass=pymysql.cursors.DictCursor)
        try:
            with conn.cursor() as cur:
                sql = (
                    f"INSERT INTO env_macro_monthly ({', '.join(_TGT)}, update_time) "
                    f"VALUES ({', '.join(['%s'] * len(_TGT))}, NOW()) "
                    "ON DUPLICATE KEY UPDATE "
                    + ", ".join(f"{c}=COALESCE(VALUES({c}), {c})" for c in _TGT
                                if c not in ("stat_month", "indicator"))
                    + ", update_time=NOW()"
                )
                cur.executemany(sql, [tuple(r[c] for c in _TGT) for r in rows])
            conn.commit()
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS n FROM env_macro_monthly")
                total = cur.fetchone()["n"]
                cur.execute("SELECT MAX(stat_month) AS m FROM env_macro_monthly")
                latest = cur.fetchone()["m"]
        finally:
            conn.close()

        msg = (f"宏观序列 upsert {len(rows)} 行（表内 {total} 行 / 最新 {latest}）｜"
               f"gdp/pmi/cpi/ppi")
        logger.info("✅ %s", msg)
        return with_steps(
            {"records_written": len(rows), "error_count": len(errors),
             "errors": errors[:10], "note": msg},
            RUN_STEPS,
            {
                1: f"4 指标，共 {n} 行",
                2: "中文月份/季度 → 规范 stat_month（GDP 季度末月）",
                3: f"upsert {len(rows)} 行（表内 {total} 行 / 最新 {latest}）",
            },
        )
