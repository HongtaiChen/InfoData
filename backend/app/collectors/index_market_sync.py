#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
InvestBuddy 指数日线采集器（dc_index_market 表，21 个主流指数增量，分四组）

分组（宽基=基准锚，风格=攻防/市值开关，热点=行业雷达）：
- 宽基 9：上证指数 / 上证50 / 沪深300 / 中证全指 / 中证500 / 中证1000 / 中证2000 / 深证成指 / 深证100
- 风格 6：科创50 / 科创100 / 创业板指 / 创业板50 / 中证红利 / 中证转债
- 热点 5：中证全指房地产 / 证券公司 / 中证银行 / 国证芯片 / 中证白酒
- 北证 1：北证50
分组/角色说明由 INDEX_META 随行写入 index_group / group_desc（2026-09-13 v2.3 新增列），供前端分组与语义展示。

数据源优先级（2026-09 起东财主源在本机网络不可达，已降为末位）：
1. 中证指数官网 API（csindex.com.cn）—— 沪市 / 中证 / 北证系列 16 个指数（含 399 系中证管理行业指数），全字段：
   open / high / low / close / change / changePct / tradingVol(股) / tradingValue(亿元)
   → volume = tradingVol / 100（手），amount = tradingValue × 1e8（元）
2. 国证指数（akshare index_hist_cni）—— 深证系列 4 个 + 国证芯片 980017，提供成交额（亿元）与涨跌幅（比例值）；
   OHLCV 由腾讯非复权日 K 提供（国证「成交量」口径不稳定，只取其成交额，避免单位污染）
3. 东财 ak.index_zh_a_hist —— 保留为降级主源（本机不可达时自动跳过）
4. 腾讯 ifzq 非复权日 K —— 最后降级，仅 OHLCV

调度契约：
- 增量窗口带 overlap_days 重叠回补（默认 7 天），配合「涨跌自愈」用前一交易日收盘补算 change，
  修掉「每批首行涨跌额/涨跌幅为 NULL」的历史缺陷。
- 每轮 DELETE 窗口内旧行后批量重建，幂等；data_source 实记命中源。
- 收盘基准取 trade_calendar 最近交易日；各指数最新日落后于基准则计入 errors 告警（不再静默跳过）。
"""
import logging
from datetime import date, datetime, timedelta

import pymysql
import requests
import akshare as ak

from ..db import get_db_config
from ._common import with_steps

logger = logging.getLogger(__name__)

# 运行步骤链模板（供前端「数据流·整链拓扑」展示运行逻辑）
RUN_STEPS = [
    {"no": 1, "name": "构建指数清单", "params": "INDEX_MAP 21 个主流 + DB 已存指数（含增补）"},
    {"no": 2, "name": "逐指数定增量窗口", "params": "本地 MAX(trade_date) 起回退 7 天重叠 → 今天；无记录回补 500 天"},
    {"no": 3, "name": "多源拉取", "params": "中证官网 / 国证 + 腾讯 → 东财 → 腾讯 逐级降级"},
    {"no": 4, "name": "涨跌自愈", "params": "用前一交易日收盘补算缺失的涨跌额/涨跌幅"},
    {"no": 5, "name": "窗口清理 + 写入 + 名称/分组归一", "params": "DELETE 窗口内旧行 → 批量 INSERT（含 index_group/group_desc）→ 同码历史行统一到规范值"},
]

# 指数代码 → (腾讯市场前缀, 规范简称)。腾讯前缀仅降级路径使用。
# 2026-09-13 扩容 13→21：+中证全指/中证2000/中证红利/中证转债（形势感知）
#                      +证券公司/中证银行/国证芯片/中证白酒（热点雷达）
INDEX_MAP: dict[str, tuple[str, str]] = {
    "000001": ("sh", "上证指数"),
    "000016": ("sh", "上证50"),
    "000300": ("sh", "沪深300"),
    "000985": ("sh", "中证全指"),
    "000905": ("sh", "中证500"),
    "000852": ("sh", "中证1000"),
    "932000": ("sh", "中证2000"),
    "399001": ("sz", "深证成指"),
    "399330": ("sz", "深证100"),
    "000688": ("sh", "科创50"),
    "000698": ("sh", "科创100"),
    "399006": ("sz", "创业板指"),
    "399673": ("sz", "创业板50"),
    "000922": ("sh", "中证红利"),
    "000832": ("sh", "中证转债"),
    "931775": ("csi", "中证全指房地产指数"),
    "399975": ("sz", "证券公司"),
    "399986": ("sz", "中证银行"),
    "980017": ("sz", "国证芯片"),
    "399997": ("sz", "中证白酒"),
    "899050": ("bj", "北证50"),
}

# 指数代码 → (分组, 角色说明)。随行写入 index_group / group_desc 两列，供前端分组展示与形势感知语义。
INDEX_META: dict[str, tuple[str, str]] = {
    # ---- 宽基 9 ----
    "000001": ("宽基", "沪市旗舰基准"),
    "000016": ("宽基", "超大盘权重"),
    "000300": ("宽基", "核心资产基准"),
    "000985": ("宽基", "全A基准锚点"),
    "000905": ("宽基", "中盘基准"),
    "000852": ("宽基", "小盘基准"),
    "932000": ("宽基", "微盘情绪"),
    "399001": ("宽基", "深市旗舰"),
    "399330": ("宽基", "深市核心"),
    # ---- 风格 6 ----
    "000688": ("风格", "硬科技大盘"),
    "000698": ("风格", "硬科技中盘"),
    "399006": ("风格", "成长风格基准"),
    "399673": ("风格", "成长高弹性"),
    "000922": ("风格", "防守/高股息开关"),
    "000832": ("风格", "股债联动温度计"),
    # ---- 热点 5 ----
    "931775": ("热点", "地产链晴雨"),
    "399975": ("热点", "牛市旗手"),
    "399986": ("热点", "权重防守/利率映射"),
    "980017": ("热点", "硬科技主线"),
    "399997": ("热点", "核心消费/外资偏好"),
    # ---- 北证 1 ----
    "899050": ("北证", "北交所旗舰"),
}

# 中证指数官网覆盖（沪市 / 中证 / 北证系列，含 399 系中证管理行业指数），含成交额
CSINDEX_CODES = {
    "000001", "000016", "000300", "000688", "000698", "000852", "000905",
    "000832", "000922", "000985", "931775", "932000",
    "399975", "399986", "399997",
    "899050",
}
# 深证系列（国证指数），成交额取自 akshare index_hist_cni
CNINDEX_CODES = {"399001", "399006", "399330", "399673", "980017"}

CSINDEX_URL = "https://www.csindex.com.cn/csindex-home/perf/index-perf"
CSINDEX_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.csindex.com.cn/",
}

EM_COLS = {"日期", "开盘", "收盘", "最高", "最低", "成交量", "成交额", "涨跌额", "涨跌幅", "换手率"}


def _today_str() -> str:
    return date.today().isoformat()


def _num(v):
    """安全转 float：失败或 NaN 返回 None"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    return f


def _guess_market(code: str) -> str:
    """按指数代码段推断腾讯市场前缀（仅降级路径需要）"""
    if code.startswith("399"):
        return "sz"
    if code.startswith(("8", "4")):
        return "bj"
    return "sh"


class IndexMarketSyncCollector:
    """主流指数日线增量同步"""

    def __init__(self, days_init: int = 500, overlap_days: int = 7):
        # days_init：某指数本地无记录时的初始回补天数
        self.days_init = days_init
        # overlap_days：增量窗口向前重叠天数，用于补算首行涨跌
        self.overlap_days = overlap_days

    # ---------- 数据源：中证指数官网 ----------
    def _fetch_csindex(self, code: str, start: str, end: str) -> list[dict] | None:
        """中证指数官网历史（含成交额；返回 None 表示源失败，[] 表示区间无数据）"""
        try:
            resp = requests.get(
                CSINDEX_URL,
                params={
                    "indexCode": code,
                    "startDate": start.replace("-", ""),
                    "endDate": end.replace("-", ""),
                },
                headers=CSINDEX_HEADERS,
                timeout=25,
            )
            j = resp.json()
        except Exception as e:
            logger.warning(f"指数 {code} 中证官网源失败: {str(e)[:90]}")
            return None
        if str(j.get("code")) != "200":
            logger.warning(f"指数 {code} 中证官网返回异常 code={j.get('code')} msg={j.get('msg')}")
            return None
        data = j.get("data") or []
        if not data:
            return []
        rows = []
        for r in data:
            td = str(r.get("tradeDate") or "")
            if len(td) != 8:
                continue
            vol = _num(r.get("tradingVol"))   # 单位：股
            amt = _num(r.get("tradingValue"))  # 单位：亿元
            rows.append({
                "trade_date": f"{td[:4]}-{td[4:6]}-{td[6:]}",
                "open": _num(r.get("open")),
                "high": _num(r.get("high")),
                "low": _num(r.get("low")),
                "close": _num(r.get("close")),
                "volume": int(vol / 100) if vol is not None else None,
                "amount": int(round(amt * 1e8)) if amt is not None else None,
                "change_amount": _num(r.get("change")),
                "change_pct": _num(r.get("changePct")),
                "turnover_ratio": None,
            })
        return rows

    # ---------- 数据源：国证指数（深证系列，成交额） ----------
    def _fetch_cnindex(self, code: str, start: str, end: str) -> list[dict] | None:
        """国证指数历史：只有成交额与涨跌幅可信，volume 交由腾讯补齐"""
        try:
            df = ak.index_hist_cni(
                symbol=code,
                start_date=start.replace("-", ""),
                end_date=end.replace("-", ""),
            )
        except Exception as e:
            logger.warning(f"指数 {code} 国证源失败: {str(e)[:90]}")
            return None
        if df is None or df.empty:
            return []
        rows = []
        for _, r in df.iterrows():
            amt = _num(r.get("成交额"))   # 亿元
            pct = _num(r.get("涨跌幅"))   # 比例值（-0.0108 = -1.08%）
            rows.append({
                "trade_date": str(r["日期"])[:10],
                "open": _num(r.get("开盘价")),
                "high": _num(r.get("最高价")),
                "low": _num(r.get("最低价")),
                "close": _num(r.get("收盘价")),
                "volume": None,
                "amount": int(round(amt * 1e8)) if amt is not None else None,
                "change_amount": None,
                "change_pct": round(pct * 100, 4) if pct is not None else None,
                "turnover_ratio": None,
            })
        return rows

    # ---------- 数据源：东财（降级主源） ----------
    def _fetch_em(self, code: str, start: str, end: str) -> list[dict] | None:
        """东财指数历史（全字段）"""
        try:
            df = ak.index_zh_a_hist(
                symbol=code, period="daily",
                start_date=start.replace("-", ""), end_date=end.replace("-", ""),
            )
        except Exception as e:
            logger.warning(f"指数 {code} 东财源失败: {str(e)[:90]}")
            return None
        if df is None or df.empty:
            return []
        if not EM_COLS.issubset(df.columns):
            logger.warning(f"指数 {code} 东财返回列异常: {list(df.columns)}")
            return None
        rows = []
        for _, r in df.iterrows():
            rows.append({
                "trade_date": str(r["日期"])[:10],
                "open": r["开盘"], "high": r["最高"], "low": r["最低"], "close": r["收盘"],
                "volume": r["成交量"], "amount": r["成交额"],
                "change_amount": r["涨跌额"], "change_pct": r["涨跌幅"],
                "turnover_ratio": r["换手率"],
            })
        return rows

    # ---------- 数据源：腾讯（最后降级，仅 OHLCV） ----------
    def _fetch_tencent(self, code: str, market: str, start: str, end: str) -> list[dict] | None:
        """腾讯非复权日 K（仅 OHLCV；change 由帧内收盘链自算，amount/turnover 为 NULL）"""
        if not market:
            return None
        try:
            url = (
                f"https://web.ifzq.gtimg.cn/appstock/app/kline/kline"
                f"?param={market}{code},day,{start},{end},800"
            )
            resp = requests.get(url, timeout=15)
            data = resp.json()
            day = ((data.get("data") or {}).get(f"{market}{code}") or {}).get("day") or []
        except Exception as e:
            logger.warning(f"指数 {code} 腾讯源失败: {str(e)[:90]}")
            return None
        if not day:
            return []
        rows = []
        prev_close = None
        for it in day:
            try:
                d, o, c, h, l, v = it[0], float(it[1]), float(it[2]), float(it[3]), float(it[4]), float(it[5])
            except (ValueError, IndexError, TypeError):
                continue
            chg_amt = (c - prev_close) if prev_close is not None else None
            chg_pct = (100.0 * (c - prev_close) / prev_close) if prev_close not in (None, 0) else None
            rows.append({
                "trade_date": d, "open": o, "high": h, "low": l, "close": c,
                "volume": int(v) if v else None, "amount": None,
                "change_amount": round(chg_amt, 3) if chg_amt is not None else None,
                "change_pct": round(chg_pct, 4) if chg_pct is not None else None,
                "turnover_ratio": None,
            })
            prev_close = c
        return rows

    # ---------- 深证系列：腾讯 OHLCV + 国证成交额 合并 ----------
    def _fetch_sz(self, code: str, market: str, start: str, end: str) -> tuple[list[dict] | None, str]:
        bars = self._fetch_tencent(code, market, start, end)
        amts = self._fetch_cnindex(code, start, end)
        if bars is None and amts is None:
            return None, ""
        bars = bars or []
        if amts:
            by_date = {r["trade_date"]: r for r in amts}
            for b in bars:
                a = by_date.get(b["trade_date"])
                if not a:
                    continue
                if a.get("amount") is not None:
                    b["amount"] = a["amount"]
                if a.get("change_pct") is not None:
                    b["change_pct"] = a["change_pct"]
            have = {b["trade_date"] for b in bars}
            for a in amts:
                if a["trade_date"] not in have:
                    bars.append(a)
            bars.sort(key=lambda x: x["trade_date"])
        source = "CNINDEX+TENCENT" if amts else "TENCENT"
        return bars, source

    # ---------- 统一取数入口 ----------
    def _fetch(self, code: str, market: str, start: str, end: str) -> tuple[list[dict] | None, str]:
        """按指数归属路由数据源，返回 (rows, source)；rows 为 None 表示全部源失败"""
        if code in CSINDEX_CODES:
            rows = self._fetch_csindex(code, start, end)
            if rows is not None:
                return rows, "CSINDEX"
            rows = self._fetch_em(code, start, end)
            if rows is not None:
                return rows, "AKSHARE"
            return self._fetch_tencent(code, market, start, end), "TENCENT"
        if code in CNINDEX_CODES:
            return self._fetch_sz(code, market or "sz", start, end)
        # 增补 / 未知指数：老路径
        rows = self._fetch_em(code, start, end)
        if rows is not None:
            return rows, "AKSHARE"
        return self._fetch_tencent(code, market, start, end), "TENCENT"

    # ---------- 涨跌自愈 ----------
    @staticmethod
    def _heal_change(cur, code: str, rows: list[dict]) -> int:
        """补算缺失的涨跌额/涨跌幅：帧内收盘链优先，首行回查 DB 前一交易日收盘"""
        fixed = 0
        prev_close = None
        cur.execute(
            "SELECT close FROM dc_index_market WHERE index_code=%s AND trade_date<%s "
            "ORDER BY trade_date DESC LIMIT 1",
            (code, rows[0]["trade_date"]),
        )
        r0 = cur.fetchone()
        if r0 and r0[0] is not None:
            prev_close = float(r0[0])
        for r in rows:
            close = r.get("close")
            if prev_close not in (None, 0) and close is not None and (
                r.get("change_amount") is None or r.get("change_pct") is None
            ):
                if r.get("change_amount") is None:
                    r["change_amount"] = round(float(close) - prev_close, 3)
                    fixed += 1
                if r.get("change_pct") is None:
                    r["change_pct"] = round(100.0 * (float(close) - prev_close) / prev_close, 4)
            if close is not None:
                prev_close = float(close)
        return fixed

    # ---------- 收盘基准 ----------
    @staticmethod
    def _last_trading_day(cur) -> str | None:
        """取 trade_calendar 中不晚于今天的最近交易日（失败则返回 None）"""
        try:
            cur.execute(
                "SELECT MAX(trade_date) FROM trade_calendar "
                "WHERE is_trading_day=1 AND trade_date<=CURDATE()"
            )
            v = cur.fetchone()[0]
            return v.isoformat() if v is not None else None
        except Exception:
            return None

    # ---------- 全量回补（按区间强制重建） ----------
    def backfill(self, start: str, end: str | None = None, codes: list[str] | None = None) -> dict:
        """忽略增量窗口，对 [start, end] 区间强制重建（用于历史成交额 / 涨跌缺口 / 全史回补）。
        codes：指定只回补这些指数（默认 None = 全部）"""
        end = end or _today_str()
        conn = pymysql.connect(**get_db_config().to_dict())
        written = healed = 0
        notes: list[str] = []
        errors: list[str] = []
        try:
            with conn.cursor() as cur:
                index_map = dict(INDEX_MAP)
                cur.execute("SELECT DISTINCT index_code, index_name FROM dc_index_market")
                for code, name in cur.fetchall():
                    index_map.setdefault(code, (_guess_market(code), name))
                if codes:
                    index_map = {c: v for c, v in index_map.items() if c in set(codes)}

                for code, (market, name) in index_map.items():
                    grp, desc = INDEX_META.get(code, (None, None))
                    rows, source = self._fetch(code, market, start, end)
                    if rows is None:
                        errors.append(f"{code} {name} 全部数据源失败")
                        continue
                    if not rows:
                        continue
                    rows.sort(key=lambda x: x["trade_date"])
                    healed += self._heal_change(cur, code, rows)
                    cur.execute(
                        "DELETE FROM dc_index_market WHERE index_code=%s AND trade_date>=%s "
                        "AND data_source IN ('AKSHARE','TENCENT','CSINDEX','CNINDEX+TENCENT','EM','SINA')",
                        (code, start),
                    )
                    payload = [
                        (
                            code, name, r["trade_date"], r["open"], r["high"], r["low"], r["close"],
                            r["volume"], r["amount"], r["change_amount"], r["change_pct"],
                            r["turnover_ratio"], grp, desc, source,
                        )
                        for r in rows
                    ]
                    cur.executemany(
                        "INSERT INTO dc_index_market "
                        "(index_code, index_name, trade_date, open, high, low, close, volume, amount, "
                        " change_amount, change_pct, turnover_ratio, index_group, group_desc, update_time, data_source) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), %s)",
                        payload,
                    )
                    cur.execute(
                        "UPDATE dc_index_market SET index_name=%s, index_group=%s, group_desc=%s "
                        "WHERE index_code=%s AND (index_name<>%s OR index_group<>%s OR group_desc<>%s "
                        " OR index_name IS NULL OR index_group IS NULL OR group_desc IS NULL)",
                        (name, grp, desc, code, name, grp, desc),
                    )
                    written += len(payload)
                    notes.append(f"{name} {len(payload)} 行({source})")
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        msg = f"指数回补 {start}~{end}：写入 {written} 行 / {len(notes)} 个指数（涨跌自愈 {healed} 处）"
        logger.info(f"✅ {msg}")
        return {
            "records_written": written,
            "error_count": len(errors),
            "errors": errors,
            "note": msg,
            "detail": notes,
        }

    # ---------- 主流程 ----------
    def run(self) -> dict:
        conn = pymysql.connect(**get_db_config().to_dict())
        written = skipped = healed = 0
        notes: list[str] = []
        errors: list[str] = []
        end = _today_str()
        try:
            with conn.cursor() as cur:
                bench = self._last_trading_day(cur) or end

                # DB 中已有的指数集合与名称（可含内置外的增补）
                cur.execute("SELECT DISTINCT index_code, index_name FROM dc_index_market")
                db_map = {r[0]: r[1] for r in cur.fetchall()}
                index_map = dict(INDEX_MAP)
                for code, name in db_map.items():
                    index_map.setdefault(code, (_guess_market(code), name))

                for code, (market, name) in index_map.items():
                    cur.execute(
                        "SELECT COALESCE(MAX(trade_date), NULL) FROM dc_index_market WHERE index_code=%s",
                        (code,),
                    )
                    max_d = cur.fetchone()[0]
                    if max_d is not None:
                        if max_d.isoformat() >= bench:  # 已含最近交易日（周末 / 节假日自然跳过）
                            skipped += 1
                            continue
                        start = (max_d - timedelta(days=self.overlap_days)).isoformat()
                    else:
                        start = (date.today() - timedelta(days=self.days_init)).isoformat()

                    rows, source = self._fetch(code, market, start, end)
                    if rows is None:
                        errors.append(f"{code} {name} 全部数据源失败，本轮跳过（保留旧数据）")
                        logger.warning(f"指数 {code} {name} 全部数据源失败，本轮跳过")
                        continue
                    if not rows:
                        skipped += 1
                        continue

                    rows.sort(key=lambda x: x["trade_date"])
                    healed += self._heal_change(cur, code, rows)

                    # 清理窗口内旧行（含历史降级源残留防重复）+ 写入
                    cur.execute(
                        "DELETE FROM dc_index_market WHERE index_code=%s AND trade_date>=%s "
                        "AND data_source IN ('AKSHARE','TENCENT','CSINDEX','CNINDEX+TENCENT','EM','SINA')",
                        (code, start),
                    )
                    payload = [
                        (
                            code, name, r["trade_date"], r["open"], r["high"], r["low"], r["close"],
                            r["volume"], r["amount"], r["change_amount"], r["change_pct"],
                            r["turnover_ratio"], grp, desc, source,
                        )
                        for r in rows
                    ]
                    cur.executemany(
                        "INSERT INTO dc_index_market "
                        "(index_code, index_name, trade_date, open, high, low, close, volume, amount, "
                        " change_amount, change_pct, turnover_ratio, index_group, group_desc, update_time, data_source) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), %s)",
                        payload,
                    )
                    written += len(payload)
                    notes.append(f"{name} +{len(payload)}({source})")

                    # 名称/分组归一：同码历史行统一到规范值（修历史行劈叉 + 新增分组列回填）
                    cur.execute(
                        "UPDATE dc_index_market SET index_name=%s, index_group=%s, group_desc=%s "
                        "WHERE index_code=%s AND (index_name<>%s OR index_group<>%s OR group_desc<>%s "
                        " OR index_name IS NULL OR index_group IS NULL OR group_desc IS NULL)",
                        (name, grp, desc, code, name, grp, desc),
                    )

                # 收盘滞后巡检（覆盖被 skip 的指数）：最新日落后于基准即告警
                cur.execute("SELECT index_code, MAX(trade_date) FROM dc_index_market GROUP BY index_code")
                for c, d in cur.fetchall():
                    nm = index_map.get(c, ("", c))[1]
                    if d is None or d.isoformat() < bench:
                        errors.append(
                            f"{c} {nm} 数据滞后：最新 {d.isoformat() if d else '无'} < 最近交易日 {bench}"
                        )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

        msg = f"指数日线新增 {written} 行 / {len(notes)} 个指数更新（涨跌自愈 {healed} 处）"
        if errors:
            msg += f"；异常 {len(errors)} 项"
        logger.info(f"✅ {msg}")
        return with_steps(
            {
                "records_written": written,
                "error_count": len(errors),
                "errors": errors,
                "note": msg,
            },
            RUN_STEPS,
            {
                1: f"清单 {len(index_map)} 个",
                2: f"已最新/空跳过 {skipped} 个",
                3: f"{len(notes)} 个指数拉取成功（中证官网 / 国证 → 东财 → 腾讯）",
                4: f"补算涨跌 {healed} 处",
                5: f"写入 {written} 行，名称归一完成",
            },
        )
