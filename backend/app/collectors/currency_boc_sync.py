#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
InvestBuddy 人民币外汇牌价采集器（currency_boc_daily）—— 蓝图 E「跨市场与外部情绪」

信号读法（报告 §6 蓝图 E）：**汇率破位 = 外资流出压力**。
  人民币中间价与即期价是同一件事的两个侧面：中间价是政策意图，即期（中行汇买/汇卖）是市场实现。
  两者背离本身就是信息（中间价稳住而即期走弱 = 贬值压力靠逆周期因子硬压）。
  故本表同时存中间价与中行牌价，而不是只存一个。

数据源：`ak.currency_boc_sina(symbol='美元', start_date, end_date)`
  列 = 日期 / 中行汇买价 / 中行钞买价 / 中行钞卖价(汇卖价) / 央行中间价 / 中行折算价

⚠️ 单位陷阱（务必注意）：源按**每 100 外币**报价（美元 675.80 = 6.7580 元/美元），
   本采集器统一 **÷100 归一为「元/1 外币」**后入库。表注释与列名均已声明该口径，
   下游可直接与「10Y 国债」等人民币口径的量放在一起，不需要再除。

📌 2026-09-25 扩展：4 币种 → **源侧合法全集 17 币种**（理由与口径纠错见 CURRENCIES 上方注释）。

幂等：uk_currency_date(currency, trade_date) + 增量（起点 = MAX(trade_date) 的次日；
首次回扫 first_lookback_days）。
"""
import logging
from datetime import date, datetime, timedelta

import pymysql
import akshare as ak

from ..db import get_db_config
from ._common import with_steps, call_with_timeout

logger = logging.getLogger(__name__)

RUN_STEPS = [
    {"no": 1, "name": "读取本地水位", "params": "逐币种 MAX(trade_date)；首次回扫 first_lookback_days（默认 5 年）"},
    {"no": 2, "name": "拉取中行牌价", "params": "currency_boc_sina(symbol=<币种中文名>, start, end)"},
    {"no": 3, "name": "单位归一", "params": "源为「每 100 外币」→ ÷100 转「元/1 外币」；剔除非交易日全空行"},
    {"no": 4, "name": "批量 Upsert", "params": "uk_currency_date(currency, trade_date) 幂等"},
]

# (源中文名, 币种代码, 展示名)
# ⚠️ 源名必须逐字对齐 akshare currency_boc_sina 的合法取值集合（money_code 映射表的 key），
#    拼错不会报「不支持」，而是在 _currency_boc_sina_map 里抛 KeyError——本机实测：
#    港元 ✗ → 港币 ✓（'澳门元'、'韩国元' 亦为源侧写法，非「澳门币」「韩元」）。
#    合法全集（17 个，本表即全集；2026-09-25 由 4 个扩到 17）：
#      美元/英镑/欧元/澳门元/泰国铢/菲律宾比索/港币/瑞士法郎/新加坡元/
#      瑞典克朗/丹麦克朗/挪威克朗/日元/加拿大元/澳大利亚元/新西兰元/韩国元
#
# 📌 2026-09-25 扩展说明（货币流动性批次 2C）：
#   原表只有美元/欧元/日元/港元 4 个。扩到全集的理由有二：
#     ① 覆盖 **ICE DXY 的 6 个成分货币**（欧元/日元/英镑/加元/瑞典克朗/瑞士法郎），
#        使「美元指数自算」多一条独立口径可交叉复核（自算主用 SAFE 宽表，本表可作对照）；
#     ② 「外部约束」维度要看的是一篮子货币的相对强弱，只盯美元会漏掉日元/英镑的独立行情。
#   ⚠️ 口径纠正：设计文档初稿写「扩到 26 币种」，那是把 `currency_boc_safe`（SAFE 宽表，25 币种）
#      的币种数误套到了本表上。`currency_boc_sina`（中行牌价）**只有 17 个**，已按实际全集落地。
CURRENCIES = [
    ("美元", "USD", "美元"),
    ("欧元", "EUR", "欧元"),
    ("日元", "JPY", "日元"),
    ("港币", "HKD", "港元"),
    ("英镑", "GBP", "英镑"),
    ("澳大利亚元", "AUD", "澳元"),
    ("新西兰元", "NZD", "新西兰元"),
    ("新加坡元", "SGD", "新加坡元"),
    ("瑞士法郎", "CHF", "瑞士法郎"),
    ("加拿大元", "CAD", "加元"),
    ("瑞典克朗", "SEK", "瑞典克朗"),
    ("丹麦克朗", "DKK", "丹麦克朗"),
    ("挪威克朗", "NOK", "挪威克朗"),
    ("澳门元", "MOP", "澳门元"),
    ("韩国元", "KRW", "韩元"),
    ("泰国铢", "THB", "泰铢"),
    ("菲律宾比索", "PHP", "菲律宾比索"),
]
_TGT = ["currency", "currency_name", "trade_date",
        "mid_price", "spot_buy", "spot_sell", "ref_price"]


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


def _norm(v):
    """源值（每 100 外币）→ 元/1 外币，保留 6 位"""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f or f <= 0:
        return None
    return round(f / 100.0, 6)


class CurrencyBocSyncCollector:
    """人民币外汇牌价（中行）日频增量同步"""

    def __init__(self, timeout_sec: float = 60, first_lookback_days: int = 1825,
                 from_date=None):
        self.timeout_sec = float(timeout_sec)
        self.first_lookback_days = int(first_lookback_days)
        self.from_date = from_date          # 'YYYY-MM-DD'，运维显式指定起点

    def run(self) -> dict:
        conn = pymysql.connect(**get_db_config().to_dict(),
                               cursorclass=pymysql.cursors.DictCursor)
        written, errors, per = 0, [], {}
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT currency, MAX(trade_date) AS d FROM currency_boc_daily "
                            "GROUP BY currency")
                wm = {r["currency"]: r["d"] for r in cur.fetchall()}

                insert_sql = (
                    f"INSERT INTO currency_boc_daily ({', '.join(_TGT)}, update_time, data_source) "
                    f"VALUES ({', '.join(['%s'] * len(_TGT))}, NOW(), 'AKSHARE') "
                    "ON DUPLICATE KEY UPDATE "
                    + ", ".join(f"{c}=COALESCE(VALUES({c}), {c})"
                                for c in _TGT if c not in ("currency", "trade_date"))
                    + ", update_time=NOW()"
                )

                today = date.today()
                for name_cn, code, disp in CURRENCIES:
                    last = wm.get(code)
                    if self.from_date:
                        start = _d(self.from_date) or (today - timedelta(days=self.first_lookback_days))
                    elif last is not None:
                        start = last + timedelta(days=1)
                    else:
                        start = today - timedelta(days=self.first_lookback_days)
                    if start > today:
                        start = today
                    try:
                        df = call_with_timeout(
                            ak.currency_boc_sina, self.timeout_sec,
                            symbol=name_cn,
                            start_date=start.strftime("%Y%m%d"),
                            end_date=today.strftime("%Y%m%d"),
                        )
                    except Exception as e:              # noqa: BLE001
                        errors.append(f"{disp}: {type(e).__name__} {str(e)[:60]}")
                        continue
                    if df is None or df.empty:
                        per[code] = 0
                        continue
                    rows = []
                    for _, r in df.iterrows():
                        d = _d(r.get("日期"))
                        if d is None:
                            continue
                        mid = _norm(r.get("央行中间价"))
                        buy = _norm(r.get("中行汇买价"))
                        sell = _norm(r.get("中行钞卖价/汇卖价"))
                        ref = _norm(r.get("中行折算价"))
                        if mid is None and buy is None and sell is None and ref is None:
                            continue        # 非交易日 / 尚未报价（实测周末行中间价为空）
                        rows.append((code, disp, d, mid, buy, sell, ref))
                    if rows:
                        cur.executemany(insert_sql, rows)
                        conn.commit()
                    written += len(rows)
                    per[code] = len(rows)
                    logger.info("  %s：源 %s 行 → 写 %s 行（%s ~ %s）",
                                disp, len(df), len(rows), start, today)

            with conn.cursor() as cur:
                cur.execute("SELECT currency, COUNT(*) AS n, MAX(trade_date) AS d "
                            "FROM currency_boc_daily GROUP BY currency ORDER BY currency")
                stat = cur.fetchall()
        finally:
            conn.close()

        msg = f"外汇牌价新增 {written} 行（{len(per)}/{len(CURRENCIES)} 个币种）"
        if errors:
            msg += f"；异常 {len(errors)} 项"
        logger.info("✅ %s", msg)
        return with_steps(
            {"records_written": written, "error_count": len(errors), "errors": errors[:20], "note": msg},
            RUN_STEPS,
            {
                1: " · ".join(f"{c}至{wm.get(c) or '无'}" for _, c, _ in CURRENCIES),
                2: f"拉取 {len(per)}/{len(CURRENCIES)} 个币种",
                3: " · ".join(f"{c} +{per[c]}" for c in per) or "无增量",
                4: "；".join(f"{r['currency']} {r['n']}行至{r['d']}" for r in stat),
            },
        )
