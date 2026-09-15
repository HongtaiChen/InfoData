#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""分析模块：交叉印证（cross-check）—— 参照系第 ④ 类「跟另一个独立维度比」

🔒 第一原则「数据用来比较才有意义」（registry.py / 设计规范 §1.0）：单个数字不是信息，
   只是噪声；只有放进一个比较关系里，它才成为信号。

五类参照系里，①②③⑤ 市场风向已落地（六组收益差 / pct·z·adj / 超额 / 宽度分解），
唯独 **④ 交叉印证完全空白**——所有指标都在「股票市场内部」打转，
从没跟利率、商品、杠杆资金、概念口径比过。而**所有背离信号都来自这一类**。

**比较的最高级用法是「背离」**：两个本该同向的维度不同向，才是可判断的信息。
本模块 6 项，每项都是「一个股票市场内维度 × 一个独立外部维度」：

  key        维度对                                      能读出的背离
  ---------  ----------------------------------------  --------------------------------
  leverage   风偏分数        × 两融余额                风格切防守而杠杆未撤（杠杆固执）
  bond       大势位置        × 10Y 国债收益率          低利率而股市低位（流动性未传导）
  commodity  科技/政策周期组  × 商品期货基差             实体改善未被股价反映
  concept    指数六组口径     × 概念板块口径            两个独立数据源互证或打架
  micro      涨停/跌停        × 换手率中位数            恐慌缩量 = 抛压衰竭前兆
  pxvol      大盘 20 日收益   × 成交额量比              缩量上涨 = 虚涨

**判定用「方向背离法」，不做加权打分**——打分说不清分数怎么来的，而方向背离可解释、可复核：
  ① 每一维先算自己的变化量（20 日）；
  ② 用**该维度自身的典型变化幅度**（近一年 20 日变化的绝对值中位数）做死区，低于其一半视为噪声不动；
  ③ 两维方向相反 = 背离；相同 = 一致；任一维落在死区内 = 中性。
死区取「自身典型幅度的一半」而不是写死的绝对阈值：不同维度的自然波动量级相差几个数量级
（风偏 20 日变化约 ±10pp、两融约 ±2%、换手率约 ±0.5pt），写死阈值必然在某个维度上失效。

性能（2026-09-15 实测，重要）：本模块刻意只挑**亚秒级**的数据源（两融 0.04s / 国债 0.02s /
商品基差 0.43s / 概念 1.15s），因为它是 `/api/analysis/market-wind` 的一部分，
不能让页面等。唯一的例外是换手率——`stock_market_daily` 按 trade_date 取该列是随机回表
（单日 15 秒），故**已物化**为 `market_style_daily.turnover_med`（见 market_style_sync 文件头）。

⚠️ 已知缺口（诚实标注，不假装有）：库内**没有指数估值分母**（PE/PB 全表为空），
故本模块**不做 ERP（股权风险溢价）**，股债项只做方向性判读。接入指数估值后应升级为 ERP 分位。
"""
from __future__ import annotations

import logging

from ..db import query_all

logger = logging.getLogger(__name__)

AGREE, DIVERGE, NEUTRAL, NODATA = "agree", "diverge", "neutral", "nodata"
VERDICT_TEXT = {AGREE: "一致", DIVERGE: "背离", NEUTRAL: "中性", NODATA: "数据缺失"}

# 方向死区比例：低于「该维度自身近一年 20 日变化的绝对值中位数」的这一比例，视为噪声不动
DEAD_RATIO = 0.5
# 死区与分位的观察窗口（与市场风向 pct 口径一致）
WINDOW = 250
CHG_LAG = 20

# 交叉印证清单说明（随响应下发，前端只透传不手抄 —— 设计规范 §1.0 硬性约束 ②）
FRAMEWORK_NOTE = (
    "交叉印证 = 五类参照系的第 ④ 类：拿股票市场内部的一个维度，去跟另一个**独立维度**比。"
    "6 项里每一项都配了外部参照物（杠杆资金 / 无风险利率 / 商品实体 / 概念口径 / 微观结构 / 量能），"
    "用途只有一个——发现**背离**：两个本该同向的维度不同向。"
    "判定用方向背离法：每维取自身 20 日变化，以「自身近一年变化幅度的中位数」的一半为死区，"
    "两维方向相反即背离。不做加权打分，因为打分说不清分数怎么来的。"
)


# ---------------------------------------------------------------- 通用工具


def _f(v) -> float | None:
    return None if v is None else float(v)


def _r(v, n: int = 2) -> float | None:
    return None if v is None else round(float(v), n)


def _pctile(vals: list, cur) -> float | None:
    """cur 在 vals 中的分位（0~100）；vals 顺序不限"""
    v = [float(x) for x in vals if x is not None]
    if not v or cur is None:
        return None
    le = sum(1 for x in v if x <= cur)
    return round(le / len(v) * 100, 1)


def _corr(xs: list, ys: list) -> float | None:
    n = len(xs)
    if n < 5:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / n
    sx = (sum((x - mx) ** 2 for x in xs) / n) ** 0.5
    sy = (sum((y - my) ** 2 for y in ys) / n) ** 0.5
    return None if sx * sy == 0 else round(cov / (sx * sy), 3)


def _typical_abs(vals: list) -> float | None:
    """近一年 20 日变化的绝对值中位数（死区尺度，见模块 docstring）"""
    a = sorted(abs(float(v)) for v in vals if v is not None)
    return a[len(a) // 2] if a else None


def _direction(delta: float | None, typical: float | None) -> int:
    """带死区的方向：|delta| < typical×DEAD_RATIO → 0（噪声不动）"""
    if delta is None:
        return 0
    dead = abs(typical or 0) * DEAD_RATIO
    if dead > 0 and abs(delta) < dead:
        return 0
    return 1 if delta > 0 else (-1 if delta < 0 else 0)


def _dim(name: str, value=None, unit: str = "", tone: str = "neutral", pct=None,
         delta=None, delta_label: str = "20日变化", delta_unit: str = "",
         as_of: str | None = None, sub: str | None = None) -> dict:
    """一个维度的读数（值 / 变化 / 分位 / 截至日 / 补充说明）"""
    nd = 4 if unit == "z" else 2          # z 保留 4 位（±0.65 与 ±0.6 有区别），其余 2 位
    return {"name": name, "value": _r(value, nd), "unit": unit,
            "tone": tone, "pct": pct, "delta": _r(delta), "delta_label": delta_label,
            "delta_unit": delta_unit, "as_of": as_of, "sub": sub}


def _ev(label: str, value) -> dict:
    return {"label": label, "value": value}


def _nodata(key: str, label: str, pair: str, hint: str) -> dict:
    return {"key": key, "label": label, "pair": pair, "level": NODATA, "verdict": VERDICT_TEXT[NODATA],
            "reading": "该项数据源暂不可用或样本不足，未参与判定。", "dims": [], "evidence": [],
            "hint": hint}


# ---------------------------------------------------------------- 上下文


def _context(as_of: str | None) -> dict | None:
    """共用上下文：最新风格行 + 交易日序列（供各项算滞后）"""
    cond = "WHERE trade_date <= %s" if as_of else ""
    args = [as_of] if as_of else []
    rows = query_all(f"SELECT * FROM market_style_daily {cond} ORDER BY trade_date DESC LIMIT 1", args)
    if not rows:
        return None
    cur = rows[0]
    dates = [str(r["trade_date"]) for r in query_all(
        f"SELECT trade_date FROM market_style_daily {cond} ORDER BY trade_date DESC LIMIT 300", args)]
    return {"cur": cur, "as_of": str(cur["trade_date"]), "dates": dates, "replay": bool(as_of)}


def _lag(dates: list[str], item_date: str | None) -> int:
    """item_date 之后主数据源还走出了几个交易日（0 = 同步）"""
    if not item_date:
        return 0
    return sum(1 for d in dates if d > item_date)


def _style_series(as_of: str | None, cols: list[str], days: int) -> list[dict]:
    """近 days 行（按日期倒序）"""
    cond = "WHERE trade_date <= %s" if as_of else ""
    args = [as_of] if as_of else []
    return query_all(
        f"SELECT trade_date, {', '.join(cols)} FROM market_style_daily {cond} "
        "ORDER BY trade_date DESC LIMIT %s", args + [days])


# ---------------------------------------------------------------- ① 杠杆印证


def _leverage(ctx: dict) -> dict:
    """风偏分数 × 两融余额 —— 风险偏好与杠杆资金并不同步（实测 corr 仅 0.33）

    杠杆资金是风偏的「筹码面印证」：风险偏好是想法，两融余额是真金白银借来的钱。
    两者本该同向；不同向即为最有价值的信号。
    """
    key, label, pair = "leverage", "杠杆印证", "风偏分数 × 两融余额"
    hint = ("风偏分数 = 科技成长组 − 股息防守组 20 日等权收益差（pp）；两融余额取 securities_margin.rzrqye。"
            "两者衡量「想法」与「真金白银」，实测相关系数仅约 0.33 —— 它们是两件事，不是同一信号的两种写法。"
            "⚠️ 该表不含可用占比列（rzrqyecz 存值 ≈ 余额而非占流通市值比，列名与值不符，已刻意不用）。")
    srows = _style_series(ctx["as_of"], ["risk_appetite_20"], 320)
    srows.reverse()                                     # 升序，便于 i-20 取值
    if len(srows) < 40:
        return _nodata(key, label, pair, hint)
    dates = [str(r["trade_date"]) for r in srows]
    ra = [_f(r["risk_appetite_20"]) for r in srows]

    mrows = query_all(
        "SELECT trade_date, rzrqye FROM securities_margin "
        "WHERE trade_date >= %s AND trade_date <= %s AND rzrqye IS NOT NULL ORDER BY trade_date",
        [dates[0], ctx["as_of"]])
    m = {str(r["trade_date"]): float(r["rzrqye"]) for r in mrows}
    if not m:
        return _nodata(key, label, pair, hint)

    # 20 日变化配对（两腿都必须有 day-20 的值）
    pairs = []                                          # [(Δ风偏pp, Δ两融%)]
    for i in range(CHG_LAG, len(dates)):
        d, d0 = dates[i], dates[i - CHG_LAG]
        if ra[i] is None or ra[i - CHG_LAG] is None or d not in m or d0 not in m:
            continue
        if m[d0] == 0:
            continue
        pairs.append((ra[i] - ra[i - CHG_LAG], (m[d] / m[d0] - 1) * 100))
    if len(pairs) < 10:
        return _nodata(key, label, pair, hint)

    typ_ra = _typical_abs([p[0] for p in pairs])
    typ_rz = _typical_abs([p[1] for p in pairs])
    last_ra, last_rz = pairs[-1]
    d_ra, d_rz = _direction(last_ra, typ_ra), _direction(last_rz, typ_rz)

    # 当前状态：两腿各自最新可比日
    i_cur = max(i for i in range(CHG_LAG, len(dates))
                if ra[i] is not None and ra[i - CHG_LAG] is not None
                and dates[i] in m and dates[i - CHG_LAG] in m)
    m_date = dates[i_cur]
    rz_cur = m[m_date]
    margin_date = max(m)
    rz_hist = query_all(
        "SELECT rzrqye FROM securities_margin WHERE rzrqye IS NOT NULL AND trade_date <= %s "
        "ORDER BY trade_date DESC LIMIT 500", [margin_date])
    rz_pct = _pctile([r["rzrqye"] for r in rz_hist], rz_cur)

    # 判定
    ra_z = abs(last_ra) / typ_ra if typ_ra else None
    rz_z = abs(last_rz) / typ_rz if typ_rz else None
    if not d_ra or not d_rz:
        level = NEUTRAL
        reading = "两维 20 日变化均未超出各自典型幅度，无可判读的印证关系。"
    elif d_ra == d_rz:
        level = AGREE
        # 「同向但力度严重不对称」也是信息：方向一致只说明没有背离，不代表力度对等。
        # 实测当前就是这一形态（风偏 -22.3pp ≈ 2.3× 自身典型，两融 -2.4% ≈ 0.9×）——
        # 这正是探索报告里「杠杆固执」要说的事，只是方向并未相反，故归入一致并补一句。
        tail = ""
        if ra_z is not None and rz_z is not None and ra_z >= 1.5 and rz_z <= 1.0:
            tail = (f" 但两者力度严重不对称：风偏的变动是自身典型幅度的 {ra_z:.1f} 倍，"
                    f"两融只有 {rz_z:.1f} 倍 —— 属「风格已切、杠杆未跟」的半印证状态。")
        elif rz_pct is not None and rz_pct >= 60:
            tail = f" 且杠杆仍在近一年 {rz_pct}% 分位的高位，收缩并不充分。"
        reading = ("风险偏好与杠杆资金同步上升 —— 属「真升温」，两维互相确认。" if d_ra > 0
                   else "风险偏好与杠杆资金同步收缩 —— 属「真收缩」，不是单一维度的错杀。") + tail
    else:
        level = DIVERGE
        if d_ra < 0:
            # 风偏降 → 两融必为升（否则就是一致），故此处读法围绕「杠杆在加但风格在退」
            if rz_pct is not None and rz_pct >= 60:
                reading = (f"股票风格已切向防守，但杠杆盘仍在加码、余额停在近一年 {rz_pct}% 分位的高位 —— "
                           "要么后续补跌，要么杠杆盘判断这是错杀；**两者不可能都对**。")
            else:
                reading = ("股票风格转防守，杠杆资金反而小幅加码 —— 但余额仍在近一年低位，"
                           "更像底部区域的对抗性资金，而非趋势性看多。")
        else:
            reading = ("风格已转向进攻，杠杆资金却在减仓 —— 行情缺少筹码面确认，持续性存疑。")

    return {
        "key": key, "label": label, "pair": pair,
        "level": level, "verdict": VERDICT_TEXT[level], "reading": reading,
        "dims": [
            _dim("风偏分数", ra[i_cur], "pp", "updown", _pctile(ra[-WINDOW:], ra[i_cur]),
                 last_ra, "20日变化", "pp", ctx["as_of"]),
            _dim("两融余额", rz_cur / 1e8, "亿元", "updown", rz_pct,
                 last_rz, "20日变化", "%", margin_date),
        ],
        "evidence": [
            _ev("相关系数（20日变化）", _corr([p[0] for p in pairs], [p[1] for p in pairs])),
            _ev("配对样本", f"{len(pairs)} 组"),
            _ev("两融近500日分位", None if rz_pct is None else f"{rz_pct}%"),
            _ev("两融绝对值", f"{rz_cur/1e12:.2f} 万亿"),
            _ev("力度比（风偏/两融）", None if ra_z is None or rz_z is None else f"{ra_z:.1f}× / {rz_z:.1f}×"),
            _ev("死区尺度（风偏/两融）", f"{typ_ra:.2f}pp / {typ_rz:.2f}%"),
        ],
        "hint": hint, "as_of": m_date, "stale": _lag(ctx["dates"], margin_date),
    }


# ---------------------------------------------------------------- ② 股债印证


def _bond(ctx: dict) -> dict:
    """大势位置 × 10Y 国债收益率 —— 钱便宜到极致而股市不涨 = 流动性未传导"""
    key, label, pair = "bond", "股债印证", "大势位置 × 10Y 国债收益率"
    hint = ("10Y 国债收益率取 bond_profit_daily.cn_bond_10y（中债），分位为近一年口径；"
            "大势位置 = 中证全指在近 250 日高低区间的分位。"
            "⚠️ 库内**无指数估值分母**（stock_market_current 的 PE/PB 等 8 列全表为空），"
            "故本项**不做 ERP（股权风险溢价）**，只做「利率水平 × 股指位置」的方向性判读；"
            "接入指数估值后应升级为 ERP 分位（通常 ERP≥90% 分位对应中长期底部区域）。")
    brows = query_all(
        "SELECT trade_date, cn_bond_10y, cn_bond_10y_2y_spread FROM bond_profit_daily "
        "WHERE cn_bond_10y IS NOT NULL AND trade_date <= %s ORDER BY trade_date DESC LIMIT %s",
        [ctx["as_of"], WINDOW + 10])
    if len(brows) < 60:
        return _nodata(key, label, pair, hint)
    y = float(brows[0]["cn_bond_10y"])
    bdate = str(brows[0]["trade_date"])
    vals = [float(r["cn_bond_10y"]) for r in brows]
    y_pct = _pctile(vals, y)
    y_min = min(vals)
    spread = _f(brows[0]["cn_bond_10y_2y_spread"])
    s_vals = [r["cn_bond_10y_2y_spread"] for r in brows if r["cn_bond_10y_2y_spread"] is not None]
    s_pct = _pctile(s_vals, spread)

    bench = _f(ctx["cur"].get("bench_pos_pct"))
    if bench is None or y_pct is None:
        return _nodata(key, label, pair, hint)

    lo_y, hi_y = y_pct <= 30, y_pct >= 70
    lo_b, hi_b = bench <= 30, bench >= 70
    if lo_y and lo_b:
        level = DIVERGE
        reading = (f"无风险利率处于近一年 {y_pct}% 分位（距区间最低仅 {(y-y_min)*100:.0f}bp），"
                   f"而股指也在低位（{bench}% 分位）—— **流动性未传导到风险资产**，是股债背离的典型形态。")
    elif lo_y and hi_b:
        level = AGREE
        reading = "低利率 + 高位股指 —— 估值扩张有资金面支撑，属「流动性驱动」的一致形态。"
    elif hi_y and lo_b:
        level = AGREE
        reading = "利率抬升 + 低位股指 —— 紧资金压制风险资产，两维一致偏空。"
    elif hi_y and hi_b:
        level = AGREE
        reading = "利率抬升 + 高位股指 —— 两维方向一致；需警惕利率继续上行对估值的压力。"
    else:
        level = NEUTRAL
        reading = "利率与股指位置均处中位区间，未形成可判读的背离或共振。"

    return {
        "key": key, "label": label, "pair": pair,
        "level": level, "verdict": VERDICT_TEXT[level], "reading": reading,
        "dims": [
            _dim("大势位置（250日分位）", bench, "%", "neutral", None,
                 None, as_of=ctx["as_of"], sub="80+ 高位 / 20- 低位"),
            _dim("10Y 国债收益率", y, "%", "neutral", y_pct,
                 (y - float(brows[1]["cn_bond_10y"])) * 100 if len(brows) > 1 else None,
                 "较前一日", "bp", bdate),
        ],
        "evidence": [
            _ev("10Y 近一年分位", f"{y_pct}%"),
            _ev("距近一年最低", f"{(y - y_min)*100:.0f} bp"),
            _ev("10Y−2Y 期限利差", None if spread is None else f"{spread}%"),
            _ev("期限利差分位", None if s_pct is None else f"{s_pct}%"),
            _ev("数据截至", f"{bdate}（滞后 {_lag(ctx['dates'], bdate)} 个交易日）"),
        ],
        "hint": hint, "as_of": bdate, "stale": _lag(ctx["dates"], bdate),
    }


# ---------------------------------------------------------------- ③ 股商印证

# 商品分组 ↔ 对应股票组（用库内六组指数，因其已在 market_style_daily 物化、零额外取数成本）
GOOD_GROUPS = [
    ("新能源链（光伏·锂电）", ["多晶硅", "碳酸锂", "工业硅"], "tech", "科技成长"),
    ("黑色建材链", ["螺纹钢", "铁矿石", "焦煤", "焦炭", "热轧卷板", "玻璃", "纯碱"], "pol", "政策周期"),
]


def _commodity(ctx: dict) -> dict:
    """商品期货基差 × 股票周期组 —— 实体景气与股价给出相反判断（股商背离）"""
    key, label, pair = "commodity", "股商印证", "商品期货基差 × 科技·周期组"
    hint = ("基差 = 主力合约价 − 现货价，>0 升水（期货预期更高）、<0 贴水；"
            "「偏离」= 当前基差 − 其 180 日均值。为使不同品种可比（单位分别是元/吨、元/桶…），"
            "每个品种的偏离先除以**自身近一年偏离的标准差**得到 z（口径同市场风向的「风险调整」），"
            "再取全品种中位得到「商品整体景气 z」。z>0 表示商品整体由贴水转升水。"
            "⚠️ 股票腿用六组指数代理（新能源链→科技成长组、黑色链→政策周期组）——"
            "更贴切的做法是配申万「电力设备/钢铁」行业，但那需要扫个股日线（单次约 8 秒），"
            "为不拖慢市场风向页而刻意不用；见「板块轮动」模块。")
    d0 = query_all("SELECT MIN(trade_date) AS d FROM futures_spot_price WHERE trade_date >= "
                   "(SELECT DATE_SUB(%s, INTERVAL 500 DAY))", [ctx["as_of"]])
    start = str(d0[0]["d"]) if d0 and d0[0]["d"] else None
    if not start:
        return _nodata(key, label, pair, hint)
    rows = query_all(
        "SELECT trade_date, good_name, main_contract_basis AS b, main_basis_avg_180d AS m "
        "FROM futures_spot_price WHERE main_contract_basis IS NOT NULL AND main_basis_avg_180d IS NOT NULL "
        "AND trade_date >= %s AND trade_date <= %s ORDER BY good_name, trade_date",
        [start, ctx["as_of"]])
    if len(rows) < 200:
        return _nodata(key, label, pair, hint)

    # 按品种算 偏离 与 自身尺度
    per: dict[str, list[tuple[str, float]]] = {}
    for r in rows:
        per.setdefault(r["good_name"], []).append(
            (str(r["trade_date"]), float(r["b"]) - float(r["m"])))
    z_by_date: dict[str, list[float]] = {}
    gz: dict[str, dict[str, float]] = {}                # 品种 → 日期 → z
    for g, seq in per.items():
        devs = [v for _, v in seq]
        if len(devs) < 60:
            continue
        mu = sum(devs) / len(devs)
        sd = (sum((v - mu) ** 2 for v in devs) / len(devs)) ** 0.5
        if sd <= 0:
            continue
        gz[g] = {d: (v - 0.0) / sd for d, v in seq}     # 减 0：保留升贴水方向（见 docstring）
        for d, z in gz[g].items():
            z_by_date.setdefault(d, []).append(z)
    if not z_by_date:
        return _nodata(key, label, pair, hint)

    jingqi = {d: sorted(v)[len(v) // 2] for d, v in z_by_date.items() if len(v) >= 10}
    if not jingqi:
        return _nodata(key, label, pair, hint)
    last_d = max(jingqi)
    z_cur = jingqi[last_d]
    z_pct = _pctile([jingqi[d] for d in sorted(jingqi)[-WINDOW:]], z_cur)

    # 分组 z + 对应股票组 20 日收益
    sub_dims, evidence, readings = [], [], []
    verdicts = []
    for gname, members, gkey, glabel in GOOD_GROUPS:
        avail = [g for g in members if g in gz and last_d in gz[g]]
        if not avail:
            continue
        gvals = sorted(gz[g][last_d] for g in avail)
        gz_v = gvals[len(gvals) // 2]
        ret = _f(ctx["cur"].get(f"ret_{gkey}_20"))
        sub_dims.append(_dim(f"{gname}基差偏离", gz_v, "z", "neutral", None,
                             None, as_of=last_d, sub=f"{len(avail)} 个品种"))
        if ret is None:
            continue
        dir_g = _direction(gz_v, 0.5)
        dir_r = _direction(ret, 3.0)
        if dir_g and dir_r and dir_g != dir_r:
            verdicts.append(DIVERGE)
            readings.append(f"{gname}基差偏离 {gz_v:+.2f}z（{'升水走强' if gz_v > 0 else '贴水走弱'}）"
                            f"而{glabel}组 20 日 {ret:+.2f}% —— 实体与股价相反")
        elif dir_g and dir_r:
            verdicts.append(AGREE)
        evidence.append(_ev(f"{glabel}组 20日收益", f"{ret:+.2f}%"))

    if not verdicts:
        return _nodata(key, label, pair, hint)
    level = DIVERGE if DIVERGE in verdicts else AGREE
    if level == DIVERGE:
        reading = ("；".join(readings) + "。这类背离要么是股票超跌、要么是商品抢跑，"
                   "**不可能两个都对** —— 这正是最该捕捉的信号。")
    else:
        reading = "商品基差与相关股票组的 20 日方向一致，实体与股价未出现背离。"

    return {
        "key": key, "label": label, "pair": pair,
        "level": level, "verdict": VERDICT_TEXT[level], "reading": reading,
        "dims": [_dim("商品整体景气 z", z_cur, "z", "neutral", z_pct, None,
                      as_of=last_d, sub=f"{len(z_by_date[last_d])} 个品种中位")] + sub_dims,
        "evidence": evidence + [
            _ev("商品景气近一年分位", None if z_pct is None else f"{z_pct}%"),
            _ev("覆盖品种", f"{len(gz)} 个"),
            _ev("数据截至", f"{last_d}（滞后 {_lag(ctx['dates'], last_d)} 个交易日）"),
        ],
        "hint": hint, "as_of": last_d, "stale": _lag(ctx["dates"], last_d),
    }


# ---------------------------------------------------------------- ④ 口径互证


def _concept_anchor(as_of: str) -> str | None:
    """找「概念覆盖完整」的最近交易日

    ⚠️ 源侧（同花顺）**分批发布**：收盘后一段时间只有 ~190/375 个概念，
    22:00 补班次才补齐。若直接以主数据源日为准，概念样本会缺一半、中位数失真
    （实测 9-15 只 190 个概念时中位 -3.66% / 涨占比 8.4%，而完整日 375 个时
    中位 -3.07% / 涨占比 15.8%）——故必须锚定到「覆盖数达标」的交易日。
    """
    rows = query_all(
        "SELECT trade_date FROM ths_concept_market WHERE trade_date <= %s "
        "GROUP BY trade_date HAVING COUNT(DISTINCT concept_code) >= 300 "
        "ORDER BY trade_date DESC LIMIT 1", [as_of])
    return str(rows[0]["trade_date"]) if rows else None


def _concept(ctx: dict) -> dict:
    """指数六组口径 × 概念板块口径 —— 两个独立数据源互证或打架"""
    key, label, pair = "concept", "口径互证", "指数口径 × 概念口径"
    hint = ("概念口径 = 同花顺概念指数（375 个）各自最近 21 个交易日的收益中位数与上涨占比；"
            "指数口径 = 库内六组等权 20 日收益的中位数。两者是**完全独立**的数据集"
            "（同花顺概念编制 vs 中证/国证指数），同向即为互证成立。"
            "⚠️ 已剔除 |20日收益|>60% 的异常值（源存在异常样本，如实测 WiFi6 曾出现 +110%）；"
            "⚠️ 概念数据按「覆盖数达标日」锚定，可能与主数据源差 1 个交易日（源侧分批发布所致）。")
    anchor = _concept_anchor(ctx["as_of"])
    if not anchor:
        return _nodata(key, label, pair, hint)
    rows = query_all(
        "SELECT concept_code, "
        "  (MAX(CASE WHEN rn=1 THEN close END)/MAX(CASE WHEN rn=21 THEN close END)-1)*100 AS ret_20 "
        "FROM (SELECT concept_code, close, ROW_NUMBER() OVER "
        "        (PARTITION BY concept_code ORDER BY trade_date DESC) rn "
        "      FROM ths_concept_market WHERE trade_date <= %s AND trade_date >= %s) x "
        "GROUP BY concept_code", [anchor, _shift_days(anchor, 150)])
    vals = sorted(float(r["ret_20"]) for r in rows
                  if r["ret_20"] is not None and abs(float(r["ret_20"])) <= 60)
    if len(vals) < 50:
        return _nodata(key, label, pair, hint)
    c_med = vals[len(vals) // 2]
    c_up = round(sum(1 for v in vals if v > 0) / len(vals) * 100, 1)

    gvals = sorted(v for v in (_f(ctx["cur"].get(f"ret_{g}_20"))
                               for g in ("bench", "size", "tech", "sent", "div", "pol"))
                   if v is not None)
    if not gvals:
        return _nodata(key, label, pair, hint)
    g_med = gvals[len(gvals) // 2]

    if c_med > 0 and g_med > 0:
        level = AGREE
        reading = (f"概念口径中位 {c_med:+.2f}%、指数口径中位 {g_med:+.2f}% —— 两个独立数据集同向偏多，"
                   f"**互证成立**。")
    elif c_med < 0 and g_med < 0:
        level = AGREE
        reading = (f"概念口径中位 {c_med:+.2f}%、指数口径中位 {g_med:+.2f}% —— 两个独立数据集同向偏空，"
                   f"**互证成立**：普跌不是算法产物。")
    else:
        level = DIVERGE
        reading = (f"概念口径中位 {c_med:+.2f}% 与指数口径中位 {g_med:+.2f}% **方向相反** —— "
                   "两个独立数据集给出矛盾判断。这种口径分歧本身就是信息"
                   "（成分与加权方式不同所致），但也说明当前没有一致的市场叙事。")

    return {
        "key": key, "label": label, "pair": pair,
        "level": level, "verdict": VERDICT_TEXT[level], "reading": reading,
        "dims": [
            _dim("概念口径（20日中位）", c_med, "%", "updown", None,
                 as_of=anchor, sub=f"{len(vals)} 个概念"),
            _dim("指数口径（六组中位）", g_med, "%", "updown", None,
                 as_of=ctx["as_of"], sub="六组等权 20 日"),
        ],
        "evidence": [
            _ev("概念上涨占比", f"{c_up}%"),
            _ev("概念样本", f"{len(vals)} 个（已剔除 |收益|>60% 异常值）"),
            _ev("概念数据截至", f"{anchor}（滞后 {_lag(ctx['dates'], anchor)} 个交易日）"),
        ],
        "hint": hint, "as_of": anchor, "stale": _lag(ctx["dates"], anchor),
    }


def _shift_days(d: str, n: int) -> str:
    """日期减 n 天（仅用于限定窗口，不要求精确交易日）"""
    from datetime import date, timedelta
    y, m, dd = (int(x) for x in d.split("-"))
    return str(date(y, m, dd) - timedelta(days=n))


# ---------------------------------------------------------------- ⑤ 微观结构


def _micro(ctx: dict) -> dict:
    """涨停/跌停 × 换手率中位数 —— 恐慌缩量 = 抛压衰竭前兆"""
    key, label, pair = "micro", "微观结构", "跌停×涨停 × 换手率中位数"
    hint = ("换手率 = market_style_daily.turnover_med，即全市场个股换手率的**中位数**（%）。"
            "用中位数不用均值：源列有极端异常值（全史最大 20.27），均值实测被污染近 10 倍。"
            "成对判据：跌停多于涨停（恐慌）而换手率处于低分位（缩量）→ 抛压衰竭前兆；"
            "反之放量恐慌则是真实抛压。单看涨跌停数无法区分这两种情形。")
    c = ctx["cur"]
    lu, ld = c.get("limit_up"), c.get("limit_down")
    tm = _f(c.get("turnover_med"))
    hl = c.get("hl_diff60")
    if lu is None or ld is None:
        return _nodata(key, label, pair, hint)
    lu, ld = int(lu), int(ld)
    if tm is None:
        return _nodata(key, label, pair, hint)
    series = _style_series(ctx["as_of"], ["turnover_med"], WINDOW)
    t_pct = _pctile([r["turnover_med"] for r in series], tm)

    if t_pct is None:
        return _nodata(key, label, pair, hint)
    panic = ld > lu
    if panic and t_pct <= 30:
        level, reading = DIVERGE, (f"跌停 {ld} 家多于涨停 {lu} 家（恐慌），但换手率仅近一年 {t_pct}% 分位"
                                   "（缩量）—— 抛压衰竭前兆：卖的人已经卖完了。")
    elif panic and t_pct >= 70:
        level, reading = AGREE, (f"跌停 {ld} 家多于涨停 {lu} 家，换手率处近一年 {t_pct}% 分位（放量）"
                                 "—— 真实抛压，仍在出逃过程中。")
    elif not panic and t_pct <= 30:
        level, reading = DIVERGE, (f"涨停 {lu} 家多于跌停 {ld} 家，但换手率仅近一年 {t_pct}% 分位"
                                   "—— 缩量上涨，缺乏增量资金确认。")
    elif not panic and t_pct >= 70:
        level, reading = AGREE, (f"涨停 {lu} 家多于跌停 {ld} 家，换手率处近一年 {t_pct}% 分位（放量）"
                                 "—— 量价配合，情绪真实高涨。")
    else:
        level, reading = NEUTRAL, "换手率处中位区间，涨跌停结构未与量能形成可判读的印证。"

    return {
        "key": key, "label": label, "pair": pair,
        "level": level, "verdict": VERDICT_TEXT[level], "reading": reading,
        "dims": [
            _dim("涨停 / 跌停", lu - ld, "家", "neutral", None, None,
                 as_of=ctx["as_of"], sub=f"涨停 {lu} · 跌停 {ld}"),
            _dim("换手率中位数", tm, "%", "neutral", t_pct, None, as_of=ctx["as_of"],
                 sub="全市场个股中位"),
        ],
        "evidence": [
            _ev("涨停 / 跌停", f"{lu} / {ld}"),
            _ev("换手率近一年分位", f"{t_pct}%"),
            _ev("60日新高−新低", hl),
        ],
        "hint": hint, "as_of": ctx["as_of"], "stale": 0,
    }


# ---------------------------------------------------------------- ⑥ 量价印证


def _pxvol(ctx: dict) -> dict:
    """大盘 20 日收益 × 成交额量比 —— 缩量上涨 = 虚涨，缩量下跌 = 抛压衰竭"""
    key, label, pair = "pxvol", "量价印证", "大盘 20 日收益 × 成交额量比"
    hint = ("量比 = market_style_daily.amount_ratio_20（全市场成交额 ÷ 近 20 日均值 ×100%）；"
            "收益腿取市场基准组 20 日等权收益。同样是跌，放量下跌是恐慌抛售、缩量下跌更像抛压衰竭；"
            "同样是涨，缩量上涨缺增量资金。只看涨跌幅完全无法区分。")
    c = ctx["cur"]
    ratio, ret = _f(c.get("amount_ratio_20")), _f(c.get("ret_bench_20"))
    if ratio is None or ret is None:
        return _nodata(key, label, pair, hint)
    if abs(ret) < 0.5:
        level, reading = NEUTRAL, "大盘 20 日收益接近持平，量价关系不可判读。"
    elif ret < 0 and ratio < 95:
        level, reading = DIVERGE, (f"大盘 20 日 {ret:+.2f}% 而量比仅 {ratio:.0f}%（缩量）"
                                   "—— 缩量下跌，抛压有衰竭迹象（跌幅未放量证实）。")
    elif ret < 0 and ratio > 105:
        level, reading = AGREE, (f"大盘 20 日 {ret:+.2f}% 且量比 {ratio:.0f}%（放量）"
                                 "—— 放量下跌，属真实抛压。")
    elif ret > 0 and ratio < 95:
        level, reading = DIVERGE, (f"大盘 20 日 {ret:+.2f}% 而量比仅 {ratio:.0f}%（缩量）"
                                   "—— 缩量上涨，缺增量资金确认，属虚涨。")
    else:
        level, reading = AGREE, (f"大盘 20 日 {ret:+.2f}% 且量比 {ratio:.0f}%（放量）"
                                 "—— 量价配合。")
    return {
        "key": key, "label": label, "pair": pair,
        "level": level, "verdict": VERDICT_TEXT[level], "reading": reading,
        "dims": [
            _dim("大盘 20 日收益", ret, "%", "updown", None, None, as_of=ctx["as_of"],
                 sub="市场基准组等权"),
            _dim("成交额量比", ratio, "%", "neutral",
                 _pctile([r["amount_ratio_20"] for r in _style_series(ctx["as_of"], ["amount_ratio_20"], WINDOW)],
                         ratio),
                 _f(c.get("market_amount")), "成交额", "亿元", ctx["as_of"]),
        ],
        "evidence": [
            _ev("量比判据", ">105% 放量 · 95~105% 平稳 · <95% 缩量"),
            _ev("成交额", None if c.get("market_amount") is None else f"{c['market_amount']} 亿元"),
            _ev("全市场涨跌家数", f"{c.get('breadth_up')} / {c.get('breadth_down')}"),
        ],
        "hint": hint, "as_of": ctx["as_of"], "stale": 0,
    }


# ---------------------------------------------------------------- 入口


def cross_checks(as_of: str | None = None) -> dict:
    """交叉印证清单（并发往 /api/analysis/market-wind 的 cross_checks 字段）

    逐项 fail-soft：任何一项取数异常只把自己降级为「数据缺失」，不拖垮整页。
    """
    ctx = _context(as_of)
    if not ctx:
        return {"items": [], "summary": {"agree": 0, "diverge": 0, "neutral": 0, "nodata": 0},
                "note": FRAMEWORK_NOTE, "as_of": None}
    items: list[dict] = []
    for fn in (_leverage, _bond, _commodity, _concept, _micro, _pxvol):
        try:
            it = fn(ctx)
        except Exception as e:                          # noqa: BLE001 —— 刻意 fail-soft
            logger.warning(f"交叉印证 {fn.__name__} 计算失败，已降级：{type(e).__name__}: {e}")
            it = None
        if it:
            items.append(it)
    summary = {k: sum(1 for i in items if i["level"] == k)
               for k in (AGREE, DIVERGE, NEUTRAL, NODATA)}
    return {"items": items, "summary": summary, "note": FRAMEWORK_NOTE, "as_of": ctx["as_of"]}
