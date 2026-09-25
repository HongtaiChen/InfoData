#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
InvestBuddy 美元指数采集器（global_usd_index_daily）—— 自算 + 快照标定

蓝图的哪一块：**货币流动性 · 全球层 G1「美元指数 = 全球美元松紧的总闸门」**（设计文档 §2.1）。

为什么必须自算（三条官方历史源全部实测失败，2026-09-25）：
  · 东财  `push2his` 域名整体不通（kline 路径被拦）⇒ `ak.index_global_hist_em` 全族挂
  · 新浪  `gi.finance.sina.com.cn/hq/daily` 对 `UDI` / `DINIW` 返回
          `not found baseinfo`（它只支持 DAX/CAC/UKX/SX5E/NKY/KOSPI 等）
  · 外盘期货 `ak.futures_foreign_hist('DX')` 只返 13 行（2019 年的陈年数据）
  · 新浪实时 `hq.sinajs.cn/list=DINIW` **可达**，但只给当前快照、**无历史**

自算方案：ICE DXY 的 6 个成分货币全部在 `ak.currency_boc_safe()`
（人民币中间价宽表，25 币种，1994 起）里 ⇒ 用标准权重公式算交叉汇率。

🎯 精度实测（2026-09-25，修正单位错后）：
  自算 **101.3532**  vs  新浪官方 DINIW 快照 **101.2675**  ⇒  偏差 **+0.085%**
  ⇒ 两条口径实际**基本一致**，自算值可直接用于绝对值，不必只当趋势参考。

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

⚠️ 仍然存在的口径差异（真实、但很小）：中间价是官方参考价、ICE 用银行间市场价，
   两者在极端行情下会有日间分歧；且 `dxy_snapshot` 是**实时价**、`dxy_calc` 是**中间价日价**，
   两者可能相差 1 天。故 `dxy_snapshot` 仅作标定基准，两张口径并存、不互相覆盖。

DXY 标准公式（ICE 官方权重，合计 100%）：
  DXY = 50.14348112 × EURUSD^(-0.576) × USDJPY^(0.136) × GBPUSD^(-0.119)
                     × USDCAD^(0.091) × USDSEK^(0.042) × USDCHF^(0.036)
  权重为正的货币（JPY/CAD/SEK/CHF）是"美元/该币"，为负的（EUR/GBP）是"该币/美元"。

幂等：PRIMARY KEY(trade_date) + 增量（起点 = 本地 MAX(trade_date) 的次日）。
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
    {"no": 1, "name": "拉取人民币中间价宽表", "params": "currency_boc_safe（25 币种，含 DXY 全部 6 个成分）"},
    {"no": 2, "name": "算交叉汇率", "params": "由「每 100 外币兑人民币」相除得 EURUSD/USDJPY/GBPUSD/USDCAD/USDSEK/USDCHF"},
    {"no": 3, "name": "套 ICE 权重公式", "params": "DXY = 50.14348112 × EURUSD^-0.576 × USDJPY^0.136 × GBPUSD^-0.119 × USDCAD^0.091 × USDSEK^0.042 × USDCHF^0.036"},
    {"no": 4, "name": "拉 DINIW 实时快照", "params": "hq.sinajs.cn/list=DINIW（官方口径，仅当日，作标定基准）"},
    {"no": 5, "name": "增量 Upsert", "params": "PRIMARY KEY(trade_date)；起点 = 本地 MAX(trade_date) 次日"},
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

_TGT = ["trade_date", "dxy_calc", "dxy_snapshot",
        "eur_usd", "usd_jpy", "gbp_usd", "usd_cad", "usd_sek", "usd_chf"]


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
    """美元指数日频同步（自算为主 + DINIW 快照标定）"""

    def __init__(self, timeout_sec: float = 90, first_lookback_days: int = 3650):
        self.timeout_sec = float(timeout_sec)
        self.first_lookback_days = int(first_lookback_days)

    @staticmethod
    def _stitch_snapshot(cur, snap: float | None, snap_date: date | None,
                         fallback_last: date | None = None) -> str:
        """把快照补写到表里 → 返回落位说明（'exact' / 'latest' / ''）

        存在的必要：快照的报价日通常**比源表最新一行还新 1 天**（新浪 DINIW 是实时价，
        而人民币中间价宽表的末行是 T 或 T−1），且增量窗口起点是「本地水位+1」——
        两者在「本地已追平源」时不会相交 ⇒ 若只靠 INSERT，快照会永远写不进去
        （首跑实测正是如此：快照取到了 101.2656，却因当日无行而整条丢失）。

        落位顺序：
          1) 快照报价日恰好有行 → 写该行（精确对齐）
          2) 否则回落到**表内最新一行**（fallback_last）—— 两者相隔 ≤1 天，
             对「标定口径偏离」这个用途而言，多出的日间波动（实测 ~0.1%）远小于要
             衡量的口径差异量级，但必须在文档与 UI 里讲清「快照日期 ≠ 自算日期」。
        """
        if snap is None:
            return ""
        if snap_date is not None:
            cur.execute(
                "UPDATE global_usd_index_daily SET dxy_snapshot=%s, update_time=NOW() "
                "WHERE trade_date=%s AND dxy_snapshot IS NULL", (snap, snap_date))
            if cur.rowcount > 0:
                return "exact"
        if fallback_last is not None and (snap_date is None or fallback_last != snap_date):
            cur.execute(
                "UPDATE global_usd_index_daily SET dxy_snapshot=%s, update_time=NOW() "
                "WHERE trade_date=%s AND dxy_snapshot IS NULL", (snap, fallback_last))
            if cur.rowcount > 0:
                return "latest"
        return ""

    def run(self) -> dict:
        errors: list[str] = []
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
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT MAX(trade_date) AS d FROM global_usd_index_daily")
                last = cur.fetchone()["d"]
                if last is None:
                    start = date.today() - timedelta(days=self.first_lookback_days)
                else:
                    start = last + timedelta(days=1)

                snap, snap_date = _fetch_diniw()

                rows, skipped_dates, src_last = [], 0, None
                for _, r in df.iterrows():
                    d = _d(r.get("日期"))
                    if d is None:
                        continue
                    src_last = d if src_last is None or d > src_last else src_last
                    if d < start:
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
                    rows.append((d, dxy, snap if d == snap_date else None,
                                 cross["eur_usd"], cross["usd_jpy"], cross["gbp_usd"],
                                 cross["usd_cad"], cross["usd_sek"], cross["usd_chf"]))
                fallback_last = rows[-1][0] if rows else src_last

                if not rows:
                    # 增量窗口为空（本地已追平源）时，快照仍必须尝试落库 ——
                    # 否则「采集器跑在中间价发布之前」的那一天，快照会被静默丢掉。
                    where = self._stitch_snapshot(cur, snap, snap_date, fallback_last)
                    msg = (f"美元指数无新增（本地已至 {last}，源最新 {src_last}）"
                           f"｜快照 {snap}（{snap_date}，落位={where or '无'}"
                           f"{'，已回落到表内最新行 ' + str(fallback_last) if where == 'latest' else ''}）")
                    logger.info("✅ %s", msg)
                    return with_steps(
                        {"records_written": 0, "error_count": len(errors),
                         "errors": errors[:20], "note": msg},
                        RUN_STEPS,
                        {1: f"{len(df)} 行（跳过 {skipped_dates} 行窗口外）", 2: "无新增", 3: "无新增",
                         4: f"快照 {snap}（{snap_date}，落位={where or '无'}）",
                         5: f"本地已至 {last}"},
                    )

                sql = (
                    f"INSERT INTO global_usd_index_daily ({', '.join(_TGT)}, update_time, data_source) "
                    f"VALUES ({', '.join(['%s'] * len(_TGT))}, NOW(), 'AKSHARE+BOC_SAFE') "
                    "ON DUPLICATE KEY UPDATE "
                    + ", ".join(f"{c}=COALESCE(VALUES({c}), {c})"
                                for c in _TGT if c != "trade_date")
                    + ", update_time=NOW()"
                )
                cur.executemany(sql, rows)
                # 快照兜底：报价日通常比源表最新一行还新 1 天，靠 INSERT 挂不上，需单独写
                snap_where = self._stitch_snapshot(cur, snap, snap_date, fallback_last)
                cur.execute("SELECT COUNT(*) AS n, MIN(trade_date) AS d0, MAX(trade_date) AS d1, "
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
        msg = (f"美元指数写 {written} 行｜表内 {st['n']} 行（{st['d0']} ~ {st['d1']}）"
               f"｜快照基准累积至 {st['d_snap']}｜"
               f"⚠️ 自算为人民币中间价交叉汇率口径（实测与 ICE 官方快照偏差 <0.1%，但仍是两套口径，"
               f"以趋势与相对位置为准）")
        logger.info("✅ %s", msg)
        return with_steps(
            {"records_written": written, "error_count": len(errors), "errors": errors[:20],
             "note": msg},
            RUN_STEPS,
            {
                1: f"{len(df)} 行 · 25 币种",
                2: f"6 成分齐备的日数 {len(rows)}",
                3: f"自算 {len(rows)} 行（DXY 越界 {bad_range} · 交叉汇率越界 {bad_cross}）",
                4: (f"DINIW 快照 {snap}（报价日 {snap_date}，落位={snap_where or '无'}）"
                    if snap is not None else "DINIW 快照未取到"),
                5: f"upsert {written} 行（表内 {st['n']} 行，最新 {st['d1']}）",
            },
        )
