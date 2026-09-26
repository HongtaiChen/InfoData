#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
InvestBuddy 中国官方外储 / 黄金储备月度采集器（cn_reserve_monthly）

蓝图的哪一块：**货币流动性 · 中国层「央行对外资产」**（补源方案 v2.1 §11.3 批次 C2）。

为什么要有这张表（先勘误，避免重复建设）：
  v2.0 §7 批次 C 与复核报告 §5.1 都写「外储/黄金 ❌ 未落地」。**实测推翻了它** ——
  `cn_cb_balance_monthly`（货币当局资产负债表）**已有** `fx_reserve`（217,247.55 **亿元** @2026-08）
  与 `monetary_gold`（5,243.77 **亿元** @2026-08），**且未停更**。
  ⇒ 缺的只是 **①官方美元口径（亿美元）** 与 **②黄金实物量（万盎司）**：
      ① 是**国际可比**口径（能与 BIS / IMF 的储备口径横向比）；
      ② 支撑「央行连续增持黄金 / 储备多元化」叙事。
  ⇒ 若照原结论直接建一张含「人民币外储」的新表，会与 `cn_cb_balance_monthly`
     **重复且口径打架**。故本表只装这两项。

源（实测 2026-09-26）：
  `akshare.macro_china_foreign_exchange_gold()` → 419 行 / 3 列 / **1978-12 ~ 2026-08**
  列：`统计时间`（字符串 `'YYYY.M'`） | `黄金储备`（万盎司） | `国家外汇储备`（亿美元）

🔴 三条必须遵守的实测坑（勿回退）：

1. **`统计时间` 是字符串 `'YYYY.M'`，字符串序 ≠ 时间序**
   `'2025.10' < '2025.2'` ⇒ **akshare 返回的 df 本身就是错序的**
   （实测 `df.tail(28)` = …2025.1, 2025.10, 2025.11, 2025.12, 2025.2, 2025.3…）。
   ⇒ **禁止 `df.iloc[-1]` / `max(月份字符串)` 取最新**；本采集器一律先经公共件
     `parse_cn_month()` 解析成 `(year, month)` 元组，水位也按元组比较。
     这是本项目同类坑的**第二次出现**（`cn_liquidity_sync._month` 已处理过 `2026.8`），
     故已把解析逻辑沉淀到 `_common.parse_cn_month()` —— 第三个采集器不必再写第三份。

2. **早期数据有负值/极端小量级**：实测 1980-12 `国家外汇储备 = -12.96`（亿美元）；
   1978-1989 全段 min=-12.96 / max=89.01。⇒ 同比只在「两期都存在且基期 > 0」时计算；
   量级 DQ **只守 2015 年后**（2015+ 实测 min=29,982 / max=38,134 亿美元）。

3. **黄金在未增持月是常数**（不是 0 变动）：`cn_cb_balance_monthly.monetary_gold`
   实测 2024-06~2024-09 恒为 4192.60。⇒ `gold_reserve_oz_chg` 记为差值，
   「连续增持 N 月」必须判 **`chg <> 0`**，不能按月份序连续推断。

⚠️ **双口径软断言（本采集器内，刻意不做成 DQ）**：
  「人民币表内口径 ÷ 官方美元口径」= 隐含折算率。它**不是汇率** ——
  全史 345 个共有月份实测 6.1683~9.4068（1994 汇率并轨后长期在 9 附近），
  近年（2015+，140 期）为 6.2362~7.4930、|月变动| max 2.881%；同期市场汇率约 7.0~7.3。
  ⇒ 按市价互校（如「偏差 >1% 即告警」）会**稳定误报**；
  ⚠️ 方案原拟的「区间 [6.0,7.4] + 月度变动 <2%」实测**两项都会误报**
     （全史 |月变动| p99=8.347%、max=16.603%，345 个月里 25 个月 >2%）
  ⇒ 已按分段实测重定为：**只对 2015 年起断言**，区间 **[6.0, 7.6]**、|月变动| **≤5%**
     （阈值取在实测极值之上留余量；单位/口径级错误会产生 10x~10000x 跳变，5% 依然一击命中）。
  ⚠️ 该互校**跨两张表**，而本库 DQ 检查器是**单表 + where**（无 JOIN）
  ⇒ 只能放在采集器内的断言 + 日志（与美国层「官网 ⟷ DBnomics 双轨」同套路）。

幂等：PRIMARY KEY(stat_month) + 全量 upsert（表仅约 420 行，重跑无成本）。
"""
import logging
from datetime import date

import pymysql
import akshare as ak

from ..db import get_db_config
from ._common import with_steps, call_with_timeout, parse_cn_month

logger = logging.getLogger(__name__)

RUN_STEPS = [
    {"no": 1, "name": "拉取外储/黄金",
     "params": "macro_china_foreign_exchange_gold（419 期，1978-12 起；列=统计时间/黄金储备(万盎司)/国家外汇储备(亿美元)）"},
    {"no": 2, "name": "解析月份并**按 (年,月) 元组排序**",
     "params": "公共件 parse_cn_month；⚠️ 源月份是字符串 'YYYY.M'，字符串序会把 2025.10 排在 2025.2 之前 ⇒ 禁用 iloc[-1]"},
    {"no": 3, "name": "算派生列", "params": "fx_reserve_usd_yoy（同月比，两期存在且基期>0）/ gold_reserve_oz_chg（环比差，正=增持）"},
    {"no": 4, "name": "双口径软断言（跨表，刻意不入 DQ）",
     "params": "隐含折算率 = cn_cb_balance_monthly.fx_reserve(亿元) ÷ 本表 fx_reserve_usd(亿美元)。⚠️ **只对 2015+ 断言**（早年比率在 9 附近且 1993-1999 是季度数据，纳入即常年误报），阈值按实测极值标定：区间 [6.0,7.6] + |月变动| ≤5%（全史实测 |Δ| p99=8.35%/max=16.60%，故「<2%」会稳定误报）"},
    {"no": 5, "name": "全量 Upsert", "params": "PRIMARY KEY(stat_month)，约 420 行，重跑无成本"},
    {"no": 6, "name": "复核水位", "params": "MAX(stat_month) + 逐列水位；stat_month 不得晚于当月（错序/误解析哨兵）"},
]

_TGT = ["stat_month", "fx_reserve_usd", "fx_reserve_usd_yoy",
        "gold_reserve_oz", "gold_reserve_oz_chg"]

# 隐含折算率软断言的**实测标定**（2026-09-26 用 345 个共有月份重定，勿凭直觉改）
#
# ⚠️ 方案 v2.1 §11.3.5 原拟「区间 [6.0, 7.4] + 月度变动 < 2%」—— 实测**两项都会稳定误报**：
#     · 全史比率 6.1683 ~ 9.4068（1994 汇率并轨后长期在 9 附近，2000-2014 都在 7.2~9.1）
#     · |月变动| 全史 p50 0.576% / p90 1.704% / p99 8.347% / **max 16.603%**，
#       345 个月里 **25 个月 > 2%**（按原阈值 = 约 7% 的月份报假警）
#    ⇒ 按年段实测重定（与 DQ 量级守护取同一窗口）：
#       1993-1999  比率 6.17~9.41（**季度**数据，汇率并轨前后）
#       2000-2009  比率 7.19~9.10，|Δ| max 6.789%（>2% 共 9 个月）
#       2010-2014  比率 6.81~7.69，|Δ| max 2.953%（>2% 共 7 个月）
#       **2015-2026  比率 6.2362~7.4930，|Δ| max 2.881%（>2% 仅 2 个月、>3% 为 0）**
#
# ⇒ 断言**只作用于 2015 年起**（近年才是「表内历史成本口径 ÷ 官方美元口径」这个稳定机制），
#   阈值取在实测极值之上留余量：区间 [6.0, 7.6]、|月变动| ≤ 5%。
#   单位/口径级错误（亿元↔万美元、万盎司↔吨等）会产生 10x~10000x 的跳变，
#   5% 的阈值对这种错误**依然一击命中**，故收紧到 2% 毫无收益、只会埋掉真信号。
_XCHECK_LO, _XCHECK_HI = 6.0, 7.6
_XCHECK_MOM = 5.0
_XCHECK_FROM_YEAR = 2015


def _f(v, nd: int = 2):
    if v is None:
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if x != x else round(x, nd)


class CnReserveSyncCollector:
    """中国官方外储（亿美元）+ 黄金储备（万盎司）月度同步"""

    def __init__(self, timeout_sec: float = 90):
        self.timeout_sec = float(timeout_sec)

    # ---------- 源 ----------

    def _fetch(self, errors: list[str]) -> dict[tuple[int, int], dict]:
        """→ {(year, month): {fx, gold}}；月份一律经 parse_cn_month 解析"""
        try:
            df = call_with_timeout(ak.macro_china_foreign_exchange_gold,
                                   max(self.timeout_sec, 60))
        except Exception as e:  # noqa: BLE001
            errors.append(f"foreign_exchange_gold: {type(e).__name__} {str(e)[:60]}")
            return {}
        if df is None or df.empty:
            errors.append("foreign_exchange_gold: 返回为空")
            return {}
        out: dict[tuple[int, int], dict] = {}
        bad = 0
        for _, r in df.iterrows():
            k = parse_cn_month(r.get("统计时间"))
            if k is None:
                bad += 1
                continue
            out[k] = {"fx": _f(r.get("国家外汇储备")), "gold": _f(r.get("黄金储备"))}
        if bad:
            errors.append(f"foreign_exchange_gold: {bad} 行月份无法解析（已跳过）")
        return out

    # ---------- 双口径软断言（跨表 ⇒ 只能在这里，写不成 DQ） ----------

    def _xcheck(self, rec: dict[tuple[int, int], dict]) -> dict:
        """把「人民币表内口径 ÷ 美元官方口径」的隐含折算率做区间 + 变动软断言

        ⚠️ 结论与市价无关：该比率是**历史成本/复合折算**（与市场汇率 7.0~7.3 无关），
           故只做区间与环比两项，**不做「与市场汇率比偏差」**。
        ⚠️ 断言**只作用于 `_XCHECK_FROM_YEAR` 起的月份**（见上方阈值标定注释）：
           早年的比率在 9 附近（汇率并轨后）且 1993-1999 是季度数据，纳入即常年误报。
        """
        info: dict = {"n": 0, "n_win": 0, "lo": None, "hi": None, "lo_win": None, "hi_win": None,
                      "mom_max": None, "out_of_range": [], "jumps": [],
                      "latest": None, "window": f"{_XCHECK_FROM_YEAR}+", "note": ""}
        if not rec:
            return info
        try:
            conn = pymysql.connect(**get_db_config().to_dict(),
                                   cursorclass=pymysql.cursors.DictCursor)
        except Exception as e:  # noqa: BLE001
            info["note"] = f"跨表互校跳过（连库失败：{type(e).__name__}）"
            return info
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT stat_month, fx_reserve FROM cn_cb_balance_monthly "
                    "WHERE fx_reserve IS NOT NULL ORDER BY stat_month")
                rows = cur.fetchall()
        except Exception as e:  # noqa: BLE001
            info["note"] = f"跨表互校跳过（{type(e).__name__}: {str(e)[:50]}）"
            return info
        finally:
            conn.close()

        series: list[tuple[tuple[int, int], float]] = []
        for r in rows:
            sm = r["stat_month"]
            k = (sm.year, sm.month)
            usd = (rec.get(k) or {}).get("fx")
            cny = _f(r["fx_reserve"])
            if not usd or not cny:
                continue
            series.append((k, cny / usd))
        if not series:
            info["note"] = "跨表互校无共有月份（两侧月份未对齐）"
            return info
        series.sort(key=lambda x: x[0])
        info["n"] = len(series)
        rates = [v for _, v in series]
        info["lo"], info["hi"] = round(min(rates), 4), round(max(rates), 4)

        win = [x for x in series if x[0][0] >= _XCHECK_FROM_YEAR]
        win_rates = [v for _, v in win]
        info["n_win"] = len(win)
        if win_rates:
            info["lo_win"], info["hi_win"] = round(min(win_rates), 4), round(max(win_rates), 4)
        for k, v in win:
            if not (_XCHECK_LO <= v <= _XCHECK_HI):
                info["out_of_range"].append({"month": f"{k[0]}-{k[1]:02d}", "rate": round(v, 4)})
        # 连续月之间的变动（跳月不算，避免把「中间月缺失」误当跳变）
        consecutive = []
        for (k0, v0), (k1, v1) in zip(win, win[1:]):
            nxt = (k0[0], k0[1] + 1) if k0[1] < 12 else (k0[0] + 1, 1)
            if k1 == nxt and v0:
                consecutive.append(abs(v1 / v0 - 1) * 100)
        if consecutive:
            info["mom_max"] = round(max(consecutive), 3)
        for (k0, v0), (k1, v1) in zip(win, win[1:]):
            nxt = (k0[0], k0[1] + 1) if k0[1] < 12 else (k0[0] + 1, 1)
            if k1 == nxt and v0 and abs(v1 / v0 - 1) * 100 > _XCHECK_MOM:
                info["jumps"].append({"month": f"{k1[0]}-{k1[1]:02d}",
                                      "chg_pct": round((v1 / v0 - 1) * 100, 3)})
        k, v = series[-1]
        info["latest"] = {"month": f"{k[0]}-{k[1]:02d}", "rate": round(v, 4)}
        info["note"] = (
            f"隐含折算率（人民币表内 ÷ 美元官方）：全史 {info['n']} 期 {info['lo']}~{info['hi']}"
            f"；**断言窗口 {_XCHECK_FROM_YEAR}+**（{info['n_win']} 期）实测 "
            f"{info['lo_win']}~{info['hi_win']}、|月变动| max {info['mom_max']}%，"
            f"故取区间 [{_XCHECK_LO},{_XCHECK_HI}] + 月度变动 ≤{_XCHECK_MOM}% 两项（阈值在实测极值之上，留余量）"
            f"；最新 {info['latest']['month']} = {info['latest']['rate']}。"
            f"⚠️ 这是**历史成本/复合折算口径、不是汇率**（同期市场汇率约 7.0~7.3），"
            f"按市价互校会稳定误报；早年比率在 9 附近（1994 汇率并轨后）且 1993-1999 为季度数据，纳入即常年误报")
        if info["out_of_range"] or info["jumps"]:
            logger.warning("⚠️ 外储双口径软断言命中：越界 %s 项、跳变 %s 项 —— %s",
                           len(info["out_of_range"]), len(info["jumps"]),
                           (info["out_of_range"] + info["jumps"])[:3])
        else:
            logger.info("✅ 外储双口径软断言通过（窗口 %s+ 共 %s 期，比率 %s~%s）",
                        _XCHECK_FROM_YEAR, info["n_win"], info["lo_win"], info["hi_win"])
        return info

    # ---------- 主流程 ----------

    def run(self) -> dict:
        errors: list[str] = []
        rec = self._fetch(errors)
        if not rec:
            raise RuntimeError("外储/黄金源未取到数据：" + "; ".join(errors[:3]))

        # ⚠️ 关键：月份是 (year, month) 元组，排序即时间序。
        #    源返回的 df 本身是该字符串序错序的（见文件头坑 1），此处是唯一正确的排序点。
        months = sorted(rec)
        prev_by_key = {m: rec.get((m[0] - 1, m[1])) for m in months}   # 去年同月
        logger.info("  外储/黄金 %s 期（%s ~ %s）", len(months), months[0], months[-1])

        rows = []
        for i, m in enumerate(months):
            cur_ = rec[m]
            base = prev_by_key[m]
            fx, gold = cur_.get("fx"), cur_.get("gold")
            # 同比：两期都存在且**基期 > 0**（早期有负值，如 1980-12 = -12.96 亿美元）
            yoy = None
            if fx is not None and base and base.get("fx") and base["fx"] > 0:
                yoy = round((fx / base["fx"] - 1) * 100, 2)
            # 黄金环比差：只在**紧邻上一月**存在时算（隔月差会把中间月份的增持漏掉/重复计）
            chg = None
            if i > 0:
                prev_key = (m[0], m[1] - 1) if m[1] > 1 else (m[0] - 1, 12)
                if months[i - 1] == prev_key:
                    p = rec[months[i - 1]]
                    if gold is not None and p.get("gold") is not None:
                        chg = round(gold - p["gold"], 2)
            rows.append((date(m[0], m[1], 1), fx, yoy, gold, chg))

        xcheck = self._xcheck(rec)

        conn = pymysql.connect(**get_db_config().to_dict(),
                               cursorclass=pymysql.cursors.DictCursor)
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS n FROM cn_reserve_monthly")
                before = cur.fetchone()["n"]
                sql = (
                    f"INSERT INTO cn_reserve_monthly ({', '.join(_TGT)}, update_time, data_source) "
                    f"VALUES ({', '.join(['%s'] * len(_TGT))}, NOW(), 'AKSHARE') "
                    "ON DUPLICATE KEY UPDATE "
                    + ", ".join(f"{c}=COALESCE(VALUES({c}), {c})" for c in _TGT
                                if c != "stat_month")
                    + ", update_time=NOW()"
                )
                cur.executemany(sql, rows)
            conn.commit()
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) AS n, MAX(stat_month) AS d, "
                    "MAX(CASE WHEN fx_reserve_usd IS NOT NULL THEN stat_month END) AS d_fx, "
                    "MAX(CASE WHEN gold_reserve_oz IS NOT NULL THEN stat_month END) AS d_gold, "
                    "SUM(stat_month > CURDATE()) AS n_future "
                    "FROM cn_reserve_monthly")
                r = cur.fetchone()
            new_rows = max(0, r["n"] - before)
        finally:
            conn.close()

        used = (r["d_fx"], r["d_gold"])
        msg = (f"外储/黄金 upsert {len(rows)} 月（新增 {new_rows}）｜"
               f"水位 外储 {used[0]} · 黄金 {used[1]}"
               + (f"｜⚠️ 有 {int(r['n_future'])} 行 stat_month 落在未来" if r["n_future"] else ""))
        logger.info("✅ %s", msg)
        if r["n_future"]:
            logger.error("🔴 stat_month 出现未来月份 —— 月份解析或源格式已变，请立即排查")

        return with_steps(
            {"records_written": new_rows if new_rows else len(rows),
             "error_count": len(errors), "errors": errors[:20], "note": msg,
             "xcheck": xcheck},
            RUN_STEPS,
            {
                1: f"{len(rec)} 期",
                2: f"排序后 {months[0]} ~ {months[-1]}（源为字符串序错序，已按元组重排）",
                3: f"同比覆盖 {sum(1 for x in rows if x[2] is not None)} 期 · "
                   f"黄金环比覆盖 {sum(1 for x in rows if x[4] is not None)} 期",
                4: xcheck.get("note") or "未执行",
                5: f"upsert {len(rows)} 行（表内 {r['n']} 行）",
                6: f"外储 {r['d_fx']} · 黄金 {r['d_gold']} · 未来月 {int(r['n_future'])} 行",
            },
        )
