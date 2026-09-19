#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""分析模块：跨市场对照（美股 / 中国香港 / A股）

🔒 第一原则「数据用来比较才有意义」（设计规范 §1.0）：
本模块落的是参照系 ① **同类横比**（各市场之间谁强谁弱）、
③ **基准超额**（A股相对美股是真强还是普跌里跌得少）、
② **自身纵比**（各市场 20 日收益处于自己的近一年什么位置），
核心是 ④ **交叉印证**（隔夜美股 ↔ 次日 A股：外部信息到底能不能传导进来）。

**为什么需要这个模块**：`overseas_index_daily` 自 2004 年起每日采集（20,367 行，四个指数），
但此前只有采集器与 DQ 规则在写、**没有任何视图消费**（典型"只进不出"）。
跨市场对照的价值在于：A股经常被"隔夜美股怎么样"解释，但这个说法**从来没被量化过** ——
本模块把「隔夜传导强度」做成一个可跟踪的分位指标，让"外围影响"从叙事变成读数。

数据与口径（⚠️ 三条已实测的坑，勿回退）：
1. **交易日不同步**：美股与 A股 的当地交易日不重合（时差 + 各自休市）。隔夜配对规则是
   「对每个 A股 交易日 d，取严格早于 d 的最近一个 SPX 交易日」—— 不能用自然日减 1 天，
   否则每逢周末与长假就整片对不上（实测：自然日 −1 天配对只有约 62% 的日期能配上）。
2. **恒生指数起点是 2013-08-20**，比 SPX/DJI/IXIC（2004-01-02）晚 9 年。
   做长窗口统计前必须按各指数自身可取的行数收缩，不能统一用一个长度
   （否则恒生会取到空序列，或者用别的指数的长度掩盖了自己的缺失）。
3. **各市场"一天"的定义不同**：美股收盘对应 A股 次日开盘前，"隔夜"是信息领先；
   而恒生与 A股 交易时段部分重叠，"隔夜"含义不同。故隔夜传导只用 **SPX × 中证全指** 这一对，
   不把恒生混进来 —— 混算会把两个不同的机制平均掉，得到没有解释力的中间值。
"""
from __future__ import annotations

import logging

from ..db import query_all
from ._cache import ttl_cache
from ._kpi import corr, f, is_extreme, pctile, r, scale

logger = logging.getLogger(__name__)

# 24 个自然日 ≈ 17 个交易日的 20 日收益（用交易日而非自然日，见 sector_rotation 的同类处理）
RET_LAG = 20
# 分位窗口：近一年（见文件头注释）。因为是重叠窗口的 20 日收益，样本量取 250
WINDOW_1Y = 250
# 隔夜配对的滚动窗口（交易日）
ROLL_DAYS = 60

# 指数市场白名单（列名/表名由内部传入，不接受外部输入）
OVERSEAS: list[tuple[str, str, str]] = [
    ("SPX", "标普500", "美国"),
    ("IXIC", "纳斯达克综合", "美国"),
    ("DJI", "道琼斯工业", "美国"),
    ("HSI", "恒生指数", "中国香港"),
]
A_SHARE = ("000985", "中证全指")
# 隔夜传导只用这一对（见文件头坑 3）
OVERNIGHT_PAIR = ("SPX", "标普500")

# 图表配色：主色蓝给 A股（"我们"），其余走蓝/灰梯度。
# ⚠️ 刻意不用金色 —— 金色在本项目专供"亮点/极值"标记（蓝骨金魂：金色 ≤10% 强调），
#    拿它当普通折线色会破坏"亮得少才算信号"的纪律。
CHART_COLORS = {"000985": "#185FA5", "SPX": "#7FA9CD", "IXIC": "#B9C6D4", "HSI": "#6B7280"}

CROSS_MARKET_NOTE = (
    "本模块回答三件事：**外面在涨还是跌、我们相对外面强还是弱、外面的信息能不能传导进来**。"
    "⚠️ 绝对收益跨期不可比，故每个市场的 20 日收益都配一个「自身近一年分位」；"
    "而「隔夜传导强度」用**同向率**（隔夜美股与次日 A股 同涨同跌的比例）而非相关系数表述 —— "
    "同向率有直接的行为含义（“跟不跟”），相关系数要读者自己换算。相关系数仍一并给出作为参照。"
    "⚠️ 恒生指数自 2013-08 才有数据，长窗口统计会按各指数自身长度收缩。"
)


def _series(code: str, n: int, as_of: str | None) -> list[tuple[str, float]]:
    """取某指数最近 n 个交易日收盘价（升序）。海外指数走 overseas_index_daily，A股 走 dc_index_market"""
    table = "dc_index_market" if code == A_SHARE[0] else "overseas_index_daily"
    cond = "trade_date <= %s AND" if as_of else ""
    args: list = [as_of] if as_of else []
    rows = query_all(
        f"SELECT trade_date, close FROM {table} WHERE {cond} index_code = %s AND close > 0 "
        "ORDER BY trade_date DESC LIMIT %s",
        args + [code, n])
    return [(str(x["trade_date"]), float(x["close"])) for x in reversed(rows)]


def _profile(closes: list[float]) -> dict:
    """由收盘序列导出：当日涨跌 / 20 日收益 / 60 日收益 / 20 日收益的近一年分位"""
    if len(closes) < RET_LAG + 2:
        return {"change_pct": None, "ret_20": None, "ret_60": None, "ret_20_pct": None}
    cur = closes[-1]
    change = (cur / closes[-2] - 1) * 100
    r20 = (cur / closes[-1 - RET_LAG] - 1) * 100
    r60 = (cur / closes[-1 - 60] - 1) * 100 if len(closes) > 61 else None
    # 滚动 20 日收益序列 → 当前值在其中的分位（重叠窗口，与 market_wind 同口径）
    series = [(closes[i] / closes[i - RET_LAG] - 1) * 100 for i in range(RET_LAG, len(closes))]
    win = series[-WINDOW_1Y:]
    return {"change_pct": r(change, 2), "ret_20": r(r20, 2), "ret_60": r(r60, 2),
            "ret_20_pct": pctile(win, r20)}


def _overnight(n: int, as_of: str | None) -> dict:
    """隔夜传导：标普500 前夜收益 ↔ 中证全指次日收益

    配对规则见文件头坑 1：对每个 A股 交易日 d，取**严格早于 d** 的最近一个 SPX 交易日。
    """
    spx = _series("SPX", n + 5, as_of)
    cn = _series(A_SHARE[0], n + 5, as_of)
    if len(spx) < 30 or len(cn) < 30:
        return {"same_rate": None, "corr": None, "series": {"dates": [], "values": []},
                "avg_overseas": None, "avg_local": None, "pairs": 0}
    # ⚠️ 别写成 `for i, d in enumerate(spx)` —— spx 的元素是 (date, close) 元组，
    #    enumerate 给出的第二项是**元组本身**而不是日期，会让 key 变成 (date, close)。
    #    因为元组比较先比日期，配对逻辑会"碰巧"仍然正确、只在 dates 输出里暴露成嵌套数组，
    #    属于最难发现的那类错（结果对、结构错）。显式用下标取 [0]。
    s_ret = {spx[i][0]: (spx[i][1] / spx[i - 1][1] - 1) * 100 for i in range(1, len(spx))}
    c_ret = {cn[i][0]: (cn[i][1] / cn[i - 1][1] - 1) * 100 for i in range(1, len(cn))}
    s_dates = sorted(s_ret)
    pairs: list[tuple[str, float, float]] = []
    for d in sorted(c_ret):
        prev = [x for x in s_dates if x < d]
        if prev:
            pairs.append((d, s_ret[prev[-1]], c_ret[d]))
    if len(pairs) < ROLL_DAYS + 1:
        return {"same_rate": None, "corr": None, "series": {"dates": [], "values": []},
                "avg_overseas": None, "avg_local": None, "pairs": len(pairs)}

    def _same(sub) -> float:
        return sum(1 for _, x, y in sub if (x > 0) == (y > 0)) / len(sub) * 100

    # 滚动 60 日同向率（用于分位与图表）；latest 即"当前传导强度"
    roll = [(pairs[i][0], _same(pairs[i - ROLL_DAYS + 1:i + 1])) for i in range(ROLL_DAYS - 1, len(pairs))]
    same_rate = roll[-1][1]
    win = [v for _, v in roll[-WINDOW_1Y:]]
    same_pct = pctile(win, same_rate)
    tail = pairs[-WINDOW_1Y:]
    return {
        "same_rate": r(same_rate, 1),
        "same_rate_250": r(_same(tail), 1),
        "same_rate_pct": same_pct,
        "corr": r(corr([x for _, x, _ in tail], [y for _, _, y in tail]), 3),
        "avg_overseas": r(sum(x for _, x, _ in tail) / len(tail), 3),
        "avg_local": r(sum(y for _, _, y in tail) / len(tail), 3),
        "series": {"dates": [d for d, _ in roll[-WINDOW_1Y:]],
                   "values": [r(v, 1) for _, v in roll[-WINDOW_1Y:]]},
        "pairs": len(pairs),
    }


def _card_verdict(cn: dict, gap_us, overnight: dict) -> dict:
    """卡片顶部一句话结论（口径在后端生成 —— 见 registry.py 卡片墙契约 ①）

    tone：**只表达异常程度**。本模块是"对照"型，输出的是相对关系而非机会判断，
    故不使用 opportunity（金色）—— 金色在本项目专供亮点/极值信号，
    给一个纯观察型模块染金会让"亮"失去筛选意义。
    仅当隔夜传导强度进近一年极值区（>=90 分位 = 联动异常强 / <=10 = 传导几近失效）时转琥珀：
    此时"外围影响"这个叙事本身在走样，任何单向外推都不可靠。
    """
    c = cn.get("ret_20")
    if c is None:
        return {"headline": "跨市场数据未就绪", "detail": "", "tone": "normal"}
    head = f"A股 20 日 {c:+.2f}%"
    if gap_us is not None:
        head += f"、{'跑赢' if gap_us >= 0 else '落后'}美股 {abs(gap_us):.2f}pp"
    sr, sp = overnight.get("same_rate"), overnight.get("same_rate_pct")
    if sr is not None:
        head += f"、隔夜传导 {sr:.0f}%"
        if sp is not None:
            head += f"（近一年 {sp:.0f}% 分位）"

    tone = "normal"
    if sp is not None and (sp >= 90 or sp <= 10):
        tone = "caution"

    ov = overnight.get("overseas") or []
    detail = "｜".join(f"{m['name']} {m['ret_20']:+.2f}%" for m in ov) if ov else ""
    return {"headline": head, "detail": detail, "tone": tone}


@ttl_cache(600)
def cross_market(as_of: str | None = None, trend_days: int = 500) -> dict:
    """跨市场对照（/api/analysis/cross-market）

    参数
    - as_of：历史回放锚点 YYYY-MM-DD；非空时只取该日及之前的数据
    - trend_days：归一化走势图回看的交易日数（各市场按自身长度收缩）
    """
    need = max(WINDOW_1Y + RET_LAG + 5, min(int(trend_days), 1200))
    markets: list[dict] = []
    closes_map: dict[str, list[tuple[str, float]]] = {}
    for code, name, region in OVERSEAS:
        ser = _series(code, need, as_of)
        closes_map[code] = ser
        p = _profile([c for _, c in ser])
        markets.append({"code": code, "name": name, "region": region,
                        "last_close": r(ser[-1][1], 2) if ser else None,
                        "last_date": ser[-1][0] if ser else None,
                        "start_date": ser[0][0] if ser else None,
                        "rows": len(ser), **p})
    cn_ser = _series(A_SHARE[0], need, as_of)
    closes_map[A_SHARE[0]] = cn_ser
    cn = {"code": A_SHARE[0], "name": A_SHARE[1], "region": "A股",
          "last_close": r(cn_ser[-1][1], 2) if cn_ser else None,
          "last_date": cn_ser[-1][0] if cn_ser else None,
          "start_date": cn_ser[0][0] if cn_ser else None, "rows": len(cn_ser),
          **_profile([c for _, c in cn_ser])}
    if cn["ret_20"] is None:
        return {"as_of": None, "note": "dc_index_market 缺少中证全指（000985）行情，无法做基准超额"}

    data_as_of = cn["last_date"]
    # ③ 基准超额：A股 − 各市场（同期同窗口的 20 日收益差）
    by_code = {m["code"]: m for m in markets}
    gaps = [{"code": m["code"], "name": m["name"], "region": m["region"],
             "gap_pp": r(cn["ret_20"] - m["ret_20"], 2) if m["ret_20"] is not None else None}
            for m in markets]
    gaps.sort(key=lambda x: (x["gap_pp"] is None, -(x["gap_pp"] or 0)))

    overnight = _overnight(max(400, min(int(trend_days), 1200)), as_of)
    overnight["overseas"] = markets                     # 供 verdict 拼 detail（避免二次查询）
    overnight["local"] = {"name": A_SHARE[1], "ret_20": cn["ret_20"]}

    # 归一化走势（base=100）：跨市场可比的前提是把绝对点位统一成指数。
    # ⚠️ **必须按日期对齐，不能按下标对齐**：四个市场的交易日历不同（时差 + 各自休市），
    #    同样的 500 根 K 线覆盖的日期区间并不相同（实测 A股 500 根回到 2024-08-28，
    #    标普 500 根只回到 2024-09-20）。按下标铺到同一条 x 轴上会把相差近一个月的日期
    #    画成同一时刻，产生一条看似合理、实则错位的"领先/落后"曲线 —— 而错位方向恰好
    #    会被读成"美股领先 A股"。故取日期并集 + 前值填充，让每个市场在别人的非交易日
    #    沿用自己最近一次收盘（这也是"当时已知信息"的正确语义）。
    n_chart = max(60, min(int(trend_days), 1200))
    codes = (A_SHARE[0], "SPX", "IXIC", "HSI")
    cmap = {c: dict(closes_map.get(c) or []) for c in codes}
    union = sorted({d for c in codes for d in cmap[c]})[-n_chart:]
    chart: dict = {"dates": union, "series": []}
    for code in codes:
        m = cmap[code]
        vals: list[float | None] = []
        last: float | None = None
        for d in union:
            if d in m:
                last = m[d]
            vals.append(last)                      # 前值填充（见上）
        base = next((v for v in vals if v is not None), None)
        if base is None:
            continue
        name = cn["name"] if code == A_SHARE[0] else by_code[code]["name"]
        chart["series"].append({
            "name": name, "code": code, "color": CHART_COLORS[code],
            "base_date": next(d for d, v in zip(union, vals) if v is not None),
            "values": [None if v is None else r(v / base * 100, 2) for v in vals],
        })

    gap_us = next((g["gap_pp"] for g in gaps if g["code"] == "SPX"), None)
    gap_hk = next((g["gap_pp"] for g in gaps if g["code"] == "HSI"), None)

    # KPI（card_rank 1~3 上卡片；半宽卡放 3 个）——三个盒子各答一个正交问题：
    #   我们在涨还是跌（cn_ret20）/ 相对外面强还是弱（gap_cn_us）/ 外面能不能传导进来（overnight_same）
    # tone：cn_ret20 是**真正的行情涨跌**→ updown（红涨绿跌，A 股铁律）；
    #      gap_cn_us 是**跨市场收益差**→ diff（主色蓝 + 保留正负号，见契约第 ④ 条）；
      #      overnight_same 是占比类无量纲量 → neutral。
    kpis = [
        {"key": "cn_ret20", "card_rank": 1, "label": "A股中证全指（20日）", "value": cn["ret_20"],
         "unit": "%", "tone": "updown",
         "status": f"当日 {cn['change_pct']:+.2f}%" if cn["change_pct"] is not None else "数据未就绪",
         "pct": cn["ret_20_pct"], "scale": scale(cn["ret_20_pct"], "近一年"),
         "highlight": is_extreme(cn["ret_20_pct"]), "anchor": "cm-trend",
         "hint": "中证全指 20 个交易日收益。它是本模块的**基准腿**：所有“跑赢/落后”都以它为被减数"},
        {"key": "gap_cn_us", "card_rank": 2, "label": "A股 − 标普500（20日超额）", "value": gap_us,
         "unit": "pp", "tone": "diff",
         "status": ("跑赢美股" if (gap_us or 0) >= 0 else "落后美股") if gap_us is not None else "数据未就绪",
         "pct": None, "scale": None, "highlight": False, "anchor": "cm-gap",
         "hint": "中证全指 20 日收益 − 标普500 同期 20 日收益。"
                 "⚠️ 正值不一定代表“我们强”——普跌行情里跌得少也会是正超额，"
                 "务必与第一个盒子（自身 20 日收益）成对阅读"},
        {"key": "overnight_same", "card_rank": 3, "label": f"隔夜传导（滚动{ROLL_DAYS}日同向率）",
         "value": overnight.get("same_rate"), "unit": "%", "tone": "neutral",
         "status": (f"近 250 日 {overnight['same_rate_250']}%｜相关系数 {overnight['corr']}"
                    if overnight.get("same_rate") is not None else "数据未就绪"),
         "pct": overnight.get("same_rate_pct"),
         "scale": scale(overnight.get("same_rate_pct"), "近一年"),
         "highlight": is_extreme(overnight.get("same_rate_pct")), "anchor": "cm-overnight",
         "hint": "标普500 前夜涨跌与中证全指次日涨跌**方向相同**的比例。"
                 "50% = 完全无关，持续高于 60% 说明外部信息确实在传导；"
                 "⚠️ 它衡量的是“跟不跟”，不衡量幅度（幅度看相关系数）"},
        # 未标 card_rank：详情页完整呈现
        *[{"key": f"ret20_{m['code']}", "label": f"{m['name']}（20日）", "value": m["ret_20"],
           "unit": "%", "tone": "updown",
           "status": f"当日 {m['change_pct']:+.2f}%" if m["change_pct"] is not None else "数据未就绪",
           "pct": m["ret_20_pct"], "scale": None, "highlight": False, "anchor": "cm-trend",
           "hint": f"{m['region']} · {m['name']} 20 个**当地交易日**收益（各市场交易日不同步，"
                   "不构成严格同期对比，故只作侧面参照）"} for m in markets],
        {"key": "gap_cn_hk", "label": "A股 − 中国香港恒生（20日超额）", "value": gap_hk,
         "unit": "pp", "tone": "diff", "status": "与港股比", "pct": None, "scale": None,
         "highlight": False, "anchor": "cm-gap",
         "hint": "中证全指 − 恒生指数 20 日收益。恒生与 A股 交易时段部分重叠，"
                 "相关性天然高于美股，超额也更小 —— 这是正常的，不是信号"},
    ]

    return {
        "as_of": data_as_of, "is_replay": bool(as_of),
        "kpis": kpis,
        "cn": cn, "markets": markets, "gaps": gaps,
        "overnight": {k: v for k, v in overnight.items() if k not in ("overseas", "local")},
        "chart": chart,
        "verdict": _card_verdict(cn, gap_us, overnight),
        "note": CROSS_MARKET_NOTE,
    }
