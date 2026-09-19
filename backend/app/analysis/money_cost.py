#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""分析模块：钱贵不贵（银行间资金价格 / 期限结构 / 政策利率）

🔒 第一原则「数据用来比较才有意义」（设计规范 §1.0）：
本模块落的是参照系 ② **自身纵比**（Shibor 3M 现在处于近一年什么位置）、
① **同类横比**（ON→1W→1M→3M 四个期限之间的利率阶梯）、
⑤ **结构分解**（陡/平/倒挂 —— 同样一个 3M 利率，曲线形状不同含义完全不同），
并在末尾做一次 ④ **交叉印证**（政策利率按兵不动 ↔ 市场利率是否在动；
资金价格 ↔ 权益位置）。

**为什么需要这个模块**：`interbank_rate_daily` 自 2006 年起每日采集（4,984 行，3M 无缺失），
但此前只有采集器与 DQ 规则在写、**没有任何视图消费**（典型"只进不出"）。
而"钱贵不贵"是 A 股最上游的定价变量之一 —— 利率是估值分母，且它的**分位**比绝对值重要得多
（2013 年的 1.4% 与 2026 年的 1.4%，两市含义完全不同）。

数据与口径（⚠️ 三条已实测的坑，勿回退）：
1. **分位窗口用「近一年（250 个交易日）」而非全历史**：Shibor 2006 年起步，穿越了 2008
   与 2013 两次钱荒，全历史窗口会把当下永远压在极低分位（实测 3M 在全历史分位 <10%，
   在近一年是 34.6%）—— 那不是"钱便宜"，那是"没经历过钱荒"，属窗口选错导致的伪信号。
   窗口文案由后端通过 `scale.label` 下发（见 `_kpi.scale`）。
2. **LPR 有新旧两套机制**：2013-2019 是旧的"贷款基础利率"（每日报价、会天天动），
   2019-08-20 起才是新 LPR（每月 20 日报价）。所以"连续未动月数"只在**新机制期内**才有意义 ——
   本模块只从最后一个**变动点**起算，天然落在新机制期，不需要额外判断年份，但别去看
   2014-2019 的日频跳变当成"政策频繁调整"。
3. **3M 是四个期限里唯一从 2006 年至今完整无缺的**（ON/1W/1M 同为 4,984 行，LPR 1Y 3,220 行、
   5Y 1,766 行）。期限结构统一以 ON 为锚算差，不要拿 LPR 参与期限差（报价机制不同、频率不同）。
"""
from __future__ import annotations

import logging

from ..db import query_all, query_one
from ._cache import ttl_cache
from ._kpi import f, is_extreme, median, pctile, r, scale, std

logger = logging.getLogger(__name__)

# 分位窗口：近一年交易日数（见文件头坑 1）
WINDOW_1Y = 250
# 期限结构：短端 → 长端（列名白名单，不接受外部输入）
TERMS: list[tuple[str, str]] = [
    ("ON", "shibor_on"),
    ("1W", "shibor_1w"),
    ("1M", "shibor_1m"),
    ("3M", "shibor_3m"),
]

MONEY_COST_NOTE = (
    "本模块回答「钱贵不贵」：Shibor 3M 回答**绝对水平**（近一年什么位置），"
    "期限结构回答**资金预期**（曲线是陡还是平 —— 平坦说明短端没有溢价要求、"
    "市场预期资金持续宽松；倒挂则是流动性紧张的信号），"
    "LPR 回答**政策姿态**。⚠️ 利率的绝对值跨期不可比，所有判断一律走「近一年分位」，"
    "不看绝对值本身。"
)


# ---------------- 取数 ----------------

def _fetch(days: int, as_of: str | None) -> list[dict]:
    """按日期降序取最近 days 行（as_of 非空时只取该日及之前）"""
    cond = "WHERE trade_date <= %s" if as_of else ""
    args: list = [as_of] if as_of else []
    return query_all(
        "SELECT trade_date, shibor_on, shibor_1w, shibor_1m, shibor_3m, lpr_1y, lpr_5y "
        f"FROM interbank_rate_daily {cond} ORDER BY trade_date DESC LIMIT %s",
        args + [days])


def _lpr_state(as_of: str | None) -> dict:
    """LPR 当前值 + 连续未动月数 + 历史变动点（取全部历史，用于"上次调整是什么时候"）

    ⚠️ 只从**最后一个变动点**起算月数，天然规避新旧 LPR 机制混算（见文件头坑 2）。
    """
    cond = "WHERE lpr_1y IS NOT NULL" + (" AND trade_date <= %s" if as_of else "")
    args: list = [as_of] if as_of else []
    rows = query_all(
        "SELECT trade_date, lpr_1y, lpr_5y FROM interbank_rate_daily "
        f"{cond} ORDER BY trade_date DESC",
        args)
    if not rows:
        return {"lpr_1y": None, "lpr_5y": None, "since": None, "prev": None,
                "idle_months": None, "events": []}

    cur_1y = f(rows[0]["lpr_1y"])
    # rows 按日期降序：当前值占据前缀 rows[0..i-1]，首个不同值出现在 rows[i]
    i = 0
    while i < len(rows) and f(rows[i]["lpr_1y"]) == cur_1y:
        i += 1
    since = rows[i - 1]["trade_date"] if i > 0 else None       # 当前值首次生效日
    # ⚠️ prev = 旧取值的**最后一日**，不是「变动生效日」。LPR 在库里按交易日做前值填充，
    #    所以变动的生效日记录为新值首次出现的那天（= since），前一天仍记为旧值。
    #    前端展示变动日期时必须用 since，否则同一次调整会同时出现「05-19」和「05-20」两个日期。
    prev = ({"date": str(rows[i]["trade_date"]), "lpr_1y": f(rows[i]["lpr_1y"]),
             "lpr_5y": f(rows[i]["lpr_5y"])} if i < len(rows) else None)

    idle = None
    if since is not None:
        cur_d = rows[0]["trade_date"]
        idle = (cur_d.year * 12 + cur_d.month) - (since.year * 12 + since.month)

    # 变动点（升序，仅保留值发生变化的日期）
    asc = list(reversed(rows))
    events: list[dict] = []
    last = None
    for x in asc:
        v = f(x["lpr_1y"])
        if v is None:
            continue
        if last is None or v != last:
            events.append({"date": str(x["trade_date"]), "lpr_1y": v, "lpr_5y": f(x["lpr_5y"])})
            last = v
    return {"lpr_1y": cur_1y, "lpr_5y": f(rows[0]["lpr_5y"]), "since": str(since) if since else None,
            "prev": prev, "idle_months": idle, "events": events[-10:]}


# ---------------- 判读词 ----------------

def _level_word(p: float | None) -> str:
    """资金价格水平（分位越低 = 钱越便宜）"""
    if p is None:
        return "数据未就绪"
    if p <= 20:
        return "偏松（近一年低位）"
    if p <= 40:
        return "偏松"
    if p <= 60:
        return "中性"
    if p <= 80:
        return "偏紧"
    return "偏紧（近一年高位）"


def _shape_word(spread_bp: float | None, p: float | None) -> str:
    """期限结构形状：3M − ON 的利差，分位越高越陡"""
    if spread_bp is None:
        return "数据未就绪"
    if spread_bp < 0:
        return "倒挂（短端比长端贵）"
    if p is None:
        return "已就绪"
    if p <= 10:
        return "极度平坦"
    if p <= 30:
        return "偏平坦"
    if p <= 70:
        return "中性"
    if p <= 90:
        return "偏陡"
    return "极陡"


# ---------------- 卡片判读条 ----------------

def _card_verdict(level_pct, spread_bp, spread_pct, lpr: dict) -> dict:
    """卡片顶部一句话结论（口径在后端生成，前端只透传 —— 见 registry.py 卡片墙契约 ①）

    tone：资金价格进近一年低位（<=20 分位）→ 金（低估/机会侧：利率是估值分母，钱便宜对权益是顺风）；
          进高位（>=80）或**期限结构倒挂** → 琥珀（提醒）；其余蓝。
          倒挂单独提级，因为它不是"贵"而是"紧"—— 流动性紧张的定性完全不同。
    """
    head = f"资金价格{_level_word(level_pct)}"
    if level_pct is not None:
        head += f"（近一年 {level_pct:.0f}% 分位）"
    head += "、期限结构" + _shape_word(spread_bp, spread_pct)
    if spread_pct is not None:
        head += f"（{spread_pct:.0f}% 分位）"

    l1 = lpr.get("lpr_1y")
    idle = lpr.get("idle_months")
    if l1 is None:
        detail = "LPR 数据未就绪"
    elif idle is None:
        detail = f"LPR 1Y {l1:.2f}%"
    else:
        detail = f"LPR 1Y {l1:.2f}% 已连续 {idle} 个月未动"
        if lpr.get("since"):
            detail += f"（{lpr['since']} 起）"
    return {"headline": head, "detail": detail, "tone": _verdict_tone(level_pct, spread_bp)}


def _verdict_tone(level_pct, spread_bp) -> str:
    if spread_bp is not None and spread_bp < 0:
        return "caution"                       # 倒挂 = 流动性紧张信号
    if level_pct is None:
        return "normal"
    if level_pct <= 20:
        return "opportunity"
    if level_pct >= 80:
        return "caution"
    return "normal"


# ---- 三卡子判读（2026-09-19 拆卡）：每张卡只答一件事；底数与 _card_verdict 相同，零额外查询 ----

def _verdict_level(level_pct) -> dict:
    """q1 钱现在贵不贵 —— Shibor 3M 的近一年分位"""
    if level_pct is None:
        return {"headline": "资金价格数据未就绪", "detail": "", "tone": "normal"}
    head = f"资金价格{_level_word(level_pct)}（近一年 {level_pct:.0f}% 分位）"
    tone = "opportunity" if level_pct <= 20 else "caution" if level_pct >= 80 else "normal"
    return {"headline": head, "detail": "利率是估值分母：钱便宜对权益是顺风", "tone": tone}


def _verdict_expectation(spread_bp, spread_pct) -> dict:
    """q2 资金预期是松是紧 —— 期限利差（3M − 隔夜）的陡平与倒挂"""
    if spread_bp is None:
        return {"headline": "期限利差数据未就绪", "detail": "", "tone": "normal"}
    head = f"期限结构{_shape_word(spread_bp, spread_pct)}"
    if spread_pct is not None:
        head += f"（近一年 {spread_pct:.0f}% 分位）"
    # 倒挂不是「贵」而是「紧」—— 流动性紧张的定性，单独提级（与 _verdict_tone 同规）
    tone = "caution" if spread_bp < 0 else "normal"
    return {"headline": head,
            "detail": "利差越陡 = 短端越宽裕；**倒挂**（3M 低于隔夜）= 流动性紧张信号",
            "tone": tone}


def _verdict_policy(lpr: dict) -> dict:
    """q3 政策利率动没动 —— LPR 1Y 连续未动月数（政策姿态，非市场资金价格）"""
    l1 = lpr.get("lpr_1y")
    if l1 is None:
        return {"headline": "LPR 数据未就绪", "detail": "", "tone": "normal"}
    head = f"LPR 1Y {l1:.2f}%"
    if lpr.get("idle_months") is not None:
        head += f"、已连续 {lpr['idle_months']} 个月未动"
    detail = f"政策报价自 {lpr['since']} 起未变；与市场利率（Shibor）背离本身就是信息" if lpr.get("since") else ""
    return {"headline": head, "detail": detail, "tone": "normal"}


# ---------------- 主装配 ----------------

@ttl_cache(600)
def money_cost(as_of: str | None = None, trend_days: int = 500) -> dict:
    """钱贵不贵（/api/analysis/money-cost）

    参数
    - as_of：历史回放锚点 YYYY-MM-DD；非空时只取该日及之前的数据（复盘「那一天的资金面」）
    - trend_days：时序图回看的交易日数
    """
    days = max(WINDOW_1Y + 5, min(int(trend_days), 2000))
    rows = _fetch(days, as_of)
    if not rows:
        return {"as_of": None, "note": "interbank_rate_daily 尚无数据，等待 interbank_rate_sync 首跑"}
    cur = rows[0]
    data_as_of = str(cur["trade_date"])
    win = rows[:WINDOW_1Y]                     # 分位窗口 = 近 250 个交易日（含当日）
    base_1y = rows[WINDOW_1Y] if len(rows) > WINDOW_1Y else rows[-1]

    # ---- 期限结构（① 同类横比 + ⑤ 结构分解）----
    on_cur = f(cur["shibor_on"])
    curve: list[dict] = []
    for label, col in TERMS:
        vals = [f(x[col]) for x in win if x[col] is not None]
        cv = f(cur[col])
        curve.append({
            "term": label,
            "cur": r(cv, 4),
            "prev_year": r(f(base_1y[col]), 4),
            "min_1y": r(min(vals), 4) if vals else None,
            "max_1y": r(max(vals), 4) if vals else None,
            # 相对隔夜的利差（bp）：ON 自身为 0，作为锚
            "spread_bp": r((cv - on_cur) * 100, 1) if (cv is not None and on_cur is not None) else None,
        })

    s3_cur = f(cur["shibor_3m"])
    s3_vals = [f(x["shibor_3m"]) for x in win if x["shibor_3m"] is not None]
    pct_3m = pctile(s3_vals, s3_cur)

    spread_cur = (s3_cur - on_cur) * 100 if (s3_cur is not None and on_cur is not None) else None
    spread_vals = [(f(x["shibor_3m"]) - f(x["shibor_on"])) * 100
                   for x in win if x["shibor_3m"] is not None and x["shibor_on"] is not None]
    pct_spread = pctile(spread_vals, spread_cur)

    # 20 交易日方向（用交易日而非自然日：表本身按交易日聚集）
    chg20 = None
    if len(rows) > 20 and rows[20]["shibor_3m"] is not None and s3_cur is not None:
        chg20 = (s3_cur - f(rows[20]["shibor_3m"])) * 100

    lpr = _lpr_state(as_of)

    # 近一年振幅：3M 的高低差（bp）—— 用来说明"政策未动期间市场自己动了多少"
    amp_1y = r((max(s3_vals) - min(s3_vals)) * 100, 1) if s3_vals else None

    # ---- ④ 交叉印证一：政策 vs 市场 ----
    policy_market = {
        "lpr_idle_months": lpr.get("idle_months"),
        "lpr_since": lpr.get("since"),
        "market_amplitude_bp": amp_1y,
        "reading": None,
    }
    if lpr.get("idle_months") is not None and amp_1y is not None:
        policy_market["reading"] = (
            f"政策利率（LPR 1Y）已连续 {lpr['idle_months']} 个月按兵不动，"
            f"而市场利率（Shibor 3M）在这一年里自己走了 {amp_1y}bp 的振幅 —— "
            "说明当前是**政策观察期、市场自发定价**：资金面的边际变化来自市场供求而非政策调整，"
            "盯 Shibor 的边际变化比等 LPR 更能提前看到转向。"
        )
    elif lpr.get("idle_months") is not None:
        policy_market["reading"] = f"政策利率已连续 {lpr['idle_months']} 个月未动。"

    # ---- ④ 交叉印证二：资金价格 × 权益位置 ----
    # 利率是估值分母。单独看"钱便宜"没有交易含义，得跟"权益已经走到哪了"配着看：
    # 钱便宜 + 位置低 = 潜在机会；钱便宜 + 位置高 = 宽松已被 price in。
    eq_cond = "WHERE bench_pos_pct IS NOT NULL"
    eq_args: list = []
    if as_of:
        eq_cond += " AND trade_date <= %s"
        eq_args.append(as_of)
    else:
        eq_cond += " AND trade_date <= %s"
        eq_args.append(data_as_of)
    eq = query_one(f"SELECT trade_date, bench_pos_pct FROM market_style_daily {eq_cond} "
                   "ORDER BY trade_date DESC", eq_args)
    bench_pos = f(eq["bench_pos_pct"]) if eq else None
    equity_cross = {"bench_pos_pct": r(bench_pos, 1), "bench_date": str(eq["trade_date"]) if eq else None,
                    "reading": None}
    if pct_3m is not None and bench_pos is not None:
        cheap = pct_3m <= 40
        low_pos = bench_pos <= 40
        if cheap and low_pos:
            verdict, lvl = "钱便宜而权益位置也低 —— 分母（利率）与分子（位置）同时顺风", "opportunity"
        elif cheap and not low_pos:
            verdict, lvl = "钱便宜但权益位置不低 —— 宽松大概率已被 price in", "normal"
        elif not cheap and low_pos:
            verdict, lvl = "钱不便宜但权益位置低 —— 分母在收紧，位置低不足以单独构成机会", "caution"
        else:
            verdict, lvl = "钱不便宜且权益位置不低 —— 两个维度都不顺风", "caution"
        equity_cross["level"] = lvl
        equity_cross["reading"] = (
            f"Shibor 3M 近一年 {pct_3m}% 分位（{'偏便宜' if cheap else '偏贵'}），"
            f"中证全指位置 {bench_pos:.0f}% 分位（{'偏低' if low_pos else '偏高'}）：{verdict}。"
            "⚠️ 这是**状态叠加**而不是择时信号——利率传导到估值需要时间，"
            "两者同向只说明方向一致，不构成买卖依据。"
        )

    # ---- 时序（供图表）----
    # 主图只放三条 Shibor：LPR 1Y 在 3.0% 而 Shibor 在 1.4% 附近，同轴会把三条 Shibor
    # 压成底部一坨、失去可读性。政策利率另给一条**独立纵轴**的阶梯序列（lpr.step），
    # 日期轴与主图对齐，前端画第二张小图。
    chart_rows = rows[:min(len(rows), max(60, int(trend_days)))]
    chart_rows = list(reversed(chart_rows))
    history = {
        "dates": [str(x["trade_date"]) for x in chart_rows],
        "series": [
            {"name": "Shibor 3M", "key": "shibor_3m", "color": "#185FA5",
             "values": [r(f(x["shibor_3m"]), 4) for x in chart_rows]},
            {"name": "Shibor 1M", "key": "shibor_1m", "color": "#7FA9CD",
             "values": [r(f(x["shibor_1m"]), 4) for x in chart_rows]},
            {"name": "Shibor 隔夜", "key": "shibor_on", "color": "#9CA3AF",
             "values": [r(f(x["shibor_on"]), 4) for x in chart_rows]},
        ],
    }
    # 政策利率阶梯（与主图同一日期轴；LPR 只在每月 20 日变，画出来天然是阶梯）
    lpr["step"] = {
        "dates": history["dates"],
        "values": [r(f(x["lpr_1y"]), 4) for x in chart_rows],
        "values_5y": [r(f(x["lpr_5y"]), 4) for x in chart_rows],
    }

    # ---- KPI（card_rank 1~3 上卡片；半宽卡放 3 个）----
    # 三个盒子各答一个正交问题：钱贵不贵（水平）/ 资金预期陡不陡（结构）/ 政策动没动（姿态）。
    # tone：利率水平与利差都不是"某个资产在涨跌"，故全 neutral（主色蓝、不带正号）——
    #      利率上行/下行交给 status 文案与刻度条表达，染红绿会把"资金收紧"误读成"利好"。
    #      （见 registry.py 卡片墙契约第 ④ 条）
    kpis = [
        {"key": "shibor_3m", "card_rank": 1, "questions": ["q1"], "label": "Shibor 3M（资金价格）",
         "value": r(s3_cur, 2), "unit": "%", "tone": "neutral",
         "status": f"{_level_word(pct_3m)}｜20日 {('%+.1fbp' % chg20) if chg20 is not None else '--'}",
         "pct": pct_3m, "scale": scale(pct_3m, "近一年"), "highlight": is_extreme(pct_3m),
         "anchor": "mc-trend",
         "hint": "3 个月期 Shibor（银行间同业拆借利率），是 A 股最上游的定价变量之一："
                 "利率是估值分母。⚠️ 绝对值跨期不可比，判断“贵不贵”一律看近一年分位"},
        {"key": "term_spread", "card_rank": 2, "questions": ["q2"], "label": "期限利差（3M − 隔夜）",
         "value": r(spread_cur, 1), "unit": "bp", "tone": "neutral",
         "status": f"{_shape_word(spread_cur, pct_spread)}",
         "pct": pct_spread, "scale": scale(pct_spread, "近一年"), "highlight": is_extreme(pct_spread),
         "anchor": "mc-curve",
         "hint": "长端减短端的利差，衡量资金期限结构：越陡说明短端资金越宽裕、"
                 "越平说明市场预期资金持续宽松；**倒挂**（3M 比隔夜还便宜）是流动性紧张的信号"},
        {"key": "lpr", "card_rank": 3, "questions": ["q3"], "label": "LPR 1Y（政策利率）",
         "value": r(lpr.get("lpr_1y"), 2), "unit": "%", "tone": "neutral",
         "status": (f"已连续 {lpr['idle_months']} 个月未动"
                    if lpr.get("idle_months") is not None
                    else ("数据未就绪" if lpr.get("lpr_1y") is None else "--")),
         "pct": None, "scale": None, "highlight": False, "anchor": "mc-lpr",
         "hint": "贷款市场报价利率（1 年期），每月 20 日报价。它是政策姿态的观察窗，"
                 "但与市场资金价格（Shibor）不同步——两者背离本身就是信息"},
        # 未标 card_rank：详情页完整呈现，卡片墙不放（避免挤掉上面三个正交问题）
        {"key": "shibor_on", "label": "Shibor 隔夜",
         "value": r(on_cur, 2), "unit": "%", "tone": "neutral",
         "status": "短期资金面", "pct": pctile([f(x["shibor_on"]) for x in win if x["shibor_on"] is not None],
                                               on_cur),
         "scale": None, "highlight": False, "anchor": "mc-curve",
         "hint": "隔夜 Shibor。日间波动最大，是资金面松紧最敏感的读数，但噪声也最大"},
        {"key": "curve_dispersion", "label": "期限离散度",
         "value": r(std([c["cur"] for c in curve if c["cur"] is not None]), 4), "unit": "%",
         "tone": "neutral", "status": "四个期限的离散程度（越大=曲线越扭曲）",
         "pct": None, "scale": None, "highlight": False, "anchor": "mc-curve",
         "hint": "ON/1W/1M/3M 四个期限利率的标准差。与期限利差互补："
                 "利差只看两端，离散度能看出中间期限有没有异常拐点"},
    ]

    return {
        "as_of": data_as_of,
        "base_date_1y": str(base_1y["trade_date"]),
        "is_replay": bool(as_of),
        "kpis": kpis,
        "curve": curve,
        "history": history,
        "lpr": lpr,
        "policy_vs_market": policy_market,
        "equity_cross": equity_cross,
        "level_pct": pct_3m,
        "spread_pct": pct_spread,
        "spread_bp": r(spread_cur, 1),
        "chg20_bp": r(chg20, 1),
        "median_1y": r(median(s3_vals), 4),
        "verdict": _card_verdict(pct_3m, spread_cur, pct_spread, lpr),
        # 三卡子判读（2026-09-19 拆卡）：总览页三张分卡按 question 各取一条
        "verdicts": {
            "q1": _verdict_level(pct_3m),
            "q2": _verdict_expectation(spread_cur, pct_spread),
            "q3": _verdict_policy(lpr),
        },
        "note": MONEY_COST_NOTE,
    }
