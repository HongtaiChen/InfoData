#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
InvestBuddy 指数估值采集器（index_valuation_daily）—— 蓝图 B「股债性价比」的估值分母

背景（docs/市场风向分析数据蓝图探索报告_2026-09-15.md §3.1 / §8 P2）：
  库内**没有任何指数估值数据**（`stock_market_current` 的 dynamic_pe/pb 等 8 列全表为空，
  见报告 §7-①），导致「④交叉印证」里的股债一项只能做方向性判读、**做不出 ERP**。
  本采集器补上这个分母，ERP（股权风险溢价）随之成立：
      ERP = 1 / 滚动市盈率 − 10Y 国债收益率
      股息率利差 = 股息率 − 10Y 国债收益率
  报告实证 4 的原话：「钱便宜到极致 + 股市不涨 = 流动性未传导」——有 ERP 之后，
  这句话才能从「描述」升级为「分位判断」（ERP 高分位通常对应中长期底部区域）。

三源并用，刻意不合并成一条序列（不同方法论的数字混在一起会互相污染）：

  source     接口                                    覆盖                       用途
  ---------  --------------------------------------  -------------------------  --------------------------
  csindex    stock_zh_index_value_csindex            近 20 个交易日（滚动）      当日最新 PE/股息率
  legu       stock_index_pe_lg                       2005 至今（月末 + 最新）    **长历史分位**（ERP 分位用它算）
  legu_pb    stock_index_pb_lg                       2005 至今（月末 + 最新）    PB 长历史（2026-10-18 新增）
  all_a      stock_a_ttm_lyr                          全 A 等权/中位 + 现成分位   横截面参照（不依赖成分股权重）

⚠️ 三源口径不同，**数值不可直接比大小**（实测沪深300 滚动 PE：中证官网 16.92、
   乐咕 12.68 —— 前者按整体法含亏损股、后者剔除负值），只能同源纵向比。这是刻意的：
   把口径分歧留下来，正是「交叉印证」要的东西（registry.py 第一原则）。

2026-10-18 扩容（投资环境模块 · 估值覆盖补全）：
  - LEGU 4 → 12 指数（乐咕 PE/PB 的 symbol_map 本就支持 12 个，含深市创业板50/深证100/深证红利）
  - 新增 PB 采集（stock_index_pb_lg），落 3 列 pb / pb_ew / pb_median
  - CSINDEX 补 000001 上证指数 / 000985 中证全指（当日 PE/股息率）
  - 乐咕接口有 429/504 限流，加逐指数重试+延时（12 指数串行打）
  - 注：创业板指(399006)、科创50(000688) 的长历史 PE/PB 无免费源（归国证指数/理杏仁付费），本期不覆盖
"""
import logging
import time
from datetime import date, datetime, timedelta

import pymysql
import akshare as ak

from ..db import get_db_config
from ._common import with_steps, call_with_timeout

logger = logging.getLogger(__name__)

RUN_STEPS = [
    {"no": 1, "name": "中证官网估值", "params": "stock_zh_index_value_csindex × 8 指数（滚动 20 交易日窗口）"},
    {"no": 2, "name": "乐咕长历史 PE", "params": "stock_index_pe_lg × 12 指数（2005 至今月末序列，全量入库——ERP 分位的样本就是这个）"},
    {"no": 3, "name": "乐咕长历史 PB", "params": "stock_index_pb_lg × 12 指数（2005 至今月末序列，落 pb/pb_ew/pb_median）"},
    {"no": 4, "name": "全 A 等权口径", "params": "stock_a_ttm_lyr（含近十年分位字段，独立于成分股权重）"},
    {"no": 5, "name": "批量 Upsert", "params": "uk_index_date_source(index_code, trade_date, source) 幂等；各源各自留痕"},
]

# 中证官网（code → 名称）。code 用 6 位完整码，与 index_profile / index_constituents 对齐
# 2026-10-18 扩容：补 000001 上证指数 / 000985 中证全指（当日 PE/股息率，无 PB、无长历史）
CSINDEX = {
    "000300": "沪深300",
    "000016": "上证50",
    "000905": "中证500",
    "000852": "中证1000",
    "000688": "科创50",
    "931775": "中证全指房地产指数",
    "000001": "上证指数",
    "000985": "中证全指",
}
# 乐咕（symbol 为中文名 → 目标 6 位码）。
# 2026-10-18 扩容 4→12：读 akshare.stock_a_pe_and_pb 源码，stock_index_pe_lg / stock_index_pb_lg
# 的 symbol_map 完全一致，均支持这 12 个指数（含深市创业板50/深证100/深证红利）。
LEGU = {
    "上证50": "000016",
    "沪深300": "000300",
    "上证380": "000009",
    "创业板50": "399673",
    "中证500": "000905",
    "上证180": "000010",
    "深证红利": "399324",
    "深证100": "399330",
    "中证1000": "000852",
    "上证红利": "000015",
    "中证100": "000903",
    "中证800": "000906",
}
# 全 A 等权/中位口径（非指数，单独一行）
ALL_A_CODE = "ALL_A"
ALL_A_NAME = "全 A（等权/中位口径）"

# 乐咕接口 429/504 限流重试参数（2026-10-18 实测乐咕服务器有限流，12 指数不能并发打）
_LEGU_RETRIES = 3
_LEGU_RETRY_DELAY = 3.0

_TGT = ["index_code", "index_name", "trade_date", "source", "pe_lyr", "pe_ttm",
        "pe_ttm_median", "pe_lyr_median", "pb", "pb_ew", "pb_median",
        "dividend_yield", "dividend_yield2", "pe_ttm_pct10y", "close_point"]


def _f(v, nd: int = 4):
    """转 float（NaN/空 → None），并按目标列精度截断"""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f:                                # NaN
        return None
    return round(f, nd)


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


class IndexValuationSyncCollector:
    """指数估值同步（中证官网 + 乐咕 + 全A 三源）"""

    def __init__(self, timeout_sec: float = 60, lookback_days: int = 0):
        self.timeout_sec = float(timeout_sec)
        # ⚠️ 默认 0 = 不截断，**必须**保留乐咕/全A 的完整历史：
        #    乐咕是「月末 + 最新」序列（沪深300 共 258 点，横跨 2005 至今），
        #    ERP 分位就是在这条序列上算的；若按「近 N 天」截断，样本会只剩十几个点，
        #    分位彻底失去意义（实测 400 天只留 13 点）。整表仅约 1,300 行，全量 upsert 无成本。
        #    保留该参数只为极端情况（如只想快跑一轮当日值）下的应急手段。
        self.lookback_days = int(lookback_days or 0)

    def _in_window(self, d: date) -> bool:
        return self.lookback_days <= 0 or d >= date.today() - timedelta(days=self.lookback_days)

    # ---------- 三个源 ----------

    def _from_csindex(self, out: list[dict], errors: list[str]) -> int:
        n = 0
        for code, name in CSINDEX.items():
            try:
                df = call_with_timeout(ak.stock_zh_index_value_csindex,
                                       self.timeout_sec, symbol=code)
            except Exception as e:                # noqa: BLE001
                errors.append(f"csindex {code} {name}: {type(e).__name__} {str(e)[:60]}")
                continue
            if df is None or df.empty:
                errors.append(f"csindex {code} {name}: 返回为空")
                continue
            for _, r in df.iterrows():
                d = _d(r.get("日期"))
                if d is None:
                    continue
                out.append({
                    "index_code": code, "index_name": name, "trade_date": d,
                    "source": "csindex",
                    "pe_lyr": _f(r.get("市盈率1")), "pe_ttm": _f(r.get("市盈率2")),
                    "pe_ttm_median": None, "pe_lyr_median": None,
                    "pb": None, "pb_ew": None, "pb_median": None,
                    "dividend_yield": _f(r.get("股息率1")), "dividend_yield2": _f(r.get("股息率2")),
                    "pe_ttm_pct10y": None, "close_point": None,
                })
                n += 1
            logger.info("  中证 %s %s：%s 行", code, name, len(df))
        return n

    def _call_legu(self, fn, symbol: str):
        """带限流重试调用乐咕接口（实测 429/504，逐指数重试+延时）"""
        last = None
        for attempt in range(_LEGU_RETRIES):
            try:
                return call_with_timeout(fn, self.timeout_sec, symbol=symbol)
            except Exception as e:                # noqa: BLE001
                last = e
                if "429" in str(e) or "504" in str(e) or "500" in str(e):
                    time.sleep(_LEGU_RETRY_DELAY)
                    continue
                raise
        raise last

    def _from_legu(self, out: list[dict], errors: list[str]) -> int:
        n = 0
        for symbol, code in LEGU.items():
            try:
                df = self._call_legu(ak.stock_index_pe_lg, symbol)
            except Exception as e:                # noqa: BLE001
                errors.append(f"legu PE {symbol}: {type(e).__name__} {str(e)[:60]}")
                continue
            if df is None or df.empty:
                errors.append(f"legu PE {symbol}: 返回为空")
                continue
            # 与中证官网源同口径：保留全历史（乐咕仅 ~258 点，全量 upsert 最省心也最稳）
            for _, r in df.iterrows():
                d = _d(r.get("日期"))
                if d is None or not self._in_window(d):
                    continue
                out.append({
                    "index_code": code, "index_name": symbol, "trade_date": d,
                    "source": "legu",
                    "pe_lyr": _f(r.get("静态市盈率")), "pe_ttm": _f(r.get("滚动市盈率")),
                    "pe_ttm_median": _f(r.get("滚动市盈率中位数")),
                    "pe_lyr_median": _f(r.get("静态市盈率中位数")),
                    "pb": None, "pb_ew": None, "pb_median": None,
                    "dividend_yield": None, "dividend_yield2": None,
                    "pe_ttm_pct10y": None,
                    # ⚠️ 乐咕该列名为「指数」，存的其实是**点位数值**（如 4507.39），不是名称
                    "close_point": _f(r.get("指数"), 2),
                })
                n += 1
            logger.info("  乐咕 PE %s：%s 行（窗口内 %s）", symbol, len(df), n)
            time.sleep(0.5)                        # 12 指数串行，轻间隔防限流
        return n

    def _from_legu_pb(self, out: list[dict], errors: list[str]) -> int:
        """乐咕 PB 采集（source=legu_pb，与 PE 同 12 指数）"""
        n = 0
        for symbol, code in LEGU.items():
            try:
                df = self._call_legu(ak.stock_index_pb_lg, symbol)
            except Exception as e:                # noqa: BLE001
                errors.append(f"legu PB {symbol}: {type(e).__name__} {str(e)[:60]}")
                continue
            if df is None or df.empty:
                errors.append(f"legu PB {symbol}: 返回为空")
                continue
            for _, r in df.iterrows():
                d = _d(r.get("日期"))
                if d is None or not self._in_window(d):
                    continue
                out.append({
                    "index_code": code, "index_name": symbol, "trade_date": d,
                    "source": "legu_pb",
                    "pe_lyr": None, "pe_ttm": None,
                    "pe_ttm_median": None, "pe_lyr_median": None,
                    # 乐咕 PB 列名映射：市净率(addPb 整体法) / 等权市净率(pb) / 市净率中位数(middlePb)
                    "pb": _f(r.get("市净率")), "pb_ew": _f(r.get("等权市净率")),
                    "pb_median": _f(r.get("市净率中位数")),
                    "dividend_yield": None, "dividend_yield2": None,
                    "pe_ttm_pct10y": None,
                    "close_point": _f(r.get("指数"), 2),
                })
                n += 1
            logger.info("  乐咕 PB %s：%s 行（窗口内 %s）", symbol, len(df), n)
            time.sleep(0.5)
        return n

    def _from_all_a(self, out: list[dict], errors: list[str]) -> int:
        try:
            df = call_with_timeout(ak.stock_a_ttm_lyr, self.timeout_sec)
        except Exception as e:                    # noqa: BLE001
            errors.append(f"all_a: {type(e).__name__} {str(e)[:60]}")
            return 0
        if df is None or df.empty:
            errors.append("all_a: 返回为空")
            return 0
        cutoff = date.today() - timedelta(days=self.lookback_days) if self.lookback_days > 0 else None
        n = 0
        for _, r in df.iterrows():
            d = _d(r.get("date"))
            if d is None or (cutoff is not None and d < cutoff):
                continue
            out.append({
                "index_code": ALL_A_CODE, "index_name": ALL_A_NAME, "trade_date": d,
                "source": "all_a",
                "pe_lyr": _f(r.get("averagePELYR")), "pe_ttm": _f(r.get("averagePETTM")),
                "pe_ttm_median": _f(r.get("middlePETTM")), "pe_lyr_median": _f(r.get("middlePELYR")),
                "pb": None, "pb_ew": None, "pb_median": None,
                "dividend_yield": None, "dividend_yield2": None,
                # 源直接给近十年分位（0~1），是全库唯一的「现成分位」字段
                "pe_ttm_pct10y": _f(r.get("quantileInRecent10YearsMiddlePeTtm"), 6),
                "close_point": _f(r.get("close"), 2),
            })
            n += 1
        logger.info("  全A 等权：%s 行（窗口内 %s）", len(df), n)
        return n

    # ---------- 主流程 ----------

    @staticmethod
    def _filter_non_trading_csindex(cur, rows: list[dict]) -> tuple[list[dict], int]:
        """交易日闸门：只过滤 source='csindex' 的休市日复述行，返回 (过滤后行, 剔除数)。

        为什么只针对 csindex（2026-10-19 假日假 K 线根治的配套）：
        csindex 源是「近 20 个交易日滚动」的**日频**序列，中证官网在休市日也会发布估值
        （且值 = 上一交易日复述），落库后会把 48 行休市日行（中秋 09-25 + 国庆 10-01~07）
        带入 env_fundamental_daily 派生，产生与 dc_index_market 假日假 bar 同源的污染。
        而 legu/legu_pb/all_a 是「月末」快照，月末天然可能落在周末（如 06-30/05-31），
        属估值长历史分位的**预期形态**，不可按交易日过滤，否则会误删历史月末样本。

        ⚠️ 边界：trade_calendar 只覆盖 2005-01-01 起，2005 前的行不做过滤（csindex 源
        本身只回近 20 日，实际不可能触达 2005 前，此处仅防御性豁免）。
        """
        cs_rows = [r for r in rows if r.get("source") == "csindex"]
        if not cs_rows:
            return rows, 0
        dates = sorted({r["trade_date"] for r in cs_rows if r["trade_date"] >= date(2005, 1, 1)})
        if not dates:
            return rows, 0
        trading = set()
        try:
            cur.execute(
                "SELECT trade_date FROM trade_calendar "
                "WHERE is_trading_day=1 AND trade_date BETWEEN %s AND %s",
                (dates[0], dates[-1]),
            )
            trading = {r[0] for r in cur.fetchall()}
        except Exception:
            # 日历不可用时宁放过，不误删
            return rows, 0
        kept = [
            r for r in rows
            if r.get("source") != "csindex"
            or r["trade_date"] < date(2005, 1, 1)
            or r["trade_date"] in trading
        ]
        return kept, len(rows) - len(kept)

    def run(self) -> dict:
        out: list[dict] = []
        errors: list[str] = []
        n_cs = self._from_csindex(out, errors)
        n_lg = self._from_legu(out, errors)
        n_pb = self._from_legu_pb(out, errors)
        n_aa = self._from_all_a(out, errors)

        if not out:
            raise RuntimeError("估值各源全部失败：" + "; ".join(errors[:5]))

        # 同一 (code, date, source) 可能重复（源内重复行）→ 后者覆盖前者
        uniq: dict[tuple, dict] = {}
        for r in out:
            uniq[(r["index_code"], r["trade_date"], r["source"])] = r
        rows = list(uniq.values())

        conn = pymysql.connect(**get_db_config().to_dict(),
                               cursorclass=pymysql.cursors.DictCursor)
        try:
            with conn.cursor() as cur:
                # 交易日闸门：仅过滤 csindex 源的休市日复述（中证官网休市日也发布估值，
                # 如 2026-10-01~07 与 09-25 中秋全复述上一交易日值，落库会污染
                # env_fundamental_daily 派生）。legu/legu_pb/all_a 是「月末」快照，
                # 月末天然可能落在周末，属预期，**不**过滤。
                rows, dropped = self._filter_non_trading_csindex(cur, rows)
                if dropped:
                    logger.warning("⚠️ 估值采集剔除 %d 行 csindex 休市日复述", dropped)

                cur.execute("SELECT COUNT(*) AS n FROM index_valuation_daily")
                before = cur.fetchone()["n"]
                sql = (
                    f"INSERT INTO index_valuation_daily ({', '.join(_TGT)}, update_time, data_source) "
                    f"VALUES ({', '.join(['%s'] * len(_TGT))}, NOW(), 'AKSHARE') "
                    "ON DUPLICATE KEY UPDATE "
                    + ", ".join(f"{c}=COALESCE(VALUES({c}), {c})" for c in _TGT if c not in
                                ("index_code", "trade_date", "source"))
                    + ", update_time=NOW()"
                )
                cur.executemany(sql, [tuple(r[c] for c in _TGT) for r in rows])
            conn.commit()
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS n FROM index_valuation_daily")
                after = cur.fetchone()["n"]
                cur.execute("SELECT MAX(trade_date) AS d, COUNT(DISTINCT index_code) AS k, "
                            "COUNT(DISTINCT source) AS s FROM index_valuation_daily")
                r = cur.fetchone()
            new_rows = max(0, after - before)
        finally:
            conn.close()

        msg = (f"指数估值 upsert {len(rows)} 行（新增 {new_rows}）｜"
               f"中证 {n_cs} · 乐咕PE {n_lg} · 乐咕PB {n_pb} · 全A {n_aa}")
        logger.info("✅ %s", msg)
        return with_steps(
            {"records_written": new_rows if new_rows else len(rows),
             "error_count": len(errors), "errors": errors[:20], "note": msg},
            RUN_STEPS,
            {
                1: f"{len(CSINDEX)} 个指数，{n_cs} 行",
                2: f"{len(LEGU)} 个指数，{n_lg} 行（全量历史，供 ERP 分位）",
                3: f"{len(LEGU)} 个指数，{n_pb} 行（PB 长历史）",
                4: f"全 A 等权/中位，{n_aa} 行",
                5: f"upsert {len(rows)} 行（表内 {after} 行 / 最新 {r['d']} / {r['k']} 个代码 / {r['s']} 个源）",
            },
        )
