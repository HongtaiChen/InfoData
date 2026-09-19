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
  all_a      stock_a_ttm_lyr                          全 A 等权/中位 + 现成分位   横截面参照（不依赖成分股权重）

⚠️ 三源口径不同，**数值不可直接比大小**（实测沪深300 滚动 PE：中证官网 16.92、
   乐咕 12.68 —— 前者按整体法含亏损股、后者剔除负值），只能同源纵向比。这是刻意的：
   把口径分歧留下来，正是「交叉印证」要的东西（registry.py 第一原则）。
"""
import logging
from datetime import date, datetime, timedelta

import pymysql
import akshare as ak

from ..db import get_db_config
from ._common import with_steps, call_with_timeout

logger = logging.getLogger(__name__)

RUN_STEPS = [
    {"no": 1, "name": "中证官网估值", "params": "stock_zh_index_value_csindex × 6 指数（滚动 20 交易日窗口）"},
    {"no": 2, "name": "乐咕长历史 PE", "params": "stock_index_pe_lg × 4 指数（2005 至今月末序列，全量入库——ERP 分位的样本就是这个）"},
    {"no": 3, "name": "全 A 等权口径", "params": "stock_a_ttm_lyr（含近十年分位字段，独立于成分股权重）"},
    {"no": 4, "name": "批量 Upsert", "params": "uk_index_date_source(index_code, trade_date, source) 幂等；三源各自留痕"},
]

# 中证官网（code → 名称）。code 用 6 位完整码，与 index_profile / index_constituents 对齐
CSINDEX = {
    "000300": "沪深300",
    "000016": "上证50",
    "000905": "中证500",
    "000852": "中证1000",
    "000688": "科创50",
    "931775": "中证全指房地产指数",
}
# 乐咕（symbol 为中文名 → 目标 6 位码）
LEGU = {
    "沪深300": "000300",
    "上证50": "000016",
    "中证500": "000905",
    "中证1000": "000852",
}
# 全 A 等权/中位口径（非指数，单独一行）
ALL_A_CODE = "ALL_A"
ALL_A_NAME = "全 A（等权/中位口径）"

_TGT = ["index_code", "index_name", "trade_date", "source", "pe_lyr", "pe_ttm",
        "pe_ttm_median", "pe_lyr_median", "dividend_yield", "dividend_yield2",
        "pe_ttm_pct10y", "close_point"]


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
                    "dividend_yield": _f(r.get("股息率1")), "dividend_yield2": _f(r.get("股息率2")),
                    "pe_ttm_pct10y": None, "close_point": None,
                })
                n += 1
            logger.info("  中证 %s %s：%s 行", code, name, len(df))
        return n

    def _from_legu(self, out: list[dict], errors: list[str]) -> int:
        n = 0
        for symbol, code in LEGU.items():
            try:
                df = call_with_timeout(ak.stock_index_pe_lg, self.timeout_sec, symbol=symbol)
            except Exception as e:                # noqa: BLE001
                errors.append(f"legu {symbol}: {type(e).__name__} {str(e)[:60]}")
                continue
            if df is None or df.empty:
                errors.append(f"legu {symbol}: 返回为空")
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
                    "dividend_yield": None, "dividend_yield2": None,
                    "pe_ttm_pct10y": None,
                    # ⚠️ 乐咕该列名为「指数」，存的其实是**点位数值**（如 4507.39），不是名称
                    "close_point": _f(r.get("指数"), 2),
                })
                n += 1
            logger.info("  乐咕 %s：%s 行（窗口内 %s）", symbol, len(df), n)
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
                "dividend_yield": None, "dividend_yield2": None,
                # 源直接给近十年分位（0~1），是全库唯一的「现成分位」字段
                "pe_ttm_pct10y": _f(r.get("quantileInRecent10YearsMiddlePeTtm"), 6),
                "close_point": _f(r.get("close"), 2),
            })
            n += 1
        logger.info("  全A 等权：%s 行（窗口内 %s）", len(df), n)
        return n

    # ---------- 主流程 ----------

    def run(self) -> dict:
        out: list[dict] = []
        errors: list[str] = []
        n_cs = self._from_csindex(out, errors)
        n_lg = self._from_legu(out, errors)
        n_aa = self._from_all_a(out, errors)

        if not out:
            raise RuntimeError("估值三源全部失败：" + "; ".join(errors[:5]))

        # 同一 (code, date, source) 可能重复（源内重复行）→ 后者覆盖前者
        uniq: dict[tuple, dict] = {}
        for r in out:
            uniq[(r["index_code"], r["trade_date"], r["source"])] = r
        rows = list(uniq.values())

        conn = pymysql.connect(**get_db_config().to_dict(),
                               cursorclass=pymysql.cursors.DictCursor)
        try:
            with conn.cursor() as cur:
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
               f"中证 {n_cs} · 乐咕 {n_lg} · 全A {n_aa}")
        logger.info("✅ %s", msg)
        return with_steps(
            {"records_written": new_rows if new_rows else len(rows),
             "error_count": len(errors), "errors": errors[:20], "note": msg},
            RUN_STEPS,
            {
                1: f"{len(CSINDEX)} 个指数，{n_cs} 行",
                2: f"{len(LEGU)} 个指数，{n_lg} 行（全量历史，供 ERP 分位）",
                3: f"全 A 等权/中位，{n_aa} 行",
                4: f"upsert {len(rows)} 行（表内 {after} 行 / 最新 {r['d']} / {r['k']} 个代码 / {r['s']} 个源）",
            },
        )
