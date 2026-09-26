#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""美国货币市场日度采集器（us_money_market_daily）—— 纽约联储 + 美财政部（双源、零 Key）

蓝图的哪一块：**货币流动性 · 美国价格维度（政策利率的成交价）+ 数量维度（TGA 抽水）**
（补源方案 v2.0 §7 批次 A）。

本表支撑两个关键口径：
  ① **政策利率的实际成交价**：EFFR 相对目标区间的位置 = 银行体系准备金是否充裕
     （贴上限 = 紧、贴下限 = 松），比看「目标利率」本身信息量大得多；
  ② **「美元净流动性」合成指标 A5** = 美联储总资产 − TGA − ON RRP
     —— 市场最常用的「美元真实流动性」口径，三个源本表全部覆盖。
     ⚠️ **合成口径、非官方**，UI 必须显式标注（分析层 `money_cost` 负责打标）。

⚠️ 四条实测纪律：

  1. **单位不统一，且无法统一**（源就是这么给的）：利率 = %、成交量/ON RRP/TGA = **百万美元**、
     国债总额 = **美元**。一律以列注释为准，**不要凭列名猜**（本项目已在汇率单位上栽过）。

  2. **三个源的交易日历不同**（回购市场 / 联邦基金 / 财政工作日）⇒ 表内日期是**并集**、
     每列只在各自有值的日期非空；增量水位必须**按列取**，否则一条腿的当天会被静默跳过。

  3. **ON RRP 只有 `last/N` 可用**：实测 `reverserepo/.../search.json` 返回 **400**
     ⇒ 无法按日期窗口拉取，只能取最近 N 次操作（本采集器取 250 次 ≈ 1 年，
        每日 upsert 滚动覆盖，对增量采集足够）。

  4. **财政部返回的数字是字符串、缺失值是字符串 `"null"`**：`close_today_bal` 实测为 `"null"`，
     直接 `float()` 会抛或被当成 0。必须显式判空串与 `"null"`。
     ⇒ TGA 取 `close_today_bal`，缺失时回落 `open_today_bal`（实测后者有值 957,409）。

幂等：PRIMARY KEY(trade_date) + 按列水位增量（重叠 5 天覆盖修订）。
"""
import logging
from datetime import date, datetime, timedelta

import pymysql

from ..db import get_db_config
from ._common import with_steps
from ._http import get_json

logger = logging.getLogger(__name__)

RUN_STEPS = [
    {"no": 1, "name": "纽约联储 SOFR（有担保隔夜融资利率）",
     "params": "markets.newyorkfed.org/api/rates/secured/sofr/search.json?startDate&endDate（按年分片；含 1/25/75/99 分位与成交量）"},
    {"no": 2, "name": "纽约联储 EFFR（有效联邦基金利率）",
     "params": "api/rates/unsecured/effr/search.json（含目标区间上下限与成交量）"},
    {"no": 3, "name": "纽约联储 ON RRP 操作",
     "params": "api/rp/reverserepo/all/results/last/250.json（⚠️ search 端点实测 400，只能取最近 N 次）"},
    {"no": 4, "name": "美财政部 TGA 与国债总额",
     "params": "fiscaldata operating_cash_balance（TGA 余额，2005 起）/ debt_to_penny（国债总额）"},
    {"no": 5, "name": "按日期并集合并 + 按列水位 Upsert",
     "params": "PRIMARY KEY(trade_date)；三源日历不同，水位按列取，重叠 5 天"},
]

_NYFED = "https://markets.newyorkfed.org/api"
_TREASURY = "https://api.fiscaldata.treasury.gov/services/api/fiscal_service"

_SOFR_FIRST = date(2018, 4, 2)     # SOFR 首个发布日
_EFFR_FIRST = date(1954, 7, 1)     # EFFR 历史起点（实际按水位推进，不会真从 1954 拉）
_TGA_FIRST = date(2005, 10, 3)     # 实测 operating_cash_balance 最早一条
_ONRRP_LAST_N = 250
_OVERLAP_DAYS = 5

_TGT = ["trade_date", "sofr", "sofr_p1", "sofr_p25", "sofr_p75", "sofr_p99", "sofr_volume_bn",
        "effr", "effr_target_low", "effr_target_high", "effr_volume_bn",
        "on_rrp_amt", "tga", "debt_total"]


def _f(v, nd=4):
    """宽松浮点：None / '' / 'null' / 非数 → None"""
    if v is None:
        return None
    s = str(v).strip()
    if s == "" or s.lower() in ("null", "nan", "none"):
        return None
    try:
        x = float(s)
    except (TypeError, ValueError):
        return None
    if x != x:
        return None
    return round(x, nd)


def _d(v):
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return datetime.strptime(str(v).strip()[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def _fetch_nyfed_rate(kind: str, start: date, end: date, timeout: float = 45) -> dict:
    """纽约联储参考利率（SOFR/EFFR）→ {date: {...}}。按年分片避免单次窗口过大。"""
    out: dict = {}
    cur = start
    while cur <= end:
        chunk_end = min(date(cur.year, 12, 31), end)
        url = (f"{_NYFED}/rates/{kind}/search.json"
               f"?startDate={cur:%Y-%m-%d}&endDate={chunk_end:%Y-%m-%d}")
        try:
            d = get_json(url, timeout=timeout)
        except Exception as e:                  # noqa: BLE001
            logger.warning("NY Fed %s 分片 %s~%s 失败：%s", kind, cur, chunk_end, str(e)[:70])
            cur = chunk_end + timedelta(days=1)
            continue
        for r in (d.get("refRates") or []):
            dd = _d(r.get("effectiveDate"))
            if dd is None:
                continue
            out[dd] = r
        cur = chunk_end + timedelta(days=1)
    return out


def _fetch_onrrp(timeout: float = 45) -> dict:
    """纽约联储 ON RRP 最近 N 次操作 → {date: 接纳额(百万美元)}。

    ⚠️ 实测 `reverserepo/.../search.json` 返回 **400**（该端点不支持日期窗口），
       只有 `last/N` 可用 ⇒ 取最近 N 次滚动覆盖。
    ⚠️ 返回的操作里 `operationType` 可能是 `Reverse Repo`；只取该类（正回购 Repo 另算）。
    """
    url = f"{_NYFED}/rp/reverserepo/all/results/last/{_ONRRP_LAST_N}.json"
    try:
        d = get_json(url, timeout=timeout)
    except Exception as e:                      # noqa: BLE001
        logger.warning("NY Fed ON RRP 失败：%s", str(e)[:80])
        return {}
    ops = (d.get("repo") or {}).get("operations") or d.get("operations") or []
    out: dict = {}
    for o in ops:
        if str(o.get("operationType", "")).lower().startswith("reverse") is False:
            continue
        dd = _d(o.get("operationDate"))
        amt = _f(o.get("totalAmtAccepted"), 2)          # 源单位：美元
        if dd is None or amt is None:
            continue
        out[dd] = round(amt / 1e6, 2)                    # → 百万美元
    return out


def _fetch_treasury_tga(start: date, timeout: float = 60) -> dict:
    """财政部 TGA 余额 → {date: 百万美元}。

    先试服务端 filter（`account_type:eq:Treasury General Account (TGA) Closing Balance`），
    失败/返回空则回落「取全量再 Python 侧筛」—— 该字段含空格与括号，URL 编码易出问题。
    """
    base = (f"{_TREASURY}/v1/accounting/dts/operating_cash_balance"
            f"?page%5Bsize%5D=10000&sort=record_date"
            f"&filter=record_date:gte:{start:%Y-%m-%d}")

    def _pick(rows):
        out: dict = {}
        for r in rows:
            at = str(r.get("account_type") or "")
            if "TGA" not in at and "General Account" not in at:
                continue
            dd = _d(r.get("record_date"))
            if dd is None:
                continue
            v = _f(r.get("close_today_bal"), 2)
            if v is None:
                v = _f(r.get("open_today_bal"), 2)
            if v is not None:
                out[dd] = v
        return out

    try:
        d = get_json(base, timeout=timeout)
        rows = d.get("data") or []
        out = _pick(rows)
        if out:
            return out
    except Exception as e:                      # noqa: BLE001
        logger.warning("财政部 TGA（filter 形式）失败，回落全量：%s", str(e)[:70])

    out_all: dict = {}
    page = 1
    while page <= 12:                            # 全量兜底：分页拉
        url = (f"{_TREASURY}/v1/accounting/dts/operating_cash_balance"
               f"?page%5Bsize%5D=10000&page%5Bnumber%5D={page}&sort=record_date")
        try:
            d = get_json(url, timeout=timeout)
        except Exception as e:                  # noqa: BLE001
            logger.warning("财政部 TGA 全量第 %d 页失败：%s", page, str(e)[:60])
            break
        rows = d.get("data") or []
        if not rows:
            break
        for dd, v in _pick(rows).items():
            if dd < start:
                continue
            out_all[dd] = v
        if len(rows) < 10000:
            break
        page += 1
    return out_all


def _fetch_treasury_debt(start: date, timeout: float = 60) -> dict:
    """美国未偿国债总额 → {date: 美元}"""
    url = (f"{_TREASURY}/v2/accounting/od/debt_to_penny"
           f"?page%5Bsize%5D=10000&sort=record_date&filter=record_date:gte:{start:%Y-%m-%d}")
    try:
        d = get_json(url, timeout=timeout)
    except Exception as e:                      # noqa: BLE001
        logger.warning("财政部国债总额失败：%s", str(e)[:80])
        return {}
    out: dict = {}
    for r in (d.get("data") or []):
        dd = _d(r.get("record_date"))
        v = _f(r.get("tot_pub_debt_out_amt"), 2)
        if dd is not None and v is not None:
            out[dd] = v
    return out


class UsMoneyMarketSyncCollector:
    """美国货币市场日度同步（NY Fed SOFR/EFFR/ON RRP + 美财政部 TGA/国债总额）"""

    def __init__(self, timeout_sec: float = 60):
        self.timeout_sec = float(timeout_sec)

    def run(self) -> dict:
        errors: list[str] = []
        conn = pymysql.connect(**get_db_config().to_dict(), cursorclass=pymysql.cursors.DictCursor)
        try:
            with conn.cursor() as cur:
                # ⚠️ 水位必须**按列取**：三个源日历不同，按表取会被「只有 TGA 的那天」顶前、
                #    静默跳过另外两条腿的一整天（本项目已有同类前科）。
                w = {}
                for col in ("sofr", "effr", "on_rrp_amt", "tga", "debt_total", "sofr_volume_bn"):
                    cur.execute(f"SELECT MAX(trade_date) AS d FROM us_money_market_daily "
                                f"WHERE {col} IS NOT NULL")
                    w[col] = cur.fetchone()["d"]

                today = date.today()
                s_sofr = (w["sofr"] - timedelta(days=_OVERLAP_DAYS)) if w["sofr"] else _SOFR_FIRST
                s_effr = (w["effr"] - timedelta(days=_OVERLAP_DAYS)) if w["effr"] else date(2015, 1, 1)
                s_tga = (w["tga"] - timedelta(days=_OVERLAP_DAYS)) if w["tga"] else _TGA_FIRST
                s_debt = (w["debt_total"] - timedelta(days=_OVERLAP_DAYS)) if w["debt_total"] else _TGA_FIRST

                merged: dict = {}

                sofr = _fetch_nyfed_rate("secured/sofr", s_sofr, today, self.timeout_sec)
                for dd, r in sofr.items():
                    s = merged.setdefault(dd, {})
                    s["sofr"] = _f(r.get("percentRate"), 4)
                    s["sofr_p1"] = _f(r.get("percentPercentile1"), 4)
                    s["sofr_p25"] = _f(r.get("percentPercentile25"), 4)
                    s["sofr_p75"] = _f(r.get("percentPercentile75"), 4)
                    s["sofr_p99"] = _f(r.get("percentPercentile99"), 4)
                    s["sofr_volume_bn"] = _f(r.get("volumeInBillions"), 2)
                if not sofr:
                    errors.append("SOFR 未取到任何数据")

                effr = _fetch_nyfed_rate("unsecured/effr", s_effr, today, self.timeout_sec)
                for dd, r in effr.items():
                    s = merged.setdefault(dd, {})
                    s["effr"] = _f(r.get("percentRate"), 4)
                    s["effr_target_low"] = _f(r.get("targetRateFrom"), 4)
                    s["effr_target_high"] = _f(r.get("targetRateTo"), 4)
                    s["effr_volume_bn"] = _f(r.get("volumeInBillions"), 2)
                if not effr:
                    errors.append("EFFR 未取到任何数据")

                onrrp = _fetch_onrrp(self.timeout_sec)
                for dd, v in onrrp.items():
                    merged.setdefault(dd, {})["on_rrp_amt"] = v
                if not onrrp:
                    errors.append("ON RRP 未取到任何数据（该端点只支持 last/N，注意是否限流）")

                tga = _fetch_treasury_tga(s_tga, self.timeout_sec)
                for dd, v in tga.items():
                    merged.setdefault(dd, {})["tga"] = v
                if not tga:
                    errors.append("TGA 未取到任何数据")

                debt = _fetch_treasury_debt(s_debt, self.timeout_sec)
                for dd, v in debt.items():
                    merged.setdefault(dd, {})["debt_total"] = v
                if not debt:
                    errors.append("国债总额未取到任何数据")

                if not merged:
                    raise RuntimeError(f"四个源全部无数据：{errors[:4]}")

                rows = [(d, *(m.get(k) for k in _TGT[1:])) for d, m in sorted(merged.items())]
                sql = (
                    f"INSERT INTO us_money_market_daily ({', '.join(_TGT)}, update_time, data_source) "
                    f"VALUES ({', '.join(['%s'] * len(_TGT))}, NOW(), %s) "
                    "ON DUPLICATE KEY UPDATE "
                    + ", ".join(f"{c}=COALESCE(VALUES({c}), {c})" for c in _TGT if c != "trade_date")
                    + ", update_time=NOW()"
                )
                cur.executemany(sql, [(*r, "NYFED+TREASURY_FISCALDATA") for r in rows])

                cur.execute(
                    "SELECT COUNT(*) AS n, MIN(trade_date) AS d0, MAX(trade_date) AS d1, "
                    "SUM(sofr IS NOT NULL) AS n_sofr, SUM(effr IS NOT NULL) AS n_effr, "
                    "SUM(on_rrp_amt IS NOT NULL) AS n_rrp, SUM(tga IS NOT NULL) AS n_tga, "
                    "SUM(debt_total IS NOT NULL) AS n_debt, "
                    "MAX(CASE WHEN tga IS NOT NULL THEN trade_date END) AS d_tga, "
                    "MAX(CASE WHEN sofr IS NOT NULL THEN trade_date END) AS d_sofr "
                    "FROM us_money_market_daily")
                st = cur.fetchone()
                cur.execute("SELECT trade_date, sofr, effr, on_rrp_amt, tga, debt_total "
                            "FROM us_money_market_daily ORDER BY trade_date DESC LIMIT 1")
                tail = cur.fetchone()
            conn.commit()
        finally:
            conn.close()

        msg = (f"美国货币市场写 {len(rows)} 行｜表内 {st['n']} 行（{st['d0']} ~ {st['d1']}）"
               f"｜SOFR {st['n_sofr']}（至 {st['d_sofr']}）· EFFR {st['n_effr']} · "
               f"ON RRP {st['n_rrp']} · TGA {st['n_tga']}（至 {st['d_tga']}）· 国债 {st['n_debt']}"
               f"｜最新 {tail['trade_date'] if tail else '-'}：SOFR={tail['sofr'] if tail else '-'} "
               f"EFFR={tail['effr'] if tail else '-'} ON RRP={tail['on_rrp_amt'] if tail else '-'} "
               f"TGA={tail['tga'] if tail else '-'}")
        logger.info("✅ %s", msg)
        return with_steps(
            {"records_written": len(rows), "error_count": len(errors), "errors": errors[:20], "note": msg},
            RUN_STEPS,
            {
                1: f"{len(sofr)} 个交易日（起 {s_sofr}）",
                2: f"{len(effr)} 个交易日（起 {s_effr}）",
                3: f"{len(onrrp)} 次操作（last/{_ONRRP_LAST_N}）",
                4: f"TGA {len(tga)} 天 · 国债 {len(debt)} 天（起 {s_tga}）",
                5: f"并集合计 {len(rows)} 行（表内 {st['n']} 行，最新 {st['d1']}）",
            },
        )
