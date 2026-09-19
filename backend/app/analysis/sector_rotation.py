#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""分析模块：板块轮动（申万行业 × 同花顺概念，含双侧口径对比）

🔒 第一原则「数据用来比较才有意义」（设计规范 §1.0）：
本模块落的是参照系 ① **同类横比**（行业之间谁强谁弱）与 ③ **基准超额**
（行业相对全指是真强还是水涨船高），并在末尾做一次 ④ **交叉印证**
（两个独立数据集的收益排行是否互证）。

数据与口径：
- **申万行业**：`stock_industry_sw`（一级 31 / 二级 131）× `stock_market_daily`
  个股收盘价 → 行业内个股**等权** 20 日收益均值。等权而非市值加权，与市场风向的
  六组口径保持一致（市值加权会被少数巨头主导，测不到「板块普遍性」）。
- **同花顺概念**：`ths_concept_market` 概念指数收盘价 → 每概念自身最近 21 个交易日的收益。
- **相对超额**：行业 20 日收益 − 市场基准组 20 日收益（`market_style_daily.ret_bench_20`）。

⚠️ 三个已实测的坑（都已在代码里处理，勿回退）：
1. **必须按 stock_code 顺序取数**：`stock_market_daily` 按 (stock_code, trade_date) 聚集，
   按 trade_date 等值取非索引列是随机回表（5 千行 15 秒）。本模块用
   `WHERE trade_date IN (d1,d2) ORDER BY stock_code` 强制走索引顺序扫描（实测 ~8 秒），
   再在 Python 里按股票配对。正因为这 8 秒，模块整体套了 TTL 缓存（见 _cache.py）。
2. **`stock_industry_sw.industry_type` 的取值是「申万一级」/「申万二级」**，
   不是「一级行业」；且一级与二级**混存同一列** `industry_name` —— 不过滤会把
   142 个行业混在一起算（实测踩到过）。
3. **概念源分批发布**：收盘后一段时间只有 ~190/375 个概念，晚 22:00 补班才齐。
   故概念必须锚定到「覆盖数达标」的最近交易日，不能用主数据源日期（否则样本缺一半、
   中位数失真：190 个概念时中位 -3.66% / 涨占比 8.4%，375 个时中位 -3.07% / 涨占比 15.8%）。
   另外概念指数存在异常值（实测 WiFi6 曾 +110%），须剔除 |20日收益|>60%。
"""
from __future__ import annotations

import logging

from ..db import query_all
from ._cache import ttl_cache

logger = logging.getLogger(__name__)

# 申万行业类型的实际取值（见文件头坑 2）
SW_TYPES = {"一级": "申万一级", "二级": "申万二级"}
DEFAULT_LEVEL = "一级"
# 概念异常值剔除阈值（%）
CONCEPT_OUTLIER = 60.0
# 概念「覆盖完整」下限（日常 375 个，分批发布时可能只有 ~190）
CONCEPT_FULL_MIN = 300
# 20 日收益的滞后交易日数
RET_LAG = 20

ROTATION_NOTE = (
    "两个口径是独立数据集（申万行业分类+个股等权 vs 同花顺概念指数），"
    "同向即为互证成立；打架本身就是信息——说明没有一致的市场叙事。"
    "两个口径都会「就近一年做比较」：行业给相对市场基准的超额，概念给上涨占比与中位收益。"
    "⚠️ 绝对收益跨期不可比，故本模块的结论一律成对给出（收益 + 超额 / 收益 + 占比），不单报数字。"
)


def _f(v) -> float | None:
    return None if v is None else float(v)


def _r(v, n: int = 2):
    return None if v is None else round(float(v), n)


def _median(vals: list[float]) -> float | None:
    v = sorted(vals)
    return None if not v else v[len(v) // 2]


def _std(vals: list[float]) -> float | None:
    if len(vals) < 2:
        return None
    m = sum(vals) / len(vals)
    return (sum((x - m) ** 2 for x in vals) / len(vals)) ** 0.5


def _trade_dates(as_of: str | None, n: int) -> list[str]:
    """交易日历（用 market_style_daily），按日期**降序**取 n 个"""
    cond = "WHERE trade_date <= %s" if as_of else ""
    args = [as_of] if as_of else []
    rows = query_all(f"SELECT trade_date FROM market_style_daily {cond} "
                     "ORDER BY trade_date DESC LIMIT %s", args + [n])
    return [str(r["trade_date"]) for r in rows]


def _industry_frame(d_old: str, d_cur: str) -> dict[str, list[float]]:
    """行业内个股 20 日收益（等权）

    ⚠️ 取数顺序即性能关键：`ORDER BY stock_code` 让优化器走索引顺序扫描（~8 秒），
    去掉 ORDER BY 会退化成 filesort + 随机回表（实测 20 秒）。见文件头坑 1。
    """
    rows = query_all(
        "SELECT stock_code, trade_date, close FROM stock_market_daily "
        "WHERE trade_date IN (%s, %s) AND close > 0 ORDER BY stock_code",
        [d_old, d_cur])
    cur_map: dict[str, float] = {}
    old_map: dict[str, float] = {}
    for r in rows:
        (cur_map if str(r["trade_date"]) == d_cur else old_map)[r["stock_code"]] = float(r["close"])
    if not cur_map:
        return {}
    return {c: (cv / old_map[c] - 1) * 100 for c, cv in cur_map.items()
            if old_map.get(c, 0) > 0}


def _industry_agg(level: str, d_old: str, d_cur: str) -> dict:
    """行业内个股等权 20 日收益（沪深两市快照配对）"""
    rets = _industry_frame(d_old, d_cur)
    if not rets:
        return {"items": {}, "count": 0}
    ind = query_all("SELECT stock_code, industry_name FROM stock_industry_sw WHERE industry_type = %s",
                    [SW_TYPES[level]])
    acc: dict[str, list[float]] = {}
    for r in ind:
        v = rets.get(r["stock_code"])
        if v is not None:
            acc.setdefault(r["industry_name"], []).append(v)
    return {"items": acc, "count": len(rets)}


def _concept_rank(as_of: str) -> dict:
    """概念 20 日收益排行（每概念自身最近 21 个交易日）"""
    anchor_rows = query_all(
        "SELECT trade_date FROM ths_concept_market WHERE trade_date <= %s "
        "GROUP BY trade_date HAVING COUNT(DISTINCT concept_code) >= %s "
        "ORDER BY trade_date DESC LIMIT 1", [as_of, CONCEPT_FULL_MIN])
    if not anchor_rows:
        return {"as_of": None, "items": [], "outliers": 0}
    anchor = str(anchor_rows[0]["trade_date"])
    from datetime import date, timedelta
    y, m, d = (int(x) for x in anchor.split("-"))
    start = str(date(y, m, d) - timedelta(days=150))
    # 收益：每概念自身最近 21 个交易日（rn=1 vs rn=21）—— 对源的「分批发布」不敏感
    rows = query_all(
        "SELECT concept_code, "
        "  (MAX(CASE WHEN rn=1 THEN close END)/MAX(CASE WHEN rn=21 THEN close END)-1)*100 AS ret_20 "
        "FROM (SELECT concept_code, close, ROW_NUMBER() OVER "
        "        (PARTITION BY concept_code ORDER BY trade_date DESC) rn "
        "      FROM ths_concept_market WHERE trade_date <= %s AND trade_date >= %s) x "
        "GROUP BY concept_code", [anchor, start])
    # 名称单取当日一列（不与收益查询做 JOIN：JOIN 会引入窗口外的概念，导致计数与
    # 「交叉印证」模块的口径互证项对不上 —— 实测 378 vs 375）
    names = {r["concept_code"]: r["concept_name"] for r in query_all(
        "SELECT DISTINCT concept_code, concept_name FROM ths_concept_market WHERE trade_date = %s",
        [anchor])}
    items, outliers = [], 0
    for r in rows:
        v = _f(r["ret_20"])
        if v is None:
            continue
        if abs(v) > CONCEPT_OUTLIER:            # 异常值剔除（见文件头坑 3）
            outliers += 1
            continue
        items.append({"name": names.get(r["concept_code"]) or r["concept_code"], "ret_20": _r(v)})
    items.sort(key=lambda x: -x["ret_20"])
    return {"as_of": anchor, "items": items, "outliers": outliers}


# 卡片判读条（2026-09-19）：与 market-wind 同一套机制（口径在后端生成、前端只透传），
# 机制说明与「别凭直觉改」的实测依据见 app/analysis/market_wind.py 的 _card_verdict。
#
# 本模块的结论取三条正交信息：
#   ① 口径互证结果 —— 模块的核心产出，也是本模块唯一「找背离」的地方；
#   ② 相对市场基准的超额 —— ⚠️ 该值此前只在详情页可见（industry.bench_ret_20），
#      卡片上完全看不到；而它恰恰是「钱是不是真在往板块里走」最直接的量化；
#   ③ 广度（中位 + 上涨占比）。
# tone：互证背离 → 琥珀（两个独立数据集给出矛盾判断，此时任何单一口径的结论都不该被
#       独立采信）；其余 → 蓝。
def _card_verdict(compare: dict, industry: dict, concept: dict) -> dict:
    im, cm = industry.get("median"), concept.get("median")
    up, bench = industry.get("up_ratio"), industry.get("bench_ret_20")
    level = (compare or {}).get("level")
    if im is None:
        return {"headline": "板块数据未就绪", "detail": "", "tone": "normal"}

    if level == "diverge":
        head, tone = "行业与概念两个口径背离 —— 当前没有一致的市场叙事", "caution"
    elif level == "agree":
        head, tone = "行业与概念双口径互证一致", "normal"
    else:
        head, tone = "口径互证数据不足", "normal"
    if bench is not None:
        excess = im - bench
        head += f"、{'超基准' if excess >= 0 else '落后基准'} {abs(excess):.2f}pp"

    lvl = industry.get("level") or "一级"
    detail = f"申万{lvl} {industry.get('count')} 个行业 {up}% 上涨、中位 {im:+.2f}%"
    if cm is not None:
        detail += f"｜概念 {concept.get('count')} 个、中位 {cm:+.2f}%"
    return {"headline": head, "detail": detail, "tone": tone}


@ttl_cache(600)
def sector_rotation(as_of: str | None = None, level: str = DEFAULT_LEVEL) -> dict:
    """板块轮动模块数据装配（/api/analysis/sector-rotation）

    参数
    - as_of：历史回放锚点，非空时只取该日及之前的数据
    - level：申万行业层级，「一级」=31 个 / 「二级」=131 个
    """
    level = level if level in SW_TYPES else DEFAULT_LEVEL
    dates = _trade_dates(as_of, RET_LAG + 6)
    if len(dates) < RET_LAG + 1:
        return {"as_of": None, "note": "交易日不足，无法计算 20 日收益"}
    d_cur, d_old = dates[0], dates[RET_LAG]

    agg = _industry_agg(level, d_old, d_cur)
    if not agg["items"]:
        return {"as_of": None, "note": "行情快照取数失败（stock_market_daily 无对应日期数据）"}
    bench = query_all(
        "SELECT ret_bench_20 FROM market_style_daily WHERE trade_date <= %s "
        "ORDER BY trade_date DESC LIMIT 1", [d_cur])
    b_ret = _f(bench[0]["ret_bench_20"]) if bench else None

    items = []
    for name, vals in agg["items"].items():
        if len(vals) < 5:                       # 样本过少的行业不参与排行（口径纯净）
            continue
        avg = sum(vals) / len(vals)
        items.append({"name": name, "n": len(vals), "ret_20": _r(avg),
                      "excess": None if b_ret is None else _r(avg - b_ret)})
    if not items:
        return {"as_of": None, "note": "行业内有效样本不足"}
    items.sort(key=lambda x: -x["ret_20"])
    rets = [i["ret_20"] for i in items]

    concept = _concept_rank(d_cur)
    c_rets = [i["ret_20"] for i in concept["items"]]

    industry = {
        "as_of": d_cur, "base_date": d_old, "level": level,
        "items": items,
        "count": len(items), "stock_count": agg["count"],
        "median": _r(_median(rets)), "up_ratio": _r(sum(1 for v in rets if v > 0) / len(rets) * 100, 1),
        "dispersion": _r(_std(rets)),
        "spread": _r(rets[0] - rets[-1]) if len(rets) > 1 else None,
        "bench_ret_20": _r(b_ret),
    }
    concept_out = {
        "as_of": concept["as_of"], "items": concept["items"], "count": len(concept["items"]),
        "median": _r(_median(c_rets)),
        "up_ratio": _r(sum(1 for v in c_rets if v > 0) / len(c_rets) * 100, 1) if c_rets else None,
        "outliers": concept["outliers"],
    }

    # 口径互证（参照系 ④：两个独立数据集是否给出同一结论）
    im, cm = industry["median"], concept_out["median"]
    if im is None or cm is None:
        compare = {"level": "nodata", "verdict": "数据缺失", "reading": "一侧口径无有效样本。"}
    elif (im > 0) == (cm > 0):
        compare = {"level": "agree", "verdict": "一致",
                   "reading": (f"申万行业口径中位 {im:+.2f}% 与概念口径中位 {cm:+.2f}% 同向 —— "
                               "两个独立数据集互证成立。")}
    else:
        compare = {"level": "diverge", "verdict": "背离",
                   "reading": (f"申万行业口径中位 {im:+.2f}% 与概念口径中位 {cm:+.2f}% **方向相反** —— "
                               "两个独立数据集给出矛盾判断（成分与加权方式不同），"
                               "说明当前没有一致的市场叙事，任何单一口径的结论都不该被独立采信。")}

    # card_rank（2026-09-19）：卡片墙只放这 3 个（总览页据此挑，不再取数组前 3 个）。
    # 取向与 market-wind 一致 —— 每个盒子回答一个不同的问题、且不重复判读条已说过的话：
    #   industry_median 答「涨得广不广」／dispersion 答「轮动快不快」／industry_spread 答「分化有多极端」。
    # ⚠️ concept_median 刻意不上卡片：判读条已用「双口径互证一致/背离」+ 概念中位表述过，
    #    重复上卡片只会挤掉「轮动速度」这个独立维度。它仍完整出现在详情页。
    kpis = [
        {"key": "industry_median", "card_rank": 1, "label": f"申万{level}行业中位（20日）", "value": im, "unit": "%",
         "tone": "updown", "status": f"{industry['up_ratio']}% 的行业上涨",
         "hint": f"申万{level}行业个股等权 20 日收益的中位数；上涨占比与它成对出现，"
                 "避免只看中位数而漏掉「一半以上行业在涨但被少数大跌拖累」"},
        {"key": "dispersion", "card_rank": 2, "label": "行业离散度（20日）", "value": industry["dispersion"], "unit": "pp",
         "tone": "neutral", "status": "（越大=轮动越剧烈）",
         "hint": "各行业 20 日收益的标准差。数值越大说明行业间分化越剧烈、轮动越快；"
                 "越小说明齐涨齐跌。这是「轮动速度」的量化描述，单看排行看不出来"},
        {"key": "industry_spread", "card_rank": 3, "label": "首尾差（20日）", "value": industry["spread"], "unit": "pp",
         "tone": "neutral",
         "status": f"{items[0]['name']} {items[0]['ret_20']:+.2f}% / {items[-1]['name']} {items[-1]['ret_20']:+.2f}%",
         "hint": "最强行业 − 最弱行业的 20 日收益差。配合离散度读：离散度大而首尾差小，"
                 "说明分化是全面的而非个别行业极端"},
        {"key": "concept_median", "label": "概念中位（20日）", "value": cm, "unit": "%",
         "tone": "updown", "status": f"{concept_out['count']} 个概念 · {concept_out['up_ratio']}% 上涨",
         "hint": "同花顺概念指数 20 日收益的中位数（已剔除 |收益|>60% 的异常样本）；"
                 "与申万行业口径互为正交验证"},
    ]

    return {
        "as_of": d_cur, "base_date": d_old, "is_replay": bool(as_of),
        "kpis": kpis, "industry": industry, "concept": concept_out, "compare": compare,
        # 卡片墙的一句话结论（卡片专用；详情页有自己的完整面板，不重复渲染）
        "verdict": _card_verdict(compare, industry, concept_out),
        "note": ROTATION_NOTE,
    }
