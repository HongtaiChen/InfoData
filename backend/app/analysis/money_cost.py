#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""分析模块：货币流动性（银行间资金价格 / 期限结构 / 政策姿态 / 外部约束）

🔒 第一原则「数据用来比较才有意义」（设计规范 §1.0）：
本模块落的是参照系 ② **自身纵比**（Shibor 3M 现在处于近一年什么位置）、
① **同类横比**（ON→1W→1M→3M 四个期限之间的利率阶梯）、
⑤ **结构分解**（陡/平/倒挂 —— 同样一个 3M 利率，曲线形状不同含义完全不同），
并在末尾做一次 ④ **交叉印证**（LPR 报价按兵不动 ↔ 市场利率是否在动；
资金价格 ↔ 权益位置；**国内松紧 ↔ 外部约束**）。

**五个切面（2026-09-25 批次 1 补齐第四面；2026-09-26 补第五面「数量」）**：
  ① 价格   —— Shibor 3M/隔夜（近一年分位）
  ② 预期   —— 期限结构（ON→1W→1M→3M 的陡平与倒挂）
  ③ 政策   —— LPR 1Y 报价与连续未动月数
  ④ 外部约束 —— 美债曲线 / 中美 10Y 利差 / 美元指数（`_external_state`）
  ⑤ 数量   —— 货币总量：BIS 境外美元信贷 / 美联储总资产 + A5 / 美国 M2 / 欧元区 M3 /
              中国 M2、外储与黄金 / 央行 OMO 操作量（`_quantity_state`）
开放经济下国内流动性受外部硬约束：利差决定跨境资金方向、美元指数决定全球美元松紧、
美债曲线是全球风险资产的贴现率。缺了这一面，只看 Shibor 会把「外部在收、国内在放」
误读成纯宽松。而**只补了价格、没补数量**，则会把「钱变贵了」当成「钱变少了」——
价格是边际、数量是水位，两者回答的是不同的问题（见 `_quantity_state` 的模块头纪律）。

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
    "本模块回答「货币流动性松紧」，**价格与数量两面**："
    "**价格**（Shibor 3M 在近一年什么位置）、"
    "**预期**（期限结构是陡还是平 —— 平坦说明短端没有溢价要求、市场预期资金持续宽松；"
    "倒挂则是流动性紧张的信号）、**政策**（LPR 报价与连续未动月数）、"
    "**外部约束**（美债曲线、中美 10Y 利差、美元指数 —— 开放经济下国内流动性的硬约束）；"
    "**数量**（货币总量：BIS 境外美元信贷 / 美联储总资产与美元净流动性 / 美国 M2 / 欧元区 M3 / "
    "中国 M2、外储与黄金 —— 见 `quantity` 块）。"
    "⚠️ 价格是**边际**、数量是**水位**：只看价格会把「没人借钱」读成「钱变多了」，"
    "只看数量会把「存量高企但边际收紧」读成「还在放水」——两者必须配着看。"
    "⚠️ 利率的绝对值跨期不可比，所有价格类判断一律走「近一年分位」；"
    "而**存量水平不做分位**（单调上行序列的分位恒贴近 100，是伪信号），改看同比与同比的方向。"
    "**领域边界**：本模块管的是**钱的价格与数量（因）** —— 资金贵不贵、政策给不给、"
    "外部让不让、总量多不多；至于「钱有没有真的进到市场里」"
    "（人民币汇率体现的跨境资金意愿、新发基金、产业资本回购），那是**果**，归「资金温度」，"
    "不在本模块重复。"
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


# ---------------- 口径两轴：region × market（R8 · 2026-09-27）----------------
# 需求依据：需求文档 v2.5 的 R8「地区／市场口径上标」。本页横跨 6 类市场、
# 4 个地区，原先一个口径字都没有 ⇒ 读者会拿「货币总量的存量口径」和
# 「银行间同业拆借的价格口径」并列比较（见需求 §四 S5 失败②）。
#
# 🔴 三条纪律（勿破坏）：
# 1. **唯一真源**：16 项的两轴归属只在本 `SCOPE` 里可枚举 ⇒ 验收脚本可直接
#    `import SCOPE` 做 MECE（互斥完备）断言，不必 read 源码文本。
# 2. **单一注入点**：`_tag()` 是唯一写入口，覆盖 3 个装配点（结论区字面量 /
#    外部 spec 的两个分支 / 数量工厂）。漏注入一处 ⇒ 16 项里掉几项，是典型的
#    「看着改好了」。
# 3. **label 前缀写进 `label` 本身** ⇒ 结论区卡 / 卡片墙卡 / 明细区 KpiCards
#    三处自动逐字一致（它们消费同一个字段），R8 的「逐字一致」由构造保证。
# ⚠️ 两轴各自合法 ≠ 组合合法：「欧元区 × 银行间」是无效组合（见 `LEGAL_COMBOS`）。
SCOPE: dict[str, tuple[str, str]] = {
    # key                 region          market
    "shibor_3m":        ("中国 · 在岸", "银行间"),
    "shibor_on":        ("中国 · 在岸", "银行间"),
    "term_spread":      ("中国 · 在岸", "银行间"),
    "curve_dispersion": ("中国 · 在岸", "银行间"),
    "lpr":              ("中国 · 在岸", "贷款报价"),
    "cn_m2":            ("中国 · 在岸", "货币总量"),
    "cn_fx_reserve":    ("中国 · 在岸", "外汇储备"),
    "us_m2":            ("美国",        "货币总量"),
    "fed_assets":       ("美国",        "央行资产"),
    "us_net_liq":       ("美国",        "央行资产"),
    "us_10y":           ("美国",        "国债"),
    "us_10y_2y":        ("美国",        "国债"),
    "eu_m3":            ("欧元区",      "货币总量"),
    "cn_us_10y":        ("全球 · 离岸", "国债"),
    "dxy":              ("全球 · 离岸", "外汇"),
    "bis_offshore":     ("全球 · 离岸", "境外信贷"),
}
REGIONS = ("中国 · 在岸", "美国", "欧元区", "全球 · 离岸")
MARKETS = ("银行间", "贷款报价", "货币总量", "央行资产", "国债", "外汇", "外汇储备", "境外信贷")
LEGAL_COMBOS = frozenset({
    ("中国 · 在岸", "银行间"), ("中国 · 在岸", "贷款报价"),
    ("中国 · 在岸", "货币总量"), ("中国 · 在岸", "外汇储备"),
    ("美国", "货币总量"), ("美国", "央行资产"), ("美国", "国债"),
    ("欧元区", "货币总量"),
    ("全球 · 离岸", "国债"), ("全球 · 离岸", "外汇"), ("全球 · 离岸", "境外信贷"),
})


# =====================================================================================
# R12 用途口径五段式（P2 · 2026-09-27）
# =====================================================================================
# 需求 R12：每个读数的悬浮说明统一为五段 ——
# **① 是什么 · ② 做什么 · ③ 为什么 · ④ 异常情况 · ⑤ 何时失效**。
# **前四段是必需集（必写）**；⑤「何时失效」**有陷阱才写**，无陷阱则**整键省略**（不做补位）。
#
# 🔴 四条纪律（改动本段前必读）：
# 1. **段序与标签只在 `HINT_SEGMENTS` 定义一次**。前端不内联词表、不认 key 顺序 ——
#    一律按 `order` 排；分组线按 `group` 画（D6-a：前端认组不认 key）⇒ 以后加段只改这里。
# 2. **⑤ 缺省 = 不出现**：`_hint_parts()` 用 `if s["key"] in row` 过滤，不产占位项、不产空串。
#    ⇒ 前端"缺省不补位"是**后端保证**的结果，前端无需为它写特判分支。
# 3. **文案不许写进装配点**：五段全文只住在本字典，装配点通过 `_tag()` 自动获得 `hint_parts`
#    —— 与两轴 region/market 同一哲学（由 key 派生、不许手抄）。
# 4. **字数预算只告警、不抛错**：R12.1 定的预算是「必需集 ≤250 字 / 含⑤ ≤310 字」，但实测
#    R12.3 的定稿文案（去掉当日值后）仍超预算 —— 若在此 `raise`，后端**直接起不来**。
#    ⇒ 硬拦只拦**结构性**错误（见 `_selfcheck_hint_parts`），超标走 WARNING ＋ 验收报告。
#    ⚠️ 这是**需求 R12.1 与 R12.3 之间的自相矛盾**（已记入勘误，待所有者裁量），
#       不是本段实现走样；预算的实际用途是限制浮窗行数，而浮窗已明确不加 max-height。
HINT_SEGMENTS: list[dict] = [
    {"key": "what",     "label": "是什么",   "order": 1, "required": True,  "group": "understand"},
    {"key": "use",      "label": "做什么",   "order": 2, "required": True,  "group": "understand"},
    {"key": "why",      "label": "为什么",   "order": 3, "required": True,  "group": "understand"},
    {"key": "abnormal", "label": "异常情况", "order": 4, "required": True,  "group": "operate"},
    {"key": "invalid",  "label": "何时失效", "order": 5, "required": False, "group": "operate"},
]
# 🔴 分段字数预算（需求 R12.1）。**2026-09-27 由 250/310 放宽到 300/400**（决策：方案 A）：
#    原预算与 R12.3 的**定稿文案**自相矛盾 —— 按 R12.3 逐字落进 `HINT_PARTS`，实测
#    `term_spread` 必需集 **270 字**（上限 250）、含 ⑤ **359 字**（上限 310），**无法通过
#    改代码消除**。所有者裁定放宽预算（而非压缩已定稿文案），并留出 P3 铺满 16 条时的余量。
#    放宽后的量测依据：浮窗 `max-width: 320px`、正文 12.5px/1.65 ⇒ 约 23 字/行；
#    400 字 ≈ 18 行 ≈ 370px 正文高度（浮窗总高约 420px），仍在 1080p 可视区内。
HINT_BUDGET = {"required": 300, "total": 400}

# 🔴 与需求 R12.3 的**唯一有意偏离**：定稿文案里的 `当前 1.43%` / `当前 24.4 分位 ⇒ 偏平坦`
#    这类**当日数值**已剔除。三个理由：
#    ① 数值已有唯一真源（卡片大数字 + 分位刻度条），口径里再写一遍就是**第二个真源**，
#       次日必然与卡面不一致（"卡片说 5.2、口径说 6.6"—— 比不写更坏）；
#    ② 硬编码的当日值**必然过期**，且过期是静默的；
#    ③ 剔除后文案与日期解耦，才能安全地以常量形式写进代码（本字典的前提）。
#    ⇒ 保留的是**判据本身**（阈值、口径阶梯、机制），那些才是"口径"该承载的信息。
HINT_PARTS: dict[str, dict[str, str]] = {
    # ── 样板①（**正**：含 ⑤）· 期限利差 · 来源：需求 R12.3 第 2 条 ──
    "term_spread": {
        "what": "3M Shibor − 隔夜 Shibor，衡量资金**期限结构**的宽窄，单位 bp（基点，1bp = 0.01%）。",
        "use": "判断「这个松是全面的、还是只有短端松」。它回答松的**结构**，"
               "不回答松的**程度**（程度看 Shibor 3M）。",
        "why": "利差被压平 = 短端被投放填满、机构敢用隔夜滚长钱 → 杠杆套息交易活跃 → "
               "一旦利差回摆，去杠杆会先砸高杠杆、高波动品种。",
        "abnormal": "**倒挂（<0，即 3M 比隔夜还便宜）是流动性紧张的经典信号**；"
                    "分位口径：≤10 极度平坦 / ≤30 偏平坦 / 30–70 中性 / 70–90 偏陡 / >90 极陡。",
        "invalid": "① 曲线单调时它与「期限离散度」**信息共线**，二者只看一个即可；"
                   "② 跨季/跨节时点会因“跨节资金”虚增，与趋势无关；"
                   "③ 分位窗口为近一年，跨越降准/降息台阶后参照系整体漂移。",
    },
    # ── 以下 15 条为 P3 铺满（2026-09-27）。来源：需求 R12.3 的 16 条定稿文案；
    #    与定稿文案的**唯一有意偏离**同上（剔除当日数值，理由见上方注释）。
    #    ⑤「何时失效」**16/16 全部保留** —— R12.3 每条都写了实质陷阱，
    #    "有陷阱才写"的判据下没有需要省略的项（决策 #7 目标 ≥14/16 已超额达成）。
    # ── 1 · Shibor 3M ──
    "shibor_3m": {
        "what": "3 个月期上海银行间同业拆放利率 —— 在岸人民币银行间的 3 个月期资金价格，单位 %。",
        "use": "判断「钱贵不贵」，是四条链条最上游的价格锚。它回答融资成本的**水平**，"
               "不回答投放的**数量**（数量看央行 OMO / 美联储总资产）。",
        "why": "3M 是银行负债成本的定价基准 → 它下行会压低新发贷款与债券的票息 → "
               "折现率下降 → 高估值成长风格受益、银行息差承压。",
        "abnormal": "看**近一年分位**：≤20 低位（偏松）/ ≤40 偏松 / 40–60 中性 / 60–80 偏紧 / "
                    ">80 高位；≤10 或 ≥90 后端金色高亮。**绝对水平不设阈值** —— "
                    "同一个 1.4% 在有的年份算高、有的年份算低。",
        "invalid": "① 绝对值**跨期不可比**，一律看分位；② 它是**报价**利率不是成交利率，"
                   "极端行情下报价可能失真；③ 季末 / 年末 / 缴税日的跳升是时点性的，不构成趋势。",
    },
    # ── 3 · LPR 1Y ──
    "lpr": {
        "what": "1 年期**贷款市场报价利率**，由报价行每月 20 日报出，单位 %。",
        "use": "判断「政策姿态动没动」。LPR 是政策**传导的结果**；与 Shibor 对照，"
               "可把「市场自己在松」与「政策在松」分开。",
        "why": "LPR 不动 → 存量浮动利率贷款的利息支出不变 → 实体付息成本不降 → "
               "宽货币没有变成宽信用；它的下调才会改善企业与房贷的还款现金流。",
        "abnormal": "**不做分位** —— 政策利率是台阶状，长期不变时「分位」无意义。改看三件事："
                    "① 本轮持续时长；② 上次调整的方向与幅度；③ **LPR 与 Shibor 的背离**："
                    "Shibor 明显下行而 LPR 不动 ⇒ 宽松来自市场端而非政策端。",
        "invalid": "① 它是**报价**利率，不等于实际贷款利率（真实成本看新发放贷款加权平均利率）；"
                   "② 每月 20 日才更新，**月中不变是设计而不是信号**；"
                   "③ 1Y 与 5Y 是两条腿，只看 1Y 会漏掉房贷利率取向。",
    },
    # ── 4 · Shibor 隔夜 ──
    "shibor_on": {
        "what": "隔夜期上海银行间同业拆放利率，单位 %。",
        "use": "判断「**当下这一天**钱紧不紧」。它是资金面松紧**最敏感**的读数，"
               "也是观察央行 OMO「呵护不呵护」的第一现场。",
        "why": "隔夜是银行日终轧差的边际价格 → 它跳升最先抬高杠杆资金的滚动成本 → "
               "压制两融与中性策略的持仓意愿；这是资金面传到股市**最快**的一环。",
        "abnormal": "近一年分位：≤20 低位 / ≤40 偏松 / 40–60 中性 / 60–80 偏紧 / >80 高位"
                    "（与 Shibor 3M 同一套判定）。极端信号：**持续贴近 SLF 利率上限**"
                    "（走廊上沿）或单日跳升 >30bp。",
        "invalid": "① **噪声最大** —— 受缴税、缴准、跨节、政府债发行扰动，单日跳升不构成趋势；"
                   "② 必须与 7 天逆回购利率、R007 对照才可定性；③ **日频数据不可外推为月度结论**。",
    },
    # ── 5 · 期限离散度 ──
    "curve_dispersion": {
        "what": "ON/1W/1M/3M 四期限 Shibor 的**标准差**，单位 %。",
        "use": "判断曲线**中间**有没有拐点。期限利差只看**两端**，离散度看**全形** —— "
               "能发现两端看不出的中间畸变。",
        "why": "中间期限异常**凹陷** → 该期限资金淤积、利率被压低 → 机构倾向用该期限滚动融资 → "
               "一旦结构性因素（跨季、MPA 考核）消退，该期限利率回摆、滚动成本突升。",
        "abnormal": "**没有天然零点、没有均值回复 ⇒ 不能做分位**。异常判据：曲线**非单调**"
                    "（不满足 ON < 1W < 1M < 3M）**且**中间期限相对两端线性插值**凹陷 >5bp**；"
                    "凸起不计（7 天资金跨月末的日历效应，不是曲线异常）。",
        "invalid": "曲线**单调**时它与「期限利差」**信息共线**、无独立信息量 —— 此时本卡不进结论区，"
                   "只在期限结构明细的读数条里显示（处置见 R14-4）。",
    },
    # ── 6 · 中美 10Y 利差 ──
    "cn_us_10y": {
        "what": "中债 10Y 收益率 − 美债 10Y 收益率，单位 pp（百分点，1pp = 1%）。",
        "use": "判断「人民币资产相对美元的**吸引力**」，是汇率与资本外流的**压力源**读数。",
        "why": "利差收窄 → 对冲后持有人民币债券的收益低于美债 → 外资配置盘减配 → "
               "影响债市增量资金与汇率预期 → 再经汇率预期影响北向资金的进出节奏。",
        "abnormal": "近一年分位 **≤35 记为「外部收紧」一项**；≤10 后端金色高亮"
                    "（近一年最窄 = 极端）。",
        "invalid": "① 美债与中债**交易时段不同**，两腿原生日期可能差 1 个交易日；"
                   "② 资本管制使「利差 → 资金流」的传导**不是即时的**，利差走阔 ≠ 资金立刻回流；"
                   "③ 它不是汇率预测器。",
    },
    # ── 7 · 美债 10Y ──
    "us_10y": {
        "what": "美国 10 年期国债收益率，单位 %。",
        "use": "它是**全球无风险利率的锚**，也是全球风险资产估值分母的直接来源。",
        "why": "它上行 → 全球贴现率抬升 → 长久期成长资产估值先于盈利受压；"
               "同时抬高美元融资成本 → 压制新兴市场风险偏好与再通胀交易。",
        "abnormal": "近一年分位 **≥65 记为「外部收紧」一项**；≥90 后端金色高亮"
                    "（近一年最高 = 极端）。绝对水平可参照前一轮周期高点作为强警示。",
        "invalid": "① 绝对值**跨期不可比**（不同年代的同一数字含义完全不同）；"
                   "② 它是**名义**收益率，高通胀期要看 TIPS 实际利率才有意义；"
                   "③ 期限溢价与降息预期会让它与「美联储政策松紧」短期背离。",
    },
    # ── 8 · 美债 10Y − 2Y ──
    "us_10y_2y": {
        "what": "美债 10Y 收益率 − 2Y 收益率，单位 pp。",
        "use": "判断美国长端曲线的**陡平与倒挂**，是市场对「紧缩末期 / 衰退风险」的核心定价。",
        "why": "倒挂 = 市场预期未来短端利率低于当前 → 隐含对增长与通胀的下修 → 衰退预期 → "
               "经企业盈利下修传导至全球权益。",
        "abnormal": "**<0 = 倒挂**；倒挂是 1980 年以来最可靠的**单一**衰退预警信号，"
                    "历史上前置衰退 6–24 个月。分位 ≤35 记「外部收紧」一项。",
        "invalid": "① 倒挂的**领先期长且不稳定**（6–24 个月），**不能用于择时**；"
                   "② 需与信用利差（HY OAS）配合才能判断「会不会真衰退」；"
                   "③ 「高位 + 平坦」是紧缩中后段的典型形态，但**形态相似 ≠ 结局相同**。",
    },
    # ── 9 · 美元指数 ──
    "dxy": {
        "what": "美元对一篮子货币（EUR / JPY / GBP / CAD / SEK / CHF，欧元权重约 58%）"
                "的加权汇率指数。",
        "use": "它是**全球美元松紧的总闸门**，回答的是「美元相对强弱」这一层。",
        "why": "美元走强 → 非美主体的美元债务偿付成本上升 → 新兴市场融资条件收紧；"
               "同时美元计价大宗对非美买家变贵 → 输入型需求走弱 → 压制全球再通胀交易。",
        "abnormal": "分位口径：≤10 近一年最弱 / ≤30 偏弱 / 30–70 居中 / 70–90 偏强 / "
                    ">90 近一年最强；近一年分位 **≥65 记「外部收紧」一项**。",
        "invalid": "① 它**只反映美元与他国货币的相对强弱**，推不出美元绝对流动性"
                   "（那是美元净流动性的活）；② 权重偏欧洲（欧元约 58%），"
                   "「欧元涨」会机械压低 DXY；③ 卡内已有**三条口径互校**"
                   "（官方日线 / 自算 / DINIW 快照）。",
    },
    # ── 10 · 境外美元信贷（离岸美元）──
    "bis_offshore": {
        "what": "BIS 官方口径 —— **全体非美借款人**持有的**美元计价**信贷"
                "（跨境 + 借款人本地外币），单位万亿美元。",
        "use": "看**离岸美元存量**的水位与趋势，是「美元潮汐」的官方读数。",
        "why": "离岸美元信贷收缩 → 非美主体再融资缺口扩大 → 被迫去杠杆或抛售美元资产 → "
               "放大新兴市场汇率与信用利差波动。",
        "abnormal": "**不做分位**（季频、趋势性、低波动）。看两点：① **同比**"
                    "（为正 = 离岸美元仍在扩张）；② **结构** —— 债券占比快速上升 = "
                    "融资从银行贷款转向市场，对利率更敏感。",
        "invalid": "① **季度数据、T+1 季发布**，绝不能当「当前」读数；"
                   "② 只含**美元**口径，不含欧元 / 日元计价的境外信贷；"
                   "③ 口径已实测自证（贷款 + 债券 − 总量 = 0.0%）。",
    },
    # ── 11 · 美联储总资产 ──
    "fed_assets": {
        "what": "美联储资产负债表总规模（周度 H41），单位万亿美元。",
        "use": "判断美联储在**扩表还是缩表**（扩表 = 放水、缩表 = 收水），"
               "也是「美元净流动性」的被减项之一。",
        "why": "缩表直接减少银行准备金供给 → 货币市场利率承压 → 传导至全球美元融资成本；"
               "扩表则反向 —— 它是美元流动性的总阀门。",
        "abnormal": "**不做分位**（存量、单调）。看三点：① **13 周方向**；② **52 周方向**；"
                    "③ 缩表节奏是否偏离计划（taper / 停止）。",
        "invalid": "① 它是**总闸门但不等于市场流动性** —— 必须减去 TGA 与 ON RRP 才是"
                   "「真流动性」（见美元净流动性卡）；② 周度数据固定**周三**发布口径，"
                   "月中看到的可能滞后一周；③ 不能据此推断准备金充裕度"
                   "（那要看单一银行准备金余额）。",
    },
    # ── 12 · 美元净流动性（合成 A5）──
    "us_net_liq": {
        "what": "**合成口径、非官方**：美联储总资产 − 财政部 TGA 余额 − 隔夜逆回购 ON RRP，"
                "单位万亿美元。",
        "use": "看**美元「真实可用」流动性的水位**。它是市场最常用的美元流动性代理变量 —— "
               "总闸门减掉被财政部与美联储自己「锁住」的部分，剩下的才是流向市场的钱。",
        "why": "TGA 与 ON RRP 是两把「锁」：它们吸收投放 → 市场可用资金减少 → "
               "货币基金与回购市场资金变紧 → 风险资产估值分母端承压；水位持续回落即这一链条在起作用。",
        "abnormal": "**非官方、无历史分布可比 ⇒ 不做分位**。看 **13 周变化方向**："
                    "持续下行（如 −5% 以上）是美元流动性收紧的强信号；三腿**同日**读数缺失时"
                    "当日不出值。",
        "invalid": "① 🔴 **它不是官方公布值**，是本项目自合成；② 三腿必须取**同一日期**，"
                   "拿各自最近值硬凑会产生假信号；③ 它不含海外美元（那是 BIS 卡），"
                   "也不含货币基金 / 回购市场的结构变化。",
    },
    # ── 13 · 美国 M2 ──
    "us_m2": {
        "what": "美国 M2 货币供应量（月度 H6）＝ M1 + 储蓄存款 + 小额定期 + 零售货币基金。"
                "原生单位**十亿美元**，本卡已折**万亿美元**。",
        "use": "看美国**广义货币水位与增速**，是判断「美元流动性宽还是收」的月度慢变量，"
               "与美联储总资产（政策端）互为印证。",
        "why": "M2 增速回升 → 名义购买力扩张 → 支撑消费与企业名义营收 → "
               "也决定通胀回落的粘性；它是美联储政策空间的上游约束。",
        "abnormal": "**不做分位**（存量、单调上行 ⇒ 分位恒贴近 100，是**伪信号**）。"
                    "看**同比**与**同比方向的拐点**：同比由负转正 = 货币收缩结束。",
        "invalid": "① 原生单位是十亿美元，与欧元区 M3（百万欧元）、中国 M2（亿元）"
                   "**单位全不同** —— 本卡已统一折算，**拿原值直接比大小会得到相反结论**；"
                   "② 疫情后 M2 与物价 / 名义 GDP 的关系**已不稳定**（货币流通速度变化）；"
                   "③ 月度且有事后修订。",
    },
    # ── 14 · 欧元区 M3 ──
    "eu_m3": {
        "what": "欧元区 M3（月度 ECB 口径），原生单位**百万欧元**，本卡按 EUR/USD 中间价折美元。",
        "use": "看欧元区广义货币水位 —— 它是 ECB 政策与欧元的「数量侧」参照，"
               "也是判断「欧元区流动性 vs 美国」的相对位置。",
        "why": "M3 增速与 ECB 参考值的距离 → 决定 ECB 的政策容忍度 → 影响欧元利率与汇率 → "
               "经 EUR/USD 传导到美元指数与非美资产定价。",
        "abnormal": "**不做分位**。看**同比**与 ECB 参考值的关系：增速持续低于参考值 ⇒ 政策偏紧。",
        "invalid": "① 数据源**只有 DBnomics 一条通道**（ECB 直连三域全断）—— 它失效则本卡断供，"
                   "DQ freshness 是唯一防线；② 折算用 **EUR/USD 中间价**，"
                   "汇率变动会污染同比的可比性；③ 月度、滞后约 3 周。",
    },
    # ── 15 · 中国 M2 ──
    "cn_m2": {
        "what": "中国 M2（月度，原生单位**亿元**），折美元需 USD/CNY 中间价。",
        "use": "看中国**广义货币水位与活化度**：M2 同比看「水多不多」，"
               "**M1−M2 剪刀差**看「钱活不活」。",
        "why": "M2 扩张但剪刀差为负 → 钱停在定期 / 理财 → 宽货币未进入实体与股市；"
               "只有剪刀差转正并走阔，增量资金才会去找风险资产。",
        "abnormal": "**不做分位**。看两点：① M2 同比；② **M1−M2 剪刀差** —— 为负 ⇒ 活化度回落；"
                    "剪刀差由负转正并走阔是股市增量资金的经典前兆。",
        "invalid": "① **不折美元就与美国 M2 比大小是错的** —— 两者单位不同"
                   "（亿元 vs 十亿美元），折算前后的数量级甚至方向都不同；"
                   "② 口径历经多次修订，**跨口径的长期序列不可直接比**；"
                   "③ 月度、央行不发布环比，只有同比与余额。",
    },
    # ── 16 · 中国官方外汇储备 ──
    "cn_fx_reserve": {
        "what": "中国官方外汇储备（月度，原生单位**亿美元**，无需汇率折算）。",
        "use": "看**央行的干预能力与汇率防守弹药**。外储下降 = 央行在消耗弹药稳汇率、"
               "或有资本外流。",
        "why": "外储持续下降 → 稳汇率弹药减少 → 汇率防守的容错空间收窄；"
               "它同时反映跨境资金的净流出强度。",
        "abnormal": "**不做分位**。看三点：① **月度变动的方向与幅度**"
                    "（连续大幅减少 = 干预 / 流出压力）；② **与汇率的关系**"
                    "（人民币贬值而外储稳 ⇒ 干预少、属市场自发）；③ **黄金连续增持月数**"
                    "（储备多元化的持续信号）。",
        "invalid": "① ⚠️ 中国层「外储」有**三套口径**：本卡（美元官方口径）／"
                   "黄金**实物量**（万盎司，非金额）／央行表内**人民币口径**（亿元）—— "
                   "三者**单位不同、折算率不是市场汇率** ⇒ **严禁相加、严禁按市价互校**；"
                   "② 外储变动含**估值效应**（美元指数与美债价格变动会机械改变折算值），"
                   "不全是交易性变动。",
    },
}


def hint_parts_budget() -> list[dict]:
    """逐项字数体检（供验收脚本与启动日志消费）。⚠️ 只报告，不判错 —— 见纪律 4。"""
    out: list[dict] = []
    for key, row in sorted(HINT_PARTS.items()):
        req = sum(len(row[s["key"]]) for s in HINT_SEGMENTS if s["required"] and s["key"] in row)
        tot = sum(len(row[s["key"]]) for s in HINT_SEGMENTS if s["key"] in row)
        out.append({
            "key": key, "required_chars": req, "total_chars": tot,
            "has_invalid": "invalid" in row,
            "over_required": req > HINT_BUDGET["required"],
            "over_total": tot > HINT_BUDGET["total"],
        })
    return out


def _selfcheck_hint_parts() -> None:
    """导入时自检（P2）：**硬拦结构性错误**，字数预算只 WARNING。

    为什么硬拦这几条而不是"写文案时小心点"：这四条一旦破了，症状都是**静默**的 ——
    段键拼错 ⇒ 该段凭空消失；必需集缺段 ⇒ 读者拿到的口径信息量只有别人的 3/4；
    空串段 ⇒ 前端渲染出一条空白（比缺省更糟：缺省是"不补位"，空串是"占位"）。
    """
    valid = {s["key"] for s in HINT_SEGMENTS}
    for key, row in HINT_PARTS.items():
        if key not in SCOPE:
            raise ValueError(f"HINT_PARTS[{key!r}] 不是 SCOPE 登记的读数 key（16 项全集见 SCOPE）")
        bad = sorted(set(row) - valid)
        if bad:
            raise ValueError(f"HINT_PARTS[{key!r}] 含未定义段键 {bad}（合法键={sorted(valid)}）")
        miss = [s["key"] for s in HINT_SEGMENTS if s["required"] and s["key"] not in row]
        if miss:
            raise ValueError(f"HINT_PARTS[{key!r}] 必需集缺段 {miss}（①②③④ 必写）")
        empty = sorted(k for k, v in row.items() if not v.strip())
        if empty:
            raise ValueError(f"HINT_PARTS[{key!r}] 有空段 {empty}"
                             "（无陷阱应**整键省略**，不是写空串）")
    log = logging.getLogger(__name__)
    for b in hint_parts_budget():
        if b["over_required"] or b["over_total"]:
            log.warning(
                "R12 字数超预算 %s：必需集 %d/%d 字、含⑤ %d/%d 字"
                "（预算已于 2026-09-27 放宽到 300/400，此处超出即为**新增**超限）",
                b["key"], b["required_chars"], HINT_BUDGET["required"],
                b["total_chars"], HINT_BUDGET["total"])


def _hint_parts(key: str, row: dict | None = None) -> list[dict]:
    """按 `HINT_SEGMENTS` 顺序生成**有序**段数组；⑤ 缺省时**整键省略**（不产占位项）。

    `row` 只留作**可测**的口子：缺省走 `HINT_PARTS[key]`；测试可传入一个不含 `invalid`
    的行来验证"缺省不补位"（需求附录 A⑥ 的「一正一反两条样板」，见 `_scratch/_verify_p2.py`）。
    """
    r = HINT_PARTS.get(key) if row is None else row
    if not r:
        return []
    return [{"key": s["key"], "text": r[s["key"]]}
            for s in HINT_SEGMENTS if s["key"] in r]


_selfcheck_hint_parts()


def _tag(key: str, d: dict) -> dict:
    """给任一 KPI 注入两轴字段 + label 前缀（幂等）。🔴 唯一注入点，见上方纪律 2。

    ⚠️ 未在 `SCOPE` 登记的 key 会**显式抛错**而不是静默跳过 —— 静默跳过等于
       16 项里悄悄掉几项（正是纪律 2 要防的"看着改好了"）。新增读数时必须同批登记。
    """
    if key not in SCOPE:
        raise KeyError(f"_tag: key={key!r} 未在 SCOPE 登记（16 项全集见 SCOPE）")
    r, m = SCOPE[key]
    d["region"], d["market"] = r, m
    base = d.get("label") or ""
    prefix = f"{r} / {m}　"
    # ⚠️ 幂等判据必须比对**完整前缀**，不能只看 `startswith(region)` ——
    #    实测踩过：`美国 M2` / `欧元区 M3` 这些 label **本身就以地区名开头**，
    #    用 `startswith("美国")` 判会被误认为"已加过前缀"而不加（A22 实测抓出 2/16）。
    if base and not base.startswith(prefix):
        d["label"] = prefix + base
    # R12 五段（P2 · 2026-09-27）：与两轴**同一哲学** —— 由 key 派生、不许在手写点抄文案。
    # 登记在 `HINT_PARTS` 的项自动获得 `hint_parts`；未登记项**不带该字段** ⇒ 前端回落旧 `hint`。
    # ⚠️ 本模块已于 P3（2026-09-27）**铺满 16/16** ⇒ **本模块不再进入回落态**（实测全页 15/15 五段）；
    #    该回落分支仍为**另 4 个模块**与**未来新增读数**保留 —— 由 `_probe_p3_dom.js` 的 `DP2-6b`
    #    **读源码**断言其存在（避免"以为还在、其实已被删"）。
    # ⚠️ 另注：下方各手写点的 `hint` 与 `HINT_PARTS` 是**两份独立拷贝**（`_tag` 只追加、不派生），
    #    P3 后前端只消费 `parts` ⇒ 那些 `hint` 已成**展示上不可达**的内容。是否停发见落地方案 §6.7 勘误 ⑬。
    parts = _hint_parts(key)
    if parts:
        d["hint_parts"] = parts
    return d


def _scope_text(items: list[dict], head: str = "本区口径") -> dict:
    """区块级口径声明：由 items 的两轴字段**聚合**生成，绝不手写字面量。

    🔴 调用时机：必须在 R14-4 的条件过滤**之后** —— 若在过滤前调用，异常日与
       常态日都会得到「5 张」的计数，正是需求 E4 勘误（"上一版笼统断言银行间
       市场"）那类错误的翻版。
    """
    rc: dict[str, int] = {}
    mc: dict[str, int] = {}
    for it in items:
        r, m = it.get("region"), it.get("market")
        if r:
            rc[r] = rc.get(r, 0) + 1
        if m:
            mc[m] = mc.get(m, 0) + 1
    r_txt = "、".join(f"{k} {v}" for k, v in rc.items())
    m_txt = "、".join(f"{k} {v} 张" for k, v in mc.items())
    return {"region_counts": rc, "market_counts": mc,
            "text": f"{head}：{r_txt}｜市场：{m_txt}"}


# R14-4 判据阈值：中间期限相对两端线性插值的**凹陷**幅度门槛（bp）。
# 只认凹陷方向 ⇒ 天然排除 1W 跨月末凸起这一类日历效应（见 `_curve_anomaly` 注释）。
CURVE_ANOMALY_BP = 5.0


def _curve_anomaly(curve: list[dict]) -> dict:
    """四期限是否出现「中间期限异常**凹陷**」（R14-4 条件显示的判据 · 方案 A）。

    判据两条**同时成立**才算异常：
      ① 曲线非单调（不满足 ON < 1W < 1M < 3M）；
      ② **凹陷方向**存在中间期限相对两端线性插值低于门槛（取凹陷最深者）。

    🔴 为什么是「**只认凹陷**」而不是原来的「任一形态 + 取绝对值最大者」——
       2026-09-27 实测定案（方案 A）。全史 4989 个交易日扫描：
       原判据（双形态 + max|偏离| > 5bp）年均触发 **54.6 日**，其中 **88% 是 `1W` 高于
       两端的**凸起** —— 那是 7 天资金**跨月末的日历效应**，不是曲线异常。两个后果：
       ① 结论区卡数每月抖动 4~5 次，「常态 4 张」这个新基线不稳定；
       ② 「狼来了」式提醒会把读者的注意力训练到忽略它。
       ⇒ 按 R6-1 的方案 A 把日历效应从判据中**排除**（处理根因，而非调高阈值）。

    🔴 为什么取「**凹陷分支的极值**」而不是「max|偏离| 且要求它是凹陷」（备选 A-1）——
       A-1 会被凸起**遮蔽**：若 `1W` 凸起 +20bp 而 `1M` 凹陷 -8bp，`max|偏离|` 取到的是
       `1W` 的 +20 ⇒ 判为非凹陷 ⇒ **真实异常被静默漏报**（实测近 4 年漏报 **17 日**）。
       只在凹陷分支里取极值，则不受凸起幅度干扰。

    实测触发（`_scratch/_scan_anomaly_A.py`，2026-09-27）：
       口径                全史年均   近 4 年年均
       原判据（双形态）      54.6       37.2
       A-1（max 且凹陷）      6.4       11.6   ← 有遮蔽漏报
       **A-2（本实现）**    **13.1**   **15.0**   ← 零漏报，仍在「年均 ≤20 日」门槛内
    当日（2026-09-24）：1W +1.4bp、1M +1.8bp，**无凹陷** ⇒ 非异常（结论区 4 张）。
    """
    vals = [c.get("cur") for c in curve]
    empty = {"is_anomaly": False, "term": None, "dev_bp": None, "all_devs": {}, "text": ""}
    if len(vals) < 3 or any(v is None for v in vals):
        return empty
    devs: dict[str, float] = {}
    for i in range(1, len(vals) - 1):
        exp = vals[0] + (vals[-1] - vals[0]) * i / (len(vals) - 1)
        devs[curve[i]["term"]] = round((vals[i] - exp) * 100, 1)
    if not devs:
        return empty
    mono = all(vals[i] < vals[i + 1] for i in range(len(vals) - 1))
    dips = {t: d for t, d in devs.items() if d < 0}      # 只认凹陷方向（排除跨月末凸起）
    if mono or not dips:
        return {"is_anomaly": False, "term": None, "dev_bp": None,
                "all_devs": devs, "text": ""}
    worst_term, worst_dev = min(dips.items(), key=lambda kv: kv[1])
    hit = worst_dev < -CURVE_ANOMALY_BP
    return {
        "is_anomaly": hit,
        "term": worst_term if hit else None,
        "dev_bp": worst_dev if hit else None,
        "all_devs": devs,
        "text": (f"{worst_term} 相对曲线两端凹陷 {abs(worst_dev)}bp"
                 f"（>{CURVE_ANOMALY_BP:g}bp），曲线非单调 —— 该期限资金淤积，"
                 f"存在供给或考核约束"
                 if hit else ""),
    }


def _shape_tone(shape: str) -> str:
    """形状词对应的**着色档**（前端 `mc-tag--{na|warn|flat|ok}` 直接消费）。

    🔴 为什么要后端下发而不是前端映射：颜色档若写在前端，就等于前端**内联了第二份
       形状词表** —— 后端把「偏平坦」改成「偏平」时颜色会静默失配，且两头各自演化。
       由后端随词一起下发 ⇒ 词与色永远同源（需求附录 B #14 / #15 的同一纪律）。
    只表达"是否要注意"，不表达方向（卡片墙契约 ④）。
    """
    if shape == "锚位" or shape == "数据未就绪":
        return "na"
    if shape.startswith("倒挂"):
        return "warn"
    if "平坦" in shape:
        return "flat"
    return "ok"


def _dispersion_note(curve: list[dict], anom: dict) -> str:
    """`mc-curve` 卡内「期限离散度」常驻读数条的说明句（D4-a：移走 ≠ 删除）。

    单调时给"与期限利差共线"的解释（说明它为何不在结论区），异常时给具体期限名。
    """
    if any(c.get("cur") is None for c in curve):
        return "曲线数据未就绪"
    if anom.get("is_anomaly"):
        return anom["text"]
    return "曲线单调、无中间拐点 ⇒ 与「期限利差」信息共线，结论区不再单列"


# =====================================================================================
# R11｜方向语义与影响句（"这意味着什么"）—— 词表**一处定义**
# =====================================================================================
# 需求 R11 的五条纪律：
#   ① **只对「权益」与「债」两个维度**，不扩展到汇率 / 大宗 / 行业（两个维度是 L2 读者
#      能用上的上限，再多就变成研究报告）；
#   ② 两者**共用同一套词表** `正面影响 / 负面影响 / 中性`，不得引入券商 / 债市的行话变体
#      （**具体的禁用词清单在需求 R11 纪律 2** —— 这里不逐字复述，因为项目级断言 A15
#       扫描全部 `.py/.vue/.ts` 源码找旧词，注释里写全清单会被自己扫出来）——
#      单套词表是**可机器断言**的前提（枚举断言只认这三个值）；
#   ③ **必须带因果**：本页把因果放在口径浮窗的 ③「为什么」段（与影响句**同源同方向**，
#      由 P3 的人工复核项守护），卡面只留标签 ⇒ 卡宽有限，且两处都写必然不一致；
#   ④ **方向不明确时老实留白**（返回 `None`），硬凑一个"中性"就是**新增误导** ——
#      读者无法区分"真的中性"与"我们没想清楚"；
#   ⑤ 整句 ≤ 24 字（实测最长 18 字）。
IMPACT_SHORT = {"pos": "正面影响", "neg": "负面影响", "neu": "中性"}

# 资金价格水平 → (权益, 债)：钱便宜 → 两者皆正面；钱贵 → 皆负面。
# ⚠️ `_level_word` 的输出**带后缀**（如「偏松（近一年低位）」）⇒ 按**前缀**匹配。
#    键序无关：三个前缀互不为前缀（实测「偏松（近一年低位）」只命中「偏松」）。
LEVEL_IMPACT: dict[str, tuple[str, str]] = {
    "偏松": ("pos", "pos"),
    "中性": ("neu", "neu"),
    "偏紧": ("neg", "neg"),
}

# 期限结构形状 → (权益, 债)（词表 = `_shape_brief` 的 6 个非缺省值）
#   平坦：短端被投放填满、杠杆成本低 ⇒ 权益**正面**；长端无期限溢价 ⇒ 债**中性**。
#   陡  ：短端相对紧张、杠杆成本抬升 ⇒ 权益**负面**；长端期限溢价走阔 ⇒ 债**中性**。
#   倒挂：当下短端比长端贵（资金紧张）⇒ 权益**负面**；同时市场已定价未来降息、
#         长端有资本利得空间 ⇒ 债**正面**。
#   ⚠️ **「倒挂」一行是本实现的推断** —— 需求 R11 的定稿句只给了「偏平坦」一档
#      （见 §B6 的表）。已登记为 P3 一致性复核的第一顺位，人工确认后如有异议即改此表。
SHAPE_IMPACT: dict[str, tuple[str, str]] = {
    "倒挂": ("neg", "pos"),
    "极度平坦": ("pos", "neu"),
    "偏平坦": ("pos", "neu"),
    "中性": ("neu", "neu"),
    "偏陡": ("neg", "neu"),
    "极陡": ("neg", "neu"),
}


def _impact(eq: str, bd: str) -> str:
    """R11 固定句式（**不得自由改写**）：`对权益：X · 对债：Y`（实测最长 18 字）。"""
    return f"对权益：{IMPACT_SHORT[eq]} · 对债：{IMPACT_SHORT[bd]}"


def _impact_of(word: str | None, table: dict[str, tuple[str, str]]) -> str | None:
    """判断词 → 影响句；**未命中或缺省一律返回 `None`**（不产影响句，前端 `v-if` 自然不渲染）。

    为什么宁缺勿凑（纪律 ④）：硬凑的"中性"与真实的"中性"在页面上长得一样，
    却一个是判断、一个是没判断 —— 这正是本需求要消灭的那类**静默歧义**。
    """
    if not word or word.startswith("数据未就绪"):
        return None
    for k, (a, b) in table.items():
        if word.startswith(k):
            return _impact(a, b)
    return None


def _impact_lpr(lpr: dict) -> str | None:
    """LPR 的方向 = **本月是否调整过**（判据是 `idle_months == 0`）。

    🔴 不能直接比较「当前值 vs `prev`」—— `prev` 是 `_lpr_state` 里**上一次不同取值**，
       当前值已静止 16 个月时 `prev` 仍是 16 个月前那个值 ⇒ `cur != prev` **恒成立**，
       会永远判成"刚调整过"，进而给出"正面影响"。这是 P3 首版实现被验收脚本 AP3-3
       抓出来的真 bug（症状：`status` 说"已连续 16 个月未动"，`impact` 却说"正面影响"，
       两者当场矛盾）。
    ⇒ 判据改为 `idle_months`：`0` = 本月刚调整（按下调 / 上调给方向）；
       `≥1` = 处于**未动**状态 ⇒ **中性**（需求 R11 定稿句："没给新增宽松，也没收紧"）。
    """
    cur, prev = f(lpr.get("lpr_1y")), f((lpr.get("prev") or {}).get("lpr_1y"))
    idle = lpr.get("idle_months")
    if cur is None or prev is None or idle is None:
        return None
    if idle >= 1:
        return _impact("neu", "neu")
    return _impact("pos", "pos") if cur < prev else _impact("neg", "neg")


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
    # R12.4：把「合成规则」也**说出口** —— 读者看到「外部约束偏紧」应能自行复核，
    # 而不是只能相信一个不给依据的定性词（需求 R12.4 的核心诉求）。
    rule = ("合成规则：每项按**近一年分位**判紧（中美利差 / 美债期差取「偏低」口径，"
            "美债 10Y / 美元指数取「偏高」口径，阈值 35 / 65 分位）；"
            "记紧项数 ≥3 → 偏紧（互相印证），2 项 → 偏紧，1 项 → 大体中性，0 项 → 不构成压力。"
            f"当前：{nt}/{n} 项记紧。")
    return {"headline": head, "detail": " ".join(parts), "tone": tone,
            "tight_n": nt, "total_n": n, "combo": combo, "rule": rule}


def _external_state(bond: list[dict], dxy: list[dict], data_as_of: str,
                    dom_pct: float | None, as_of: str | None) -> dict:
    """外部约束块：三项利率读数 + 美元指数 + 美债曲线 + 全球央行方向 + 内外组合判读"""
    # 卡片墙（P3 · 2026-09-26）：外部卡（q5 = 四环之④「对外与资产」）择优取三个代表读数 ——
    # 中美 10Y 利差（利差吸引力）/ 美债 10Y（全球贴现率）/ 美元指数（美元总闸门，见 dxy_item）。
    # ⚠️ 不标 `questions` 的读数**上不了卡**，还会触发卡片墙的静默回退 ⇒ 本组四项一律标 q5。
    _EXT_CARD_RANK = {"cn_us_10y": 1, "us_10y": 2}
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
            # ⚠️ 降级分支**也必须**注入两轴（漏这一处 ⇒ 降级态丢 region/market，
            #    区块声明会算错计数）—— 故统一走 `_tag()` 唯一注入点。
            items.append(_tag(k, {
                          "key": k, "label": sp["label"], "value": None, "unit": sp["unit"],
                          "tone": sp["tone"], "status": "数据未就绪", "pct": None, "scale": None,
                          "highlight": False, "anchor": sp["anchor"], "hint": sp["hint"],
                          "as_of": None, "prev_year": None, "min_1y": None, "max_1y": None,
                          "chg20": None, "tight": None,
                          "questions": ["q5"], "card_rank": _EXT_CARD_RANK.get(k)}))
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
        items.append(_tag(k, {
                      "key": k, "label": sp["label"], "value": r(cur, 2), "unit": sp["unit"],
                      "tone": sp["tone"], "status": st,
                      "pct": p, "scale": scale(p, "近一年"), "highlight": is_extreme(p),
                      "anchor": sp["anchor"], "hint": sp["hint"],
                      "as_of": str(a0), "prev_year": r(prev, 2) if prev is not None else None,
                      "min_1y": r(min(win), 2) if win else None,
                      "max_1y": r(max(win), 2) if win else None,
                      "chg20": chg20, "tight": tg,
                      "questions": ["q5"], "card_rank": _EXT_CARD_RANK.get(k)}))

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
        dxy_item = _tag("dxy", {
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
            # 卡片墙（P3）：美元指数是外部卡的卡内第 3 格（利差 → 美债 → 美元）
            "questions": ["q5"], "card_rank": 3,
        })
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


# ---------------- 货币总量（数量维度，2026-09-26）----------------
# 「钱多不多」—— 与上面四个「钱的价格」切面正交。低频存量读数（季/周/月），
# 落在同一详情页的独立分区（`quantity` 块），**不拆卡片墙的 track 卡**（D1=A，对齐「不拆大卡」）。
#
# 数据源（全部只读，采集由各自 sync 负责；本模块不采集、不写库）：
#   global_liquidity_bis     BIS 境外美元信贷（季度，2000-03 起 6,720 行）
#   us_fed_balance_weekly    美联储总资产（周度 H41，2002-12 起 1,241 行）
#   us_money_supply_monthly  美国 M2/M1（月度 H6，1959-01 起 812 行）
#   us_money_market_daily    ON RRP / TGA 余额（日度）→ A5 的两个减项
#   eu_money_supply_monthly  欧元区 M3/M2/M1（月度 DBnomics/ECB）
#   cn_liquidity_monthly     中国 M2/M1 + M2 结构（月度）
#   cn_reserve_monthly       中国官方外储 / 黄金（月度，批次 C2）
#   cn_cb_balance_monthly    中国央行表内外汇（人民币口径，月度）
#   cn_omo_daily             央行公开市场操作量事实（日度）
#
# 🔴 八条已实测的取数纪律（勿回退）：
# 1. **BIS 只有 6 条件一条路**：`3P×N×A×I×B×USD` = 境外美元信贷总量。实测错写法
#    （`borrowers_cty<>'US'` 全量 SUM）= 45,951,383 百万 = **真值 3.12 倍** —— 三重重复：
#    `B` 已含 `D`/`G`、聚合码 `3P` 与各国明细**并存**、`unit_measure='771'` 是**同比 %** 不是金额。
# 2. **D 腿在 `lenders_sector='A'`、G 腿在 `lenders_sector='B'`**（2026-09-26 实测，两者不是同一值）
#    ⇒ 靠 `_BIS_LEGS` 显式声明，不靠猜。自证：D+G = 8,064,648.187+6,683,005.559
#    = 14,747,653.746 **逐位等于** B（本函数把它做成运行时软断言 `xcheck`）。
# 3. **`unit_measure='771'` 直接可用**：BIS 官方算好的同比（%），免自算季度同比、避开季节性。
# 4. **A5 必须取「同一日期的三值」**：美联储总资产是周度（全为周三）、ON RRP 只有 249 天有值、
#    TGA 1,248 天 ⇒ 实测全史仅 **52 个「三腿同日」**（最近 2026-09-23）。**不能退化成"各自最近值"**，
#    否则等于拿三个不同日期相减（TGA 日波动可达 ±10 万百万美元，足以改变结论）。
# 5. **ON RRP / TGA / 美联储总资产同为「百万美元」**（`us_money_market_sync` 已显式 `/1e6` 归一）
#    ⇒ A5 三腿直减，无需换算。⚠️ 本项目在汇率单位上有前科，单位一律以采集器注释为准。
# 6. **中国层「外储」是三套口径**（美元官方 / 黄金实物量 / 人民币表内），单位互不相同，
#    折算率**不是市场汇率**（实测 24 期隐含折算率由 6.886 单调降至 6.318）
#    ⇒ **严禁相加、严禁按市价互校**；本函数只分列并各标口径，**不做任何跨口径换算**。
# 7. **OMO 两个栏目不可混加**（`omo_trade` 逆回购/央票 vs `outright_repo` 买断式，
#    量级差一个数量级、各自独立编号）⇒ 分列；且**不做「净投放」派生**（D4=B 主动决策，非遗漏）。
# 8. **存量水平的"近一年分位"无意义**（M2 单调上行 ⇒ 分位永远贴近 100）
#    ⇒ 本区一律用**同比 yoy + 同比的 3 期变化（方向）**当比较锚，
#    **不下发 `scale` 分位刻度条**（`_kpi.scale` 的前提是序列有均值回复）。
#
# 单位归一（D5=A 定稿）：跨区金额**统一折美元**，目标展示单位 **万亿美元**；
# 换算只在 `_to_usd_tn` 一处完成，输出 `value + unit`（+ `native` 原生口径供 tooltip），
# **前端不经手任何换算**。不可比的量（黄金实物量、人民币表内外汇、OMO 操作量）**留原生单位**。

_Q_UNIT_TN = {
    "bn_usd": 1e-3,   # 十亿美元 → 万亿
    "mn_usd": 1e-6,   # 百万美元 → 万亿
    "yi_usd": 1e-4,   # 亿美元   → 万亿
}
# BIS 三条腿的官维取值（键 → (lenders_sector, l_instr)）—— 见纪律 2
_BIS_LEGS = {
    "total": ("A", "B"),   # 贷款 + 债券**合计**（headline）
    "bonds": ("A", "D"),   # 国际债券（IDS，子项）
    "loans": ("B", "G"),   # 银行信贷（子项）
}
_Q_BIS_XCHECK_MAX = 0.5    # B 与 D+G 的允许偏差（%）—— 实测逐位吻合，>0.5% 即口径读错

QUANTITY_NOTE = (
    "「数量」与「价格」是同一枚硬币的两面：上面四区答**钱贵不贵**（利率、利差、汇率），"
    "本区答**钱多不多**（水位与存量）。为什么必须两者一起看：价格是边际，数量是水位 —— "
    "价格下行可能只是「没人借钱」，数量扩张才说明「钱真的在变多」。"
    "⚠️ 本区是**低频存量读数**（BIS 季度、美联储周度、其余月度），"
    "**天然不该当「每日必看」**，故不占卡片墙，只作详情页分区。"
    "⚠️ 跨区金额一律**统一折美元**（万亿美元），换算集中在本模块一处、前端只格式化；"
    "但**不可比的量留原生单位**（黄金实物量/人民币表内外汇/OMO 操作量），"
    "中国层的三套「外储」口径**严禁相加、严禁按市价互校**（折算率非市场汇率）。"
    "⚠️ 存量水平不做「近一年分位」（单调上行序列的分位恒贴近 100，是伪信号），"
    "一律用**同比 + 同比的方向**做比较锚。"
)


def _to_usd_tn(v, native: str, fx: float | None = None) -> float | None:
    """各源原生金额 → **万亿美元**（D5=A）。换算只在这里做一次，前端不做任何换算。

    native 语义与换算：
      bn_usd  十亿美元   ÷ 1e3
      mn_usd  百万美元   ÷ 1e6
      yi_usd  亿美元     ÷ 1e4（**本就美元，不需要汇率**）
      mn_eur  百万欧元   ÷ 1e6 × EUR/USD
      yi_cny  亿元       ÷ 1e4 ÷ USD/CNY
    ⚠️ `fx` 语义随 native 变：`mn_eur` 传 EUR/USD（1 欧元 = ? 美元）；
       `yi_cny` 传 USD/CNY（1 美元 = ? 人民币）。任一缺失 → None（**宁可"数据未就绪"**）。
    """
    x = f(v)
    if x is None:
        return None
    if native in _Q_UNIT_TN:
        return x * _Q_UNIT_TN[native]
    if native == "mn_eur":
        return (x / 1e6) * fx if fx else None
    if native == "yi_cny":
        return (x / 1e4) / fx if fx else None
    return None


def _fetch_fx(as_of: str | None) -> tuple[float | None, float | None]:
    """取 (USD/CNY, EUR/USD)（人民币中间价交叉）

    口径：`currency_boc_daily` 的 `mid_price` / `ref_price` 都是**人民币中间价**形态
    （1 外币 = X 人民币；实测 2026-09-26：USD 6.7489、EUR 7.6631）
    ⇒ EUR/USD = eur_cny ÷ usd_cny = 7.6631 ÷ 6.7489 = 1.1355。
    ⚠️ 两列交替为空（`mid_price` 常为 NULL 而 `ref_price` 有值）⇒ 一律 COALESCE。
    ⚠️ 不用 `global_usd_index_daily`：那是 **ICE 口径的指数**，换不出 EUR/USD。
    ⚠️ 任一腿缺失 → 该折算为 None：**不拿旧汇率凑**（汇率错是「全体平移」形态，
       比缺数更难发现 —— 本项目在瑞典克朗标价法上已栽过一次）。
    """
    out: dict[str, float | None] = {}
    for code in ("USD", "EUR"):
        cond = " AND trade_date <= %s" if as_of else ""
        args: list = [code] + ([as_of] if as_of else [])
        row = query_one(
            "SELECT trade_date, COALESCE(mid_price, ref_price) AS px "
            "FROM currency_boc_daily "
            f"WHERE currency=%s AND COALESCE(mid_price, ref_price) IS NOT NULL{cond} "
            "ORDER BY trade_date DESC", args)
        out[code] = f(row["px"]) if row else None
        if row:
            logger.info("货币总量·汇率 %s = %s（%s）", code, row["px"], row["trade_date"])
    usd_cny, eur_cny = out.get("USD"), out.get("EUR")
    eur_usd = (eur_cny / usd_cny) if (usd_cny and eur_cny) else None
    return usd_cny, eur_usd


def _bis_offshore(as_of: str | None) -> dict:
    """全球层 anchor：BIS 境外美元信贷总量（季度）—— 官方 6 条件口径

    ⚠️ 中文表述用「**境外美元信贷**」（官方维度名 *Cross-border & Local in FCY*
       = 跨境 **+ 借款人本地外币**），**不要**简化成「跨境美元信贷」。
    ⚠️ 本表**只含美元计价**（采集器只拉 `Q.USD`），表名 `global_liquidity_bis` 有误导性 ⇒ UI 须明示。
    ⚠️ 季度、T+1 季发布（2026-Q1 约 2026-07 才出）⇒ **不可当"当前"读数**。
    """
    cond = " AND time_period <= %s" if as_of else ""
    args: list = [as_of] if as_of else []
    rows = query_all(
        "SELECT time_period, lenders_sector, l_instr, unit_measure, obs_value "
        "FROM global_liquidity_bis "
        "WHERE borrowers_cty='3P' AND borrowers_sector='N' AND l_pos_type='I' "
        "AND unit_measure IN ('USD','771')" + cond + " ORDER BY time_period DESC", args)
    if not rows:
        return {"period": None, "total_tn": None, "total_yoy": None, "total_yoy_chg": None,
                "loans_tn": None, "loans_yoy": None, "bonds_tn": None, "bonds_yoy": None,
                "bond_share": None, "xcheck_pct": None, "trend": []}
    latest = rows[0]["time_period"]
    cur = {(x["lenders_sector"], x["l_instr"], x["unit_measure"]): f(x["obs_value"])
           for x in rows if x["time_period"] == latest}
    total = cur.get((*_BIS_LEGS["total"], "USD"))
    loans = cur.get((*_BIS_LEGS["loans"], "USD"))
    bonds = cur.get((*_BIS_LEGS["bonds"], "USD"))
    total_yoy = cur.get((*_BIS_LEGS["total"], "771"))
    # 自证：B 必须等于 D + G（官方公布值逐位吻合）；偏差 > 0.5% 即维度/口径读错
    xcheck = None
    if total and loans is not None and bonds is not None:
        xcheck = abs(loans + bonds - total) / total * 100
        if xcheck > _Q_BIS_XCHECK_MAX:
            logger.warning(
                "BIS 口径自证失败：D+G=%.3f vs B=%.3f（偏差 %.2f%%）—— 复核 lenders_sector 取值",
                loans + bonds, total, xcheck)
    # 同比的**方向**（最新季同比 − 上季同比，pp）—— 见纪律 8：判"在加速还是在减速"
    total_yoy_chg = None
    periods = sorted({x["time_period"] for x in rows}, reverse=True)
    if len(periods) > 1:
        prev_yoy = next((f(x["obs_value"]) for x in rows
                         if x["time_period"] == periods[1]
                         and x["lenders_sector"] == _BIS_LEGS["total"][0]
                         and x["l_instr"] == _BIS_LEGS["total"][1]
                         and x["unit_measure"] == "771"), None)
        if total_yoy is not None and prev_yoy is not None:
            total_yoy_chg = r(total_yoy - prev_yoy, 2)
    # 季度序列（近 12 季）供小图
    tseries: list[dict] = []
    for p in periods[:12]:
        v = next((f(x["obs_value"]) for x in rows if x["time_period"] == p
                  and x["lenders_sector"] == _BIS_LEGS["total"][0]
                  and x["l_instr"] == _BIS_LEGS["total"][1]
                  and x["unit_measure"] == "USD"), None)
        tseries.append({"period": str(p), "total_tn": r(_to_usd_tn(v, "mn_usd"), 3)})
    tseries.reverse()
    return {
        "period": str(latest),
        "total_tn": r(_to_usd_tn(total, "mn_usd") if total else None, 2),
        "total_yoy": r(total_yoy, 2), "total_yoy_chg": total_yoy_chg,
        "loans_tn": r(_to_usd_tn(loans, "mn_usd"), 2),
        "loans_yoy": r(cur.get((*_BIS_LEGS["loans"], "771")), 2),
        "bonds_tn": r(_to_usd_tn(bonds, "mn_usd"), 2),
        "bonds_yoy": r(cur.get((*_BIS_LEGS["bonds"], "771")), 2),
        "bond_share": r(bonds / total * 100, 1) if (bonds is not None and total) else None,
        "xcheck_pct": r(xcheck, 3),
        "trend": tseries,
        "legs": {"total": _BIS_LEGS["total"], "loans": _BIS_LEGS["loans"],
                 "bonds": _BIS_LEGS["bonds"]},
    }


def _fed_assets(as_of: str | None) -> dict:
    """美国层 anchor：美联储总资产（周度 H41，百万美元）。扩表 = 放水、缩表 = 收水。

    ⚠️ 全部是**周三**数据 ⇒ 「13 周变化」就是第 13 行，不需要按自然日折算。
    """
    cond = " AND trade_date <= %s" if as_of else ""
    args: list = [as_of] if as_of else []
    rows = query_all(
        "SELECT trade_date, total_assets FROM us_fed_balance_weekly "
        "WHERE total_assets IS NOT NULL" + cond + " ORDER BY trade_date DESC LIMIT 60", args)
    if not rows:
        return {"as_of": None, "tn": None, "chg_4w_pct": None, "chg_13w_pct": None,
                "chg_52w_pct": None, "trend": []}

    def _chg(n: int) -> float | None:
        if len(rows) <= n:
            return None
        a, b = f(rows[n]["total_assets"]), f(rows[0]["total_assets"])
        return r((b / a - 1) * 100, 2) if a else None

    return {
        "as_of": str(rows[0]["trade_date"]),
        "tn": r(_to_usd_tn(rows[0]["total_assets"], "mn_usd"), 2),
        "chg_4w_pct": _chg(4), "chg_13w_pct": _chg(13), "chg_52w_pct": _chg(52),
        "trend": [{"date": str(x["trade_date"]),
                   "tn": r(_to_usd_tn(x["total_assets"], "mn_usd"), 3)}
                  for x in reversed(rows[:52])],
    }


def _us_net_liquidity(as_of: str | None) -> dict:
    """美元净流动性（合成 A5）= 美联储总资产 − TGA − ON RRP（百万美元，同单位直减）

    🔴 **必须同一日期的三值**（纪律 4）：三张源表日历不同（H41 周度 / NY Fed 日度 /
       Treasury 日度），实测全史仅 **52 个「三腿同日」**（最近 2026-09-23）
       ⇒ 用 SQL JOIN 在**日期相等**上取，**不做"各自最近值"**（那等于拿三个日期相减）。
    ⚠️ **合成口径、非官方**（市场常用、本项目自合成）⇒ UI 必须显式标注，「金色强调」也正因为它
       是派生亮点而非官方读数。
    """
    cond = " AND m.trade_date <= %s" if as_of else ""
    args: list = [as_of] if as_of else []
    rows = query_all(
        "SELECT m.trade_date, m.on_rrp_amt, m.tga, f.total_assets "
        "FROM us_money_market_daily m "
        "JOIN us_fed_balance_weekly f ON f.trade_date = m.trade_date "
        "WHERE m.on_rrp_amt IS NOT NULL AND m.tga IS NOT NULL "
        "AND f.total_assets IS NOT NULL" + cond
        + " ORDER BY m.trade_date DESC LIMIT 13", args)
    if not rows:
        return {"as_of": None, "tn": None, "fed_tn": None, "tga_tn": None, "onrrp_tn": None,
                "tga_yi": None, "onrrp_yi": None, "chg_13_pct": None, "n_days": 0, "trend": []}

    def _net(x: dict) -> float | None:
        a, b, c = f(x["total_assets"]), f(x["tga"]), f(x["on_rrp_amt"])
        return None if None in (a, b, c) else a - b - c

    head, net = rows[0], _net(rows[0])
    chg = None
    if len(rows) > 1 and net is not None:
        prev = _net(rows[-1])
        if prev:
            chg = r((net / prev - 1) * 100, 2)
    return {
        "as_of": str(head["trade_date"]),
        "tn": r(_to_usd_tn(net, "mn_usd"), 2),
        "fed_tn": r(_to_usd_tn(head["total_assets"], "mn_usd"), 2),
        # ⚠️ TGA / ON RRP 是 A5 的两个减项、量级可低到「亿美元」级（实测 ON RRP 最低 3,000 万美元）
        #    ⇒ 折成万亿只剩 0.0000x，**三位小数会把 ON RRP 抹成 0.0**（实测踩到）
        #    ⇒ 这两项同时给「万亿（4 位）」与「亿美元」两种精度，status 文案用后者。
        "tga_tn": r(_to_usd_tn(head["tga"], "mn_usd"), 4),
        "onrrp_tn": r(_to_usd_tn(head["on_rrp_amt"], "mn_usd"), 4),
        "tga_yi": r((f(head["tga"]) or 0) / 100, 2),        # 百万美元 → 亿美元
        "onrrp_yi": r((f(head["on_rrp_amt"]) or 0) / 100, 2),
        "chg_13_pct": chg,
        "n_days": len(rows),
        "trend": [{"date": str(x["trade_date"]),
                   "tn": r(_to_usd_tn(_net(x), "mn_usd"), 3)} for x in reversed(rows)],
    }


def _us_money(as_of: str | None) -> dict:
    """美国 M2 / M1 / 基础货币（月度 H6，**十亿美元**）

    ⚠️ 与欧元区 M3（**百万欧元**）、中国 M2（**亿元**）单位全不同 ⇒ 归一到万亿美元再比
       （纪律 8 的归一纪律）。实测：美国 M2 23,342.8 十亿 = 23.34 万亿；
       中国 M2 3,568,083.6 亿 = 52.87 万亿 —— **原值比大小会得到完全相反的结论**。
    """
    cond = " AND stat_month <= %s" if as_of else ""
    args: list = [as_of] if as_of else []
    rows = query_all(
        "SELECT stat_month, m2, m1, monetary_base, m2_yoy, m1_yoy "
        "FROM us_money_supply_monthly WHERE m2 IS NOT NULL" + cond
        + " ORDER BY stat_month DESC LIMIT 16", args)
    if not rows:
        return {"as_of": None, "m2_tn": None, "m2_yoy": None, "m2_yoy_d3": None,
                "m1_tn": None, "m1_yoy": None, "m2_bn": None, "m1_bn": None,
                "base_tn": None, "series": []}
    head = rows[0]

    def _yoy(col: str, yoy_col: str) -> float | None:
        """优先用列（H6 官方同比），缺失才按 12 期前自算 —— 口径与列一致，不引入第三种算法"""
        v = f(head[yoy_col])
        if v is not None:
            return v
        if len(rows) > 12 and f(rows[12][col]):
            return r((f(head[col]) / f(rows[12][col]) - 1) * 100, 2)
        return None

    # 同比的 3 期变化（方向）—— 见纪律 8
    d3 = None
    if len(rows) > 3 and f(head["m2_yoy"]) is not None and f(rows[3]["m2_yoy"]) is not None:
        d3 = r(f(head["m2_yoy"]) - f(rows[3]["m2_yoy"]), 2)
    return {
        "as_of": str(head["stat_month"]),
        "m2_tn": r(_to_usd_tn(head["m2"], "bn_usd"), 2), "m2_yoy": _yoy("m2", "m2_yoy"),
        "m2_bn": r(head["m2"], 2), "m2_yoy_d3": d3,
        "m1_tn": r(_to_usd_tn(head["m1"], "bn_usd"), 2), "m1_yoy": _yoy("m1", "m1_yoy"),
        "m1_bn": r(head["m1"], 2),
        "base_tn": r(_to_usd_tn(head["monetary_base"], "bn_usd"), 2),
        "series": [{"month": str(x["stat_month"]), "m2_tn": r(_to_usd_tn(x["m2"], "bn_usd"), 2)}
                   for x in reversed(rows[:14])],
    }


def _eu_money(as_of: str | None, eur_usd: float | None) -> dict:
    """欧元区 M3 / M2 / M1（月度，**百万欧元**）→ 折美元须 EUR/USD

    ⚠️ 源单一（ECB 直连三域全断，只能走 DBnomics）⇒ UI 必须标「**唯一通道，失效即告警**」。
    ⚠️ 单位是**百万欧元**，与美国 M2（十亿美元）、中国 M2（亿元）**直接比大小会错**（见纪律 8）。
    """
    cond = " AND stat_month <= %s" if as_of else ""
    args: list = [as_of] if as_of else []
    rows = query_all(
        "SELECT stat_month, m3, m2, m1, m3_yoy FROM eu_money_supply_monthly "
        "WHERE m3 IS NOT NULL" + cond + " ORDER BY stat_month DESC LIMIT 16", args)
    if not rows:
        return {"as_of": None, "m3_tn": None, "m3_yoy": None, "m3_yoy_d3": None,
                "m2_tn": None, "m1_tn": None, "eur_usd": eur_usd, "series": []}
    head = rows[0]
    m3_yoy = f(head["m3_yoy"])
    if m3_yoy is None and len(rows) > 12 and f(rows[12]["m3"]):
        m3_yoy = r((f(head["m3"]) / f(rows[12]["m3"]) - 1) * 100, 2)
    d3 = None
    if len(rows) > 3 and f(head["m3_yoy"]) is not None and f(rows[3]["m3_yoy"]) is not None:
        d3 = r(f(head["m3_yoy"]) - f(rows[3]["m3_yoy"]), 2)
    return {
        "as_of": str(head["stat_month"]),
        "m3_tn": r(_to_usd_tn(head["m3"], "mn_eur", eur_usd), 2),
        "m3_yoy": r(m3_yoy, 2), "m3_yoy_d3": d3,
        "m2_tn": r(_to_usd_tn(head["m2"], "mn_eur", eur_usd), 2),
        "m1_tn": r(_to_usd_tn(head["m1"], "mn_eur", eur_usd), 2),
        "eur_usd": r(eur_usd, 4),
        "series": [{"month": str(x["stat_month"]),
                    "m3_tn": r(_to_usd_tn(x["m3"], "mn_eur", eur_usd), 2)}
                   for x in reversed(rows[:14])],
    }


# 中国 M2 结构列：**源侧停更时点不齐**（实测），属口径调整而非采集失败 ⇒ 随数据下发给 UI 标注
_CN_STRUCT_COLS: list[tuple[str, str]] = [
    ("demand_deposit", "活期存款"),
    ("time_deposit", "定期存款"),
    ("savings_deposit", "储蓄存款"),
    ("other_deposit", "其他存款"),
    ("quasi_money", "准货币（M2−M1）"),
]


def _cn_money(as_of: str | None, usd_cny: float | None) -> dict:
    """中国 M2 / M1 / M0（月度，**亿元**）+ M2 结构 + M1−M2 剪刀差

    ⚠️ 结构列**停更时点不齐**（2026-09-26 实测：活期/定期停 2025-05、储蓄停 2024-12，
       而 `quasi_money` / `other_deposit` 仍更新到 2026-08）⇒ 是**源口径调整**、不是采集失败，
       本函数把每列「最后有值月」随数据下发（`struct_last`），UI 据此标注、不得当缺数处理。
    """
    cond = " AND stat_month <= %s" if as_of else ""
    args: list = [as_of] if as_of else []
    rows = query_all(
        "SELECT stat_month, m2, m1, m0, m2_yoy, m1_yoy, m1_m2_gap, demand_deposit, "
        "time_deposit, savings_deposit, other_deposit, quasi_money "
        "FROM cn_liquidity_monthly WHERE m2 IS NOT NULL" + cond
        + " ORDER BY stat_month DESC LIMIT 18", args)
    if not rows:
        return {"as_of": None, "m2_tn": None, "m2_yoy": None, "m2_yoy_d3": None,
                "gap": None, "gap_d3": None, "gap_word": None, "struct": [], "struct_last": {}}
    head = rows[0]
    d3 = None
    if len(rows) > 3 and f(head["m2_yoy"]) is not None and f(rows[3]["m2_yoy"]) is not None:
        d3 = r(f(head["m2_yoy"]) - f(rows[3]["m2_yoy"]), 2)
    gap_d3 = None
    if len(rows) > 3 and f(head["m1_m2_gap"]) is not None and f(rows[3]["m1_m2_gap"]) is not None:
        gap_d3 = r(f(head["m1_m2_gap"]) - f(rows[3]["m1_m2_gap"]), 2)
    # 结构列「最后有值月」用**独立聚合查询**取，不靠 LIMIT 窗口内扫描 ——
    # ⚠️ 实测坑：储蓄存款停在 2024-12，而 LIMIT 16 只回溯到 2025-04 ⇒ 扫描法会把
    #    「已停更」误报成「该列从未有值」（`last_month=None`），UI 就标不出「源口径调整」。
    agg_cols = ", ".join(
        f"MAX(CASE WHEN {c} IS NOT NULL THEN stat_month END) AS {c}" for c, _ in _CN_STRUCT_COLS)
    agg_cond = " WHERE stat_month <= %s" if as_of else ""
    agg = query_one(f"SELECT {agg_cols} FROM cn_liquidity_monthly{agg_cond}", args) or {}
    struct: list[dict] = []
    for col, label in _CN_STRUCT_COLS:
        last = str(agg[col]) if agg.get(col) is not None else None
        cur_v = f(head.get(col))
        stale = cur_v is None and last is not None
        struct.append({"key": col, "label": label, "last_month": last,
                       "value": r(cur_v, 2) if cur_v is not None else None,
                       "as_of": str(head["stat_month"]) if cur_v is not None else last,
                       "stale": stale})
    return {
        "as_of": str(head["stat_month"]),
        "m2_tn": r(_to_usd_tn(head["m2"], "yi_cny", usd_cny), 2),
        "m2_yi": r(head["m2"], 2), "m2_yoy": r(head["m2_yoy"], 2), "m2_yoy_d3": d3,
        "m1_tn": r(_to_usd_tn(head["m1"], "yi_cny", usd_cny), 2),
        "m1_yi": r(head["m1"], 2), "m1_yoy": r(head["m1_yoy"], 2),
        "m0_yi": r(head["m0"], 2),
        # 剪刀差方向词：**口径在后端生成**（前端只透传），见设计 §四「判读条给判断」
        "gap_word": ("活化度回升" if (gap_d3 or 0) > 0 else
                     ("活化度回落" if (gap_d3 or 0) < 0 else "活化度持平")),
        "gap": r(head["m1_m2_gap"], 2), "gap_d3": gap_d3,
        "usd_cny": r(usd_cny, 4),
        "struct": struct,
        "struct_last": {k: v["last_month"] for k, v in
                        zip([c for c, _ in _CN_STRUCT_COLS], struct)},
    }


def _cn_reserve(as_of: str | None) -> dict:
    """中国官方外汇储备 / 黄金储备（月度，`cn_reserve_monthly`，批次 C2）

    ⚠️ 外储单位是**亿美元**（本就美元 ⇒ 折算**不需要汇率**，÷1e4 即万亿）；
       黄金是**万盎司**（**实物量，不是金额**）⇒ 留原生单位、不折美元。
    ⚠️ `gold_reserve_oz_chg` 是采集器算好的**环比**（仅当紧邻上一月存在时才非空，跨年边界已在
       采集器处理）⇒ 「连续增持 N 月」逐月回溯比现场做差更稳。
    ⚠️ **不得**与 `cn_cb_balance_monthly` 的人民币口径外储相加或互校（纪律 6）。
    """
    cond = " AND stat_month <= %s" if as_of else ""
    args: list = [as_of] if as_of else []
    rows = query_all(
        "SELECT stat_month, fx_reserve_usd, fx_reserve_usd_yoy, gold_reserve_oz, "
        "gold_reserve_oz_chg FROM cn_reserve_monthly WHERE fx_reserve_usd IS NOT NULL"
        + cond + " ORDER BY stat_month DESC LIMIT 60", args)
    if not rows:
        return {"as_of": None, "fx_tn": None, "fx_yoy": None, "gold_oz": None,
                "gold_chg": None, "gold_streak": None, "trend": []}
    head = rows[0]
    # 连续增持月数：从最新月回溯，逐月 chg > 0（缺 chg 视为断链，不猜）
    streak = 0
    for x in rows:
        c = f(x["gold_reserve_oz_chg"])
        if c is None or c <= 0:
            break
        streak += 1
    return {
        "as_of": str(head["stat_month"]),
        "fx_tn": r(_to_usd_tn(head["fx_reserve_usd"], "yi_usd"), 2),
        "fx_usd_yi": r(head["fx_reserve_usd"], 2),
        "fx_yoy": r(head["fx_reserve_usd_yoy"], 2),
        "gold_oz": r(head["gold_reserve_oz"], 2),
        "gold_chg": r(head["gold_reserve_oz_chg"], 2),
        "gold_streak": streak,
        "trend": [{"month": str(x["stat_month"]),
                   "fx_tn": r(_to_usd_tn(x["fx_reserve_usd"], "yi_usd"), 3),
                   "gold_oz": r(x["gold_reserve_oz"], 0)} for x in reversed(rows[:24])],
    }


def _cn_cb(as_of: str | None) -> dict:
    """中国央行**表内外汇**（人民币口径，`cn_cb_balance_monthly`，亿元）

    ⚠️ 这是「外汇占款 ↔ 基础货币」联动用的**人民币表内口径**，与上面的美元官方口径
       **不是一套东西**（折算率是历史成本/复合折算，实测 24 期由 6.886 单调降至 6.318，
       非市场汇率）⇒ 只作**并列展示**，UI 必须各标口径，**严禁相加**。
    """
    cond = " AND stat_month <= %s" if as_of else ""
    args: list = [as_of] if as_of else []
    row = query_one(
        "SELECT stat_month, fx_reserve, monetary_gold, foreign_assets, reserve_money, "
        "total_assets FROM cn_cb_balance_monthly WHERE fx_reserve IS NOT NULL" + cond
        + " ORDER BY stat_month DESC", args)
    if not row:
        return {"as_of": None, "fx_yi": None, "gold_yi": None, "foreign_yi": None,
                "reserve_money_yi": None, "total_yi": None}
    return {
        "as_of": str(row["stat_month"]),
        "fx_yi": r(row["fx_reserve"], 2),          # 人民币口径外汇（亿元）
        "gold_yi": r(row["monetary_gold"], 2),     # 人民币口径货币黄金（亿元）
        "foreign_yi": r(row["foreign_assets"], 2),
        "reserve_money_yi": r(row["reserve_money"], 2),
        "total_yi": r(row["total_assets"], 2),
    }


def _cn_omo(as_of: str | None, per_section: int = 5) -> list[dict]:
    """央行公开市场操作**操作量事实**（**每个栏目各自最近 N 笔**，按 `section` 分列）

    🔴 **不做「净投放」派生**（D4=B 主动决策，非遗漏）：净投放须按期限滚动推算到期日
       （7 天期遇节假日顺延）、买断式期限不固定、人行另有国债买卖/国库现金定存等工具
       ⇒ 会**系统性低估**。升级路径见 v2.1 §11.4。
    🔴 **两个栏目不可混加**：`omo_trade`（逆回购/央票）与 `outright_repo`（买断式逆回购）
       各自独立编号、量级差一个数量级 ⇒ 分列。直接 `SUM(win_amount)` 会把
       「7 天期逆回购 515 亿」与「买断式 5000 亿」混成一个数。
    🔴 **窗口按「栏目各自最近 N 笔」而非「全局最近 N 日」**（实测踩到）：买断式一个月才几次
       ⇒ 用全局 5 日窗口会被整个吃掉、栏目在 UI 上凭空消失（实测近 5 日只剩 `omo_trade` 一项）。
    ⚠️ **零操作日也是有效数据**（`win_amount=0`）⇒ 不过滤 0；把 0 当 NULL 会让「近期投放」偏大。
    ⚠️ 买断式是**招标预告**：公告发布日 ≠ 操作日（实测 [2026]第18号发布 9-14、操作 9-15）
       ⇒ 一律按 `trade_date`（正文操作日）。
    """
    cond = " AND trade_date <= %s" if as_of else ""
    args: list = [as_of] if as_of else []
    rows = query_all(
        "SELECT section, trade_date, op_type, tenor_days, op_rate, win_amount "
        "FROM cn_omo_daily WHERE win_amount IS NOT NULL" + cond
        + " ORDER BY trade_date DESC, notice_no DESC", args)
    if not rows:
        return []
    out: list[dict] = []
    for sec in ("omo_trade", "outright_repo"):
        ops = [x for x in rows if x["section"] == sec][:per_section]
        if not ops:
            continue
        out.append({
            "section": sec,
            "label": "公开市场业务（逆回购 / 央票）" if sec == "omo_trade" else "买断式逆回购",
            "as_of": str(ops[0]["trade_date"]),
            "rows": [{"date": str(x["trade_date"]), "op_type": x["op_type"],
                      "tenor_days": x["tenor_days"], "rate": r(x["op_rate"], 2),
                      "amount_yi": r(x["win_amount"], 2)} for x in ops],
            "amount_sum_yi": r(sum(f(x["win_amount"]) or 0 for x in ops), 2),
            "days": len({x["trade_date"] for x in ops}),
        })
    return out


def _q_dir(v: float | None) -> int:
    """方向：+1 扩张 / -1 收缩 / 0 无法判定"""
    if v is None:
        return 0
    return 1 if v > 0 else (-1 if v < 0 else 0)


def _quantity_verdict(glb: dict, fed: dict, eu: dict, cn: dict) -> dict:
    """数量维度的「多层同向」判定（tone 规则见设计 §四）

    全球 / 美国 / 欧元区 / 中国四层：≥3 层同向扩张 → **偏松**（机会侧金）；
    ≥3 层同向收缩 → **偏紧**（琥珀）；否则**背离**（中性蓝）。

    🔴 判的是**增速的方向**（同比抬升/回落、扩表/缩表），**不是「同比为正」**：
       货币供应长期正增长，拿符号当信号等于恒真（实测四层同比**全为正**，
       但方向已分化）—— 这正是设计 §四 说的「同向」而不是「同号」。
    """
    layers = [
        ("全球", "境外美元信贷同比", glb.get("total_yoy_chg"), "pp", glb.get("total_yoy"), "%"),
        ("美国", "美联储总资产 13 周", fed.get("chg_13w_pct"), "%", None, None),
        ("欧元区", "M3 同比", eu.get("m3_yoy_d3"), "pp", eu.get("m3_yoy"), "%"),
        ("中国", "M2 同比", cn.get("m2_yoy_d3"), "pp", cn.get("m2_yoy"), "%"),
    ]
    exp_n = sum(1 for _, _, d, _, _, _ in layers if _q_dir(d) > 0)
    con_n = sum(1 for _, _, d, _, _, _ in layers if _q_dir(d) < 0)
    known = exp_n + con_n
    if known == 0:
        tone = "mixed"
    elif exp_n >= 3:
        tone = "loose"
    elif con_n >= 3:
        tone = "tight"
    else:
        tone = "mixed"

    def _txt(name: str, d: float | None, unit: str, lv: float | None, lvu: str | None) -> str:
        if d is None:
            return f"{name}方向未就绪"
        w = "扩张（加速）" if d > 0 else ("收缩（减速）" if d < 0 else "持平")
        s = f"{name} {w}"
        if lv is not None:
            s += f"（水平 {lv:+.2f}{lvu or ''}）"
        return s + f"，方向变化 {d:+.2f}{unit}"

    head_map = {
        "loose": "全球主要货币区的货币总量**同向扩张** —— 水位在抬升，数量维度偏松",
        "tight": "全球主要货币区的货币总量**同向收缩** —— 水位在回落，数量维度偏紧",
        "mixed": "各货币区的货币总量**方向背离** —— 数量维度无一致信号，须分地区看",
    }
    parts = []
    if glb.get("total_tn") is not None:
        parts.append(f"境外美元信贷 {glb['total_tn']} 万亿美元"
                     + (f"（同比 {glb['total_yoy']:+.2f}%，债券占 {glb['bond_share']}%）"
                        if glb.get("total_yoy") is not None else ""))
    if fed.get("tn") is not None:
        parts.append(f"美联储总资产 {fed['tn']} 万亿"
                     + (f"（13 周 {fed['chg_13w_pct']:+.2f}%）"
                        if fed.get("chg_13w_pct") is not None else ""))
    if cn.get("m2_tn") is not None:
        parts.append(f"中国 M2 {cn['m2_tn']} 万亿美元"
                     + (f"（同比 {cn['m2_yoy']:+.2f}%）" if cn.get("m2_yoy") is not None else ""))
    headline = ("；".join(parts) if parts else "货币总量数据未就绪") + "。" + head_map[tone].replace("**", "") + "。"
    detail = (
        f"四层方向判定：{' · '.join(_txt(n, d, u, lv, lvu) for n, _, d, u, lv, lvu in layers)}。"
        f"（同向扩张 {exp_n} 层 / 同向收缩 {con_n} 层 ⇒ 结论：{head_map[tone]}）"
        "⚠️ 这是**存量水位**的中期读数（BIS 季度、其余月/周度），**不是择时信号**；"
        "它回答「钱在变多还是变少」，不回答「什么时候进」。"
    )
    if eu.get("m3_tn") is not None:
        detail += (f"欧元区 M3 {eu['m3_tn']} 万亿美元"
                   + (f"（同比 {eu['m3_yoy']:+.2f}%）" if eu.get("m3_yoy") is not None else "")
                   + "作第二货币区的横向参照。")
    return {"headline": headline, "detail": detail, "tone": tone,
            "expand_n": exp_n, "tight_n": con_n, "known_n": known,
            "layers": [{"name": n, "metric": m, "dir": _q_dir(d), "delta": d, "unit": u,
                        "level": lv} for n, m, d, u, lv, _ in layers]}


def _quantity_state(as_of: str | None) -> dict:
    """货币总量块（数量维度）：全球 → 美国 → 欧元区 → 中国 四个分组 + 判读条

    ⚠️ **优雅降级**：任一分组的数据未就绪（表缺 / 列缺 / 源断）只让**该卡显示"数据未就绪"**，
       绝不让整个 `quantity` 块抛错（设计 §八 明文要求 —— C2 曾是可并行前置批次）。
    """
    usd_cny, eur_usd = _fetch_fx(as_of)
    glb = _bis_offshore(as_of)
    fed = _fed_assets(as_of)
    net_liq = _us_net_liquidity(as_of)
    us = _us_money(as_of)
    eu = _eu_money(as_of, eur_usd)
    cn_money = _cn_money(as_of, usd_cny)
    cn_res = _cn_reserve(as_of)
    cn_cb = _cn_cb(as_of)
    cn_omo = _cn_omo(as_of)

    def _kpi(key: str, label: str, value, status: str, anchor: str, hint: str,
             highlight: bool = False, native: dict | None = None,
             questions: list[str] | None = None, card_rank: int | None = None) -> dict:
        """数量类 KPI：tone 一律 neutral（存量水位不是「某个资产在涨跌」，不染红绿）；
        **不下发 scale**（纪律 8：单调上行序列的分位是伪信号）。

        `questions` / `card_rank`（P2 · 2026-09-26）：这张读数归**哪张卡片墙的卡**、以及卡内第几位。
        🔴 不标 `questions` ⇒ 卡片墙的 `scoped.length ? scoped : kpis` 过滤为空 ⇒ **静默回退全量**
           ⇒ 数量卡上会冒出 Shibor（内容错、却不报错）。`card_rank` 只影响卡内「取前 3」的择优。
        """
        return _tag(key, {
                "key": key, "label": label, "value": value, "unit": " 万亿美元",
                "tone": "neutral", "status": status, "highlight": highlight,
                "anchor": anchor, "hint": hint, "pct": None, "scale": None,
                "native": native, "questions": questions or [], "card_rank": card_rank})

    items: list[dict] = [
        _kpi("bis_offshore", "境外美元信贷（离岸美元）", glb.get("total_tn"),
             (f"同比 {glb['total_yoy']:+.2f}%｜债券占 {glb['bond_share']}%｜BIS 官方口径"
              if glb.get("total_yoy") is not None else "数据未就绪"),
             "mc-qty-global",
             "BIS 官方口径 `3P×N×A×I×B×USD` = **全体非美借款人**的**美元计价**信贷"
             "（跨境 + 借款人本地外币），是「境外美元总量」的官方读数。"
             f"⚠️ 口径已实测自证（贷款+债券 − 总量 = {glb.get('xcheck_pct')}%）。"
             "⚠️ 这是**季度**数据、T+1 季发布，**不可当成「当前」读数**；表内只含美元口径，"
             "不含欧元/日元等其它币种的境外信贷。",
             native={"value": None, "unit": "百万美元", "note": "BIS WS_GLI，单位原生为百万美元"},
             questions=["q5"]),
        _kpi("fed_assets", "美联储总资产", fed.get("tn"),
             (f"13 周 {fed['chg_13w_pct']:+.2f}%｜52 周 {fed['chg_52w_pct']:+.2f}%"
              if fed.get("chg_52w_pct") is not None else "数据未就绪"),
             "mc-qty-us",
             "美联储总资产（周度 H41）。**扩表 = 放水、缩表 = 收水** —— 它是全球美元总闸门，"
             "也是 A5「美元净流动性」的被减项之一。⚠️ 周度数据固定在**周三**发布口径。",
             questions=["q3"], card_rank=2),
        _kpi("us_net_liq", "美元净流动性（合成 A5）", net_liq.get("tn"),
             (f"总资产 {net_liq['fed_tn']} 万亿 − TGA {net_liq['tga_yi']:,.0f} 亿美元"
              f" − ON RRP {net_liq['onrrp_yi']:,.2f} 亿美元｜截至 {net_liq['as_of']}"
              if net_liq.get("tn") is not None else "数据未就绪"),
             "mc-qty-us",
             "🔴 **合成口径、非官方**：市场最常用的「美元真实流动性」= 美联储总资产 − "
             "财政部 TGA 余额 − 隔夜逆回购 ON RRP。三腿取**同一日期**的读数"
             "（实测全史仅 52 个「三腿同日」，故不能拿各自最近值硬凑）。"
             "⚠️ 它不是官方公布值，是本项目自合成 —— 金色强调是因为它是派生亮点，不代表官方背书。",
             highlight=True, questions=["q3"], card_rank=3),
        _kpi("us_m2", "美国 M2", us.get("m2_tn"),
             (f"同比 {us['m2_yoy']:+.2f}%｜" if us.get("m2_yoy") is not None else "")
             + (f"原生 {us['m2_bn']:,.1f} 十亿美元" if us.get("m2_bn") is not None
                else "数据未就绪"),
             "mc-qty-us",
             "美国 M2（月度 H6）。⚠️ 原生单位是**十亿美元**（23,342.8 bn），"
             "与欧元区 M3（百万欧元）、中国 M2（亿元）**单位全不同** —— "
             "本卡已统一折成万亿美元，**原值直接比大小会得到相反结论**。",
             questions=["q4"], card_rank=1),
        _kpi("eu_m3", "欧元区 M3", eu.get("m3_tn"),
             (f"同比 {eu['m3_yoy']:+.2f}%｜EUR/USD {eu['eur_usd']}"
              if eu.get("m3_yoy") is not None else "数据未就绪"),
             "mc-qty-eu",
             "欧元区 M3（月度）。⚠️ 原生单位是**百万欧元**，本卡按 EUR/USD 中间价交叉汇率折美元。"
             "⚠️ 数据源**只有 DBnomics 一条通道**（ECB 直连三域全断）—— 它失效则本卡断供，"
             "DQ freshness 是唯一防线。",
             questions=["q4"], card_rank=2),
        _kpi("cn_m2", "中国 M2", cn_money.get("m2_tn"),
             (f"同比 {cn_money['m2_yoy']:+.2f}%｜M1−M2 剪刀差 {cn_money['gap']}"
              f"（{cn_money['gap_word']}）"
              if cn_money.get("m2_yoy") is not None else "数据未就绪"),
             "mc-qty-cn",
             "中国 M2（月度，原生 **亿元**）。折美元需 USD/CNY 中间价 —— "
             "**不折美元就与美国 M2 比大小是错的**（实测折美元后中国 M2 ≈ 美国的 2.3 倍，"
             "而原值 3,568,083.6 vs 23,342.8 差 150 倍，方向也相反）。"
             "M1−M2 剪刀差衡量**资金活化度**（活钱 vs 死钱）。",
             questions=["q4"], card_rank=3),
        _kpi("cn_fx_reserve", "中国官方外汇储备", cn_res.get("fx_tn"),
             ((f"同比 {cn_res['fx_yoy']:+.2f}%｜" if cn_res.get("fx_yoy") is not None else "")
              + f"黄金 {cn_res['gold_oz']:,.0f} 万盎司（连续增持 {cn_res['gold_streak']} 月）"
              if cn_res.get("fx_tn") is not None else "数据未就绪"),
             "mc-qty-cn",
             "中国官方外汇储备（月度，原生 **亿美元** —— 本就美元，折算**不需要汇率**）。"
             "⚠️ 中国层的「外储」有**三套口径**：① 本卡的美元官方口径；② 黄金**实物量**"
             "（万盎司，非金额）；③ 央行表内的人民币口径（亿元）。三者**单位不同、"
             "折算率不是市场汇率**（实测 24 期由 6.886 单调降至 6.318）⇒ **严禁相加、严禁按市价互校**。",
             questions=["q5"]),
    ]

    # 汇率的截至日（P2 · 2026-09-26）：汇率会漂移，**不标日期的折算率不可审计** ——
    # 目标 T3 要求「三经济体同视野 + 明示折算汇率与各自 as_of」。与 `_fetch_fx` 同源同表。
    _fx_cond = " AND trade_date <= %s" if as_of else ""
    _fx_args: list = [as_of] if as_of else []
    _fx_row = query_one(
        "SELECT MAX(trade_date) AS d FROM currency_boc_daily "
        "WHERE COALESCE(mid_price, ref_price) IS NOT NULL" + _fx_cond, _fx_args)
    fx_as_of = str(_fx_row["d"]) if (_fx_row and _fx_row["d"]) else None

    reading = _quantity_verdict(glb, fed, eu, cn_money)
    return {
        "items": items,
        "global": glb,
        "us": {"fed": fed, "net_liquidity": net_liq, "money": us},
        "eu": eu,
        "cn": {"money": cn_money, "reserve": cn_res, "cb_balance": cn_cb, "omo": cn_omo},
        "fx": {"usd_cny": r(usd_cny, 4), "eur_usd": r(eur_usd, 4),
               "as_of": fx_as_of,
               "source": "currency_boc_daily（人民币中间价 COALESCE(mid_price, ref_price)）"},
        "reading": reading,
        "as_of_bis": glb.get("period"),
        "as_of_fed": fed.get("as_of"),
        "as_of_net_liq": net_liq.get("as_of"),
        "as_of_us_money": us.get("as_of"),
        "as_of_eu": eu.get("as_of"),
        "as_of_cn_money": cn_money.get("as_of"),
        "as_of_cn_reserve": cn_res.get("as_of"),
        "as_of_cn_cb": cn_cb.get("as_of"),
        "as_of_omo": (cn_omo[0]["as_of"] if cn_omo else None),
        "unit_target": "USD_TRILLION",
        "note": QUANTITY_NOTE,
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
    """期限结构形状的**短词**（判读条用；不带括号解释）

    ⚠️ 缺省词统一为「数据未就绪」（F4）：原为「未知」，而 `_shape_word` 又把它
       转成「已就绪」—— 同一个缺省在两套函数里两种说法，且「已就绪」对读者
       毫无意义。现统一；`_shape_word` 不再改写缺省词。
    ⚠️ 倒挂优先于分位：`spread_bp < 0` 是**不需要分位**就能判的硬事实。
    """
    if spread_bp is None:
        return "数据未就绪"
    if spread_bp < 0:
        return "倒挂"
    if p is None:
        return "数据未就绪"
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

    tone：资金价格进近一年低位（<=20 分位）→ 金（低估/机会侧：利率是估值分母，钱便宜对权益是正面影响）；
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
# 退化成了读数回声。现在判读条只给判断与影响（正面/负面影响、是否倒挂、政策动没动）。


def _verdict_level(level_pct) -> dict:
    """q1 钱现在贵不贵 —— Shibor 3M 的近一年分位（判断句：对权益是正面还是负面影响）"""
    if level_pct is None:
        return {"headline": "资金价格数据未就绪", "detail": "", "tone": "normal"}
    word = _level_brief(level_pct)
    if word == "偏松":
        head = "资金价格偏松，对权益是正面影响"
    elif word == "偏紧":
        head = "资金价格偏紧，对权益是负面影响"
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
    elif spread_pct is None:
        # F4：分位窗口不足时给**读得懂**的说明，不再输出「未知 / 已就绪」这类无意义词
        head = "期限结构：分位窗口不足，暂无法判陡平"
    else:
        head = f"期限结构{_shape_brief(spread_bp, spread_pct)}，尚未出现倒挂"
    # 倒挂不是「贵」而是「紧」—— 流动性紧张的定性，单独提级（与 _verdict_tone 同规）
    tone = "caution" if spread_bp < 0 else "normal"
    return {"headline": head,
            "detail": "利差越陡 = 短端越宽裕；**倒挂**（3M 低于隔夜）= 流动性紧张信号",
            "tone": tone}


def _verdict_policy(lpr: dict) -> dict:
    """q3 报价利率动没动 —— 政策姿态（月数与 LPR 报价在 KPI 区，判读条不重复）"""
    l1 = lpr.get("lpr_1y")
    if l1 is None:
        return {"headline": "LPR 数据未就绪", "detail": "", "tone": "normal"}
    idle = lpr.get("idle_months")
    head = "LPR 报价刚刚调整" if idle == 0 else "LPR 报价按兵不动"
    detail = ("LPR 每月 20 日报价；它与市场资金价格（Shibor）是否同步，本身就是信息"
              if lpr.get("since") else "")
    return {"headline": head, "detail": detail, "tone": "normal"}


# ---- 新增两问的子判读 + tone 收口（P2 · 2026-09-26）----
# 需求：卡片墙 3 张卡 → 4 张（一卡一环），新增「数量」「外部约束」两张。卡片墙对每张卡取
# `verdicts[<卡的 question>]`，缺 key 时**静默回退模块级 verdict** ⇒ 会出现「卡问数量、
# 判读条答价格」。故必须为 q4/q5 补上子判读。
#
# 🔴 判读条合法 tone 枚举 = 前端**实际定义了 CSS 类**的那几个（AnalysisOverview.vue 的 `.ao-verdict--*`）：
#   normal      → 走基类默认灰（有意不给修饰类）
#   opportunity → 金（.ao-verdict--opportunity）
#   caution     → 赭橙（.ao-verdict--caution）
#   mixed       → 蓝（.ao-verdict--mixed，本次随 D5=(b) 一并新增）
# 🔴 实测教训（2026-09-26）：`quantity.reading.tone='mixed'` 原先**没有对应 CSS 类**，直接透传会
#   生成 `ao-verdict--mixed` ⇒ **判读条静默变灰**：不报错、不告警，但「四层方向分化」这个核心
#   叙事被吃掉。故一律经 `_to_card_tone()` 收口 —— 接口**永不产出前端没有类的 tone**。
_CARD_TONES = {"normal", "opportunity", "caution", "mixed"}


def _to_card_tone(v, default: str = "normal") -> str:
    """把任意来源的 tone 收口到判读条枚举，避免生成不存在的 CSS 类（静默降级为灰）。

    ⚠️ 两套 tone **不可混用**：KPI 的 tone（`neutral` / `diff` / `updown`）与判读条的 tone
    （`normal` / `opportunity` / `caution` / `mixed`）是两个独立枚举，不要互相赋值。
    """
    return v if v in _CARD_TONES else default


def _verdict_quantity(quantity: dict) -> dict:
    """q4 钱有没有派生到实体 —— 判的是**增速的方向**，不是「同比为正」

    货币供应长期正增长，拿符号当信号等于恒真（四层同比实测全为**正**，但方向已分化）。

    卡片墙判读条遵守 registry 契约 ⑨（V1~V4：「判读条给判断与影响，KPI 给读数与分位」）：
    **不复述读数**、≤28 字、不机械拼接。故此处**不透传**模块级 `quantity.reading`
    （那是给详情页的四层总述），而按本卡口径（三张读数卡对应的美 / 欧元区 / 中国）另出判断。

    ⚠️ 2026-09-26 勘误：初版直接透传 `quantity.reading`，被判据守卫
    `_scratch/_probe_cardtext.py` 拦下 —— **V3 超长 4.6 倍（128 > 28 字）**且触发
    **V1**（「万亿美元（同比 +7.」重复 2 次，是模板拼接产物）。该守卫在契约 ⑨ 标注
    「改判读条后必跑」，P2 批次漏跑 ⇒ 这是全站 16 张卡里**唯一违规卡**。
    """
    src = quantity.get("reading") or {}
    layers = src.get("layers") or []
    # 「全球」层（BIS 境外美元信贷）是外部约束卡（q5）的论据，不属本卡口径
    sub = [x for x in layers
           if x.get("name") in ("美国", "欧元区", "中国") and x.get("dir") is not None]
    exp_n = sum(1 for x in sub if x["dir"] > 0)
    con_n = sum(1 for x in sub if x["dir"] < 0)
    if not sub:
        return {"headline": "货币总量数据未就绪", "detail": "", "tone": "normal"}
    if con_n and not exp_n:
        head = "主要货币区同向收缩，水位在回落"
    elif exp_n and not con_n:
        head = "主要货币区同向扩张，水位在抬升"
    else:
        head = "各货币区方向背离，水位未同向变化"
    return {
        "headline": head,
        "detail": "水位只判中期方向 —— 背离时须分地区看。状态描述，非择时信号。",
        "tone": _to_card_tone(src.get("tone")),
    }


def _verdict_external(external: dict) -> dict:
    """q5 外部在抽水还是送水 —— 美债曲线 / 中美利差 / 美元指数

    headline 实测 20 字（V3 合规）故原样保留；detail 初版透传模块级
    `external.reading.detail`（193 字，含四层清单 + 机制解释 + 免责），
    虽 V3 只约束 headline，但同一段话在窄卡上折约 5 行 ⇒ 此处只留**组合定性的首句**
    与短免责，机制解释留在详情页（不丢信息）。
    """
    src = external.get("reading") or {}
    combo = src.get("combo") or ""
    first = next((x.strip() for x in combo.split("。") if x.strip()), "")
    detail = (first + "。" if first else "") + "状态描述，非择时信号。"
    return {
        "headline": src.get("headline", ""),
        "detail": detail,
        "tone": _to_card_tone(src.get("tone")),
    }


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
        # 相对隔夜的利差（bp）：ON 自身恒为 0，作为锚
        sp_raw = (cv - on_cur) * 100 if (cv is not None and on_cur is not None) else None
        # 🔴 R14-1：形状词**只在后端算一次**（前端原有第二套 `shapeTag()` 已删除）。
        #    分位与判定一律用**未舍入**的 `sp_raw`：结论区 `term_spread.status` 用的是
        #    `spread_cur = (s3_cur - on_cur) * 100`，与本循环 3M 行是**同一算式、同一窗口**
        #    ⇒ 两处形状词必然逐字相同，不靠人工对齐（需求 §四 失败⑤ 的两处矛盾由此根治）。
        sp_hist = [(f(x[col]) - f(x["shibor_on"])) * 100 for x in win
                   if x[col] is not None and x["shibor_on"] is not None]
        if col == "shibor_on":
            shape = "锚位"          # F3：ON 自身即锚，利差恒为 0，根本谈不上"平陡"
        elif sp_raw is None:
            shape = "数据未就绪"
        else:
            shape = _shape_word(sp_raw, pctile(sp_hist, sp_raw))
        curve.append({
            "term": label,
            "cur": r(cv, 4),
            "prev_year": r(f(base_1y[col]), 4),
            "min_1y": r(min(vals), 4) if vals else None,
            "max_1y": r(max(vals), 4) if vals else None,
            "spread_bp": r(sp_raw, 1) if sp_raw is not None else None,
            "shape": shape,
            # 着色档随词一起下发 ⇒ 前端不再内联形状词表（见 `_shape_tone`）
            "shape_tone": _shape_tone(shape),
        })

    anom = _curve_anomaly(curve)

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

    # ---- ⑤ 数量（步骤 1，2026-09-26）：货币总量（全球 → 美国 → 欧元区 → 中国）----
    # 与上面四个「价格」切面正交；低频存量读数，**不占卡片墙**（D1=A），
    # 只作本详情页的独立分区 + 一条判读条。任一分组缺数据时**优雅降级**（见 `_quantity_state`）。
    quantity = _quantity_state(as_of)

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
            f"LPR 报价（1Y）已连续 {lpr['idle_months']} 个月按兵不动，"
            f"而市场利率（Shibor 3M）在这一年里自己走了 {amp_1y}bp 的振幅 —— "
            "说明当前是**政策观察期、市场自发定价**：资金面的边际变化来自市场供求而非政策调整，"
            "盯 Shibor 的边际变化比等 LPR 更能提前看到转向。"
        )
    elif lpr.get("idle_months") is not None:
        policy_market["reading"] = f"LPR 报价已连续 {lpr['idle_months']} 个月未动。"

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
            verdict, lvl = "钱便宜而权益位置也低 —— 分母（利率）与分子（位置）同时有利", "opportunity"
        elif cheap and not low_pos:
            verdict, lvl = "钱便宜但权益位置不低 —— 宽松大概率已被 price in", "normal"
        elif not cheap and low_pos:
            verdict, lvl = "钱不便宜但权益位置低 —— 分母在收紧，位置低不足以单独构成机会", "caution"
        else:
            verdict, lvl = "钱不便宜且权益位置不低 —— 两个维度都不利", "caution"
        equity_cross["level"] = lvl
        equity_cross["reading"] = (
            f"Shibor 3M 近一年 {pct_3m}% 分位（{'偏便宜' if cheap else '偏贵'}），"
            f"中证全指位置 {bench_pos:.0f}% 分位（{'偏低' if low_pos else '偏高'}）：{verdict}。"
            "⚠️ 这是**状态叠加**而不是择时信号——利率传导到估值需要时间，"
            "两者同向只说明方向一致，不构成买卖依据。"
        )

    # ---- 时序（供图表）----
    # 主图只放三条 Shibor：LPR 1Y 在 3.0% 而 Shibor 在 1.4% 附近，同轴会把三条 Shibor
    # 压成底部一坨、失去可读性。LPR 另给一条**独立纵轴**的阶梯序列（lpr.step），
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
    # LPR 阶梯（与主图同一日期轴；LPR 只在每月 20 日变，画出来天然是阶梯）
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
    # ---- 结论区：常态 4 张 + 异常日的第 5 张（R14-4 条件显示）----
    # 三个盒子各答一个正交问题：资金贵不贵（水平）/ 资金预期陡不陡（结构）/ 政策动没动（姿态）。
    # tone：利率水平与利差都不是"某个资产在涨跌"，故全 neutral（主色蓝、不带正号）——
    #      利率上行/下行交给 status 文案与刻度条表达，染红绿会把"资金收紧"误读成"利好"。
    #      （见 registry.py 卡片墙契约第 ④ 条）
    # 🔴 顺序约束（R8 × R14-4 的交叉）：`curve_dispersion` 的**条件追加必须在
    #    `_scope_text()` 之前** —— 区块声明报的是"当日常驻卡"的计数（常态 4 / 异常日 5）；
    #    若在追加前聚合，两种日子都会算出 5 张，正是需求 E4 勘误那类错误的翻版。
    # 两轴（R8）：每项都经 `_tag()` **唯一注入点**写入 region/market 并给 label 加前缀。
    # R10（P2 · 2026-09-27）：`Shibor 隔夜` 的分位**早就算出来了但没下发**（`scale=None`）⇒
    #    5 张卡里只有 2 张有参照系，而"绝对值对非专业读者是无意义字符串"。这里把它**算一次**
    #    提到装配前，避免在字典字面量里重复三遍同一算式（pct / scale / highlight 共用）。
    pct_on = pctile([f(x["shibor_on"]) for x in win if x["shibor_on"] is not None], on_cur)
    kpis = [
        _tag("shibor_3m", {
         "key": "shibor_3m", "card_rank": 1, "questions": ["q1"], "label": "Shibor 3M（资金价格）",
         "value": r(s3_cur, 2), "unit": "%", "tone": "neutral",
         "status": f"{_level_word(pct_3m)}｜20日 {('%+.1fbp' % chg20) if chg20 is not None else '--'}",
         "impact": _impact_of(_level_word(pct_3m), LEVEL_IMPACT),   # R11
         "pct": pct_3m, "scale": scale(pct_3m, "近一年"), "highlight": is_extreme(pct_3m),
         "anchor": "mc-trend",
         "hint": "3 个月期 Shibor（银行间同业拆借利率），是 A 股最上游的定价变量之一："
                 "利率是估值分母。⚠️ 绝对值跨期不可比，判断“贵不贵”一律看近一年分位"}),
        # 「预期」并入「价格」卡（D6=(a) · 2026-09-26）：term_spread 与 shibor_3m **同源同表**
        # （`interbank_rate_daily`），期限结构本就是「② 银行间定价」这一环的内部构成
        # ⇒ 归 q1。`verdicts.q2` 保留（不再有卡消费，无害）。
        # ⚠️ R14-1：本项 `status` 与 `curve` 中 3M 行的 `shape` 由**同一算式、同一窗口**
        #    得出（`_shape_word(spread_cur, pct_spread)`）⇒ 两处形状词必然逐字一致，
        #    不靠人工对齐 —— 需求 §四 失败⑤ 的"上下一百像素两个结论"由此根治。
        _tag("term_spread", {
         "key": "term_spread", "card_rank": 2, "questions": ["q1"], "label": "期限利差（3M − 隔夜）",
         "value": r(spread_cur, 1), "unit": "bp", "tone": "neutral",
         "status": f"{_shape_word(spread_cur, pct_spread)}",
         "impact": _impact_of(_shape_word(spread_cur, pct_spread), SHAPE_IMPACT),   # R11
         "pct": pct_spread, "scale": scale(pct_spread, "近一年"), "highlight": is_extreme(pct_spread),
         "anchor": "mc-curve",
         "hint": "长端减短端的利差，衡量资金期限结构：越陡说明短端资金越宽裕、"
                 "越平说明市场预期资金持续宽松；**倒挂**（3M 比隔夜还便宜）是流动性紧张的信号"}),
        # card_rank 3 → 1（P3 · 2026-09-26）：rank 是**卡内**排序（`pickCardKpis` 在每个
        # `question` 的子集内排序），不是全局排序。政策卡（q3 = 四环之①央行操作）现收三个读数：
        # LPR + 美联储总资产 + 美元净流动性 A5 ⇒ 本项为卡内第 1。
        # ⚠️ R14-2（D5-b：全页统一）：LPR 是**贷款市场报价利率**，不是央行设定的政策操作利率。
        #    「政策利率」一词只留给真正的政策操作利率（OMO 中标利率），本页不再拿它称 LPR。
        _tag("lpr", {
         "key": "lpr", "card_rank": 1, "questions": ["q3"], "label": "LPR 1Y（贷款报价利率）",
         "value": r(lpr.get("lpr_1y"), 2), "unit": "%", "tone": "neutral",
         "status": (f"已连续 {lpr['idle_months']} 个月未动"
                    if lpr.get("idle_months") is not None
                    else ("数据未就绪" if lpr.get("lpr_1y") is None else "--")),
         "impact": _impact_lpr(lpr),                                # R11（与上次取值比）
         "pct": None, "scale": None, "highlight": False, "anchor": "mc-lpr",
         "hint": "贷款市场报价利率（1 年期），每月 20 日报价。它是政策姿态的观察窗，"
                 "但与市场资金价格（Shibor）不同步——两者背离本身就是信息"}),
        # 补 `questions`（P2）：原先这两项不标 questions ⇒ 卡片墙过滤为空 ⇒ 静默回退全量。
        # 二者都是价格侧读数，归 q1；`shibor_on` 带 rank3 与 shibor_3m/term_spread 凑满价格卡三格。
        # ⚠️ R14-3（P2 · 2026-09-27）：原 `status` 是手写的**定义句**「短期资金面」——
        #    它不是判断（另 3 张给的都是偏松/偏平坦/16 个月未动）⇒ 读者拿不到"这算高还是低"。
        #    现接入 `_level_word(pct_on)`，与 Shibor 3M **同源同窗口**（实测 pct=65.6 ⇒ 偏紧）。
        # R10：`scale` 由 `None` 改为 `scale(pct_on, "近一年")` ⇒ 刻度条终于渲染（值早就有了）。
        # R13-4 锚点去重：隔夜是**短端利率**，它的论据是趋势图（`mc-trend`）而非期限结构表；
        #    原指向 `mc-curve`，会造成结论区 3 张卡跳同一处。
        _tag("shibor_on", {
         "key": "shibor_on", "card_rank": 3, "questions": ["q1"], "label": "Shibor 隔夜",
         "value": r(on_cur, 2), "unit": "%", "tone": "neutral",
         "status": _level_word(pct_on),
         "impact": _impact_of(_level_word(pct_on), LEVEL_IMPACT),   # R11
         "pct": pct_on, "scale": scale(pct_on, "近一年"),
         "highlight": is_extreme(pct_on), "anchor": "mc-trend",
         "hint": "隔夜 Shibor。日间波动最大，是资金面松紧最敏感的读数，但噪声也最大"}),
    ]
    # R14-4 条件显示：仅当曲线**非单调且中间期限凹陷 >5bp** 时，离散度才进结论区
    # （v2.6 · 方案 A：**只认凹陷**，凸起属跨月末日历效应，判据见 `_curve_anomaly`）；
    # 常态下它移入「银行间定价」明细的常驻读数条（说明句见 `_dispersion_note()`）—— 移走 ≠ 删除。
    # `status` 给的是**判断句**（指名哪个期限拐了），不是定义句（R14-3 的同一纪律）。
    if anom["is_anomaly"]:
        # 同样归 q1，但**不标 card_rank**：价格卡三格已被 shibor_3m / term_spread / shibor_on
        # 占满，`pickCardKpis` 优先取带 rank 的 ⇒ 本项不会挤进卡片墙，只在详情页结论区呈现。
        kpis.append(_tag("curve_dispersion", {
         "key": "curve_dispersion", "questions": ["q1"], "label": "期限离散度",
         "value": r(std([c["cur"] for c in curve if c["cur"] is not None]), 4), "unit": "%",
         "tone": "neutral",
         "status": f"曲线非单调：{anom['term']} 相对两端凹陷 {abs(anom['dev_bp'])}bp",
         # R11：凹陷 = 该期限资金淤积、利率被压低 ⇒ 机构倾向用该期限滚动融资 ⇒
         # 结构性因素消退时利率回摆、依赖该期限滚动的机构成本突升 ⇒ 权益负面；
         # 对债：该期限利率低本身不改变长端定价 ⇒ 中性。
         "impact": _impact("neg", "neu"),
         "pct": None, "scale": None, "highlight": False, "anchor": "mc-curve",
         "hint": "ON/1W/1M/3M 四个期限利率的标准差。与期限利差互补："
                 "利差只看两端，离散度能看出中间期限有没有异常拐点"}))
    # 区块级口径声明：**必须在条件追加之后**（见上方顺序约束）。
    kpis_scope = _scope_text(kpis, "本区口径")
    # 外部约束区的同类声明（R8）：与结论区**同位置、同形态**（卡组上方一行），
    # 且与外部卡片共用同一个列表 ⇒ 声明与卡片的口径必然一致（不会一处说全球、一处说美国）。
    external_kpis = external["items"] + ([external["dxy"]] if external["dxy"] else [])
    ext_scope = _scope_text(external_kpis, "本区口径")

    return {
        "as_of": data_as_of,
        "base_date_1y": str(base_1y["trade_date"]),
        "is_replay": bool(as_of),
        "kpis": kpis,
        # 卡片墙专用数组（P2 · 2026-09-26）：`AnalysisOverview.vue:201` 原本只读 `r.kpis`，
        # 而 `external_kpis` / `quantity_kpis` 是**独立数组、根本不被读取** ⇒ 新增卡上墙会是空的。
        # 这里下发三组并集（含 `questions`），供卡片墙按 `question` 过滤；
        # **详情页结论区仍读 `kpis`**（保持 5 张，不被 16 个读数撑大、稀释「结论先行」）。
        # ⚠️ 前端写法是 `r?.card_kpis ?? r?.kpis`（回退兼容）⇒ 其余 4 个模块零改动。
        "card_kpis": (kpis + external["items"]
                      + ([external["dxy"]] if external["dxy"] else [])
                      + quantity["items"]),
        # 外部约束的 KPI 单独一组（2026-09-25 批次 1）：主结论区只放国内三问 + 两个辅助读数，
        # 外部四项跟着 `mc-external` 分区一起渲染 —— 混进主结论区会把它从 5 个撑到 9 个，
        # 稀释「结论先行」。
        # （P2/P3 · 2026-09-26：原「不标 card_rank、卡片墙仍是 3 卡」的做法已由 D6=(a) 取代 ——
        #  四项中 cn_us_10y / us_10y / dxy 带 card_rank 上「外部约束」卡（q5），
        #  us_10y_2y 标 q5 但不带 rank（详情页完整呈现）。）
        "external_kpis": external_kpis,
        "external": external,
        # 数量维度（步骤 1，2026-09-26）：KPI 单独一组，跟着 `mc-quantity` 分区渲染。
        # （P2/P3 · 2026-09-26：原「不标 card_rank」的做法已由 D6=(a) 取代 —— 三经济体
        #  us_m2 / eu_m3 / cn_m2 带 card_rank 上「数量」卡（q4），其余低频明细只在详情页。）
        "quantity_kpis": quantity["items"],
        "quantity": quantity,
        "curve": curve,
        # R8：两处区块级口径声明（由各卡的 region/market **聚合**生成，绝不手写字面量）
        "scope_lines": {"kpis": kpis_scope, "external": ext_scope},
        # R12 段定义（P2 · 2026-09-27）：五段的 **key / 标签 / 序 / 必需性 / 分组** 随响应下发，
        # 前端**不内联词表**（与"窗口口径随响应下发"同一纪律，契约⑤）。
        # 分组线由 `group` 驱动（前端认组、不认 key）⇒ 将来加段只改 `HINT_SEGMENTS`，前端零改动。
        # ⚠️ 逐项 `dict(s)` 深拷一层：本字典对象是**模块级常量**，直接塞进响应会被 `ttl_cache`
        #    连引用一起缓存，此后任何一处误改都会静默污染全站（且同进程自检发现不了）。
        "hint_segments": [dict(s) for s in HINT_SEGMENTS],
        # R14-4：四期限异常判据 ＋ 「期限离散度」移入明细后的**常驻读数**（移走 ≠ 删除）。
        # 数值与说明一起下发 —— 它被移出结论区后前端没有别处能拿到；
        # 若让前端自己算标准差，就等于又开了一处「判定/计算在前端」的口子。
        "curve_anomaly": anom,
        "dispersion": {
            "value": r(std([c["cur"] for c in curve if c["cur"] is not None]), 4),
            "unit": "%",
            "note": _dispersion_note(curve, anom),
        },
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
            # q4 / q5（P2 · 2026-09-26）：新增「数量」「外部约束」两张卡后必须各有子判读 ——
            # 卡片墙取 `verdicts[m.question]`，缺 key 时 `?? r?.verdict` **静默回退模块级**
            # ⇒ 会出现「卡问数量、判读条答资金价格」，且不报错（评估报告 C1）。
            "q4": _verdict_quantity(quantity),
            "q5": _verdict_external(external),
        },
        "note": MONEY_COST_NOTE,
    }
