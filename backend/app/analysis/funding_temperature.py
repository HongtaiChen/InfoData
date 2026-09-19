#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""分析模块：资金温度（汇率 × 新发基金 × 回购，三条独立资金线索的合成）

🔒 第一原则「数据用来比较才有意义」（设计规范 §1.0）：
本模块落的是参照系 **⑤ 结构分解**（新发基金里权益 vs 固收的构成、回购的进度构成）、
**② 自身纵比**（三条线各自在近 36 个月的什么位置），
核心是 **④ 交叉印证** —— 三条线索来自三个**互不相干的数据源**：
  ① 外部资金环境 → `currency_boc_daily`（美元兑人民币中间价，央行口径）
  ② 增量资金（居民端）→ `fund_new_issue`（新基金发行，公募口径）
  ③ 产业资本（公司端）→ `stock_repurchase`（上市公司回购，公告口径）
三条同向 = 资金温度成立；两条以上背离 = 「谁在托底、谁在观望」这种更具体的判断。

**为什么需要把三张表合成一个模块**：三张表此前都**只进不出**（各有采集器与 DQ 规则，
无任何视图消费）。而单看任何一条都容易被误读 ——
"人民币升值"是好事还是坏事取决于外资流向；"基金发不动"可能只是因为行情不好，
而不是没有钱；"回购多"既可能是产业资本看好，也可能是股价跌到公司自己受不了。
**只有三条并置、互相印证，单个读数才获得方向。** 这也正是本模块不拆成三个单表的理由。

数据与口径（⚠️ 四条已实测的坑，全部在代码里处理，勿回退）：
1. **回购必须用 `start_date`（回购起始时间），不能用 `announce_date`**。
   `stock_repurchase` 的唯一键是 (stock_code, start_date)，同一计划再次公告时
   `announce_date` 被**覆盖成最新日期** → 历史月份的公告数被不断抽走、近期月份被顶高。
   实测：`announce_date` 口径的「近 90 天滚动计数」在近 36 个月里恒为 **100.0% 分位**
   （永远最高，零区分度，是个假信号）；`start_date` 不可变，同口径下当前分位 **84.2%**、
   区间 55~872，且能正确识别出 2024-02 的回购潮（滚动 90 天 853）与 2025-09 的低谷（70）。
2. **必须剔除"残缺月"**。`fund_new_issue` 最新月只覆盖到月中（实测 2026-09 只到 09-15），
   若不剔除，该月份额必然断崖，近 3 月合计会被低估一半以上。
   判定规则：若最新月的最后一条记录距该月月末 > 5 天，则整月剔除（见 `_complete_months`）。
   窗口因此始终是"完整月"，并在 `scale.label` 里如实写明。
3. **汇率的 `mid_price` 有空值，且最新一日常为空**（央行当日中间价发布晚于采集时刻，
   实测 2026-09-19 的 mid_price 为 NULL、ref_price 有值）。故一律 `mid_price IS NOT NULL`，
   不要用 ref_price 兜底 —— 两者口径不同（中间价 vs 中行折算价），混用会让序列出现台阶。
4. **三条线的分位窗口长度不同，且必须各自如实下发**：汇率是日频 → 近一年（250 交易日）；
   基金与回购是月频/事件频 → 近 36 个月。前端**不得**写死"近一年"
   （见 `_kpi.scale` 的 ⚠️ 与 registry.py 卡片墙契约第 ⑤ 条）。
"""
from __future__ import annotations

import bisect
import calendar
import logging
from datetime import date, timedelta

from ..db import query_all
from ._cache import ttl_cache
from ._kpi import f, is_extreme, pctile, r, scale

logger = logging.getLogger(__name__)

FX_WINDOW = 250          # 汇率分位窗口：近一年交易日
DEFAULT_MONTHS = 36      # 月度类分位窗口：近 36 个月
ROLL_DAYS = 90           # 回购活动强度用滚动 90 天
# 残缺月判定容差（天）：最新月最后一条记录距月末超过它 → 整月剔除（见文件头坑 2）
EOM_TOLERANCE = 5

# 新发基金三分类（子串白名单）。⚠️ 顺序有意义：先判固收，再判权益，其余入"其他"。
# 依据是 `fund_type` 的实际取值集（实测 25 个值，见 seed_table_meta 的源说明），
# 例如"债券型-混合二级"含"债"→固收、"指数型-固收"含"固收"→固收、
# "混合型-偏股"含"偏股"→权益、"FOF-稳健型"/"QDII-*"→其他。
FI_KEYS = ("债", "固收", "货币")
EQ_KEYS = ("股票", "偏股", "灵活", "平衡")
# 图表配色：权益=主色蓝（关注主体），固收=灰（对照），A股管理 UI 不用红绿
EQ_COLOR, FI_COLOR = "#185FA5", "#9CA3AF"

FUNDING_TEMPERATURE_NOTE = (
    "本模块把三条**互不相干**的资金线索并置：外部环境（人民币汇率）、"
    "增量资金（新发基金）、产业资本（上市公司回购）。"
    "单看任何一条都会被误读 —— 汇率升贬的利弊取决于资金流向，基金发不动可能只是行情差、"
    "不代表没钱，回购多既可能是看好也可能是股价低到公司自己受不了。"
    "**三条同向才算信号，背离本身就是最有价值的读数。** "
    "⚠️ 三条线频率不同（日/月/事件），分位窗口因此不同（近一年 / 近 36 个月），"
    "窗口口径随每个 KPI 的刻度条一并下发，不要跨指标比数字大小。"
)


# ---------------- 取数 ----------------

def _fx(as_of: str | None) -> dict:
    """美元兑人民币中间价（坑 3：只取 mid_price 非空行）"""
    cond = "WHERE currency = 'USD' AND mid_price IS NOT NULL"
    args: list = []
    if as_of:
        cond += " AND trade_date <= %s"
        args.append(as_of)
    rows = query_all(
        "SELECT trade_date, mid_price FROM currency_boc_daily "
        f"{cond} ORDER BY trade_date DESC LIMIT %s",
        args + [FX_WINDOW + 25])
    if len(rows) < 30:
        return {"as_of": None, "mid": None, "pct": None, "chg20": None,
                "series": {"dates": [], "values": []}}
    win = rows[:FX_WINDOW]
    cur = f(rows[0]["mid_price"])
    vals = [f(x["mid_price"]) for x in win]
    chg20 = None
    if len(rows) > 20 and rows[20]["mid_price"] is not None:
        chg20 = (cur / f(rows[20]["mid_price"]) - 1) * 100
    ser = list(reversed(rows[:FX_WINDOW]))
    return {"as_of": str(rows[0]["trade_date"]),
            "base_date_1y": str(win[-1]["trade_date"]),
            "mid": r(cur, 4),
            "min_1y": r(min(vals), 4), "max_1y": r(max(vals), 4),
            "pct": pctile(vals, cur), "chg20": r(chg20, 2),
            "series": {"dates": [str(x["trade_date"]) for x in ser],
                       "values": [r(f(x["mid_price"]), 4) for x in ser]}}


def _month_bounds(as_of: str | None) -> tuple[str, str]:
    """月度窗口的上下界：下界 = as_of（或今天）往前 months 个月的月初；上界 = as_of（或今天）"""
    anchor = date.fromisoformat(as_of) if as_of else date.today()
    y, m = anchor.year, anchor.month
    m2 = m - DEFAULT_MONTHS
    while m2 <= 0:
        m2 += 12
        y -= 1
    return f"{y:04d}-{m2:02d}-01", anchor.isoformat()


def _complete_months(rows: list[dict], date_key: str) -> list[dict]:
    """剔除残缺月（坑 2）：最新月最后一条记录距该月月末 > EOM_TOLERANCE 天则整月丢弃"""
    if not rows:
        return []
    last = rows[-1]
    d = last[date_key]
    if isinstance(d, str):
        d = date.fromisoformat(d)
    eom = date(d.year, d.month, calendar.monthrange(d.year, d.month)[1])
    return rows[:-1] if (eom - d).days > EOM_TOLERANCE else rows


def _fund(as_of: str | None) -> dict:
    """新基金发行：近 N 个完整月的份额与权益/固收结构（坑 2、坑 4）"""
    lo, hi = _month_bounds(as_of)
    rows = query_all(
        "SELECT DATE_FORMAT(establish_date, '%%Y-%%m') ym, MAX(establish_date) last_d, COUNT(*) n, "
        "  SUM(raise_share) shares, "
        "  SUM(CASE WHEN " + " OR ".join(f"fund_type LIKE '%%{k}%%'" for k in FI_KEYS) +
        "      THEN raise_share ELSE 0 END) fi, "
        "  SUM(CASE WHEN NOT (" + " OR ".join(f"fund_type LIKE '%%{k}%%'" for k in FI_KEYS) + ") AND ("
        + " OR ".join(f"fund_type LIKE '%%{k}%%'" for k in EQ_KEYS) +
        "      ) THEN raise_share ELSE 0 END) eq "
        "FROM fund_new_issue WHERE establish_date IS NOT NULL AND establish_date >= %s "
        "  AND establish_date <= %s GROUP BY ym ORDER BY ym",
        [lo, hi])
    rows = [{"ym": x["ym"], "last_d": x["last_d"], "n": int(x["n"]),
             "shares": float(x["shares"] or 0), "eq": float(x["eq"] or 0),
             "fi": float(x["fi"] or 0)} for x in rows]
    rows = _complete_months(rows, "last_d")
    win = rows[-DEFAULT_MONTHS:]
    if not win:
        return {"window": None, "monthly": [], "avg3": None, "pct": None, "last3": None}
    for x in win:
        x["other"] = r(x["shares"] - x["eq"] - x["fi"], 2)
        x["eq_pct"] = r(x["eq"] / x["shares"] * 100, 1) if x["shares"] > 0 else None
    vals = [x["shares"] for x in win]
    last3 = win[-3:]
    avg3 = sum(x["shares"] for x in last3) / len(last3)
    return {
        "window": {"start": win[0]["ym"], "end": win[-1]["ym"], "months": len(win)},
        "monthly": win,
        "last3": {"months": [x["ym"] for x in last3],
                  "shares": r(sum(x["shares"] for x in last3), 1),
                  "eq": r(sum(x["eq"] for x in last3), 1),
                  "fi": r(sum(x["fi"] for x in last3), 1),
                  "eq_pct": r(sum(x["eq"] for x in last3)
                              / max(1e-9, sum(x["shares"] for x in last3)) * 100, 1)},
        # KPI 的值 = 近 3 完整月的**月均**份额，分位也在月度分布上取 —— 量纲一致
        "avg3": r(avg3, 1),
        "pct": pctile(vals, avg3),
        "min": r(min(vals), 1), "max": r(max(vals), 1),
        "total_months": len(rows),
    }


def _repurchase(as_of: str | None) -> dict:
    """上市公司回购：滚动 90 天的「新启动计划数」+ 进度构成（坑 1：必须用 start_date）"""
    anchor = date.fromisoformat(as_of) if as_of else date.today()
    anchor_s = anchor.isoformat()
    y, m = anchor.year, anchor.month - DEFAULT_MONTHS
    while m <= 0:
        m += 12
        y -= 1
    lo = f"{y:04d}-{m:02d}-01"
    # ⚠️ 取数下界要比网格下界再往前推 ROLL_DAYS 天：滚动 90 天的窗口在网格开头
    #    需要 d-89 的数据。若不往前推，网格最早那几十天的窗口是**被截断的**
    #    （实测 min 会从真实的 55 掉到 2），整个分布被左偏，当前分位被虚高。
    lo_data = (date(y, m, 1) - timedelta(days=ROLL_DAYS)).isoformat()
    # 逐日计数 → 前缀和 → 任意窗口 O(1)（口径见文件头坑 1：start_date 不可变）
    daily = query_all(
        "SELECT start_date d, COUNT(*) n FROM stock_repurchase "
        "WHERE start_date IS NOT NULL AND start_date >= %s AND start_date <= %s "
        "GROUP BY start_date ORDER BY start_date", [lo_data, anchor_s])
    if not daily:
        return {"windows": [], "last90": None, "pct": None, "series": {"dates": [], "values": []},
                "progress": [], "done_amount_yi": None}
    per = {str(x["d"]): int(x["n"]) for x in daily}
    keys = sorted(per)
    pref = [0]
    for k in keys:
        pref.append(pref[-1] + per[k])

    def cnt(d1: date, d2: date) -> int:
        return (pref[bisect.bisect_right(keys, d2.isoformat())]
                - pref[bisect.bisect_left(keys, d1.isoformat())])

    # 以交易日为网格算滚动 90 天（用交易日而非自然日：与项目其它模块的日历口径一致）
    grid_rows = query_all(
        "SELECT trade_date FROM market_style_daily WHERE trade_date >= %s AND trade_date <= %s "
        "ORDER BY trade_date", [lo, anchor_s])
    grid = [x["trade_date"] for x in grid_rows]
    if not grid:
        return {"windows": [], "last90": None, "pct": None, "series": {"dates": [], "values": []},
                "progress": [], "done_amount_yi": None}
    series = [(d, cnt(d - timedelta(days=ROLL_DAYS - 1), d)) for d in grid]
    vals = [v for _, v in series]
    last90 = vals[-1]
    pct = pctile(vals, last90)

    # 抽样到 ~250 点（卡片墙也会拉这个接口，payload 别无声地涨到几百 KB）
    step = max(1, -(-len(series) // 250))
    sampled = series[::step]
    if sampled[-1][0] != series[-1][0]:
        sampled.append(series[-1])

    # 进度构成与累计金额统一限定在**同一个 36 个月窗口**内，并在响应里带上窗口，
    # 避免视图层把"窗口内累计"当成"近 90 天"（两个口径差了 12 倍，是最容易出错的读法）。
    prog = query_all(
        "SELECT progress, COUNT(*) n, SUM(done_amount)/1e8 amt_yi FROM stock_repurchase "
        "WHERE start_date IS NOT NULL AND start_date >= %s AND start_date <= %s "
        "GROUP BY progress ORDER BY n DESC", [lo, anchor_s])
    prog = [{"progress": x["progress"] or "未标注", "n": int(x["n"]),
             "amount_yi": r(x["amt_yi"], 1)} for x in prog]
    done_amt = next((x["amount_yi"] for x in prog if x["progress"] == "完成实施"), None)

    monthly = query_all(
        "SELECT DATE_FORMAT(start_date, '%%Y-%%m') ym, MAX(start_date) last_d, COUNT(*) n, "
        "  SUM(CASE WHEN progress = '完成实施' THEN 1 ELSE 0 END) done, "
        "  SUM(CASE WHEN progress = '实施中' THEN 1 ELSE 0 END) running, "
        "  SUM(done_amount)/1e8 amt_yi FROM stock_repurchase "
        "WHERE start_date IS NOT NULL AND start_date >= %s AND start_date <= %s "
        "GROUP BY ym ORDER BY ym", [lo, anchor_s])
    monthly = _complete_months(monthly, "last_d")           # 剔除残缺月（与 fund 同一规则）
    return {
        "last90": last90, "pct": pct, "min": min(vals), "max": max(vals),
        "window_days": ROLL_DAYS,
        # 进度构成 / 累计金额所属的窗口（前端必须据此标注，不能写成"近 90 天"）
        "window": {"start": lo, "end": anchor_s, "months": DEFAULT_MONTHS},
        "series": {"dates": [str(d) for d, _ in sampled], "values": [v for _, v in sampled]},
        "progress": prog, "done_amount_yi": done_amt,
        "monthly": [{"ym": x["ym"], "n": int(x["n"]), "done": int(x["done"] or 0),
                     "running": int(x["running"] or 0),
                     "amount_yi": r(x["amt_yi"], 1)} for x in monthly],
    }


# ---------------- 判读词 / 交叉印证 ----------------

def _fx_word(pct: float | None) -> str:
    """USD/CNY 分位越低 = 美元越便宜 = 人民币越强。方向容易反，单独成函数并注释清楚。"""
    if pct is None:
        return "数据未就绪"
    if pct <= 20:
        return "人民币近一年偏强"
    if pct <= 40:
        return "人民币略偏强"
    if pct <= 60:
        return "汇率中性"
    if pct <= 80:
        return "人民币略偏弱"
    return "人民币近一年偏弱"


def _temperature(clues: list[dict]) -> dict:
    """④ 交叉印证：三条线索合成"资金温度"

    每条线索按"对 A 股资金面是否顺风"记 1 分（口径写在各条自己的 reading 里）。
    分数只用来定位档位；**真正的信息在"哪条不顺风"**，所以 reading 一律点名到具体线索。
    """
    ok = [c for c in clues if c["supportive"] is True]
    no = [c for c in clues if c["supportive"] is False]
    score = len(ok)
    if score >= 3:
        level, tone = "偏暖", "opportunity"
    elif score == 2:
        level, tone = "中性偏暖", "normal"
    elif score == 1:
        level, tone = "中性偏冷", "normal"
    else:
        level, tone = "偏冷", "caution"

    parts = "、".join(f"{c['name']}{'顺风' if c['supportive'] else '不顺风'}" for c in clues)
    if score == 3:
        read = f"三条线索一致向好（{parts}）—— 外部环境、增量资金、产业资本同时顺风。"
    elif score == 0:
        read = f"三条线索一致偏弱（{parts}）—— 外部、增量、产业资本三个层面都没有支撑。"
    else:
        read = (f"{score}/3 条顺风（{parts}）—— 线索不同向。"
                + ("顺风的是外部环境与产业资本、不顺风的是增量资金：典型的"
                   "**内资托底、增量未至**，行情更可能是存量博弈。"
                   if {c["key"] for c in no} == {"fund"} and score == 2 else
                   "背离本身就是读数：此时任何单条线索都不该被独立采信。"))
    return {"score": score, "total": len(clues), "level": level, "tone": tone, "reading": read,
            "supportive": [c["key"] for c in ok], "against": [c["key"] for c in no]}


def _card_verdict(fx: dict, fund: dict, rep: dict, temp: dict) -> dict:
    """卡片顶部一句话结论（口径在后端生成 —— 见 registry.py 卡片墙契约 ①）"""
    if fx.get("mid") is None:
        return {"headline": "资金温度数据未就绪", "detail": "", "tone": "normal"}
    head = f"资金温度{temp['level']}"
    fxw = _fx_word(fx.get("pct"))
    bits = [fxw]
    if fund.get("avg3") is not None and fund.get("pct") is not None:
        bits.append(f"新发基金{'偏冷' if fund['pct'] <= 30 else '偏热' if fund['pct'] >= 70 else '中性'}"
                    f"（{fund['pct']:.0f}% 分位）")
    if rep.get("last90") is not None and rep.get("pct") is not None:
        bits.append(f"回购{'升温' if rep['pct'] >= 70 else '降温' if rep['pct'] <= 30 else '平稳'}"
                    f"（{rep['pct']:.0f}% 分位）")
    head += "：" + "、".join(bits)
    detail = (f"美元/人民币 {fx['mid']}｜近 3 完整月新发 {fund['last3']['shares']:.0f} 亿份"
              f"（权益占 {fund['last3']['eq_pct']}%）｜近 90 天新启动回购 {rep['last90']} 个计划"
              if fund.get("last3") and rep.get("last90") is not None else "")
    return {"headline": head, "detail": detail, "tone": temp["tone"]}


# ---------------- 主装配 ----------------

@ttl_cache(600)
def funding_temperature(as_of: str | None = None) -> dict:
    """资金温度（/api/analysis/funding-temperature）

    参数
    - as_of：历史回放锚点 YYYY-MM-DD；非空时三条线都只取该日及之前的数据
    """
    fx = _fx(as_of)
    fund = _fund(as_of)
    rep = _repurchase(as_of)
    if fx.get("as_of") is None and not fund.get("monthly") and rep.get("last90") is None:
        return {"as_of": None, "note": "三条资金线索均无数据，等待采集器首跑"}

    # ---- ④ 三条线索 ----
    clues = [
        {"key": "fx", "name": "外部环境（人民币汇率）",
         "value": fx.get("mid"), "pct": fx.get("pct"), "window": "近一年",
         # 分位低 = 人民币强 → 对外资流入是顺风
         "supportive": (None if fx.get("pct") is None else fx["pct"] <= 40),
         "label": "美元/人民币中间价",
         "reading": (f"{_fx_word(fx.get('pct'))}（近一年 {fx['pct']}% 分位，"
                     f"20 日 {'升值' if (fx.get('chg20') or 0) < 0 else '贬值'} "
                     f"{abs(fx.get('chg20') or 0)}%）。"
                     "⚠️ 汇率分位低 = 美元便宜 = 人民币强；这里只把“人民币偏强”记为顺风，"
                     "因为升值通常伴随外资流入意愿回升 —— 但它对出口链是逆风，"
                     "故这是**资金面**口径的顺风，不是全市场的利好。"
                     if fx.get("pct") is not None else "汇率数据未就绪。")},
        {"key": "fund", "name": "增量资金（新发基金）",
         "value": fund.get("avg3"), "pct": fund.get("pct"),
         "window": f"近 {DEFAULT_MONTHS} 个月",
         # 分位 >= 50 视为顺风（增量资金到位）
         "supportive": (None if fund.get("pct") is None else fund["pct"] >= 50),
         "label": "近 3 完整月月均新发份额",
         "reading": (f"近 3 个完整月（{'、'.join(fund['last3']['months'])}）合计 "
                     f"{fund['last3']['shares']} 亿份，月均 {fund['avg3']} 亿份，"
                     f"处于近 {DEFAULT_MONTHS} 个月的 {fund['pct']}% 分位；"
                     f"其中权益类占 {fund['last3']['eq_pct']}%。"
                     "公募新发是**居民增量资金**最直接的读数，但它滞后于行情 ——"
                     "发行遇冷往往是过去一段时间赚钱效应差的**结果**，而非未来下跌的原因。"
                     if fund.get("pct") is not None else "基金发行数据未就绪。")},
        {"key": "rep", "name": "产业资本（上市公司回购）",
         "value": rep.get("last90"), "pct": rep.get("pct"),
         "window": f"近 {DEFAULT_MONTHS} 个月",
         # 分位 >= 50 视为顺风（产业资本在托底）
         "supportive": (None if rep.get("pct") is None else rep["pct"] >= 50),
         "label": f"近 {ROLL_DAYS} 天新启动回购计划数",
         "reading": (f"近 {ROLL_DAYS} 天新启动回购计划 {rep['last90']} 个"
                     f"（近 {DEFAULT_MONTHS} 个月 {rep['pct']}% 分位，区间 "
                     f"{rep['min']}~{rep['max']}）；其中「完成实施」"
                     f"{next((x['n'] for x in (rep.get('progress') or []) if x['progress'] == '完成实施'), 0)} 个、"
                     f"「实施中」{next((x['n'] for x in (rep.get('progress') or []) if x['progress'] == '实施中'), 0)} 个。"
                     "回购是**产业资本**（最了解自家公司的人）的真金白银，"
                     "但它同时是“股价跌到公司自己受不了”的产物 —— 故只作为托底证据，"
                     "不能单独当作看涨信号。"
                     if rep.get("pct") is not None else "回购数据未就绪。")},
    ]
    temp = _temperature([c for c in clues if c["supportive"] is not None])
    rep_done_n = next((x["n"] for x in (rep.get("progress") or [])
                       if x["progress"] == "完成实施"), None)

    # ---- KPI（card_rank 1~3 上卡片；半宽卡放 3 个）----
    # 三个盒子各答一个正交问题：外部环境如何（汇率）/ 增量资金够不够（新发）/ 产业资本在不在（回购）。
    # tone：三条线都不是"某个资产在涨跌"，一律 neutral（主色蓝、不带正号）——
    #      汇率染红绿会让"人民币贬值"看起来像利好（A 股铁律是红涨），语义会彻底错掉。
    #      （见 registry.py 卡片墙契约第 ④ 条）
    kpis = [
        {"key": "fx_usdcny", "card_rank": 1, "label": "美元/人民币（中间价）",
         "value": fx.get("mid"), "unit": "", "tone": "neutral",
         "status": (f"{_fx_word(fx['pct'])}｜20日 {('%+.2f%%' % fx['chg20']) if fx.get('chg20') is not None else '--'}"
                    if fx.get("pct") is not None else "数据未就绪"),
         "pct": fx.get("pct"), "scale": scale(fx.get("pct"), "近一年"),
         "highlight": is_extreme(fx.get("pct")), "anchor": "ft-fx",
         "hint": "央行美元兑人民币中间价（元/1 美元）。⚠️ 分位越低 = 美元越便宜 = **人民币越强**，"
                 "方向与直觉相反，看刻度条时以「区间最低 = 人民币最强」为准"},
        {"key": "fund_avg3", "card_rank": 2, "label": "新发基金份额（近3完整月月均）",
         "value": fund.get("avg3"), "unit": "亿份", "tone": "neutral",
         "status": (f"近3月合计 {fund['last3']['shares']:.0f} 亿份｜权益占 {fund['last3']['eq_pct']}%"
                    if fund.get("last3") else "数据未就绪"),
         "pct": fund.get("pct"), "scale": scale(fund.get("pct"), f"近 {DEFAULT_MONTHS} 个月"),
         "highlight": is_extreme(fund.get("pct")), "anchor": "ft-fund",
         "hint": "近 3 个**完整月**成立的基金募集份额月均值（成立口径，不是认购口径）。"
                 "它是居民增量资金最直接的读数；⚠️ 该指标滞后于行情 —— 发行遇冷是过去赚钱效应差的结果"},
        {"key": "rep_last90", "card_rank": 3, "label": f"新启动回购（近{ROLL_DAYS}天）",
         "value": rep.get("last90"), "unit": "个", "tone": "neutral",
         # ⚠️ status 里带"近 36 个月"是必须的：完成实施数/金额是**窗口内累计**，
         #    与 headline 的"近 90 天"差 12 倍，不标周期就会被读成"90 天回购了 3405 亿"。
         "status": (f"{'升温' if (rep.get('pct') or 0) >= 70 else '降温' if (rep.get('pct') or 0) <= 30 else '平稳'}"
                    f"｜近{DEFAULT_MONTHS}个月完成实施 {rep_done_n} 个 / {rep['done_amount_yi']} 亿"
                    if rep.get("pct") is not None and rep.get("done_amount_yi") is not None
                    else "数据未就绪"),
         "pct": rep.get("pct"), "scale": scale(rep.get("pct"), f"近 {DEFAULT_MONTHS} 个月"),
         "highlight": is_extreme(rep.get("pct")), "anchor": "ft-rep",
         "hint": "近 90 天**新启动**的回购计划数（按回购起始时间口径）。"
                 "⚠️ 不能用“最新公告日”统计 —— 同一计划的公告日会被后续公告覆盖，"
                 "会让历史月份被抽空、近期永远处于最高分位（实测该口径分位恒为 100%，无区分度）"},
        # 未标 card_rank：详情页完整呈现
        {"key": "fund_eq_pct", "label": "新发基金权益占比（近3完整月）",
         "value": (fund["last3"]["eq_pct"] if fund.get("last3") else None), "unit": "%",
         "tone": "neutral", "status": "⑤ 结构分解：钱进了权益还是固收", "pct": None, "scale": None,
         "highlight": False, "anchor": "ft-fund",
         "hint": "近 3 个完整月新成立基金中，权益类（股票/偏股/灵活/平衡）份额占全部募集份额的比例。"
                 "与总份额配合读：总份额低而权益占比高，说明“钱少但偏好进攻”"},
        {"key": "rep_progress", "label": "回购进度构成（实施中）",
         "value": next((x["n"] for x in (rep.get("progress") or []) if x["progress"] == "实施中"), None),
         "unit": "个", "tone": "neutral", "status": "⑤ 结构分解：在推进还是在收尾",
         "pct": None, "scale": None, "highlight": False, "anchor": "ft-rep",
         "hint": "当前处于「实施中」的回购计划数。⚠️ 它是**快照结构**、没有历史序列，"
                 "故不下发分位刻度 —— 老计划会自然沉淀为「完成实施」，新计划多则「实施中」占比高"},
    ]

    return {
        "as_of": fx.get("as_of") or (fund.get("window") or {}).get("end"),
        "is_replay": bool(as_of),
        "kpis": kpis,
        "fx": fx, "fund": fund, "repurchase": rep,
        "clues": clues, "temperature": temp,
        "verdict": _card_verdict(fx, fund, rep, temp),
        "note": FUNDING_TEMPERATURE_NOTE,
    }
