#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
InvestBuddy 中国货币与信用月度采集器（cn_liquidity_monthly）

蓝图的哪一块：**货币流动性 · 中国层「数量维度」**（设计文档 §2.3 数量维度 C6~C10）。
  货币流动性要回答的不只是「钱贵不贵」（价格，已有 Shibor/LPR），还有「钱多不多」（数量）——
  而数量维度此前**一条数据都没有**。本采集器一次补四个接口：

  · `macro_china_money_supply`            M0/M1/M2 数量 + 同比 + 环比（2008-01 起）
  · `macro_china_new_financial_credit`    新增人民币贷款 当月/累计（2008-01 起）
  · `macro_china_shrzgm`                  社融增量 + 7 个分项（2015-01 起）
  · `macro_china_reserve_requirement_ratio` 法定准备金率调整事件（58 条，2007 起）

四条实测口径纪律（⚠️ 勿回退）：

1. **`shr zgm` 源侧停更**（实测 2026-09-25 时最新仅到 202604，而 M2/信贷同日均已到 2026-08）。
   根因：它来自**商务数据中心**（data.mofcom.gov.cn，无参 POST），源侧自身更新滞后；
   东财 datacenter 无对应的社融报告名（实测 RPT_ECONOMY_SOCIAL_FINANCING 等 5 个候选全空）。
   ⇒ 本表以 M1/M2 与新增信贷为主力，社融为辅；表注释与 table_meta 均已声明。

2. **同步发布日错位**：M2/信贷/社融来自不同上游，**同一统计月可能只有部分列先到**。
   故采集后必须写「各列各自的 MAX(stat_month)」，不能只看表的 MAX —— 否则社融停更会被
   M2 的新鲜度掩盖（这正是本项目 `column_watermark` 检查器要守的形态）。

3. **准备金率是「事件表」不是「月度序列」**：源只有 58 条调整事件（公布时间/生效时间/
   调整前/调整后/幅度）。本表按**月末生效值顺延填充**到每个统计月，列名以 rrr_ 前缀标注，
   并额外存 `rrr_effective_date`（当前生效值的生效日）—— 否则「降准了没」「距上次调整多久」
   这两个问题都答不了。这是本表**唯一的派生行为**（另一条 m1_m2_gap 是两列相减）。

4. **月份格式三种**：`2026年08月份`（M2/信贷）、`201501`（社融）、`2025年05月15日`（准备金率）。
   统一解析成「当月 1 日」入库，stat_month 即自然键。

幂等：PRIMARY KEY(stat_month) + 全量 upsert（表仅约 230 行，重跑无成本）。
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
    {"no": 1, "name": "拉取货币供应", "params": "macro_china_money_supply（M0/M1/M2 数量+同比+环比）"},
    {"no": 2, "name": "拉取新增信贷", "params": "macro_china_new_financial_credit（当月/累计/同比）"},
    {"no": 3, "name": "拉取社融", "params": "macro_china_shrzgm（增量 + 7 分项；⚠️ 源为商务数据中心，实测停更）"},
    {"no": 4, "name": "拉取准备金率事件", "params": "macro_china_reserve_requirement_ratio（58 条调整事件）"},
    {"no": 5, "name": "按月合并 + 准备金率顺延", "params": "三源按 stat_month 外连接；rrr 取该月末生效值；m1_m2_gap = m1_yoy − m2_yoy"},
    {"no": 6, "name": "全量 Upsert", "params": "PRIMARY KEY(stat_month)；表约 230 行，重跑无成本"},
]

_TGT = [
    "stat_month",
    "m2", "m2_yoy", "m2_mom", "m1", "m1_yoy", "m1_mom", "m0", "m0_yoy", "m0_mom", "m1_m2_gap",
    "credit_month", "credit_cum", "credit_yoy",
    "shrzgm", "shrzgm_rmb_loan", "shrzgm_fx_loan", "shrzgm_entrust", "shrzgm_trust",
    "shrzgm_undiscounted", "shrzgm_ent_bond", "shrzgm_equity",
    "rrr_large", "rrr_small", "rrr_effective_date",
]

_MONTH_CN = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月")
_MONTH_NUM = re.compile(r"^(\d{4})(\d{2})$")


def _month(v) -> date | None:
    """三种月份格式 → 当月 1 日

    · '2026年08月份'（M2 / 信贷）
    · '201501'      （社融）
    · date/datetime （已是日期对象）
    """
    if v is None:
        return None
    if isinstance(v, datetime):
        return date(v.year, v.month, 1)
    if isinstance(v, date):
        return date(v.year, v.month, 1)
    s = str(v).strip()
    m = _MONTH_CN.search(s)
    if m:
        return date(int(m.group(1)), int(m.group(2)), 1)
    m = _MONTH_NUM.match(s)
    if m:
        return date(int(m.group(1)), int(m.group(2)), 1)
    # '2026-08' / '2026/08'
    m = re.match(r"^(\d{4})[-/](\d{1,2})", s)
    if m:
        return date(int(m.group(1)), int(m.group(2)), 1)
    return None


def _d(v) -> date | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()
    m = _MONTH_CN.search(s)
    if m and "日" in s:
        dm = re.search(r"(\d{1,2})\s*日", s)
        if dm:
            return date(int(m.group(1)), int(m.group(2)), int(dm.group(1)))
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
        try:
            return datetime.strptime(s[:10], fmt).date()
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


class CnLiquiditySyncCollector:
    """中国货币与信用月度同步（M0/M1/M2 + 信贷 + 社融 + 准备金率）"""

    def __init__(self, timeout_sec: float = 120):
        self.timeout_sec = float(timeout_sec)

    # ---------- 各源 ----------

    def _money_supply(self, errors: list[str]) -> dict[date, dict]:
        out: dict[date, dict] = {}
        try:
            df = call_with_timeout(ak.macro_china_money_supply, self.timeout_sec)
        except Exception as e:  # noqa: BLE001
            errors.append(f"money_supply: {type(e).__name__} {str(e)[:60]}")
            return out
        if df is None or df.empty:
            errors.append("money_supply: 返回为空")
            return out
        for _, r in df.iterrows():
            m = _month(r.get("月份"))
            if m is None:
                continue
            out[m] = {
                "m2": _f(r.get("货币和准货币(M2)-数量(亿元)"), 2),
                "m2_yoy": _f(r.get("货币和准货币(M2)-同比增长"), 2),
                "m2_mom": _f(r.get("货币和准货币(M2)-环比增长"), 4),
                "m1": _f(r.get("货币(M1)-数量(亿元)"), 2),
                "m1_yoy": _f(r.get("货币(M1)-同比增长"), 2),
                "m1_mom": _f(r.get("货币(M1)-环比增长"), 4),
                "m0": _f(r.get("流通中的现金(M0)-数量(亿元)"), 2),
                "m0_yoy": _f(r.get("流通中的现金(M0)-同比增长"), 2),
                "m0_mom": _f(r.get("流通中的现金(M0)-环比增长"), 4),
            }
        return out

    def _credit(self, errors: list[str]) -> dict[date, dict]:
        out: dict[date, dict] = {}
        try:
            df = call_with_timeout(ak.macro_china_new_financial_credit, self.timeout_sec)
        except Exception as e:  # noqa: BLE001
            errors.append(f"new_financial_credit: {type(e).__name__} {str(e)[:60]}")
            return out
        if df is None or df.empty:
            errors.append("new_financial_credit: 返回为空")
            return out
        for _, r in df.iterrows():
            m = _month(r.get("月份"))
            if m is None:
                continue
            out[m] = {
                "credit_month": _f(r.get("当月"), 2),
                "credit_cum": _f(r.get("累计"), 2),
                "credit_yoy": _f(r.get("当月-同比增长"), 2),
            }
        return out

    def _shrzgm(self, errors: list[str]) -> dict[date, dict]:
        """社融增量 + 分项

        ⚠️ 该接口走商务数据中心（POST 无参），实测源侧停更（2026-09-25 时最新 202604）。
        本接口**耗时最长**（实测 ~21s），故单独记耗时日志便于排查。
        """
        out: dict[date, dict] = {}
        try:
            df = call_with_timeout(ak.macro_china_shrzgm, max(self.timeout_sec, 90))
        except Exception as e:  # noqa: BLE001
            errors.append(f"shrzgm: {type(e).__name__} {str(e)[:60]}")
            return out
        if df is None or df.empty:
            errors.append("shrzgm: 返回为空")
            return out
        for _, r in df.iterrows():
            m = _month(r.get("月份"))
            if m is None:
                continue
            out[m] = {
                "shrzgm": _f(r.get("社会融资规模增量"), 2),
                "shrzgm_rmb_loan": _f(r.get("其中-人民币贷款"), 2),
                "shrzgm_fx_loan": _f(r.get("其中-委托贷款外币贷款"), 2),
                "shrzgm_entrust": _f(r.get("其中-委托贷款"), 2),
                "shrzgm_trust": _f(r.get("其中-信托贷款"), 2),
                "shrzgm_undiscounted": _f(r.get("其中-未贴现银行承兑汇票"), 2),
                "shrzgm_ent_bond": _f(r.get("其中-企业债券"), 2),
                "shrzgm_equity": _f(r.get("其中-非金融企业境内股票融资"), 2),
            }
        return out

    def _rrr_events(self, errors: list[str]) -> list[tuple[date, float | None, float | None]]:
        """准备金率调整事件 → [(生效日, 大型调整后, 中小调整后)]，按生效日升序

        ⚠️ 取「调整后」而非「调整前」：本表要的是**生效后的水平**（顺延填充的起点）。
        """
        try:
            df = call_with_timeout(ak.macro_china_reserve_requirement_ratio, self.timeout_sec)
        except Exception as e:  # noqa: BLE001
            errors.append(f"rrr: {type(e).__name__} {str(e)[:60]}")
            return []
        if df is None or df.empty:
            errors.append("rrr: 返回为空")
            return []
        ev: list[tuple[date, float | None, float | None]] = []
        for _, r in df.iterrows():
            d = _d(r.get("生效时间"))
            if d is None:
                continue
            ev.append((d, _f(r.get("大型金融机构-调整后"), 2), _f(r.get("中小金融机构-调整后"), 2)))
        ev.sort(key=lambda x: x[0])
        return ev

    # ---------- 主流程 ----------

    def run(self) -> dict:
        errors: list[str] = []
        ms = self._money_supply(errors)
        cr = self._credit(errors)
        sz = self._shrzgm(errors)
        rrr = self._rrr_events(errors)
        logger.info("  货币供应 %s 期 · 信贷 %s 期 · 社融 %s 期 · 准备金率事件 %s 条",
                    len(ms), len(cr), len(sz), len(rrr))

        months = sorted(set(ms) | set(cr) | set(sz))
        if not months:
            raise RuntimeError("四个源均未取到数据：" + "; ".join(errors[:4]))

        # 准备金率顺延：每个统计月取「该月末（用 28 日近似，事件生效日不会落在 29~31）之前
        # 最后一次生效」的值。该月早于首个事件时留 NULL —— 刻意不做前向填充，
        # 否则会把「2007 年之前没有公布口径」伪装成「准备金率 = 某个值」。
        rrr_map: dict[date, tuple] = {}
        if rrr:
            for m in months:
                cutoff = date(m.year, m.month, 28)
                cur = None
                for d, lg, sm in rrr:
                    if d <= cutoff:
                        cur = (d, lg, sm)
                    else:
                        break
                if cur:
                    rrr_map[m] = cur

        rows = []
        for m in months:
            a, b, c = ms.get(m, {}), cr.get(m, {}), sz.get(m, {})
            m1y, m2y = a.get("m1_yoy"), a.get("m2_yoy")
            gap = round(m1y - m2y, 2) if (m1y is not None and m2y is not None) else None
            rv = rrr_map.get(m)
            rows.append((
                m,
                a.get("m2"), a.get("m2_yoy"), a.get("m2_mom"),
                a.get("m1"), a.get("m1_yoy"), a.get("m1_mom"),
                a.get("m0"), a.get("m0_yoy"), a.get("m0_mom"), gap,
                b.get("credit_month"), b.get("credit_cum"), b.get("credit_yoy"),
                c.get("shrzgm"), c.get("shrzgm_rmb_loan"), c.get("shrzgm_fx_loan"),
                c.get("shrzgm_entrust"), c.get("shrzgm_trust"), c.get("shrzgm_undiscounted"),
                c.get("shrzgm_ent_bond"), c.get("shrzgm_equity"),
                rv[1] if rv else None, rv[2] if rv else None, rv[0] if rv else None,
            ))

        conn = pymysql.connect(**get_db_config().to_dict(),
                               cursorclass=pymysql.cursors.DictCursor)
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS n FROM cn_liquidity_monthly")
                before = cur.fetchone()["n"]
                sql = (
                    f"INSERT INTO cn_liquidity_monthly ({', '.join(_TGT)}, update_time, data_source) "
                    f"VALUES ({', '.join(['%s'] * len(_TGT))}, NOW(), 'AKSHARE') "
                    "ON DUPLICATE KEY UPDATE "
                    + ", ".join(f"{c}=COALESCE(VALUES({c}), {c})" for c in _TGT if c != "stat_month")
                    + ", update_time=NOW()"
                )
                cur.executemany(sql, rows)
            conn.commit()
            # 逐列水位：同步发布日错位是本源固有形态，必须逐列量（见文件头纪律 2）
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) AS n, MAX(stat_month) AS d, "
                    "MAX(CASE WHEN m1_yoy IS NOT NULL THEN stat_month END) AS d_m1, "
                    "MAX(CASE WHEN m2_yoy IS NOT NULL THEN stat_month END) AS d_m2, "
                    "MAX(CASE WHEN credit_month IS NOT NULL THEN stat_month END) AS d_cr, "
                    "MAX(CASE WHEN shrzgm IS NOT NULL THEN stat_month END) AS d_sz, "
                    "MAX(CASE WHEN rrr_large IS NOT NULL THEN stat_month END) AS d_rrr "
                    "FROM cn_liquidity_monthly")
                r = cur.fetchone()
            new_rows = max(0, r["n"] - before)
        finally:
            conn.close()

        msg = (f"中国货币与信用 upsert {len(rows)} 月（新增 {new_rows}）｜"
               f"M1 {r['d_m1']} · M2 {r['d_m2']} · 信贷 {r['d_cr']} · 社融 {r['d_sz']} · "
               f"准备金率 {r['d_rrr']}")
        logger.info("✅ %s", msg)
        return with_steps(
            {"records_written": new_rows if new_rows else len(rows),
             "error_count": len(errors), "errors": errors[:20], "note": msg},
            RUN_STEPS,
            {
                1: f"{len(ms)} 期（{min(ms) if ms else '--'} ~ {max(ms) if ms else '--'}）",
                2: f"{len(cr)} 期（{max(cr) if cr else '--'}）",
                3: f"{len(sz)} 期（{max(sz) if sz else '--'} ⚠️ 源侧停更）",
                4: f"{len(rrr)} 条事件（最后 {rrr[-1][0] if rrr else '--'}）",
                5: f"合并 {len(rows)} 月；gap 覆盖 {sum(1 for x in rows if x[10] is not None)} 月",
                6: f"upsert {len(rows)} 行（表内 {r['n']} 行，逐列水位见上）",
            },
        )
