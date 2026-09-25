#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
InvestBuddy 多国央行政策利率采集器（cb_policy_rate）

蓝图的哪一块：**货币流动性 · 全球层 G2「央行行为」+ 日韩欧层 A1~A3**（设计文档 §2.1/§2.4）。

为什么需要：单看一国利率是"一个数字"；**四个主要央行的方向是否共振**才是全球流动性的定性——
同向宽松 = 全球 risk-on 环境；美紧欧日松 = 美元虹吸。故四国并成一表、统一 event_date 以便对齐。

⚠️⚠️ 本采集器最重要的一条实测结论（2026-09-25，务必先读）：
  `ak.macro_bank_{usa,euro,japan,english}_interest_rate` **整族接口的上游（经济日历源）
  已经停更** —— 最后非空的「今值」停在 2025 年 7~8 月：

      美国   2025-07-31 = 4.50      欧元区 2025-07-24 = 2.15
      日本   2025-07-31 = 0.50      英国   2025-08-07 = 4.00

  而表内最后**一行**的日期在 2025-09~10，其「今值」为空（= 源侧占位、尚未回填）。
  ⇒ 换接口无用：实测 11 个 macro_bank_* 全族同病（澳/巴/印/新西兰/俄/瑞士同样停在 2025-08~09），
     因此这是**上游源整体停更**，不是我们选错了函数。
  ⇒ 本表**只存 rate 非空的有效决议行**，并在下游强制暴露「最后决议日」，
     让「源更新至何时」一眼可见，绝不把 2025-07 的值当"当前利率"用。
  ⇒ 设计文档 D4 已决策「本轮不接 FRED」；美国「当前利率」的市场代理用库内
     `bond_profit_daily.us_bond_2y`（2 年期美债收益率，代表市场对政策利率路径的定价，且每日更新）。

解析口径：
  · 源列固定为 ['商品','日期','今值','预测值','前值']，本表取 `日期` → event_date、`今值` → rate。
  · `前值` 是**上一期的公布值**，源侧常有空；故 prev_rate 采用**自算**（按 event_date 排序后取前一行的
    rate），不依赖源的 `前值` 列 —— 实测源里 前值 的空值率明显高于 自算 的完整度。
    change_bp = (rate − prev_rate) × 100。

幂等：uk_country_date(country_code, event_date) + 全量 upsert（四国合计约 1,400 行，重跑无成本）。
"""
import logging
from datetime import date, datetime

import pymysql
import akshare as ak

from ..db import get_db_config
from ._common import with_steps, call_with_timeout

logger = logging.getLogger(__name__)

RUN_STEPS = [
    {"no": 1, "name": "拉取四国利率决议", "params": "macro_bank_{usa,euro,japan,english}_interest_rate"},
    {"no": 2, "name": "过滤有效决议", "params": "剔除「今值」为空的上游占位行（源侧未回填），这些行没有利率值"},
    {"no": 3, "name": "自算变动", "params": "prev_rate / change_bp 由本表按 event_date 相邻行自算（不取源的「前值」列）"},
    {"no": 4, "name": "全量 Upsert", "params": "uk_country_date(country_code, event_date) 幂等"},
]

# (国家代码, 中文名, 央行名, akshare 函数名)
COUNTRIES: list[tuple[str, str, str, str]] = [
    ("US", "美国", "美联储", "macro_bank_usa_interest_rate"),
    ("EU", "欧元区", "欧洲央行", "macro_bank_euro_interest_rate"),
    ("JP", "日本", "日本央行", "macro_bank_japan_interest_rate"),
    ("UK", "英国", "英国央行", "macro_bank_english_interest_rate"),
]

_TGT = ["country_code", "country_name", "central_bank",
        "event_date", "rate", "prev_rate", "change_bp"]


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


def _f(v, nd: int = 4):
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else round(f, nd)


class CbPolicyRateSyncCollector:
    """多国央行政策利率同步（决议事件表）"""

    def __init__(self, timeout_sec: float = 90):
        self.timeout_sec = float(timeout_sec)

    def run(self) -> dict:
        conn = pymysql.connect(**get_db_config().to_dict(),
                               cursorclass=pymysql.cursors.DictCursor)
        errors, per, rows = [], {}, []
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS n FROM cb_policy_rate")
                before = cur.fetchone()["n"]

                for code, cname, bank, fn_name in COUNTRIES:
                    fn = getattr(ak, fn_name, None)
                    if fn is None:
                        errors.append(f"{cname}: akshare 无 {fn_name}")
                        per[code] = (0, None, None)
                        continue
                    try:
                        df = call_with_timeout(fn, self.timeout_sec)
                    except Exception as e:  # noqa: BLE001
                        errors.append(f"{cname}: {type(e).__name__} {str(e)[:60]}")
                        per[code] = (0, None, None)
                        continue
                    if df is None or df.empty:
                        errors.append(f"{cname}: 返回为空")
                        per[code] = (0, None, None)
                        continue

                    # 有效决议 = 今值非空；按日期升序后自算 prev/change
                    valid = []
                    for _, r in df.iterrows():
                        d, v = _d(r.get("日期")), _f(r.get("今值"), 4)
                        if d is None or v is None:
                            continue
                        valid.append((d, v))
                    valid.sort(key=lambda x: x[0])
                    for i, (d, v) in enumerate(valid):
                        prev = valid[i - 1][1] if i > 0 else None
                        chg = round((v - prev) * 100, 2) if prev is not None else None
                        rows.append((code, cname, bank, d, v, prev, chg))
                    per[code] = (len(valid), valid[-1][0] if valid else None,
                                 valid[-1][1] if valid else None)

                if not rows:
                    raise RuntimeError("四国均未取到有效决议：" + "; ".join(errors[:4]))

                sql = (
                    f"INSERT INTO cb_policy_rate ({', '.join(_TGT)}, update_time, data_source) "
                    f"VALUES ({', '.join(['%s'] * len(_TGT))}, NOW(), 'AKSHARE') "
                    "ON DUPLICATE KEY UPDATE "
                    + ", ".join(f"{c}=COALESCE(VALUES({c}), {c})"
                                for c in _TGT if c not in ("country_code", "event_date"))
                    + ", update_time=NOW()"
                )
                cur.executemany(sql, rows)

                cur.execute(
                    "SELECT country_code, COUNT(*) AS n, MAX(event_date) AS d, "
                    "MAX(CASE WHEN change_bp IS NOT NULL AND change_bp <> 0 THEN event_date END) AS d_chg "
                    "FROM cb_policy_rate GROUP BY country_code ORDER BY country_code")
                stat = cur.fetchall()
                cur.execute("SELECT COUNT(*) AS n FROM cb_policy_rate")
                total = cur.fetchone()["n"]
            conn.commit()
            new_rows = max(0, total - before)
        finally:
            conn.close()

        # 源停更时必须在 note 里显式点出——否则「最后决议日」会被误读成「最近刚开过会」
        msg = (f"央行政策利率 upsert {len(rows)} 条决议（新增 {new_rows}）｜"
               + " · ".join(f"{r['country_code']} 至 {r['d']}" for r in stat)
               + "｜⚠️ 源整体停更于 2025-07~08，最后决议日≠当前利率")
        logger.info("✅ %s", msg)
        if errors:
            logger.warning("⚠️ 央行利率 %s 项异常: %s", len(errors), errors[:3])
        return with_steps(
            {"records_written": new_rows if new_rows else len(rows),
             "error_count": len(errors), "errors": errors[:20], "note": msg},
            RUN_STEPS,
            {
                1: " · ".join(f"{b} {per.get(c, (0,))[0]} 条" for c, _, b, _ in COUNTRIES),
                2: f"有效决议 {len(rows)} 条（已剔除上游占位空行）",
                3: "prev_rate / change_bp 按 event_date 相邻自算",
                4: "；".join(f"{r['country_code']} {r['n']}条至{r['d']}" for r in stat),
            },
        )
