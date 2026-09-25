#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""分析模块：货币流动性（银行间资金价格 / 期限结构 / 政策利率 / 外部约束）

🔒 第一原则「数据用来比较才有意义」（设计规范 §1.0）：
本模块落的是参照系 ② **自身纵比**（Shibor 3M 现在处于近一年什么位置）、
① **同类横比**（ON→1W→1M→3M 四个期限之间的利率阶梯）、
⑤ **结构分解**（陡/平/倒挂 —— 同样一个 3M 利率，曲线形状不同含义完全不同），
并在末尾做一次 ④ **交叉印证**（政策利率按兵不动 ↔ 市场利率是否在动；
资金价格 ↔ 权益位置；**国内松紧 ↔ 外部约束**）。

**四个切面（2026-09-25 批次 1 补齐第四面）**：
  ① 价格   —— Shibor 3M/隔夜（近一年分位）
  ② 预期   —— 期限结构（ON→1W→1M→3M 的陡平与倒挂）
  ③ 政策   —— LPR 1Y 报价与连续未动月数
  ④ 外部约束 —— 美债曲线 / 中美 10Y 利差 / 美元指数（`_external_state`）
开放经济下国内流动性受外部硬约束：利差决定跨境资金方向、美元指数决定全球美元松紧、
美债曲线是全球风险资产的贴现率。缺了这一面，只看 Shibor 会把「外部在收、国内在放」
误读成纯宽松。

🔒 **领域边界（2026-09-25 定稿，与 `funding_temperature.py` 配对的「因/果」划分）**：
本模块管**因** —— 钱**的价格与数量**（四切面全是「给定条件」）；
「资金温度」管**果** —— 钱**去哪了、多不多**（人民币汇率体现的跨境资金意愿、
新发基金、产业资本回购，都是「资金行为的结果」）。
⚠️ 按数据源划会打架：美元指数与人民币汇率同为汇率类指标，但前者是全球美元的**总闸门**
（外生给定 → 因，在本模块 `_external_state`），后者是内外资金博弈的**结果**
（内生 → 果，在 `funding_temperature._fx`）。判据一句话：**这个读数是「给定条件」还是「行为结果」。**
两边的模块 `note` 与 registry `desc` 互相点名，避免读者以为是重复建设。

**为什么需要这个模块**：`interbank_rate_daily` 自 2006 年起每日采集（4,984 行，3M 无缺失），
但此前只有采集器与 DQ 规则在写、**没有任何视图消费**（典型"只进不出"）。
而"货币流动性"是 A 股最上游的定价变量之一 —— 利率是估值分母，且它的**分位**比绝对值重要得多
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
4. **外部约束的时点与主模块不同轴**（2026-09-25 实测）：本模块主数据的 `data_as_of` 来自
   `interbank_rate_daily`（同步 A 股交易日，T 日 19:35 就有），而美债走 `bond_profit_daily`
   （实测滞后 1~2 个交易日）、美元指数走 `global_usd_index_daily`（滞后 1 日）。
   ⇒ 外部约束块必须**自带各自的截至日**（`external.as_of_*`）并在 UI 上显示，
   绝不能把美债的昨收价挂在「数据截至今天」的标题下 —— 那是时点口径错误。
5. **美元指数走「三口径并存」，主口径已换成官方日线**（2026-09-25 实测定案，见 `usd_index_sync`）：
   ① **主口径 `dxy_sina` = 新浪官方日线**（`NewForexService.getDayKLine?symbol=DINIW`，ICE 口径，
      1985-11-08 起 10,573 行），实测与官方实时快照偏差 **−0.004%**；
   ② `dxy_calc` = 自算（人民币中间价 6 成分货币交叉汇率 × ICE 公式），与官方日线在 2,380 个共有日
      互比：**|偏差| 中位 0.28% / p90 0.74% / p99 1.43% / 最大 2.29%**
      （⚠️ 带符号均值 +0.074% 因正负相抵而低估离散度，**不可当精度引用**；成因是中间价
      「每日 9:15 定盘 + 基于前一交易日篮子」带来的约 1 天滞后，趋势日会系统性偏离）⇒ 保留它作
      **独立第二口径**（两条互比能立刻暴露单位错，本项目正是靠它抓出瑞典克朗标价法的 bug）；
   ③ `dxy_snapshot` = 实时快照，作标定锚（自 2026-09-24 起每日累积）。
   ⚠️ 设计文档初稿写的「自算偏差 −3.09% / ±3% 系统性偏离」是**标价法单位算错**导致的假偏差
   （把间接标价的瑞典克朗当直接标价），修正后偏差 <0.1%，见该文档勘误。
   ⇒ UI 必须标注「官方日线（ICE 口径）」而非「自算」。
6. **多国央行政策利率上游已停更**（2026-09-25 实测）：`macro_bank_{usa,euro,japan,english}_interest_rate`
   全族最后有效决议统一停在 2025-07~08，表内最后一行的日期在 2025-09/10 但「今值」为空。
   本模块只展示**有效决议**并显式标注「源停更 N 个月」，不把它当当前利率用。
"""
from __future__ import annotations

import logging
from datetime import date

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
# 美债曲线期限（列名白名单）；2Y 是曲线陡平的短端锚，其自身分位不单列（避免与期差重复）
US_TERMS: list[tuple[str, str]] = [
    ("2Y", "us_bond_2y"),
    ("5Y", "us_bond_5y"),
    ("10Y", "us_bond_10y"),
    ("30Y", "us_bond_30y"),
]

MONEY_COST_NOTE = (
    "本模块回答「货币流动性松紧」，四个切面：**价格**（Shibor 3M 在近一年什么位置）、"
    "**预期**（期限结构是陡还是平 —— 平坦说明短端没有溢价要求、市场预期资金持续宽松；"
    "倒挂则是流动性紧张的信号）、**政策**（LPR 报价与连续未动月数）、"
    "**外部约束**（美债曲线、中美 10Y 利差、美元指数 —— 开放经济下国内流动性的硬约束）。"
    "⚠️ 利率的绝对值跨期不可比，所有判断一律走「近一年分位」，不看绝对值本身。"
    "**领域边界**：本模块管的是**钱的价格与数量（因）** —— 资金贵不贵、政策给不给、外部让不让；"
    "至于「钱有没有真的进到市场里」（人民币汇率体现的跨境资金意愿、新发基金、产业资本回购），"
    "那是**果**，归「资金温度」，不在本模块重复。"
)

EXTERNAL_NOTE = (
    "外部约束为什么算「货币流动性」而不算「资金温度」：这里放的都是**钱的价格**（因）——"
    "美债收益率是全球风险资产的贴现率、中美利差决定跨境资金的方向、美元指数是全球美元的总闸门；"
    "而「钱有没有真的进到市场里」（汇率体现的外资流入、基金发行、回购）归「资金温度」，那是**果**。"
    "⚠️ 美元指数主口径为**新浪官方日线**（ICE 口径，1985-11-08 起 10,573 行，与官方实时快照"
    "实测偏差 −0.004%），表内另存**自算**（人民币中间价交叉汇率，与官方日线互比 |偏差| 中位 0.28%、"
    "最大 2.29% —— 勿看带符号均值，它会被正负相抵压小）"
    "与**实时快照**两条校验口径；美债与中债走 `bond_profit_daily`，**比 A 股日线滞后 1~2 个交易日**，"
    "故本区单独标注各自截至日。"
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


# ---------------- 外部约束（批次 1，2026-09-25）----------------
# 四个切面的第四面：美债曲线 / 中美 10Y 利差 / 美元指数 / 多国央行方向。
# 数据来自三张已有或批次 2 新建的表，本模块只读不采：
#   bond_profit_daily（美债 2Y/5Y/10Y/30Y + 中债 10Y，1990 起，9,351 行）
#   global_usd_index_daily（自算 DXY + DINIW 快照，2016-12 起，2,380 行）
#   cb_policy_rate（美/欧/日/英决议，⚠️ 上游停更，见文件头坑 6）
# 时点不同轴 → 各读数自带 as_of（见文件头坑 4）。

def _sub(a, b) -> float | None:
    va, vb = f(a), f(b)
    return None if (va is None or vb is None) else va - vb


def _dxy_primary(x: dict):
    """★美元指数主口径 = 新浪官方日线 `dxy_sina`（ICE 口径）；该列缺失才回落到自算 `dxy_calc`

    ⚠️ 为什么不是自算当主口径（2026-09-25 实测定案）：官方日线可直取（1985 起 10,573 行），
       与官方实时快照偏差仅 −0.004%；自算偏差 +0.085%、且单日最大可差 2.29%（偶发跳点）。
       自算改作**交叉校验口径**（两条互比能立刻暴露单位错，本项目正是靠它抓出瑞典克朗的标价法 bug）。
    """
    for col in ("dxy_sina", "dxy_calc"):
        v = f(x.get(col))
        if v is not None:
            return v
    return None


_EXT_DERIVE = {
    "cn_us_10y": lambda x: _sub(x.get("cn_bond_10y"), x.get("us_bond_10y")),
    "us_10y": lambda x: f(x.get("us_bond_10y")),
    "us_10y_2y": lambda x: _sub(x.get("us_bond_10y"), x.get("us_bond_2y")),
    "dxy": _dxy_primary,
}


def _fetch_bond(days: int, as_of: str | None) -> list[dict]:
    """美债/中债曲线（bond_profit_daily），日期降序。⚠️ 该表比 A 股日线滞后 1~2 个交易日"""
    cond = "WHERE trade_date <= %s" if as_of else ""
    args: list = [as_of] if as_of else []
    return query_all(
        "SELECT trade_date, cn_bond_2y, cn_bond_10y, "
        "us_bond_2y, us_bond_5y, us_bond_10y, us_bond_30y "
        f"FROM bond_profit_daily {cond} ORDER BY trade_date DESC LIMIT %s",
        args + [days])


def _fetch_dxy(days: int, as_of: str | None) -> list[dict]:
    """美元指数三口径（global_usd_index_daily）。表在旧环境可能不存在 → 降级为空，不抛"""
    cond = "WHERE trade_date <= %s" if as_of else ""
    args: list = [as_of] if as_of else []
    try:
        return query_all(
            "SELECT trade_date, dxy_sina, dxy_calc, dxy_snapshot "
            f"FROM global_usd_index_daily {cond} ORDER BY trade_date DESC LIMIT %s",
            args + [days])
    except Exception as e:                                   # noqa: BLE001
        logger.warning("global_usd_index_daily 取数失败，外部约束降级：%s", str(e)[:80])
        return []



def _ext_pairs(rows: list[dict], key: str,
               limit: int = WINDOW_1Y + 1) -> list[tuple[date, float]]:
    """按日期降序取某派生值的 (date, value) 序列（跳过空值行）"""
    fn = _EXT_DERIVE[key]
    out: list[tuple[date, float]] = []
    for x in rows:
        v = fn(x)
        if v is None:
            continue
        out.append((x["trade_date"], v))
        if len(out) >= limit:
            break
    return out


def _spread_word(p: float | None) -> str:
    """中美利差的分位词（分位高 = 利差宽）"""
    if p is None:
        return "数据未就绪"
    if p <= 10:
        return "近一年最窄"
    if p <= 30:
        return "偏窄"
    if p <= 70:
        return "居中"
    if p <= 90:
        return "偏宽"
    return "近一年最宽"


def _us_yield_word(p: float | None) -> str:
    """美债收益率的分位词（分位高 = 利率高 = 外部紧）"""
    if p is None:
        return "数据未就绪"
    if p <= 10:
        return "近一年最低"
    if p <= 30:
        return "偏低"
    if p <= 70:
        return "居中"
    if p <= 90:
        return "偏高"
    return "近一年最高"


def _usd_word(p: float | None) -> str:
    """美元指数的分位词（分位高 = 美元强 = 全球美元流动性紧）"""
    if p is None:
        return "数据未就绪"
    if p <= 10:
        return "近一年最弱"
    if p <= 30:
        return "偏弱"
    if p <= 70:
        return "居中"
    if p <= 90:
        return "偏强"
    return "近一年最强"


def _is_tight(p: float | None, high_is_tight: bool, lo: float = 35, hi: float = 65):
    """单项是否指向「外部收紧」。⚠️ 方向因指标而异：
    利差越窄越紧（low）、美债收益率越高越紧（high）、期差越平越紧（low）、美元越强越紧（high）"""
    if p is None:
        return None
    return p >= hi if high_is_tight else p <= lo


def _us_curve(bond: list[dict]) -> list[dict]:
    """美债曲线四点（2Y/5Y/10Y/30Y）：各自现值 / 一年前 / 20 日变化 / 近一年区间 / 相对 2Y 的期差"""
    base2 = f(next((x["us_bond_2y"] for x in bond if x.get("us_bond_2y") is not None), None))
    out: list[dict] = []
    for label, col in US_TERMS:
        ser = [(x["trade_date"], f(x[col])) for x in bond if x.get(col) is not None]
        if not ser:
            out.append({"term": label, "cur": None, "as_of": None, "prev_year": None,
                        "chg20_bp": None, "min_1y": None, "max_1y": None, "spread_2y_bp": None})
            continue
        cur = ser[0][1]
        win = [v for _, v in ser[:WINDOW_1Y]]
        out.append({
            "term": label,
            "cur": r(cur, 2),
            "as_of": str(ser[0][0]),
            "prev_year": r(ser[WINDOW_1Y][1], 2) if len(ser) > WINDOW_1Y else None,
            "chg20_bp": r((cur - ser[20][1]) * 100, 1) if len(ser) > 20 else None,
            "min_1y": r(min(win), 2) if win else None,
            "max_1y": r(max(win), 2) if win else None,
            "spread_2y_bp": r((cur - base2) * 100, 1) if base2 is not None else None,
        })
    return out


def _central_banks(data_as_of: str, as_of: str | None) -> tuple[list[dict], str | None]:
    """四国央行最新有效决议 + 停更告警（⚠️ 上游整体停更，见文件头坑 6）"""
    cond = "WHERE rate IS NOT NULL" + (" AND event_date <= %s" if as_of else "")
    try:
        rows = query_all(
            "SELECT country_code, country_name, central_bank, event_date, rate, change_bp "
            f"FROM cb_policy_rate {cond} ORDER BY event_date DESC",
            [as_of] if as_of else [])
    except Exception as e:                                   # noqa: BLE001
        logger.warning("cb_policy_rate 取数失败：%s", str(e)[:80])
        return [], None
    if not rows:
        return [], None
    try:
        ref = date.fromisoformat(data_as_of)
    except (TypeError, ValueError):
        ref = None
    out, seen = [], set()
    for x in rows:
        code = x["country_code"]
        if code in seen:
            continue
        seen.add(code)
        ev = x["event_date"]
        # 最后一次「真正变动」的决议（change_bp 非 0）—— 与最新决议日通常不是同一天
        mv = next((y for y in rows if y["country_code"] == code
                   and f(y["change_bp"]) not in (None, 0.0)), None)
        out.append({
            "code": code, "name": x["country_name"], "bank": x["central_bank"],
            "date": str(ev), "rate": r(f(x["rate"]), 2),
            "change_bp": r(f(x["change_bp"]), 1),
            "last_move_date": str(mv["event_date"]) if mv else None,
            "last_move_bp": r(f(mv["change_bp"]), 1) if mv else None,
            "stale_months": (((ref.year * 12 + ref.month) - (ev.year * 12 + ev.month))
                             if ref else None),
        })
    order = {"US": 0, "EU": 1, "JP": 2, "UK": 3}
    out.sort(key=lambda z: order.get(z["code"], 9))
    latest = max(z["date"] for z in out)
    stale = max((z["stale_months"] or 0) for z in out)
    note = (f"⚠️ **上游已停更**：`macro_bank_*_interest_rate` 全族最后一条有效决议统一停在 "
            f"{latest}（距今 {stale} 个月）；表内更晚的日期行「今值」为空，"
            "不是「利率没变」。⇒ 本表只能读**历史方向**（各央行加息/降息周期的相对位置），"
            "**不能当当前政策利率用**。")
    return out, note


def _external_reading(items: list[dict], dxy_item: dict | None, tight: dict,
                      dom_pct: float | None) -> dict:
    """外部约束的一句话结论 + 与国内松紧的组合判定"""
    flags = [v for v in tight.values() if v is not None]
    n, nt = len(flags), sum(1 for v in flags if v)
    if n == 0:
        return {"headline": "外部约束数据未就绪", "detail": "", "tone": "normal"}
    if nt >= 3:
        head, tone = f"外部约束偏紧，{nt}/{n} 项独立口径互相印证", "caution"
    elif nt == 2:
        head, tone = f"外部约束偏紧（{nt}/{n} 项指向收紧）", "caution"
    elif nt == 1:
        head, tone = f"外部约束大体中性（仅 {nt}/{n} 项偏紧）", "normal"
    else:
        head, tone = "外部约束不构成压力", "normal"

    name_map = {"cn_us_10y": "中美利差", "us_10y": "美债 10Y",
                "us_10y_2y": "美债期差", "dxy": "美元指数"}
    parts: list[str] = []
    tight_names = [name_map[k] for k, v in tight.items() if v]
    if tight_names:
        parts.append("指向收紧的项：" + "、".join(tight_names) + "。")
    loose_names = [name_map[k] for k, v in tight.items() if v is False]
    if loose_names:
        parts.append("偏松的项：" + "、".join(loose_names) + "。")

    combo = None
    if dom_pct is not None:
        dom_loose, ext_tight = dom_pct <= 40, nt >= 2
        if dom_loose and ext_tight:
            combo = ("**内松外紧** —— 国内政策有自主空间（资金价格在近一年低位），"
                     "但外部贴现率、利差与美元在收紧。这类组合下 A 股的分母端由国内主导，"
                     "上限却受汇率与跨境资金约束：盯汇率的稳定性，比盯 Shibor 更能提前看到风险。")
        elif dom_loose and not ext_tight:
            combo = "**内外同向宽松** —— 对权益最友好的外部环境：国内分母低、外部贴现率也没抬升。"
        elif not dom_loose and ext_tight:
            combo = "**内外同向收紧** —— 权益最不利的组合：国内分母在抬、外部贴现率也在抬。"
        else:
            combo = "**内紧外松** —— 外部不构成压力，制约来自国内资金价格自身。"
        parts.append(combo)

    if nt >= 2:
        parts.append("外部偏紧时，国内宽松对外资的定价优势会被利差与汇率打折 —— "
                     "⚠️ 这是**状态描述**，不是择时信号。")
    return {"headline": head, "detail": " ".join(parts), "tone": tone,
            "tight_n": nt, "total_n": n, "combo": combo}


def _external_state(bond: list[dict], dxy: list[dict], data_as_of: str,
                    dom_pct: float | None, as_of: str | None) -> dict:
    """外部约束块：三项利率读数 + 美元指数 + 美债曲线 + 全球央行方向 + 内外组合判读"""
    specs = [
        {"key": "cn_us_10y", "label": "中美 10Y 利差", "unit": "pp", "tone": "diff",
         "anchor": "mc-ext-curve",
         "hint": "中债 10Y 减美债 10Y。它衡量中国资产相对美国的**利差吸引力**："
                 "越窄（乃至倒挂越深）= 跨境资金留在人民币资产里的补偿越少 → 汇率与资本外流的压力源。"
                 "⚠️ 美债与中债的交易时段不同，两腿的原生日期可能差 1 个交易日"},
        {"key": "us_10y", "label": "美债 10Y", "unit": "%", "tone": "neutral",
         "anchor": "mc-ext-curve",
         "hint": "美国 10 年期国债收益率，全球风险资产的**贴现率**（全球无风险利率的锚）。"
                 "⚠️ 绝对值跨期不可比（1990 年的 8% 与现在的 5% 含义完全不同），只看近一年分位"},
        {"key": "us_10y_2y", "label": "美债 10Y − 2Y", "unit": "pp", "tone": "diff",
         "anchor": "mc-ext-curve",
         "hint": "美债 10Y 减 2Y，衡量长端曲线的**陡平与是否倒挂**。倒挂（<0）是 1980 年以来"
                 "最可靠的衰退预警单一信号；收益率高位 + 期差走平 = 紧缩中后段的典型形态"},
    ]
    items: list[dict] = []
    tight: dict = {}
    for sp in specs:
        k = sp["key"]
        pairs = _ext_pairs(bond, k)
        if not pairs:
            items.append({"key": k, "label": sp["label"], "value": None, "unit": sp["unit"],
                          "tone": sp["tone"], "status": "数据未就绪", "pct": None, "scale": None,
                          "highlight": False, "anchor": sp["anchor"], "hint": sp["hint"],
                          "as_of": None, "prev_year": None, "min_1y": None, "max_1y": None,
                          "chg20": None, "tight": None})
            tight[k] = None
            continue
        a0, cur = pairs[0]
        vals = [v for _, v in pairs]
        p = pctile(vals[:WINDOW_1Y], cur)
        prev = vals[WINDOW_1Y] if len(vals) > WINDOW_1Y else None
        chg20 = r(cur - vals[20], 4) if len(vals) > 20 else None
        win = vals[:WINDOW_1Y]
        if k == "us_10y_2y":
            word = _shape_brief(cur * 100, p)
        elif k == "cn_us_10y":
            word = _spread_word(p)
        else:
            word = _us_yield_word(p)
        st = word
        if prev is not None:
            st += (f"｜一年前 {prev:+.2f}{sp['unit']}" if sp["unit"] == "pp"
                   else f"｜一年前 {prev:.2f}{sp['unit']}")
        tg = _is_tight(p, k == "us_10y")
        tight[k] = tg
        items.append({"key": k, "label": sp["label"], "value": r(cur, 2), "unit": sp["unit"],
                      "tone": sp["tone"], "status": st,
                      "pct": p, "scale": scale(p, "近一年"), "highlight": is_extreme(p),
                      "anchor": sp["anchor"], "hint": sp["hint"],
                      "as_of": str(a0), "prev_year": r(prev, 2) if prev is not None else None,
                      "min_1y": r(min(win), 2) if win else None,
                      "max_1y": r(max(win), 2) if win else None,
                      "chg20": chg20, "tight": tg})

    # ---- 美元指数（★主口径 = 新浪官方日线；自算与实时快照作交叉校验）----
    dpairs = _ext_pairs(dxy, "dxy")
    snap = next(({"date": str(x["trade_date"]), "value": f(x["dxy_snapshot"])}
                 for x in dxy if x.get("dxy_snapshot") is not None), None)
    # 最近一个「官方日线 + 自算」同时有值的日期 → 两口径互比（比 only-snapshot 的比对样本多得多）
    dual = next((x for x in dxy if x.get("dxy_sina") is not None
                 and x.get("dxy_calc") is not None), None)
    calc_ref = ({"date": str(dual["trade_date"]), "value": f(dual["dxy_calc"]),
                 "dev_pct": r((f(dual["dxy_sina"]) / f(dual["dxy_calc"]) - 1) * 100, 3)}
                if dual and f(dual["dxy_calc"]) else None)
    dxy_item = None
    if dpairs:
        a0, cur = dpairs[0]
        vals = [v for _, v in dpairs]
        primary_row = next((x for x in dxy
                            if f(x.get("dxy_sina")) is not None
                            or f(x.get("dxy_calc")) is not None), None)
        p = pctile(vals[:WINDOW_1Y], cur)
        chg20 = r(cur - vals[20], 2) if len(vals) > 20 else None
        dev = r((cur / snap["value"] - 1) * 100, 3) if (snap and snap["value"]) else None
        dxy_item = {
            "key": "dxy", "label": "美元指数", "value": r(cur, 2), "unit": "",
            "tone": "neutral",
            "status": f"{_usd_word(p)}｜20日 {('%+.2f' % chg20) if chg20 is not None else '--'}",
            "pct": p, "scale": scale(p, "近一年"), "highlight": is_extreme(p),
            "anchor": "mc-ext-dxy",
            "hint": "美元指数 = 全球美元松紧的总闸门。**主口径取新浪官方日线**"
                    "（ICE 口径，1985-11-08 起 10,573 行）；表内另有两条交叉校验口径："
                    "① 自算（人民币中间价交叉汇率，与官方日线互比 |偏差| 中位 0.28%、最大 2.29%）；"
                    "② 新浪 `DINIW` 实时快照（标定锚，实测偏差 −0.004%）。",
            "as_of": str(a0), "chg20": chg20, "prev_year": r(vals[WINDOW_1Y], 2)
            if len(vals) > WINDOW_1Y else None,
            "snapshot": snap, "deviation_pct": dev,
            "calc": calc_ref,
            # 主口径是否为官方日线（前端据此在读数旁标口径；自算回落时要说清）
            "source": ("sina_official" if (primary_row
                                           and f(primary_row.get("dxy_sina")) is not None)
                       else "calc_fallback"),
            "tight": _is_tight(p, True),
        }
        tight["dxy"] = dxy_item["tight"]


    cbs, cb_note = _central_banks(data_as_of, as_of)
    reading = _external_reading(items, dxy_item, tight, dom_pct)
    return {
        "items": items, "dxy": dxy_item, "curve": _us_curve(bond),
        "central_banks": cbs, "cb_note": cb_note, "reading": reading,
        "as_of_bond": items[0]["as_of"] if items else None,
        "as_of_us": items[1]["as_of"] if len(items) > 1 else None,
        "as_of_dxy": dxy_item["as_of"] if dxy_item else None,
        "note": EXTERNAL_NOTE,
    }


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


def _shape_brief(spread_bp: float | None, p: float | None) -> str:
    """期限结构形状的**短词**（判读条用；不带括号解释）"""
    if spread_bp is None:
        return "数据未就绪"
    if spread_bp < 0:
        return "倒挂"
    if p is None:
        return "未知"
    if p <= 10:
        return "极度平坦"
    if p <= 30:
        return "偏平坦"
    if p <= 70:
        return "中性"
    if p <= 90:
        return "偏陡"
    return "极陡"


def _shape_word(spread_bp: float | None, p: float | None) -> str:
    """期限结构形状（KPI status 用；倒挂需要括号解释方向）"""
    b = _shape_brief(spread_bp, p)
    if b == "倒挂":
        return "倒挂（短端比长端贵）"
    if b == "未知":
        return "已就绪"
    return b


def _level_brief(p: float | None) -> str:
    """资金价格水平的**短词**（判读条用；KPI status 用带括号的 _level_word）"""
    if p is None:
        return "数据未就绪"
    if p <= 40:
        return "偏松"
    if p <= 60:
        return "中性"
    return "偏紧"


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
# 文案四判据 V1~V4 见 registry.py 卡片墙契约 ⑨（回归探针 _scratch/_probe_cardtext.py）。
# 本模块 2026-09-25 踩过 V2/V4：三张卡的判读条原来都在念分位（「近一年 38% 分位」）与
# 月数（「已连续 16 个月未动」），而这些数同卡 KPI 的刻度条与 status 里都有 —— 判读条
# 退化成了读数回声。现在判读条只给判断与影响（顺风/逆风、是否倒挂、政策动没动）。


def _verdict_level(level_pct) -> dict:
    """q1 钱现在贵不贵 —— Shibor 3M 的近一年分位（判断句：对权益是顺风还是逆风）"""
    if level_pct is None:
        return {"headline": "资金价格数据未就绪", "detail": "", "tone": "normal"}
    word = _level_brief(level_pct)
    if word == "偏松":
        head = "资金价格偏松，对权益是顺风"
    elif word == "偏紧":
        head = "资金价格偏紧，对权益是逆风"
    else:
        head = "资金价格中性，对权益无明确方向"
    tone = "opportunity" if level_pct <= 20 else "caution" if level_pct >= 80 else "normal"
    return {"headline": head, "detail": "利率是估值分母 —— 分位越低 = 钱越便宜，越利于估值扩张",
            "tone": tone}


def _verdict_expectation(spread_bp, spread_pct) -> dict:
    """q2 资金预期是松是紧 —— 期限结构的形状与是否倒挂（不复述分位）"""
    if spread_bp is None:
        return {"headline": "期限利差数据未就绪", "detail": "", "tone": "normal"}
    if spread_bp < 0:
        head = "期限结构倒挂，流动性紧张"
    else:
        head = f"期限结构{_shape_brief(spread_bp, spread_pct)}，尚未出现倒挂"
    # 倒挂不是「贵」而是「紧」—— 流动性紧张的定性，单独提级（与 _verdict_tone 同规）
    tone = "caution" if spread_bp < 0 else "normal"
    return {"headline": head,
            "detail": "利差越陡 = 短端越宽裕；**倒挂**（3M 低于隔夜）= 流动性紧张信号",
            "tone": tone}


def _verdict_policy(lpr: dict) -> dict:
    """q3 政策利率动没动 —— 政策姿态（月数与 LPR 报价在 KPI 区，判读条不重复）"""
    l1 = lpr.get("lpr_1y")
    if l1 is None:
        return {"headline": "LPR 数据未就绪", "detail": "", "tone": "normal"}
    idle = lpr.get("idle_months")
    head = "政策利率刚刚调整" if idle == 0 else "政策利率按兵不动"
    detail = ("LPR 每月 20 日报价；它与市场资金价格（Shibor）是否同步，本身就是信息"
              if lpr.get("since") else "")
    return {"headline": head, "detail": detail, "tone": "normal"}


# ---------------- 主装配 ----------------

@ttl_cache(600)
def money_cost(as_of: str | None = None, trend_days: int = 500) -> dict:
    """货币流动性（/api/analysis/money-cost）

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

    # ---- ④ 外部约束（批次 1，2026-09-25）：美债曲线 / 中美 10Y 利差 / 美元指数 ----
    # 取数多要 60 行：美债列 95.7% 非空，要凑满 250 个有效日得往后再捞一截。
    external = _external_state(
        _fetch_bond(days + 60, as_of), _fetch_dxy(WINDOW_1Y + 5, as_of),
        data_as_of, pct_3m, as_of)

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
    # 三个盒子各答一个正交问题：资金贵不贵（水平）/ 资金预期陡不陡（结构）/ 政策动没动（姿态）。
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
        # 外部约束的 KPI 单独一组（2026-09-25 批次 1）：主结论区只放国内三问 + 两个辅助读数，
        # 外部四项跟着 `mc-external` 分区一起渲染 —— 混进主结论区会把它从 5 个撑到 9 个，
        # 稀释「结论先行」。另：这四项**不标 card_rank**，卡片墙仍是 3 张 track 卡
        # （见《货币流动性观测体系设计》§6 批次 3 的「不拆大卡」原则）。
        "external_kpis": external["items"] + ([external["dxy"]] if external["dxy"] else []),
        "external": external,
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
