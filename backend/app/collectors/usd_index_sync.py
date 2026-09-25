#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
InvestBuddy 美元指数采集器（global_usd_index_daily）—— 官方日线为主 + 自算交叉校验 + 实时快照

蓝图的哪一块：**货币流动性 · 全球层 G1「美元指数 = 全球美元松紧的总闸门」**（设计文档 §2.1）。

★ 主口径更新（2026-09-25，用户 D2「再找找网上有无现成可拉历史的源」的实测结论）：

  **新浪外汇 jsonp 的 `NewForexService.getDayKLine` 通道可用**，返回 ICE 口径的美元指数日线：
    https://vip.stock.finance.sina.com.cn/forex/api/jsonp.php/var%20_DINIW=/NewForexService.getDayKLine?symbol=DINIW
  实测：200 / 475,073 字节 / **10,573 段 / 1985-11-08 ~ 当日** ⇒ 写入 `dxy_sina`。
  与官方实时快照对账（2026-09-24）：官方日线收 101.2632 vs 快照 101.2675 ⇒ **偏差 −0.004%**。
  ⇒ 这才是「美元指数」应有的主口径：40 年历史 + 官方口径，不必再从人民币中间价推。

  ⚠️ 注意与**旧结论的关系**：本文件此前写着「官方历史源全部实测失败」——
     那条结论**对东财/新浪 gi/外盘期货成立，但对新浪外汇 jsonp 不成立**，因为它当时没被测到。
     复测（2026-09-25，含编号镜像与 http）：
       · 东财 `push2his.eastmoney.com` kline 路径 —— 仍全族 RemoteDisconnected（1/2/7/48/71 镜像 + http 同样）
         （但 `push2.eastmoney.com` 的**实时**接口可达，实测 secid=100.UDI 返回「美元指数」101.29）
       · 新浪 `gi.finance.sina.com.cn/hq/daily` —— 仍不支持 UDI/DINIW（`not found baseinfo`），
         它的 20 个品种（`index_global_name_table`）里**没有美元指数**
       · akshare `index_global_hist_em()` 默认参数就叫「美元指数」，但底层正是被拦的 push2his ⇒ 挂
       · akshare `forex_hist_em` 的 190 个品种映射里**没有美元指数**（全是货币对）⇒ 挂
       · 外盘期货 `ak.futures_foreign_hist('DX')` —— 仍只返 13 行（2019 年陈年数据）
       · Yahoo `DX-Y.NYB` → 403；FRED/investing → 超时；marketwatch → 验证码；stooq → JS 挑战
     ⇒ 教训：**「全部实测失败」这类结论必须写清测过哪些、没测哪些**，否则会把「没测到」当成「不存在」。

  **自算（dxy_calc）保留、但降级为交叉校验列**：
  自算 = ICE DXY 标准公式 × 6 个成分货币交叉汇率，源为 `ak.currency_boc_safe()`（人民币中间价宽表，25 币种，1994 起）。
  与官方日线互比实测（2,380 个共有日）：**|偏差| 中位 0.28% / p90 0.74% / p99 1.43% / 最大 2.29%**。
  ⚠️ **别用带符号均值（+0.074%）代表精度** —— 正负相抵会把它压得很小、看起来像 ±0.1%，
     而真实离散度是它的 4 倍（中位）到 30 倍（极值）。这是本文件 2026-09-25 自纠的一处口径错误。
  ⚠️ 偏差的成因是**机制差、不是 bug**：人民币中间价是**每日 9:15 的官方定盘价、基于前一交易日篮子**，
     而 ICE DXY 是连续交易 ⇒ **趋势日自算会系统性滞后约 1 天**。
     实例（2022-09-23 英国迷你预算日）：官方日线 111.29 → **113.03**（+1.6%），自算 110.79 → 110.60（几乎不动）；
     到 09-28 反而官方回落至 112.71、自算补涨到 113.97。
  ⇒ 保留自算的理由不是出数，而是**当独立的第二口径**：两条互比能立刻暴露单位错
     （单位错是「全体平移」型偏差，会远超 2.5%；而定盘滞后是「偶发大波动日」型，全史最大 2.29%）。

⚠️⚠️ 一条必须记住的勘误（本文件头最重要的内容）：
  设计文档初稿（货币流动性观测体系设计_2026-09-25.md §4.4）曾把偏差写成 **−3.09%**，
  并据此得出「中间价口径与 ICE 官方有约 ±3% 系统性偏离、不作绝对值引用」的结论。
  **那个 −3.09% 是我们自己的单位错，不是口径局限。**
  根因：`currency_boc_safe` 的 25 个币种**混用直接标价与间接标价**，而列名看不出来 ——
  瑞典克朗/挪威克朗等 15 个币种是「外币 / 100 人民币」（间接），
  当直接标价算会把 USDSEK 算成 4.58（真值 9.94），自算 DXY 因此偏低 3.3%。
  3.3% 恰好落在「看起来像是中间价与市场价的固有偏离」的量级上，于是错误被当成了口径特征。
  ⇒ 教训：**量级对不上的"口径差异"，先怀疑自己的单位，再怀疑口径。**
  ⇒ 防护：`_CROSS_RANGE` 逐币量级断言（任一交叉汇率越界即整行跳过并记 error）。

DXY 标准公式（ICE 官方权重，合计 100%）：
  DXY = 50.14348112 × EURUSD^(-0.576) × USDJPY^(0.136) × GBPUSD^(-0.119)
                     × USDCAD^(0.091) × USDSEK^(0.042) × USDCHF^(0.036)
  权重为正的货币（JPY/CAD/SEK/CHF）是"美元/该币"，为负的（EUR/GBP）是"该币/美元"。

幂等：PRIMARY KEY(trade_date) + 增量（**两个源各按自己的列水位**推进；官方日线首次全量回填 1985 起）。
"""
import logging
import re
from datetime import date, datetime, timedelta

import pymysql
import akshare as ak
import requests

from ..db import get_db_config
from ._common import with_steps, call_with_timeout

logger = logging.getLogger(__name__)

RUN_STEPS = [
    {"no": 1, "name": "拉取新浪官方美元指数日线", "params": "NewForexService.getDayKLine?symbol=DINIW（ICE 口径，1985-11-08 起，实测 10573 行）→ 写入 dxy_sina（★主口径）"},
    {"no": 2, "name": "拉取人民币中间价宽表", "params": "currency_boc_safe（25 币种，含 DXY 全部 6 个成分）"},
    {"no": 3, "name": "算交叉汇率", "params": "由「每 100 外币兑人民币」相除得 EURUSD/USDJPY/GBPUSD/USDCAD/USDSEK/USDCHF"},
    {"no": 4, "name": "套 ICE 权重公式", "params": "DXY = 50.14348112 × EURUSD^-0.576 × USDJPY^0.136 × GBPUSD^-0.119 × USDCAD^0.091 × USDSEK^0.042 × USDCHF^0.036 → 写入 dxy_calc（交叉校验列）"},
    {"no": 5, "name": "拉 DINIW 实时快照", "params": "hq.sinajs.cn/list=DINIW（官方口径，仅当日，作标定基准；需带 Referer 否则 403）"},
    {"no": 6, "name": "增量 Upsert", "params": "PRIMARY KEY(trade_date)；两个源各自按自己的列水位增量（dxy_sina 首次全量回填），在 Python 侧按日期并集合并"},
]

# ICE DXY 权重（正=美元/该币，负=该币/美元）
_W = {"eur_usd": -0.576, "usd_jpy": 0.136, "gbp_usd": -0.119,
      "usd_cad": 0.091, "usd_sek": 0.042, "usd_chf": 0.036}
_CONST = 50.14348112

# 人民币中间价宽表 → DXY 6 成分货币。**必须逐币标注标价法**：
#
# ⚠️⚠️ 这是本采集器最凶的一个坑（实测 2026-09-25 踩到并定位）：
#   源表 25 个币种**混用两种标价法**，而列名看不出来 ——
#     · 美元/欧元/日元/港元/英镑/澳元/新西兰元/新加坡元/瑞士法郎/加元 = **直接标价**：
#       值 = 人民币 / **100 外币**。例：美元 674.89、日元 4.259（即 6.7489 元/美元、0.04259 元/日元）。
#     · 瑞典克朗/挪威克朗/丹麦克朗/兹罗提/林吉特/卢布/兰特/韩元/迪拉姆/里亚尔/福林/里拉/
#       比索/泰铢/澳门元 = **间接标价**：值 = 外币 / **100 人民币**。
#       例：瑞典克朗 147.31 → 人民币 1 元对 1.4731 瑞典克朗。
#   这与 **CFETS 人民币汇率中间价公告**的写法完全一致（公告对前 10 个币种写
#   「1 美元对人民币 6.7489 元」，对后 15 个写「人民币 1 元对 1.4731 瑞典克朗」）。
#
#   若把瑞典克朗误当直接标价，会得到 USDSEK=4.58（真值≈9.94，差 2.17 倍），
#   自算 DXY 因此偏低 3.3%、恰好落在"看起来像是中间价与市场价的固有偏离"的量级上 ——
#   **这个错误曾被误当成口径局限写进设计文档（"±3% 系统性偏离"），后经逐币反算交叉汇率才发现
#   是单位错，修正后自算与官方快照偏差仅 +0.09%。** 故本表同时存 6 个交叉汇率 + 逐币量级断言
#   (`_CROSS_RANGE`)，让同类错误下次立刻暴露而不是伪装成"口径差异"。
_SAFE_COL: dict[str, tuple[str, str]] = {
    "usd": ("美元", "direct"),
    "eur": ("欧元", "direct"),
    "jpy": ("日元", "direct"),
    "gbp": ("英镑", "direct"),
    "cad": ("加元", "direct"),
    "chf": ("瑞士法郎", "direct"),
    "sek": ("瑞典克朗", "inverse"),
}

# 交叉汇率量级断言（超出即判为「标价法/单位写错」而非真实行情）
# 区间刻意取得宽松（覆盖极端行情），只为拦住 ≥1.5 倍的量级错误。
_CROSS_RANGE: dict[str, tuple[float, float]] = {
    "eur_usd": (0.80, 1.70),
    "usd_jpy": (70.0, 220.0),
    "gbp_usd": (0.90, 2.00),
    "usd_cad": (0.85, 2.00),
    "usd_sek": (5.5, 15.0),
    "usd_chf": (0.55, 1.50),
}

# 自算 DXY 的合理区间（ICE DXY 历史区间约 70~165；放宽到 40~250 只为拦住量级错误）
_SANE_LO, _SANE_HI = 40.0, 250.0

_TGT = ["trade_date", "dxy_sina", "dxy_calc", "dxy_snapshot",
        "eur_usd", "usd_jpy", "gbp_usd", "usd_cad", "usd_sek", "usd_chf"]

# 新浪「官方美元指数日线」通道（本轮 D2 实测找到的现成历史源）
_SINA_URL = ("https://vip.stock.finance.sina.com.cn/forex/api/jsonp.php/"
             "var%20_DINIW=/NewForexService.getDayKLine?symbol=DINIW")
_SINA_FIRST_DATE = date(1985, 11, 8)   # 源最早一日（实测），首次采集全量回填


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


def _f(v, nd: int = 6):
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else round(f, nd)


def _cny_per_unit(kind: str, v: float | None) -> float | None:
    """按标价法把源值换算成「人民币元 / 1 外币」

    ⚠️ 标价法只有**两种**，且都不是"1 单位"起步（源一律按 100 单位报价）：
      · direct  ：值 = 人民币 / **100 外币** → 人民币/1 外币 = v/100
        （美元 674.89 → 6.7489；日元 4.259 → 0.04259；两者同法，只是量级不同）
      · inverse ：值 = 外币 / **100 人民币** → 人民币/1 外币 = 100/v
        （瑞典克朗 147.31 → 0.67884）

    ⚠️ 曾把日元当成"值 = 人民币/1 日元"再除两次 100 ⇒ USDJPY 算出 15,846（真值 158.46）；
       也曾把瑞典克朗当直接标价 ⇒ USDSEK 算出 4.58（真值 9.94）、DXY 偏低 3.3%。
       两次都是**单位错伪装成口径差异**，故保留 _CROSS_RANGE 量级断言。
    """
    if not v or v <= 0:
        return None
    if kind == "direct":
        return v / 100.0
    if kind == "inverse":
        return 100.0 / v
    return None


def _cross(raw: dict[str, float | None]) -> tuple[dict, list[str]]:
    """由人民币中间价宽表算 6 个成分货币交叉汇率 → (cross, 越界项)

    ⚠️ 标价法必须逐币区分（见 _SAFE_COL 上方长注释）：把「间接标价」的瑞典克朗
       当直接标价算，会得到 USDSEK=4.58（真值≈9.94），自算 DXY 随之偏低 3.3%。
    """
    cny = {k: _cny_per_unit(kind, raw.get(k)) for k, (_, kind) in _SAFE_COL.items()}
    usd = cny.get("usd")
    if not usd:
        return {}, []
    need = ("eur", "jpy", "gbp", "cad", "sek", "chf")
    if any(not cny.get(k) for k in need):
        return {}, []

    out = {
        "eur_usd": _f(cny["eur"] / usd, 6),
        "usd_jpy": _f(usd / cny["jpy"], 6),
        "gbp_usd": _f(cny["gbp"] / usd, 6),
        "usd_cad": _f(usd / cny["cad"], 6),
        "usd_sek": _f(usd / cny["sek"], 6),
        "usd_chf": _f(usd / cny["chf"], 6),
    }
    bad = [k for k, (lo, hi) in _CROSS_RANGE.items() if out.get(k) is None
           or not (lo <= out[k] <= hi)]
    return out, bad


def _dxy(cross: dict) -> float | None:
    if not cross:
        return None
    v = _CONST
    for k, w in _W.items():
        x = cross.get(k)
        if not x or x <= 0:
            return None
        v *= x ** w
    return round(v, 4)


def _fetch_sina_daily(timeout: float = 25.0) -> tuple[dict[date, float], dict]:
    """新浪官方美元指数日线（ICE 口径）→ ({日期: 收盘}, 元信息)

    ⭐ 这是本项目「美元指数」的**主口径**来源（2026-09-25 实测找到的现成历史源）。

    响应形如（实测：200 / 475,073 字节 / 10,573 段 / 1985-11-08 ~ 当日）：
      /*<script>location.href='//sina.com';</script>*/
      var _DINIW=("1985-11-08,129.2200,128.9100,129.6600,129.1300,|1985-11-11,...|...");
    每段 `日期,开,低,高,收,`，段间用 `|` 分隔（段尾多一个逗号 → 空串需过滤）。

    ⚠️ **列序是实测反推的、不是猜的**：用 OHLC 不变量（低≤开≤高 且 低≤收≤高）检验两种假设 ——
       `日期,开,低,高,收` 命中 **10573/10573（100%）**；
       `日期,开,高,低,收` 只命中 5/10573（0.0%）。故取前者、取收盘 = 每段第 4 个数值。
       若哪天新浪改了列序，OHLC 自检（`_ohlc_bad`）会立刻把行数与比例暴露出来。

    ⚠️ Referer 必须带：实测不带 Referer 打 `hq.sinajs.cn` 直接 403 Forbidden；
       本接口（vip.stock.finance.sina.com.cn）两者都能过，但统一带上更稳。

    返回元信息便于日志与 DQ 核对：rows / d0 / d1 / bad_ohlc / bytes。
    """
    def _do():
        r = requests.get(_SINA_URL, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120",
            "Referer": "https://finance.sina.com.cn/"}, timeout=timeout)
        r.encoding = "gb18030"
        return r.text

    try:
        txt = call_with_timeout(_do, timeout + 5)
    except Exception as e:  # noqa: BLE001
        logger.warning("  DINIW 官方日线拉取失败：%s %s", type(e).__name__, str(e)[:80])
        return {}, {"error": f"{type(e).__name__}: {str(e)[:80]}"}

    m = re.search(r"\((\".*?\")\)", txt or "", re.S)
    if not m:
        logger.warning("  官方日线响应体无法匹配（前 120 字）：%s", (txt or "")[:120])
        return {}, {"error": "no_payload", "head": (txt or "")[:120]}

    body = m.group(1).strip().strip('"')
    out: dict[date, float] = {}
    bad_fmt, bad_ohlc = 0, 0
    for seg in body.split("|"):
        seg = seg.strip()
        if not seg:
            continue
        p = seg.split(",")
        if len(p) < 5:
            bad_fmt += 1
            continue
        d = _d(p[0])
        if d is None:
            bad_fmt += 1
            continue
        try:
            _o, lo, hi, close = (float(x) for x in p[1:5])
        except (TypeError, ValueError):
            bad_fmt += 1
            continue
        # OHLC 不变量自检：拦「列序变了/单位变了」这类静默错位
        if not (lo <= _o <= hi and lo <= close <= hi) or not (_SANE_LO <= close <= _SANE_HI):
            bad_ohlc += 1
            continue
        out[d] = round(close, 4)

    meta = {"rows": len(out),
            "d0": str(min(out)) if out else None,
            "d1": str(max(out)) if out else None,
            "bad_fmt": bad_fmt, "bad_ohlc": bad_ohlc, "bytes": len(txt or "")}
    return out, meta


def _fetch_diniw(timeout: float = 12.0) -> tuple[float | None, date | None]:
    """新浪官方美元指数实时快照（GB18030 编码）→ (指数值, 快照报价日)

    响应形如（实测 2026-09-25）：
      var hq_str_DINIW="09:44:44,101.2596,101.2596,101.2437,978,101.2428,101.3064,
                       101.2086,101.2596,美元指数,2026-09-25";
    字段 1 与字段 8 同为最新价（交叉校验用），字段 10 为报价日期。
    取字段 1；越界则视为不可信快照、返回 (None, None)（不写脏值）。

    ⚠️ **必须返回报价日**：人民币中间价宽表的最新一行通常**早于今天**（中间价当日发布、
       而本采集器可能在其之前跑），若按「d == today」挂快照，快照会永远写不进去
       （首跑实测正是如此：快照取到了、却因当日无行而整条丢失）。
       正确做法是让快照与**它自己报价日**的那一行对齐。
    """
    def _do():
        r = requests.get(
            "https://hq.sinajs.cn/list=DINIW",
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120",
                     "Referer": "https://finance.sina.com.cn/"},
            timeout=timeout)
        r.encoding = "gb18030"
        return r.text.strip()

    try:
        txt = call_with_timeout(_do, timeout + 3)
    except Exception as e:  # noqa: BLE001
        logger.warning("  DINIW 快照拉取失败：%s %s", type(e).__name__, str(e)[:60])
        return None, None
    m = re.search(r'"([^"]*)"', txt or "")
    if not m:
        return None, None
    parts = m.group(1).split(",")
    if len(parts) < 11:
        return None, None
    v = _f(parts[1], 4)
    if v is None or not (_SANE_LO <= v <= _SANE_HI):
        logger.warning("  DINIW 快照越界或不可解析：%s", parts[:3])
        return None, None
    return v, _d(parts[10])


class UsdIndexSyncCollector:
    """美元指数日频同步 —— ★主口径 = 新浪官方日线；自算降级为交叉校验；实时快照作标定锚

    三个口径的分工（2026-09-25 实测定案，勿回退）：

      dxy_sina     ★主口径。新浪 `NewForexService.getDayKLine?symbol=DINIW`，
                   ICE 官方口径日线收盘，**1985-11-08 起 10,573 行**。
                   实测与官方实时快照偏差 **−0.004%**（101.2632 vs 101.2675）⇒ 可直接用绝对值。
      dxy_calc     交叉校验。人民币中间价交叉汇率自算，2016-12 起 2,380 行。
                   与 dxy_sina 实测 **|偏差| 中位 0.28% / p90 0.74% / p99 1.43% / 最大 2.29%**
                   （⚠️ 带符号均值 +0.074% 会低估离散度，勿引用；成因是中间价"每日定盘 + 隔日篮子"
                     带来的约 1 天滞后，非 bug）。
                   ⇒ 保留它不是为了出数，而是当**独立的第二口径**：两条互比能立刻暴露单位错
                     （本项目正是靠它发现了「瑞典克朗标价法算错 ⇒ DXY 偏低 3.3%」这个伪装成
                     「口径差异」的 bug）。故即便已有官方源，也不删自算。
      dxy_snapshot 实时快照，仅采集当日写入，作标定锚（需带 Referer，否则 403）。

    ⚠️ 快照落位：`dxy_snapshot` 的报价日通常**比两个源的末行都新**（它是实时价）——
       旧实现靠「INSERT 挂不上就 UPDATE 表内最新行」兜底（首跑实测曾整条丢失过快照）。
       现在改为**在 Python 侧按日期并集合并**：快照天然落到它自己的报价日那一行，
       不再需要 UPDATE 兜底，也就不存在「快照日期 ≠ 读数日期」这种需要向用户解释的错位。

    ⚠️ 两个源走**各自的交易日历**（官方日线=全球汇市日、自算=中国工作日），
       所以表内日期是并集、每一列只在各自有值的日期非空；增量水位也必须**按列**取
       （`MAX(trade_date) WHERE dxy_sina IS NOT NULL` / `... dxy_calc IS NOT NULL`），
       否则「只有快照的那一行」会把水位往前顶、把另一条腿的一整天静默跳过。
    """

    def __init__(self, timeout_sec: float = 90, first_lookback_days: int = 3650):
        # ⚠️ first_lookback_days 现在**只管自算腿**（首次回填窗口）。
        #    官方日线腿首次采集恒为**全量**（源起点 1985-11-08 `_SINA_FIRST_DATE`）——
        #    「找现成历史源」的全部意义就是一次拿到 40 年，不设窗口。
        self.timeout_sec = float(timeout_sec)
        self.first_lookback_days = int(first_lookback_days)

    def run(self) -> dict:
        errors: list[str] = []

        # ① ★主口径：新浪官方美元指数日线（ICE 口径，1985-11-08 起）
        sina, sina_meta = _fetch_sina_daily(max(self.timeout_sec, 25))
        if not sina:
            # 主口径挂了要显式记 error（不能静默降级成「只有自算」还不吭声）
            errors.append(f"官方日线源不可用：{sina_meta.get('error') or sina_meta}")

        # ② 交叉校验口径：人民币中间价宽表 → 自算
        try:
            df = call_with_timeout(ak.currency_boc_safe, max(self.timeout_sec, 60))
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"人民币中间价宽表拉取失败：{type(e).__name__} {str(e)[:120]}") from e
        if df is None or df.empty:
            raise RuntimeError("人民币中间价宽表返回为空")

        missing = [c for c in (v[0] for v in _SAFE_COL.values()) if c not in df.columns]
        if missing:
            raise RuntimeError(f"源列名不匹配（疑似 akshare 升级改了列名）：缺 {missing}")

        conn = pymysql.connect(**get_db_config().to_dict(),
                               cursorclass=pymysql.cursors.DictCursor)
        written, bad_range, bad_cross = 0, 0, 0
        n_calc = n_sina = 0
        try:
            with conn.cursor() as cur:
                # ⚠️ 水位必须**按列**取：若有「只有 dxy_snapshot」的行，按表取水位会把它顶到最前，
                #    从而把另一条腿的一整天静默跳过（本项目已有同类前科）。
                cur.execute("SELECT MAX(trade_date) AS d FROM global_usd_index_daily "
                            "WHERE dxy_calc IS NOT NULL")
                last_calc = cur.fetchone()["d"]
                start_calc = ((last_calc + timedelta(days=1)) if last_calc
                              else date.today() - timedelta(days=self.first_lookback_days))
                cur.execute("SELECT MAX(trade_date) AS d FROM global_usd_index_daily "
                            "WHERE dxy_sina IS NOT NULL")
                last_sina = cur.fetchone()["d"]
                # 官方日线**首次采集直接全量回填**（源最早 1985-11-08）——
                # 这正是「去找现成历史源」的意义：一次拿到 40 年，而不是从今天开始累积。
                start_sina = (last_sina + timedelta(days=1)) if last_sina else _SINA_FIRST_DATE

                snap, snap_date = _fetch_diniw()

                # ---- 三个口径按日期并集合并（两条腿的交易日历不同）----
                merged: dict[date, dict] = {}
                skipped_dates, src_last = 0, None
                for _, r in df.iterrows():
                    d = _d(r.get("日期"))
                    if d is None:
                        continue
                    src_last = d if src_last is None or d > src_last else src_last
                    if d < start_calc:
                        skipped_dates += 1
                        continue
                    vals = {k: _f(r.get(col), 6) for k, (col, _) in _SAFE_COL.items()}
                    cross, bad = _cross(vals)
                    if bad:
                        # 量级断言不通过 ⇒ 判为标价法/单位写错，不写脏值（见 _CROSS_RANGE）
                        bad_cross += 1
                        errors.append(f"{d} 交叉汇率越界：{ {k: cross.get(k) for k in bad} }")
                        continue
                    if not cross:
                        continue          # 该日 6 成分不全（早期年份），整行不写
                    dxy = _dxy(cross)
                    if dxy is not None and not (_SANE_LO <= dxy <= _SANE_HI):
                        bad_range += 1
                        dxy = None
                    slot = merged.setdefault(d, {})
                    slot["dxy_calc"] = dxy
                    for k in ("eur_usd", "usd_jpy", "gbp_usd", "usd_cad", "usd_sek", "usd_chf"):
                        slot[k] = cross[k]
                    n_calc += 1

                for d, close in sina.items():
                    if d < start_sina:
                        continue
                    merged.setdefault(d, {})["dxy_sina"] = close
                    n_sina += 1

                # 快照落到它自己的报价日（并集合并 ⇒ 无需再 UPDATE 兜底）；报价日解析失败才回落
                snap_where = ""
                if snap is not None:
                    if snap_date is not None:
                        merged.setdefault(snap_date, {})["dxy_snapshot"] = snap
                        snap_where = "exact"
                    else:
                        fb = max(merged) if merged else None
                        if fb is None:
                            cur.execute("SELECT MAX(trade_date) AS d FROM global_usd_index_daily")
                            fb = cur.fetchone()["d"]
                        if fb is not None:
                            merged.setdefault(fb, {})["dxy_snapshot"] = snap
                            snap_where = "latest"

                rows = [(d, *(m.get(k) for k in _TGT[1:])) for d, m in sorted(merged.items())]

                sql = (
                    f"INSERT INTO global_usd_index_daily ({', '.join(_TGT)}, update_time, data_source) "
                    f"VALUES ({', '.join(['%s'] * len(_TGT))}, NOW(), %s) "
                    "ON DUPLICATE KEY UPDATE "
                    + ", ".join(f"{c}=COALESCE(VALUES({c}), {c})"
                                for c in _TGT if c != "trade_date")
                    + ", update_time=NOW()"
                )
                if rows:
                    cur.executemany(sql, [(*r, "SINA_DINIW+AKSHARE+BOC_SAFE") for r in rows])
                cur.execute(
                    "SELECT COUNT(*) AS n, MIN(trade_date) AS d0, MAX(trade_date) AS d1, "
                    "SUM(dxy_sina IS NOT NULL) AS n_sina, SUM(dxy_calc IS NOT NULL) AS n_calc, "
                    "MAX(CASE WHEN dxy_sina IS NOT NULL THEN trade_date END) AS d_sina, "
                    "MAX(CASE WHEN dxy_calc IS NOT NULL THEN trade_date END) AS d_calc, "
                    "MAX(CASE WHEN dxy_snapshot IS NOT NULL THEN trade_date END) AS d_snap "
                    "FROM global_usd_index_daily")
                st = cur.fetchone()
            conn.commit()
            written = len(rows)
        finally:
            conn.close()

        if bad_range:
            errors.append(f"{bad_range} 行自算 DXY 越界 [{_SANE_LO},{_SANE_HI}]，已置 NULL")
        if bad_cross:
            errors.append(f"{bad_cross} 行交叉汇率越界，已整行跳过（疑似标价法/单位写错）")
        msg = (f"美元指数写 {written} 行（官方日线 +{n_sina} · 自算 +{n_calc}）"
               f"｜表内 {st['n']} 行（{st['d0']} ~ {st['d1']}）"
               f"｜★官方日线 {st['n_sina']} 行至 {st['d_sina']}｜自算 {st['n_calc']} 行至 {st['d_calc']}"
               f"｜快照累计至 {st['d_snap']}｜主口径 = 新浪官方日线（ICE 口径），自算仅作交叉校验")
        logger.info("✅ %s", msg)
        return with_steps(
            {"records_written": written, "error_count": len(errors), "errors": errors[:20],
             "note": msg},
            RUN_STEPS,
            {
                1: (f"{sina_meta.get('rows')} 行（{sina_meta.get('d0')} ~ {sina_meta.get('d1')}）"
                    f"· 本次新写 {n_sina} 行 · 格式错 {sina_meta.get('bad_fmt')}"
                    f" · OHLC/值域越界 {sina_meta.get('bad_ohlc')}")
                    if sina else f"未取到（{sina_meta.get('error') or '空'}）",
                2: f"{len(df)} 行 · 25 币种（跳过 {skipped_dates} 行窗口外）",
                3: f"6 成分齐备 {n_calc} 行（交叉汇率越界 {bad_cross}）",
                4: f"自算 dxy_calc {n_calc} 行（DXY 越界 {bad_range}）",
                5: (f"DINIW 快照 {snap}（报价日 {snap_date}，落位={snap_where or '无'}）"
                    if snap is not None else "DINIW 快照未取到"),
                6: f"upsert {written} 行（表内 {st['n']} 行，最新 {st['d1']}）",
            },
        )
