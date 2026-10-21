#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
投资环境模块 · 服务层（env_service）—— 指标读取、历史序列/分位编排

只做「读数据 + 组装响应」，不做结论生成（E3 本期不做 AI）。数据来自物化表：
  env_index_tech_daily / env_fundamental_daily / env_fundamental_quarter /
  env_threshold / env_macro_monthly + index_profile（指数清单）。

分位已在物化时算好（env_fundamental_daily 的 *_pct 列），这里只透传；
技术面的「当前值相对历史分位」由 env_metrics.pct_rank 在响应时对物化列现算
（技术面列都是长序列，现算零成本且避免再造一张分位物化表）。

口径随响应下发：每个指标块带 `note`（口径说明），前端只透传不手抄。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..db import query_all
from ..services import env_metrics as M


# ---------------------------------------------------------------------------
# 国内指数清单
# ---------------------------------------------------------------------------

def domestic_indices() -> dict:
    """国内指数列表（index_profile 全量 21 个）+ 当日涨跌幅（env_index_tech_daily 最新）。"""
    prof = query_all(
        "SELECT index_code, index_name, description, base_date, base_point, "
        "importance_rank FROM index_profile ORDER BY importance_rank, index_code"
    )
    latest = query_all(
        "SELECT t.index_code, t.trade_date, t.close, t.change_pct, "
        "th.drop_threshold, th.threshold_source "
        "FROM env_index_tech_daily t "
        "JOIN (SELECT index_code, MAX(trade_date) AS md FROM env_index_tech_daily "
        "      GROUP BY index_code) m ON m.index_code = t.index_code AND m.md = t.trade_date "
        "LEFT JOIN env_threshold th ON th.index_code = t.index_code"
    )
    latest_map = {r["index_code"]: r for r in latest}
    items = []
    for p in prof:
        code = p["index_code"]
        l = latest_map.get(code, {})
        items.append({
            "index_code": code,
            "index_name": p["index_name"],
            "description": p["description"],
            "base_date": p["base_date"],
            "base_point": p["base_point"],
            "trade_date": l.get("trade_date"),
            "close": l.get("close"),
            "change_pct": l.get("change_pct"),
            "drop_threshold": l.get("drop_threshold"),
            "threshold_source": l.get("threshold_source"),
        })
    return {"total": len(items), "items": items}


# ---------------------------------------------------------------------------
# 总览（21 指数横向对比）
# ---------------------------------------------------------------------------

def domestic_overview() -> dict:
    """21 指数关键指标横向对比表（总览页）。

    每行一个指数，列 = 最新收盘 / 当日涨跌幅 / 近1月 / 近1年 / PE分位 / 股息率分位 /
    60日年化波动率 / 近1年最大回撤。全部从物化表聚合，无新采集。
    """
    prof = query_all(
        "SELECT index_code, index_name, importance_rank FROM index_profile "
        "ORDER BY importance_rank, index_code"
    )
    # 技术面最新行（含涨跌幅、近1月/近1年窗口、波动率、回撤）
    tech = query_all(
        "SELECT t.* FROM env_index_tech_daily t "
        "JOIN (SELECT index_code, MAX(trade_date) AS md FROM env_index_tech_daily "
        "      GROUP BY index_code) m ON m.index_code = t.index_code AND m.md = t.trade_date"
    )
    tech_map = {r["index_code"]: r for r in tech}
    # 基本面最新行（估值分位）
    fund = query_all(
        "SELECT f.* FROM env_fundamental_daily f "
        "JOIN (SELECT index_code, MAX(trade_date) AS md FROM env_fundamental_daily "
        "      GROUP BY index_code) m ON m.index_code = f.index_code AND m.md = f.trade_date"
    )
    fund_map = {r["index_code"]: r for r in fund}

    items = []
    for p in prof:
        code = p["index_code"]
        t = tech_map.get(code, {})
        f = fund_map.get(code, {})
        items.append({
            "index_code": code,
            "index_name": p["index_name"],
            "trade_date": t.get("trade_date"),
            "close": t.get("close"),
            "change_pct": t.get("change_pct"),
            "win_1m": t.get("win_1m"),
            "win_1y": t.get("win_1y"),
            "ann_vol_60": t.get("ann_vol_60"),
            "max_dd_1y": t.get("max_dd_1y"),
            "pe_ttm": f.get("pe_ttm"),
            "pe_ttm_pct": f.get("pe_ttm_pct"),
            "pb": f.get("pb"),
            "pb_pct": f.get("pb_pct"),
            "dividend_yield": f.get("dividend_yield"),
            "dividend_yield_pct": f.get("dividend_yield_pct"),
        })
    return {"total": len(items), "items": items}


# ---------------------------------------------------------------------------
# 多指数叠加对比（归一化净值曲线）
# ---------------------------------------------------------------------------

# 对比窗口 → 回看自然日数（用于 SQL 过滤，宽放一点再由归一起点对齐）
_COMPARE_WINDOW_DAYS = {
    "3m": 95,
    "6m": 190,
    "1y": 380,
    "3y": 1160,
}


def domestic_compare(codes: list[str], window: str = "1y",
                     start: str = "", end: str = "") -> dict:
    """多指数收盘序列 → 归一化净值曲线（起点=100）+ 各自真实收盘价。

    曲线画归一化 values（不同点位量级的指数可比强弱）；
    悬停展示用 closes（真实点位）+ 区间涨跌幅（前端相对首值计算）。
    codes 已由调用方按 index_profile 过滤/去重；
    window ∈ {3m,6m,1y,3y}；start/end（YYYYMMDD）优先于 window（自定义区间）。
    数据源用 env_index_tech_daily.close（已物化、与内容块同口径）。
    """
    if not codes:
        return {"series": [], "labels": [], "dates": []}
    params: list = list(codes)
    where = f"index_code IN ({','.join(['%s'] * len(codes))}) AND close IS NOT NULL"
    if start and end:
        where += " AND trade_date BETWEEN %s AND %s"
        params += [f"{start[:4]}-{start[4:6]}-{start[6:8]}", f"{end[:4]}-{end[4:6]}-{end[6:8]}"]
    else:
        window = window if window in _COMPARE_WINDOW_DAYS else "1y"
        where += " AND trade_date >= DATE_SUB(CURDATE(), INTERVAL %s DAY)"
        params += [_COMPARE_WINDOW_DAYS[window]]
    rows = query_all(
        f"SELECT index_code, trade_date, close FROM env_index_tech_daily "
        f"WHERE {where} ORDER BY trade_date", params,
    )
    # 指数名映射
    prof = query_all(
        f"SELECT index_code, index_name FROM index_profile WHERE index_code IN ({','.join(['%s'] * len(codes))})",
        list(codes),
    )
    name_map = {r["index_code"]: r["index_name"] for r in prof}

    df = pd.DataFrame(rows) if rows else pd.DataFrame(columns=["index_code", "trade_date", "close"])
    if df.empty:
        return {"series": [], "labels": [], "dates": []}

    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df = df.drop_duplicates(subset=["index_code", "trade_date"], keep="last")
    pivot = df.pivot(index="trade_date", columns="index_code", values="close").sort_index()
    # 归一化：每个指数取其自身序列首个非空值为基准 100（不同点位量级可比强弱）
    base = pivot.ffill().bfill().iloc[0]
    norm = pivot.ffill() / base * 100
    dates = [str(d) for d in pivot.index]

    series = []
    labels = []
    for code in codes:
        if code not in pivot.columns:
            continue
        raw = pivot[code].ffill()
        nv = norm[code]
        series.append({
            "index_code": code,
            "index_name": name_map.get(code, code),
            "values": [None if pd.isna(v) else round(float(v), 2) for v in nv.tolist()],
            "closes": [None if pd.isna(v) else round(float(v), 2) for v in raw.tolist()],
        })
        labels.append(name_map.get(code, code))
    return {"dates": dates, "labels": labels, "series": series}


# ---------------------------------------------------------------------------
# 技术面
# ---------------------------------------------------------------------------

def _tech_row(code: str, limit: int) -> list[dict]:
    rows = query_all(
        "SELECT * FROM env_index_tech_daily WHERE index_code=%s "
        "ORDER BY trade_date DESC LIMIT %s", [code, limit]
    )
    rows.reverse()
    return rows


def tech_indicators(code: str, limit: int = 500) -> dict:
    """该指数全部技术面指标（A 组趋势 + B 组波动下行风险）。

    返回按「内容块」组织的结构：每个块 = 当前值 + 历史序列 + 分位 + 口径。
    """
    rows = _tech_row(code, limit)
    if not rows:
        return {"index_code": code, "empty": True, "note": "该指数暂无技术面物化数据"}

    def series(key: str) -> list[float | None]:
        return [r.get(key) for r in rows]

    def cur(key: str):
        for r in reversed(rows):
            if r.get(key) is not None:
                return r[key]
        return None

    dates = [str(r["trade_date"]) for r in rows]

    # 12 窗口涨跌幅矩阵（当前值 + 序列）
    win_keys = [
        ("win_halfm", "半月"), ("win_1m", "一月"), ("win_15m", "一个半月"),
        ("win_2m", "两月"), ("win_25m", "两个半月"), ("win_3m", "三月"),
        ("win_6m", "六月"), ("win_1y", "一年"), ("win_ytd", "本年"),
        ("win_qtd", "本季"), ("win_mtd", "本月"), ("win_wtd", "本周"),
    ]
    windows = [{"key": k, "label": lb, "value": cur(k),
                "series": series(k)} for k, lb in win_keys]

    latest = rows[-1]

    return {
        "index_code": code,
        "empty": False,
        "as_of": str(latest["trade_date"]),
        "close": latest.get("close"),
        "change_pct": latest.get("change_pct"),
        "trend": {
            "change_pct": {
                "value": latest.get("change_pct"),
                "note": "当日涨跌幅(%)：对比页面阈值口径与历史分布自行判断是否大幅下跌",
            },
            "windows": windows,
            "moving_avg": {
                "dates": dates,
                "close": series("close"),
                "ma60": series("ma60"),
                "ma250": series("ma250"),
                "close_over_ma60_pct": latest.get("close_over_ma60_pct"),
                "close_over_ma250_pct": latest.get("close_over_ma250_pct"),
                "note": "收盘价与 60/250 日均线：当前价距两均线偏离(%)，正=站上均线",
            },
            "volume": {
                "dates": dates,
                "volume": series("vol_ma20"),
                "vol_ratio_20": latest.get("vol_ratio_20"),
                "note": "20 日均量 ±30% 区间：vol_ratio_20>100 放量 / <100 缩量（只陈述事实）",
            },
            "macd": {
                "dates": dates, "dif": series("macd_dif"),
                "dea": series("macd_dea"), "hist": series("macd_hist"),
                "note": "MACD（12/26/9）：DIF/DEA 及柱（(DIF-DEA)×2）",
            },
            "rsi": {
                "dates": dates, "rsi14": series("rsi14"),
                "value": latest.get("rsi14"),
                "note": "RSI(14)：70/30 为常见参考线（超买/超卖区，非买卖信号）",
            },
            "boll": {
                "dates": dates, "upper": series("boll_upper"),
                "mid": series("boll_mid"), "lower": series("boll_lower"),
                "note": "布林带(20,2)：上/中/下轨",
            },
            "support_resist": {
                "high_250": latest.get("high_250"), "low_250": latest.get("low_250"),
                "high_20": latest.get("high_20"), "low_20": latest.get("low_20"),
                "note": "支撑/阻力：近 250 日与近 20 日高低点（在价格图上标注水平位）",
            },
        },
        "risk": {
            "drawdown": {
                "dates": dates,
                # 回撤用 max_dd_* 列（截至当日的区间最大回撤，负值）
                "dd_1y": series("max_dd_1y"),
                "dd_3y": series("max_dd_3y"),
                "dd_all": series("max_dd_all"),
                "max_dd_1y": latest.get("max_dd_1y"),
                "max_dd_3y": latest.get("max_dd_3y"),
                "max_dd_all": latest.get("max_dd_all"),
                "note": "截至当日的历史最大回撤(%)：负值越大回撤越深",
            },
            "volatility": {
                "dates": dates,
                "ann_vol": series("ann_vol_60"),
                "down_vol": series("down_vol_60"),
                "ann_vol_60": latest.get("ann_vol_60"),
                "down_vol_60": latest.get("down_vol_60"),
                "note": "年化波动率 / 下行波动率(%)：60 日滚动，年化 ×√252",
            },
            "beta": {
                "dates": dates, "beta": series("beta_60"),
                "value": latest.get("beta_60"),
                "note": "对沪深300 的 60 日滚动 Beta（Beta=1 参考线）",
            },
            "correlation": {
                "value": latest.get("corr_300_120"),
                "note": "与沪深300 的 120 日相关性（指数间相关性矩阵见下）",
            },
        },
    }


def correlation_matrix() -> dict:
    """21 指数间 120 日相关性热力矩阵（由 env_index_tech_daily 收盘序列现算）。"""
    rows = query_all(
        "SELECT index_code, trade_date, close FROM env_index_tech_daily "
        "WHERE close IS NOT NULL ORDER BY trade_date"
    )
    if not rows:
        return {"items": [], "labels": []}
    df = pd.DataFrame(rows)
    df = df.rename(columns={"index_code": "code", "trade_date": "date"})
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df = df.drop_duplicates(subset=["code", "date"], keep="last")
    pivot = df.pivot(index="date", columns="code", values="close")
    ret = pivot.pct_change().tail(120)
    corr = ret.corr()
    labels = list(corr.columns)
    items = []
    for i, a in enumerate(labels):
        for j, b in enumerate(labels):
            v = corr.loc[a, b]
            items.append({"x": i, "y": j, "value": None if pd.isna(v) else round(float(v), 3)})
    return {"labels": labels, "items": items}


# ---------------------------------------------------------------------------
# 基本面
# ---------------------------------------------------------------------------

def fundamental_indicators(code: str) -> dict:
    """该指数基本面指标（估值分位序列 + 盈利成长 + 宏观）。"""
    fund = query_all(
        "SELECT * FROM env_fundamental_daily WHERE index_code=%s "
        "ORDER BY trade_date", [code]
    )
    if not fund:
        return {"index_code": code, "empty": True, "note": "该指数暂无基本面物化数据"}

    dates = [str(r["trade_date"]) for r in fund]
    latest = fund[-1]

    def series(key: str):
        return [r.get(key) for r in fund]

    # 估值块（每个 = 当前值 + 历史分位带 + 分位值）
    valuation = {
        "pe_ttm": {
            "dates": dates, "value": series("pe_ttm"), "pct": series("pe_ttm_pct"),
            "current": latest.get("pe_ttm"), "current_pct": latest.get("pe_ttm_pct"),
            "note": "滚动市盈率 PE-TTM（乐咕整体法，剔除负值）及全史分位：分位越低越便宜、安全边际越厚，分位越高越依赖盈利兑现",
        },
        "pb": {
            "dates": dates, "value": series("pb"), "pct": series("pb_pct"),
            "current": latest.get("pb"), "current_pct": latest.get("pb_pct"),
            "note": "市净率 PB（乐咕整体法）及全史分位：PB 越低破净/低估值保护越强，PB 高分位估值扩张空间有限",
        },
        "dividend_yield": {
            "dates": dates, "value": series("dividend_yield"),
            "pct": series("dividend_yield_pct"),
            "current": latest.get("dividend_yield"),
            "current_pct": latest.get("dividend_yield_pct"),
            "note": "股息率(%)及全史分位：股息率越高持有期现金回报越厚、抗跌性越强，过低则安全垫薄",
        },
        "erp": {
            "dates": dates, "value": series("erp"),
            "current": latest.get("erp"),
            "note": "股债收益差/风险溢价 ERP = 1/PE-TTM − 10Y 国债收益率（越高=股票相对债券越有吸引力，越低=股票相对债券偏贵）",
        },
        "pb_dispersion": {
            "dates": dates, "value": series("pb_dispersion"),
            "current": latest.get("pb_dispersion"),
            "note": "成分股 PB 离散度（横截面标准差）：离散度低=估值分化小、结构性风险低；离散度高=定价分歧大",
        },
    }

    # 盈利质量与成长（近 40 季度趋势）
    quarter = query_all(
        "SELECT * FROM env_fundamental_quarter WHERE index_code=%s "
        "ORDER BY report_date", [code]
    )
    profitability = {
        "roe": {
            "dates": [str(r["report_date"]) for r in quarter],
            "value": [r.get("roe") for r in quarter],
            "note": "成分股聚合 ROE(%)（中位数，抗极端值），近 40 季度趋势：ROE 稳定且较高=盈利质量好、下行保护强，下滑或波动大=质量转弱",
        },
        "net_profit_yoy": {
            "dates": [str(r["report_date"]) for r in quarter],
            "value": [r.get("net_profit_yoy") for r in quarter],
            "note": "成分股聚合净利润同比增速(%)（中位数），近 40 季度趋势：盈利改善是估值安全的基础，持续负增长=基础松动",
        },
        "revenue_yoy": {
            "dates": [str(r["report_date"]) for r in quarter],
            "value": [r.get("revenue_yoy") for r in quarter],
            "note": "成分股聚合营收同比增速(%)（中位数），近 40 季度趋势：收入端有支撑 vs 营收萎缩",
        },
        "ocf_ratio": {
            "dates": [str(r["report_date"]) for r in quarter],
            "value": [r.get("ocf_ratio") for r in quarter],
            "note": "现金流含金量 = 经营现金流净额之和 ÷ 净利润之和（倍）：>1 利润含金量高，<0 现金流为负、利润质量差",
        },
        "ocf_sum": {
            "dates": [str(r["report_date"]) for r in quarter],
            "value": [r.get("ocf_sum") for r in quarter],
            "note": "成分股经营活动现金流净额之和（元）：绝对值趋势反映现金创造能力，恶化=利润质量转差",
        },
    }

    return {
        "index_code": code,
        "empty": False,
        "as_of": str(latest["trade_date"]),
        "valuation": valuation,
        "profitability": profitability,
    }


# ---------------------------------------------------------------------------
# 政策与外部环境判读（手动定性判读，非 AI；状态落库可切换）
# ---------------------------------------------------------------------------

# 三个判读主题：每个主题两个互斥状态（用户拍板口径，2026-10-20）
POLICY_TOPICS: list[dict] = [
    {
        "topic": "monetary",
        "title": "货币政策",
        "options": [
            {"key": "loose", "label": "结构性宽松、流动性合理充裕",
             "hint": "利于估值扩张与成长风格，利率下行通道中股票资产性价比上升"},
            {"key": "tight", "label": "收紧或大水漫灌后退潮",
             "hint": "流动性边际收紧或前期宽松退潮，估值承压，警惕高估值板块回撤"},
        ],
    },
    {
        "topic": "fiscal",
        "title": "财政政策",
        "options": [
            {"key": "support", "label": "精准支持经济修复",
             "hint": "财政发力托底盈利预期，利好顺周期与内需链条"},
            {"key": "weak", "label": "政策力度不及预期",
             "hint": "经济修复斜率存疑，盈利下修风险，防御风格占优"},
        ],
    },
    {
        "topic": "external",
        "title": "外部环境",
        "options": [
            {"key": "friendly", "label": "美联储偏鸽、地缘稳定",
             "hint": "外部流动性压力缓解、风险偏好回升，利于外资流入与成长资产"},
            {"key": "tense", "label": "美联储鹰派、地缘冲突、全球利率上行",
             "hint": "全球贴现率上行 + 避险情绪，压制估值，高股息/低波动相对占优"},
        ],
    },
]

_POLICY_DDL = (
    "CREATE TABLE IF NOT EXISTS env_policy_view ("
    " topic VARCHAR(32) NOT NULL PRIMARY KEY,"
    " state VARCHAR(32) NOT NULL,"
    " update_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"
    ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci"
    " COMMENT='投资环境·宏观与政策环境手动判读状态'"
)


def policy_views() -> dict:
    """政策与外部环境判读：主题定义 + 当前落库状态（未选过的主题 state=None）。"""
    from ..db import execute_write

    execute_write(_POLICY_DDL)
    rows = query_all("SELECT topic, state, update_time FROM env_policy_view")
    state_map = {r["topic"]: r for r in rows}
    items = []
    for t in POLICY_TOPICS:
        cur = state_map.get(t["topic"])
        valid = cur is not None and any(o["key"] == cur["state"] for o in t["options"])
        items.append({
            **t,
            "state": cur["state"] if valid else None,
            "updated_at": str(cur["update_time"]) if valid else None,
        })
    return {"items": items,
            "note": "政策与外部环境为人工定性判读（两态互斥），点击切换后落库，供后续复盘对照"}


def set_policy_view(topic: str, state: str) -> dict:
    """切换某主题的判读状态（UPSERT）。"""
    from ..db import execute_write

    topic_def = next((t for t in POLICY_TOPICS if t["topic"] == topic), None)
    if topic_def is None:
        return {"ok": False, "error": f"未知主题: {topic}"}
    if not any(o["key"] == state for o in topic_def["options"]):
        return {"ok": False, "error": f"未知状态: {state}"}
    execute_write(_POLICY_DDL)
    execute_write(
        "INSERT INTO env_policy_view (topic, state) VALUES (%s, %s) "
        "ON DUPLICATE KEY UPDATE state = VALUES(state)",
        (topic, state),
    )
    return {"ok": True, "topic": topic, "state": state}


def macro_series() -> dict:
    """宏观组（GDP/PMI/CPI/PPI）读数 + 历史曲线。"""
    rows = query_all(
        "SELECT stat_month, indicator, value, yoy, note FROM env_macro_monthly "
        "ORDER BY stat_month"
    )
    out: dict[str, dict] = {}
    for r in rows:
        ind = r["indicator"]
        d = out.setdefault(ind, {"dates": [], "value": [], "yoy": [], "note": r.get("note")})
        d["dates"].append(r["stat_month"])
        d["value"].append(r["value"])
        d["yoy"].append(r["yoy"])
    # 每个指标补最新读数
    for ind, d in out.items():
        d["latest"] = d["dates"][-1] if d["dates"] else None
        d["latest_value"] = d["value"][-1] if d["value"] else None
        d["latest_yoy"] = d["yoy"][-1] if d["yoy"] else None
    return {"items": out}


def scope_note() -> dict:
    """指数范围说明（样本指数 / 阈值口径 / 指标口径）。"""
    thresholds = query_all(
        "SELECT index_code, index_name, drop_threshold, threshold_source, threshold_note "
        "FROM env_threshold ORDER BY index_code"
    )
    return {
        "sample": "国内指数样本 = index_profile 全量 21 个指数（上证/沪深300/创业板指/科创50/中证500 等），页面顶部切换器选择。",
        "threshold": "大幅下跌阈值：上证 1%、沪深300 1.5%、创业板·科创 2%（手定）；其余指数按「近3年日跌幅95分位」统计建议值，标注口径仅供参考——是统计事实，非 AI 判断。",
        "indicator": "技术面（12窗口涨跌幅/均线/量价/MACD/RSI/布林/支撑阻力/回撤/波动率/Beta/相关性）+ 基本面（PE/PB/股息率/ERP 及分位 + 成分股财务聚合近40季度 + 宏观GDP/PMI/CPI/PPI）。分位 = 当前值在历史窗口的排位，仅作位置参考，不含定性结论。",
        "thresholds": thresholds,
    }


# ---------------------------------------------------------------------------
# 国际指数（GlobalIndexView）—— 技术面 4 块 + 基本面
# ---------------------------------------------------------------------------

# 海外指数分组（用于前端分组渲染）
OVERSEAS_GROUPS = [
    {"key": "global", "label": "全球综合", "codes": ["ACWI", "MSWORLD", "MSEAFE", "MSEM"]},
    {"key": "us", "label": "美国", "codes": ["SPX", "NDX", "DJI"]},
    {"key": "eu", "label": "欧洲", "codes": ["DAX", "UKX", "CAC", "SX5E"]},
    {"key": "jp", "label": "日本", "codes": ["N225"]},
    {"key": "em", "label": "新兴市场", "codes": ["SENSEX", "KOSPI", "IBOV"]},
    {"key": "cn", "label": "中国相关", "codes": ["HSI", "HSCEI"]},
]

OVERSEAS_CODE_NAME = {
    "ACWI": "MSCI ACWI", "MSWORLD": "MSCI World", "MSEAFE": "MSCI EAFE",
    "MSEM": "MSCI 新兴市场", "SPX": "标普500", "NDX": "纳斯达克100",
    "DJI": "道琼斯工业", "DAX": "德国DAX", "UKX": "英国富时100",
    "CAC": "法国CAC40", "SX5E": "欧洲斯托克50", "N225": "日经225",
    "SENSEX": "印度SENSEX", "KOSPI": "韩国综合", "IBOV": "巴西Bovespa",
    "HSI": "恒生指数", "HSCEI": "恒生国企", "IXIC": "纳斯达克综合",
}


def _overseas_latest_tech() -> dict:
    """各海外指数技术面最新一行（env_overseas_tech_daily 的 MAX(trade_date)）。"""
    rows = query_all(
        "SELECT t.* FROM env_overseas_tech_daily t "
        "JOIN (SELECT index_code, MAX(trade_date) AS md FROM env_overseas_tech_daily "
        "      GROUP BY index_code) m ON m.index_code = t.index_code AND m.md = t.trade_date"
    )
    return {r["index_code"]: r for r in rows}


def global_indices() -> dict:
    """国际指数列表 + 最新技术面读数（按 6 组组织）。"""
    latest = _overseas_latest_tech()
    groups = []
    for g in OVERSEAS_GROUPS:
        items = []
        for code in g["codes"]:
            l = latest.get(code)
            if not l:
                continue
            items.append({
                "index_code": code,
                "index_name": OVERSEAS_CODE_NAME.get(code, code),
                "trade_date": str(l["trade_date"]),
                "close": l.get("close"),
                "change_pct": l.get("change_pct"),
                "ma60_pct": l.get("close_over_ma60_pct"),
                "ma250_pct": l.get("close_over_ma250_pct"),
                "rsi14": l.get("rsi14"),
                "rel_strong_60": l.get("rel_strong_60"),
                "rel_strong_250": l.get("rel_strong_250"),
                "max_dd_1y": l.get("max_dd_1y"),
                "ann_vol_60": l.get("ann_vol_60"),
                "beta_60": l.get("beta_60"),
                "corr_60": l.get("corr_60"),
            })
        if items:
            groups.append({"key": g["key"], "label": g["label"], "items": items})
    return {"groups": groups, "total": sum(len(g["items"]) for g in groups)}


def global_tech(code: str, limit: int = 500) -> dict:
    """该海外指数的技术面指标（趋势与动量 + 波动率与风险，含相对强弱）。"""
    rows = query_all(
        "SELECT * FROM env_overseas_tech_daily WHERE index_code=%s "
        "ORDER BY trade_date DESC LIMIT %s", [code, limit]
    )
    rows.reverse()
    if not rows:
        return {"index_code": code, "empty": True, "note": "该指数暂无技术面物化数据"}

    def series(key: str):
        return [r.get(key) for r in rows]

    def cur(key: str):
        for r in reversed(rows):
            if r.get(key) is not None:
                return r[key]
        return None

    dates = [str(r["trade_date"]) for r in rows]
    latest = rows[-1]

    return {
        "index_code": code,
        "index_name": OVERSEAS_CODE_NAME.get(code, code),
        "empty": False,
        "as_of": str(latest["trade_date"]),
        "close": latest.get("close"),
        "change_pct": latest.get("change_pct"),
        "trend": {
            "moving_avg": {
                "dates": dates, "close": series("close"),
                "ma60": series("ma60"), "ma250": series("ma250"),
                "close_over_ma60_pct": latest.get("close_over_ma60_pct"),
                "close_over_ma250_pct": latest.get("close_over_ma250_pct"),
                "note": "收盘价与 60/250 日均线偏离(%)：正=站上均线。⚠️ 各市场本币计价，汇率可能扭曲跨市场趋势信号",
            },
            "macd": {
                "dates": dates, "dif": series("macd_dif"),
                "dea": series("macd_dea"), "hist": series("macd_hist"),
                "note": "MACD(12/26/9)：趋势确认与超买超卖",
            },
            "rsi": {
                "dates": dates, "rsi14": series("rsi14"),
                "value": latest.get("rsi14"),
                "note": "RSI(14)：70/30 常见参考线。新兴市场技术指标与次日波动率的尾部依赖与发达市场有显著差异",
            },
            "relative_strength": {
                "dates": dates,
                "rel_strong_60": series("rel_strong_60"),
                "rel_strong_250": series("rel_strong_250"),
                "value_60": latest.get("rel_strong_60"),
                "value_250": latest.get("rel_strong_250"),
                "note": "相对 ACWI 的区间超额(%)：正=跑赢全球。用于判断全球资金在区域间的轮动方向",
            },
        },
        "risk": {
            "drawdown": {
                "dates": dates,
                "dd_1y": series("max_dd_1y"),
                "dd_3y": series("max_dd_3y"),
                "dd_all": series("max_dd_all"),
                "max_dd_1y": latest.get("max_dd_1y"),
                "max_dd_3y": latest.get("max_dd_3y"),
                "max_dd_all": latest.get("max_dd_all"),
                "note": "截至当日历史最大回撤(%)：负值越大回撤越深",
            },
            "volatility": {
                "dates": dates,
                "ann_vol": series("ann_vol_60"),
                "down_vol": series("down_vol_60"),
                "ann_vol_60": latest.get("ann_vol_60"),
                "down_vol_60": latest.get("down_vol_60"),
                "note": "指数自身年化/下行波动率(%)：60 日滚动。区域市场恐慌程度可横向对比",
            },
            "beta_corr": {
                "dates": dates, "beta": series("beta_60"), "corr": series("corr_60"),
                "beta_60": latest.get("beta_60"), "corr_60": latest.get("corr_60"),
                "note": "对 ACWI 的 60 日滚动 Beta 与相关性（跨市场联动程度）",
            },
        },
    }


def global_compare(codes: list[str], window: str = "1y",
                   start: str = "", end: str = "") -> dict:
    """多海外指数收盘序列叠加（原始收盘价，不归一化）。"""
    where = "WHERE index_code IN (%s)" % ",".join(["%s"] * len(codes)) if codes else ""
    rows = query_all(
        f"SELECT index_code, trade_date, close FROM env_overseas_tech_daily "
        f"{where} ORDER BY trade_date",
        codes,
    )
    if not rows:
        return {"items": [], "labels": []}
    df = pd.DataFrame(rows)
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    # 窗口过滤
    if start and end:
        df = df[(df["trade_date"] >= start) & (df["trade_date"] <= end)]
    elif window in ("3m", "6m", "1y", "3y"):
        n = {"3m": 63, "6m": 126, "1y": 252, "3y": 756}[window]
        last = df["trade_date"].max()
        cutoff = pd.Timestamp(last) - pd.Timedelta(days=int(n * 1.6))
        df = df[pd.to_datetime(df["trade_date"]) >= cutoff]
    items = []
    labels = []
    for code, g in df.groupby("index_code"):
        g = g.sort_values("trade_date")
        items.append({
            "index_code": code,
            "index_name": OVERSEAS_CODE_NAME.get(code, code),
            "dates": [str(d) for d in g["trade_date"]],
            "close": [float(v) if v == v else None for v in g["close"]],
        })
        labels.append(code)
    return {"items": items, "labels": labels}


def global_correlation() -> dict:
    """海外指数间 60 日相关性热力矩阵（由 env_overseas_tech_daily 现算）。"""
    rows = query_all(
        "SELECT index_code, trade_date, close FROM env_overseas_tech_daily "
        "WHERE close IS NOT NULL ORDER BY trade_date"
    )
    if not rows:
        return {"items": [], "labels": []}
    df = pd.DataFrame(rows)
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    pivot = df.pivot_table(index="trade_date", columns="index_code", values="close")
    pivot = pivot.dropna(axis=1, how="all")
    # 只保留数据较完整的指数（>60 天）
    pivot = pivot.loc[:, pivot.count() >= 60]
    ret = pivot.pct_change()
    corr = ret.corr()
    labels = list(corr.columns)
    items = []
    for i, a in enumerate(labels):
        for j, b in enumerate(labels):
            items.append({"x": i, "y": j, "value": float(corr.loc[a, b])})
    return {"items": items, "labels": labels}


def global_vix() -> dict:
    """VIX 序列 + 最新读数（波动率与风险块）。"""
    rows = query_all("SELECT trade_date, close FROM vix_daily ORDER BY trade_date")
    if not rows:
        return {"empty": True, "note": "VIX 数据未采集"}
    return {
        "empty": False,
        "dates": [str(r["trade_date"]) for r in rows],
        "close": [r["close"] for r in rows],
        "latest": rows[-1]["close"],
        "latest_date": str(rows[-1]["trade_date"]),
        "note": "VIX 标普500隐含波动率：<15 平稳，>25-30 恐慌（用户口径）",
    }


def global_cftc(years: int = 10) -> dict:
    """CFTC 货币期货非商业净仓位（资金流与仓位块）。默认只返回近 N 年。"""
    cutoff = (pd.Timestamp.now() - pd.DateOffset(years=years)).strftime("%Y-%m-%d")
    rows = query_all(
        f"SELECT currency, metric, trade_date, value FROM cftc_position_weekly "
        f"WHERE metric='net' AND trade_date >= '{cutoff}' ORDER BY trade_date"
    )
    if not rows:
        return {"empty": True, "note": "CFTC 数据未采集"}
    df = pd.DataFrame(rows)
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    pivot = df.pivot_table(index="trade_date", columns="currency", values="value")
    pivot = pivot.sort_index()
    currencies = list(pivot.columns)
    return {
        "empty": False,
        "dates": [str(d) for d in pivot.index],
        "currencies": currencies,
        "series": {c: [float(v) if v == v else None for v in pivot[c]]
                   for c in currencies},
        "latest_date": str(pivot.index[-1]),
        "note": "CFTC 货币期货非商业净仓位（周频，近 10 年窗口，页面可缩放）：极端仓位往往对应反转风险",
    }


def global_valuation() -> dict:
    """国际指数估值快照（pe_ttm/pe_fwd/cape，Siblis 免费层稀疏快照）。"""
    rows = query_all(
        "SELECT index_code, metric, trade_date, value FROM index_valuation_intl "
        "ORDER BY trade_date"
    )
    if not rows:
        return {"empty": True, "note": "国际指数估值数据未采集"}
    out = {}
    for r in rows:
        code = r["index_code"]
        d = out.setdefault(code, {
            "index_code": code,
            "index_name": OVERSEAS_CODE_NAME.get(code, code),
            "metrics": {},
        })
        m = d["metrics"].setdefault(r["metric"], {"dates": [], "values": []})
        m["dates"].append(str(r["trade_date"]))
        m["values"].append(r["value"])
    for code, d in out.items():
        for mkey, m in d["metrics"].items():
            m["latest"] = m["values"][-1] if m["values"] else None
            m["latest_date"] = m["dates"][-1] if m["dates"] else None
    items = sorted(out.values(), key=lambda x: x["index_code"])
    return {
        "empty": False,
        "items": items,
        "note": "国际指数估值（Siblis 免费层）：仅 pe_ttm/pe_fwd/cape 三指标、8 个历史快照点（2023 末起）。PB/股息率免费源不可得，已如实留空。",
    }


def global_fx_bond(years: int = 10) -> dict:
    """美元指数 + 美债利差（跨市场联动块）。默认只返回近 N 年（页面有缩放轴，全史无必要）。"""
    cutoff = (pd.Timestamp.now() - pd.DateOffset(years=years)).strftime("%Y-%m-%d")
    dxy = query_all(
        f"SELECT trade_date, dxy_sina FROM global_usd_index_daily "
        f"WHERE dxy_sina IS NOT NULL AND trade_date >= '{cutoff}' ORDER BY trade_date"
    )
    bond = query_all(
        f"SELECT trade_date, us_bond_10y, us_bond_10y_2y_spread FROM bond_profit_daily "
        f"WHERE us_bond_10y IS NOT NULL AND trade_date >= '{cutoff}' ORDER BY trade_date"
    )
    return {
        "dxy": {
            "dates": [str(r["trade_date"]) for r in dxy],
            "close": [r["dxy_sina"] for r in dxy],
            "latest": dxy[-1]["dxy_sina"] if dxy else None,
            "latest_date": str(dxy[-1]["trade_date"]) if dxy else None,
            "note": "美元指数 DXY：快速走强或急跌 = 全球流动性/避险风向（用户口径）",
        },
        "bond": {
            "dates": [str(r["trade_date"]) for r in bond],
            "y10": [r["us_bond_10y"] for r in bond],
            "spread_10y2y": [r["us_bond_10y_2y_spread"] for r in bond],
            "latest_y10": bond[-1]["us_bond_10y"] if bond else None,
            "latest_spread": bond[-1]["us_bond_10y_2y_spread"] if bond else None,
            "latest_date": str(bond[-1]["trade_date"]) if bond else None,
            "note": "美债 10Y 收益率与 10Y-2Y 利差（全球利率锚）",
        },
    }


def global_fundamental() -> dict:
    """国际指数基本面分析（4 大块：估值水平 / 盈利增长与质量 / 宏观经济与政策 / 地缘风险）。

    只读数据 + 组装响应，不做结论。数据源分层、时效与缺口都在各块 note 里诚实标注。
    """
    # ---------- ① 估值水平 ----------
    # 已有估值（Siblis pe_ttm/pe_fwd/cape）+ 标普500 股息率（Shiller）
    val_rows = query_all(
        "SELECT index_code, metric, trade_date, value FROM index_valuation_intl "
        "ORDER BY trade_date"
    )
    valuation_items = {}
    for r in val_rows:
        code = r["index_code"]
        d = valuation_items.setdefault(code, {
            "index_code": code,
            "index_name": OVERSEAS_CODE_NAME.get(code, code),
            "metrics": {},
        })
        m = d["metrics"].setdefault(r["metric"], {"dates": [], "values": []})
        m["dates"].append(str(r["trade_date"]))
        m["values"].append(r["value"])
    for code, d in valuation_items.items():
        for mkey, m in d["metrics"].items():
            m["latest"] = m["values"][-1] if m["values"] else None
            m["latest_date"] = m["dates"][-1] if m["dates"] else None
    valuation = {
        "empty": not valuation_items,
        "items": sorted(valuation_items.values(), key=lambda x: x["index_code"]),
        "note": (
            "估值口径：PE-TTM/PE-Fwd/CAPE 来自 Siblis（2026-06 快照）；标普500 股息率来自 "
            "Shiller（1871 起月度，最新 2023-06，回溯 12 个月股息÷月末价）。"
            "⚠️ PB 与除标普500外的股息率：免费源不可得（Siblis 付费层 / MSCI Fact Sheet 需登录），"
            "如实留空，不伪造。"
        ),
    }

    # ---------- ② 盈利增长与质量 ----------
    # 国际指数级 ROE/EPS 增速/盈利修正比率均无免费序列 → 只给口径说明 + 缺口标注
    earnings = {
        "empty": True,
        "note": (
            "国际指数级的 ROE / EPS 增速 / 盈利修正比率**无免费历史序列**："
            "MSCI/STOXX Fact Sheet 不含这些字段，IBES/FactSet/Bloomberg 为商业授权。"
            "免费替代：ROE 可用 PB÷PE 倒算（但 PB 免费源亦缺）；盈利修正可用 P/E Fwd 变化做"
            "弱代理（需付费数据验证）。本期如实留空，待接入付费数据源后补充。"
        ),
        "proxies": {
            "roe": "PB ÷ PE（隐含 ROE）—— 依赖 PB，而 PB 免费源不可得，暂不可算",
            "eps_growth": "无免费指数级序列（MSCI/STOXX Fact Sheet 不含）",
            "revision_ratio": "P/E Fwd 月度变化可作弱代理，但需付费数据交叉验证",
        },
    }

    # ---------- ③ 宏观经济与政策 ----------
    # 全球宏观年度（IMF WEO：gdp_growth/inflation/debt_gdp）
    macro_rows = query_all(
        "SELECT country_code, indicator, stat_year, value FROM global_macro_annual "
        "ORDER BY stat_year"
    )
    macro_countries = {}
    for r in macro_rows:
        c = r["country_code"]
        d = macro_countries.setdefault(c, {"country_code": c, "indicators": {}})
        ind = d["indicators"].setdefault(r["indicator"], {"years": [], "values": []})
        ind["years"].append(r["stat_year"])
        ind["values"].append(r["value"])
    # 欧元区 HICP 月度（及时）
    hicp_rows = query_all(
        "SELECT stat_month, value FROM eu_hicp_monthly ORDER BY stat_month"
    )
    hicp = {
        "empty": not hicp_rows,
        "dates": [r["stat_month"] for r in hicp_rows],
        "values": [r["value"] for r in hicp_rows],
        "latest": hicp_rows[-1]["value"] if hicp_rows else None,
        "latest_month": hicp_rows[-1]["stat_month"] if hicp_rows else None,
        "note": "欧元区 HICP 同比率（Eurostat，ECB 目标基准口径，及时月度）",
    }
    # 央行政策利率（已有 cb_policy_rate，源停更 2025-08）
    policy_rows = query_all(
        "SELECT country_code, country_name, event_date, rate, change_bp FROM cb_policy_rate "
        "ORDER BY event_date"
    )
    policy_countries = {}
    for r in policy_rows:
        c = r["country_code"]
        d = policy_countries.setdefault(c, {
            "country_code": c, "country_name": r["country_name"],
            "dates": [], "rates": [], "changes": [],
        })
        d["dates"].append(str(r["event_date"]))
        d["rates"].append(r["rate"])
        d["changes"].append(r["change_bp"])
    for c, d in policy_countries.items():
        d["latest_rate"] = d["rates"][-1] if d["rates"] else None
        d["latest_date"] = d["dates"][-1] if d["dates"] else None
    macro = {
        "empty": not macro_countries,
        "countries": sorted(macro_countries.values(), key=lambda x: x["country_code"]),
        "hicp": hicp,
        "policy_rate": {
            "empty": not policy_countries,
            "countries": sorted(policy_countries.values(), key=lambda x: x["country_code"]),
            "note": "央行政策利率（美/欧/日/英）：⚠️ 上游数据源停更于 2025-07~08，"
                    "最后决议日 ≠ 当前利率，仅作历史方向参考",
        },
        "note": (
            "宏观口径：GDP 增速/CPI 通胀/债务GDP 来自 IMF WEO（年度，含历史+预测，半年更新）；"
            "欧元区 HICP 来自 Eurostat（及时月度）；政策利率来自 akshare（停更 2025-08）。"
            "⚠️ 年度频率无法表达「当前月度景气度」，PMI 全球免费长历史不可得（S&P 需订阅）。"
        ),
    }

    # ---------- ④ 国际政治与地缘风险 ----------
    # 定性块：无结构化免费数据源，给口径说明 + 关联已有量化数据（VIX/油价）
    geo = {
        "empty": True,
        "note": (
            "地缘冲突/关税/制裁/主权信用为**定性判断**，无结构化免费数据源。"
            "关联量化观测：冲突升级 → 能源价格（库内 futures_spot_price 原油）与波动率（VIX）脉冲；"
            "主权信用 → 长端利率（库内 bond_profit_daily 10Y）分化。本期以定性卡呈现，"
            "数值证据引用库内 VIX / 油价 / 10Y 收益率序列。"
        ),
    }

    return {
        "valuation": valuation,
        "earnings": earnings,
        "macro": macro,
        "geo": geo,
    }


# ---------------------------------------------------------------------------
# 市场氛围（MarketMoodView）—— 技术面 5 块 + 基本面
# ---------------------------------------------------------------------------
# 数据源：技术面绝大多数读 market_style_daily（已物化的宽度/量能/换手）+ 复用
#   securities_margin（两融）/ stock_account_monthly（开户）/ fund_new_issue（新基金）
#   等既有表；基本面复用 env_macro_monthly（GDP/PMI/CPI/PPI）+ env_policy_view（判读卡）。
# 诚实标注缺口：连板高度/封板率（盘中事件型，免费无长历史）、北向（2024-08 停披露）、
#   社交情绪（无免费序列）、股指期货基差（futures_spot_price 无股指期货品种）。

_MOOD_WINDOW = 250  # 技术面序列默认近 N 交易日


def _mood_series(sql: str, params: tuple = (), n: int = _MOOD_WINDOW) -> dict:
    """读 market_style_daily 某一列，返回近 n 日的 dates/values + 最新值。"""
    rows = query_all(sql, params)
    if not rows:
        return {"dates": [], "values": [], "latest": None, "latest_date": None}
    rows = rows[-n:]
    return {
        "dates": [str(r["trade_date"]) for r in rows],
        "values": [None if r["v"] is None else float(r["v"]) for r in rows],
        "latest": float(rows[-1]["v"]) if rows[-1]["v"] is not None else None,
        "latest_date": str(rows[-1]["trade_date"]),
    }


def market_mood() -> dict:
    """市场氛围（投资环境·市场氛围章节）：技术面 5 块 + 基本面 1 块。

    只读数据组装，不做 AI 结论；每个指标带 note 诚实标注口径与数据源时效。
    """
    from ..db import execute_write

    # ---------- ① 交易热度与量能 ----------
    amount = _mood_series(
        "SELECT trade_date, market_amount AS v FROM market_style_daily "
        "WHERE market_amount IS NOT NULL ORDER BY trade_date"
    )
    amount_ratio = _mood_series(
        "SELECT trade_date, amount_ratio_20 AS v FROM market_style_daily "
        "WHERE amount_ratio_20 IS NOT NULL ORDER BY trade_date"
    )
    turnover = _mood_series(
        "SELECT trade_date, turnover_med AS v FROM market_style_daily "
        "WHERE turnover_med IS NOT NULL ORDER BY trade_date"
    )
    heat = {
        "amount": {**amount, "note": "两市成交额（亿元）：全市场个股成交额求和。放量 = 参与度抬升；"
                                   "「连续放量但指数滞涨」= 顶部换手信号，「持续地量」= 底部磨底信号。"},
        "amount_ratio": {**amount_ratio, "note": "成交额 ÷ 20 日均量 ×100%（>100 放量 / <100 缩量）。"
                                             "量价配合：放量突破 vs 放量下跌，与指数方向交叉判断。"},
        "turnover": {**turnover, "note": "个股换手率中位数（%）：筹码交换频率。历史高分位 = 过热，"
                                       "极低分位 = 冷清。中位数抗异常值（源列曾混存两种口径，已归一）。"},
        # 成交集中度（前 5% 个股成交额占比）：沿用 market_sentiment 现算，此处读历史序列成本高，
        # 交给已有 market_sentiment 模块；本块给口径说明 + 关联。
        "concentration": {
            "empty": True,
            "note": "成交集中度（前 5% 个股成交额占比）见「分析研究·市场情绪」模块（现算）。"
                    "接近/超过 45% = 抱团风险，极度分散 = 无主线。",
        },
        # 板块轮动速度：见 sector_rotation 模块（行业离散度/首尾差），本块不重复建设。
        "rotation": {
            "empty": True,
            "note": "板块轮动速度（热点持续天数/行业离散度）见「分析研究·板块轮动」模块。"
                    "轮动极快 = 无主线、轮动停滞 = 趋势尾声。",
        },
    }

    # ---------- ② 市场广度与赚钱效应 ----------
    def _width_series(col: str) -> dict:
        return _mood_series(
            f"SELECT trade_date, {col} AS v FROM market_style_daily "
            f"WHERE {col} IS NOT NULL ORDER BY trade_date"
        )

    breadth = {
        "up_down": {
            "up": _width_series("breadth_up"),
            "down": _width_series("breadth_down"),
            "ratio": _width_series("breadth_up_ratio"),
            "note": "上涨/下跌家数及上涨占比：普涨 vs 普跌。连续普涨后分化 = 见顶，连续普跌 = 超卖。"},
        "limit": {
            "up": _width_series("limit_up"),
            "down": _width_series("limit_down"),
            "note": "涨停/跌停家数（近似口径：主板 ±9.8%、创业板/科创板 ±19.8%，未单列 ST/北交所）。"
                    "涨停潮/跌停潮 = 情绪极端；涨停稀少、跌停扩散 = 情绪冰点。"},
        "above_ma": {
            "ma20": _width_series("above_ma20_pct"),
            "ma60": _width_series("above_ma60_pct"),
            "note": "收盘站上 MA20/MA60 的个股占比：市场健康度。过高后回落 = 见顶，过低后修复 = 底部。"},
        "new_high_low": {
            "high60": _width_series("new_high60"),
            "low60": _width_series("new_low60"),
            "diff60": _width_series("hl_diff60"),
            "note": "创 60 日新高/新低家数及差值：趋势强度。创新高骤降 = 动能衰竭，创新低骤增 = 走弱。"},
        "adl": {**_width_series("breadth_adl"),
                "note": "腾落线 ADL（Σ上涨−下跌 累计）：广度趋势。ADL 与指数顶/底背离 = 行情转折信号。"},
        "link_note": "连板高度/封板率/炸板率为「盘中事件型」数据（首封时间/炸板次数），免费源无长历史、"
                     "接口易失效。本期用涨停家数替代呈现，连板天梯/封板率待接入付费行情源后补充。",
    }

    # ---------- ③ 情绪与舆论 ----------
    sentiment_block = {
        "empty": True,
        "note": "社交媒体讨论量/搜索指数/股吧发帖量/媒体情绪/券商策略标题等，无免费、权威、长历史的"
                "结构化序列（券商研报给结论不给数据；CnOpenData 股吧语料为商业授权）。"
                "本期诚实留空，不做伪数据。可参考「② 市场广度」的涨停家数/连板高度作情绪代理。",
        "proxies": {
            "涨停家数": "库内 market_style_daily.limit_up（2005 起物化，情绪极端度近似）",
            "换手中位数": "库内 market_style_daily.turnover_med（关注度近似）",
            "开户数": "库内 stock_account_monthly（散户入市热情，月频）",
        },
    }

    # ---------- ④ 资金与杠杆 ----------
    margin_rows = query_all(
        "SELECT trade_date, rzye, rzrqye FROM securities_margin ORDER BY trade_date"
    )
    margin = {
        "dates": [str(r["trade_date"]) for r in margin_rows],
        "rzye": [None if r["rzye"] is None else float(r["rzye"]) / 1e8 for r in margin_rows],
        "rzrqye": [None if r["rzrqye"] is None else float(r["rzrqye"]) / 1e8 for r in margin_rows],
        "latest_rzye": float(margin_rows[-1]["rzye"]) / 1e8 if margin_rows and margin_rows[-1]["rzye"] is not None else None,
        "latest_date": str(margin_rows[-1]["trade_date"]) if margin_rows else None,
        "note": "融资余额（亿元，沪+深+北三市合计，源为元已换算）。快速飙升 = 杠杆加仓、风险积累；持续下降 = 去杠杆。"
                "⚠️ 融资余额/流通市值比逼近历史高点（如 2015 年 4.72%）为极端风险信号，见「分析研究·杠杆资金」模块。",
    }

    account_rows = query_all(
        "SELECT stat_month, a_person FROM stock_account_monthly ORDER BY stat_month"
    )
    accounts = {
        "dates": [str(r["stat_month"]) for r in account_rows],
        "values": [r["a_person"] for r in account_rows],
        "latest": float(account_rows[-1]["a_person"]) if account_rows and account_rows[-1]["a_person"] is not None else None,
        "latest_month": str(account_rows[-1]["stat_month"]) if account_rows else None,
        "note": "A 股个人新增投资者数（月频，万人）：开户激增 = 散户入市情绪高点（往往对应阶段顶），活跃度低迷 = 冷清。",
    }

    # 新基金发行（增量资金）：近 12 个月成立基金数
    fund_rows = query_all(
        "SELECT DATE_FORMAT(establish_date, '%Y-%m') AS m, COUNT(*) AS n "
        "FROM fund_new_issue WHERE establish_date IS NOT NULL "
        "GROUP BY DATE_FORMAT(establish_date, '%Y-%m') ORDER BY m"
    )
    fund_issue = {
        "dates": [r["m"] for r in fund_rows],
        "values": [int(r["n"]) for r in fund_rows],
        "latest": int(fund_rows[-1]["n"]) if fund_rows else None,
        "latest_month": fund_rows[-1]["m"] if fund_rows else None,
        "note": "新基金成立数（月频，全类型）：异常放大 = 增量资金涌入/散户抢购（爆款基金），低迷 = 发行冰点。",
    }

    etf_rows = query_all("SELECT trade_date, net_inflow, total_share FROM etf_share_daily ORDER BY trade_date")
    etf_flow = {
        "dates": [str(r["trade_date"]) for r in etf_rows],
        "net_inflow": [None if r["net_inflow"] is None else float(r["net_inflow"]) for r in etf_rows],
        "latest": float(etf_rows[-1]["net_inflow"]) if etf_rows and etf_rows[-1]["net_inflow"] is not None else None,
        "latest_date": str(etf_rows[-1]["trade_date"]) if etf_rows else None,
        "note": "股票型 ETF 净申赎（亿元）：大级别资金态度。持续净流入后转净流出 = 机构撤退，持续净流出后企稳 = 见底。"
                "⚠️ 当前库内仅有最新一日快照（采集器逐日积累中），长历史待积累。",
    }

    # 北向/南向：北向 2024-08 起停实时披露
    capital = {
        "margin": margin,
        "accounts": accounts,
        "fund_issue": fund_issue,
        "etf_flow": etf_flow,
        "north_south": {
            "empty": True,
            "note": "北向资金净买入已 2024-08-19 起停止实时披露（仅盘后成交额/季度持仓）；南向（港股通）"
                    "仍日频可查但需接入东方财富 hsgt 接口。本期留空，待接入沪深港通数据源后补充。",
        },
        "repurchase": {
            "empty": True,
            "note": "产业资本增减持（重要股东/董监高增减持、回购）见「分析研究·资金温度」模块的产业资本卡"
                    "（近 90 天回购计划数及分位）。密集减持 = 内部人看空，密集增持/回购 = 底部信号。",
        },
    }

    # ---------- ⑤ 风险偏好与衍生品 ----------
    # 黄金（futures_spot_price 有黄金品种）
    gold_rows = query_all(
        "SELECT trade_date, spot_price FROM futures_spot_price "
        "WHERE good_name='黄金' AND spot_price IS NOT NULL ORDER BY trade_date"
    )
    gold = {
        "dates": [str(r["trade_date"]) for r in gold_rows],
        "values": [float(r["spot_price"]) for r in gold_rows],
        "latest": float(gold_rows[-1]["spot_price"]) if gold_rows else None,
        "latest_date": str(gold_rows[-1]["trade_date"]) if gold_rows else None,
        "note": "黄金现货价：避险需求。黄金强 = 避险情绪升温/风险偏好回落，黄金弱 = 风险偏好回升。",
    }
    derivatives = {
        "gold": gold,
        "futures_basis": {
            "empty": True,
            "note": "股指期货 IF/IC/IM 基差（升贴水）：期货 − 现货，负为贴水。升水 = 乐观，深度贴水 = 悲观/对冲需求高。"
                    "⚠️ 库内 futures_spot_price 暂无股指期货品种（仅商品期货），需接入中金所/akshare 股指期货数据后补充。",
        },
        "ah_premium": {
            "empty": True,
            "note": "AH 溢价（两地风险偏好差异）：恒生 AH 溢价指数。溢价极端 = 两地情绪背离。需接入 AH 溢价指数数据源后补充。",
        },
    }

    # ---------- 基本面：宏观与政策 + 外部环境 + 供给制度 ----------
    # 复用 env_macro_monthly（GDP/PMI/CPI/PPI）
    macro_items = {}
    for ind in ("gdp", "pmi", "cpi", "ppi"):
        rows = query_all(
            "SELECT stat_month, value, yoy, note FROM env_macro_monthly "
            "WHERE indicator=%s ORDER BY stat_month", (ind,)
        )
        macro_items[ind] = {
            "dates": [str(r["stat_month"]) for r in rows],
            "value": [r["value"] for r in rows],
            "yoy": [r["yoy"] for r in rows],
            "latest": str(rows[-1]["stat_month"]) if rows else None,
            "latest_value": rows[-1]["value"] if rows else None,
            "latest_yoy": rows[-1]["yoy"] if rows else None,
            "note": rows[0]["note"] if rows else None,
        }

    # 政策判读卡：复用 policy_views（货币政策/财政/外部环境）+ 供给制度（定性）
    policy = policy_views()

    fundamental = {
        "macro": {
            "gdp": macro_items.get("gdp"),
            "pmi": macro_items.get("pmi"),
            "cpi": macro_items.get("cpi"),
            "ppi": macro_items.get("ppi"),
            "note": "宏观数据（GDP/PMI/CPI/PPI）来自库内 env_macro_monthly（国家统计局口径）。"
                    "PMI 50 荣枯线、CPI/PPI 价格与企业利润、GDP 经济修复斜率。",
        },
        "policy": policy,
        "supply": {
            "empty": True,
            "note": "供给与制度：IPO/再融资节奏（市场抽血）、长期资金政策（险资/养老金入市）、交易制度稳定性。"
                    "均为定性判断，无结构化免费数据源。IPO 节奏可参考库内 finance_calendar / 新基金发行作近似，本期以定性卡呈现。",
        },
    }

    return {
        "heat": heat,
        "breadth": breadth,
        "sentiment": sentiment_block,
        "capital": capital,
        "derivatives": derivatives,
        "fundamental": fundamental,
        "note": (
            "市场氛围：技术面（交易热度量能/市场广度/情绪舆论/资金杠杆/风险偏好衍生品）读库内"
            " market_style_daily 物化数据（2005 起）+ 复用两融/开户/新基金/ETF 既有表；"
            "基本面（宏观政策/外部环境/供给制度）复用 env_macro_monthly + 政策判读卡。"
            "连板/封板率、北向、社交情绪、股指基差、AH 溢价免费源不可得，已诚实标注缺口。"
        ),
    }


# ---------------------------------------------------------------------------
# 国际环境（GlobalEnvironmentView）—— 技术面 5 块 + 基本面 5 块
# ---------------------------------------------------------------------------
# 数据源：技术面波动/避险/情绪不确定性读新采集的 ovx_daily/gpr_daily/epu_daily/
#   wui_monthly/tpu_monthly/fsi_monthly + 复用 vix_daily（VIX）/global_usd_index_daily
#   （美元）/bond_profit_daily（美债）/cb_policy_rate（政策利率）/cn_reserve_monthly
#   （中国外储）。基本面地缘读 gpr_daily + 定性判读卡，经贸读 tpu_monthly，
#   治理/政策/共识复用 cb_policy_rate + env_policy_view + 定性卡。
# 诚实标注缺口：MOVE（ICE 商业授权）、OVX 早期、主权 CDS/EMBI（付费）、新兴资本流（IIF
#   付费）、GCRI/GPI/恐怖主义/军费（学术年度或付费）、跨币种基差/RORO（无免费序列）。


def _env_series(sql: str, params: tuple = (), n: int = 500, col: str = "v",
                date_col: str = "trade_date") -> dict:
    """国际环境序列读取：返回 dates/values + 最新值，供前端 HistoryCurve。"""
    rows = query_all(sql, params)
    if not rows:
        return {"dates": [], "values": [], "latest": None, "latest_date": None}
    rows = rows[-n:]
    return {
        "dates": [str(r[date_col]) for r in rows],
        "values": [None if r[col] is None else float(r[col]) for r in rows],
        "latest": float(rows[-1][col]) if rows[-1][col] is not None else None,
        "latest_date": str(rows[-1][date_col]),
    }


def _env_monthly_series(sql: str, params: tuple = (), col: str = "v",
                         date_col: str = "stat_month") -> dict:
    """国际环境月频序列读取。"""
    rows = query_all(sql, params)
    if not rows:
        return {"dates": [], "values": [], "latest": None, "latest_date": None}
    return {
        "dates": [str(r[date_col]) for r in rows],
        "values": [None if r[col] is None else float(r[col]) for r in rows],
        "latest": float(rows[-1][col]) if rows[-1][col] is not None else None,
        "latest_date": str(rows[-1][date_col]),
    }


def global_environment() -> dict:
    """国际环境（投资环境·国际环境章节）：技术面 5 块 + 基本面 5 块。

    只读数据组装，不做 AI 结论；每个指标带 note 诚实标注口径与数据源时效。
    免费源不可得的指标（MOVE/主权CDS/EMBI/新兴资本流/GCRI/GPI 等）如实标注缺口。
    """
    # ---------------- 技术面 ① 市场波动与风险偏好 ----------------
    vix = _env_series(
        "SELECT trade_date, close AS v FROM vix_daily ORDER BY trade_date", n=1000)
    ovx = _env_series(
        "SELECT trade_date, close AS v FROM ovx_daily ORDER BY trade_date", n=1000)
    gpr = _env_series(
        "SELECT trade_date, gprd AS v FROM gpr_daily ORDER BY trade_date", n=1000)
    fsi = _env_monthly_series(
        "SELECT stat_month, fsi AS v FROM fsi_monthly ORDER BY stat_month")

    volatility = {
        "vix": vix,
        "ovx": ovx,
        "fsi": fsi,
        "move": {
            "empty": True,
            "note": "MOVE 美债波动率指数属 ICE 商业授权产品（2019 年被洲际交易所收购），"
                    "无免费官方全历史直链，FRED 亦无收录。当前免费仅 Yahoo ^MOVE 内部研究用途，"
                    "本项目按合规原则不接第三方镜像，标注「数据源待接入」。",
        },
        "cds": {
            "empty": True,
            "note": "主权 CDS 利差的日频历史为 S&P Global(原Markit)/Bloomberg/LSEG 商业数据，"
                    "免费公开库无覆盖主要主权且历史足够长的序列，标注「数据源待接入」。",
        },
        "note": "VIX(<15 平稳 / >25-30 恐慌)、OVX(原油波动)、Püttmann FSI(1889-2016 超长期金融压力，"
                "均值 100，>100 压力高于历史均值)。MOVE/跨币种基差/RORO/主权CDS 免费源不可得已标注。",
    }

    # ---------------- 技术面 ② 资本流动与汇率 ----------------
    dxy = _env_series(
        "SELECT trade_date, dxy_sina AS v FROM global_usd_index_daily "
        "WHERE dxy_sina IS NOT NULL ORDER BY trade_date", n=1000)
    cny = _env_series(
        "SELECT trade_date, usd_jpy AS v FROM global_usd_index_daily "
        "WHERE usd_jpy IS NOT NULL ORDER BY trade_date", n=1000)
    reserve = _env_monthly_series(
        "SELECT stat_month, fx_reserve_usd AS v FROM cn_reserve_monthly ORDER BY stat_month",
        col="v")

    capital_flow = {
        "dxy": dxy,
        "cny": cny,
        "reserve": reserve,
        "em_flow": {
            "empty": True,
            "note": "新兴市场组合资金流向的完整历史属 IIF 订阅数据（月频新闻稿免费但无历史库），"
                    "EMBI 主权利差属 J.P. Morgan 商业产品。免费替代为 IMF BoP 季度组合投资负债项，"
                    "本期暂标注「数据源待接入」。",
        },
        "note": "美元指数（避险与流动性风向）、人民币兑美元（usd_cny 用 cn_reserve 外储侧证）、"
                "中国外汇储备（1978 起）。全球外储币种构成（COFER）为 IMF 季频免费但需 Data Explorer "
                "解析，本期暂缓。",
    }

    # ---------------- 技术面 ③ 避险资产与跨市场表现 ----------------
    gold = _env_series(
        "SELECT trade_date, main_contract_price AS v FROM futures_spot_price "
        "WHERE good_name='黄金' AND main_contract_price IS NOT NULL ORDER BY trade_date", n=1000)
    us_bond = _env_series(
        "SELECT trade_date, us_bond_10y AS v FROM bond_profit_daily "
        "WHERE us_bond_10y IS NOT NULL ORDER BY trade_date", n=1000)
    us_spread = _env_series(
        "SELECT trade_date, us_bond_10y_2y_spread AS v FROM bond_profit_daily "
        "WHERE us_bond_10y_2y_spread IS NOT NULL ORDER BY trade_date", n=1000)

    safe_haven = {
        "gold": gold,
        "us_bond_10y": us_bond,
        "us_bond_spread": us_spread,
        "jpy_chf": {
            "empty": True,
            "note": "日元/瑞郎避险货币表现可用 usd_jpy/usd_chf（global_usd_index_daily 已有），"
                    "全球股市整体表现见「国际指数」章节，商品（油价/气价）见 OVX 关联。"
                    "信用利差（ICE BofA 公司债 OAS）需 FRED 但被代理阻断，标注待接入。",
        },
        "note": "黄金（上期所沪金主力）、美债 10Y 收益率 + 10Y-2Y 利差（全球利率锚，倒挂=衰退预警）。"
                "避险抢购 vs 收益率飙升的区分看二者背离方向。",
    }

    # ---------------- 技术面 ④ 情绪与不确定性 ----------------
    epu = _env_series(
        "SELECT trade_date, epu AS v FROM epu_daily ORDER BY trade_date", n=1000)
    wui = _env_monthly_series(
        "SELECT stat_month, wui AS v FROM wui_monthly ORDER BY stat_month")
    tpu = _env_monthly_series(
        "SELECT stat_month, tpu AS v FROM tpu_monthly ORDER BY stat_month")

    uncertainty = {
        "epu": epu,
        "wui": wui,
        "tpu": tpu,
        "note": "EPU 美国经济政策不确定性（Baker/Bloom/Davis，1985 起日频）、"
                "WUI 世界不确定性（IMF 编制，全球 GDP 加权，2008 起月频）、"
                "TPU 贸易政策不确定性（Caldara et al.，1960 起月频）。"
                "三者均为报纸文本挖掘指数，低位=稳定，持续攀升=风险信号。",
    }

    # ---------------- 技术面 ⑤ 技术趋势与市场结构 ----------------
    corr = global_correlation()
    tech_trend = {
        "correlation": corr,
        "note": "跨市场相关性（60 日，来自国际指数章节）衡量风险传染程度——高相关=同涨同跌、"
                "传染风险高。全球股指均线位置见「国际指数」章节技术面，期权 PCR/股指基差/"
                "全球资金流向免费源不可得已标注。",
    }

    # ---------------- 基本面 ① 地缘政治与安全 ----------------
    geopolitics = {
        "gpr": gpr,
        "empty": False,
        "note": "GPR 地缘政治风险指数（Caldara & Iacoviello，AER 2022，10 报日频 1985 起）。"
                "武装冲突数量/核风险/恐怖主义频率/军费开支等无免费结构化长历史（GCRI/GPI 为"
                "年度或付费、ACLED 冲突事件数据需订阅），本期以 GPR 量化 + 定性判读卡呈现，"
                "不虚构数据。",
        "judgements": [
            {"topic": "地缘冲突强度", "title": "进行中的武装冲突（中东/俄乌等）",
             "states": ["冲突降温、和谈推进", "冲突升级、战线扩大"]},
            {"topic": "大国关系", "title": "中美/美俄/美欧关系稳定性",
             "states": ["对话机制畅通", "对抗升级、脱钩加速"]},
            {"topic": "核风险与军备", "title": "核威慑/军备投入态势",
             "states": ["核军控对话恢复", "核演习升级、威胁言论"]},
        ],
    }

    # ---------------- 基本面 ② 国际经济与贸易 ----------------
    trade = {
        "tpu": tpu,
        "empty": False,
        "note": "TPU 贸易政策不确定性指数（月频 1960 起，100=1% 文章占比）。"
                "贸易政策活动指数 TPA（WTO-IMF 月度）无免费公开序列，全球贸易开放度"
                "（贸易/GDP）为 WB 年度滞后数据，本期以 TPU 量化 + 定性标注呈现。",
    }

    # ---------------- 基本面 ③ 国际政治与治理 ----------------
    governance = {
        "empty": False,
        "note": "国际组织效能/国际规范强度/大国关系/国际体系格局/致命技术扩散/资源压力"
                "均为定性议题，无免费结构化量化序列。本期以定性判读卡呈现，关联 GPR 指数"
                "佐证「体系摩擦」方向。",
        "judgements": [
            {"topic": "国际体系格局", "title": "单极/两极/多极权力分布",
             "states": ["权力分布相对稳定", "权力转移引发摩擦"]},
            {"topic": "国际组织效能", "title": "联合国/WTO/IMF 协调能力",
             "states": ["多边合作有效", "机制瘫痪、改革停滞"]},
            {"topic": "贸易与制裁制度", "title": "国际规则稳定性",
             "states": ["稳定、可预期", "频繁制裁、规则武器化"]},
        ],
    }

    # ---------------- 基本面 ④ 政策与制度 ----------------
    policy_rate = query_all(
        "SELECT country_name, rate, prev_rate, change_bp, event_date FROM cb_policy_rate "
        "WHERE (country_name, event_date) IN "
        "(SELECT country_name, MAX(event_date) FROM cb_policy_rate GROUP BY country_name) "
        "ORDER BY country_name")
    policy = {
        "policy_rate": [
            {"country": r["country_name"], "rate": float(r["rate"]) if r["rate"] is not None else None,
             "change_bp": r["change_bp"], "event_date": str(r["event_date"])}
            for r in policy_rate
        ],
        "fed": {
            "empty": False,
            "note": "美联储政策见政策利率表（美国），全球长端利率见技术面③美债 10Y。"
                    "财政可持续性/主权信用/ G20 央行协调/交易制裁制度为定性议题。",
        },
        "note": "四大央行政策利率（美/欧/日/英）。⚠️ 数据源停更于 2025-07/08（akshare 与"
                " BIS 同步滞后），最新值仅供参考、不代表当前。",
    }

    # ---------------- 基本面 ⑤ 精英共识与舆论 ----------------
    consensus = {
        "empty": True,
        "note": "WEF 全球风险报告/慕尼黑安全报告/联合国全球风险调查均为年度报告，"
                "无免费结构化量化序列（WEF 报告需会员下载、GPI 为 IEP 版权年度开放数据）。"
                "本期以 EPU/WUI/TPU 文本指数（技术面④）作为舆论共识的量化代理，"
                "年度报告类标注「数据源待接入」。",
    }

    return {
        "volatility": volatility,
        "capital_flow": capital_flow,
        "safe_haven": safe_haven,
        "uncertainty": uncertainty,
        "tech_trend": tech_trend,
        "geopolitics": geopolitics,
        "trade": trade,
        "governance": governance,
        "policy": policy,
        "consensus": consensus,
        "note": (
            "国际环境：技术面（市场波动风险偏好/资本流动汇率/避险资产/情绪不确定性/技术趋势结构）"
            "读新采集 ovx_daily/gpr_daily/epu_daily/wui_monthly/tpu_monthly/fsi_monthly + 复用"
            " vix_daily/global_usd_index_daily/bond_profit_daily/cb_policy_rate/cn_reserve_monthly；"
            "基本面（地缘/经贸/治理/政策/共识）读 gpr_daily/tpu_monthly + 定性判读卡。"
            "MOVE/主权CDS/EMBI/新兴资本流/GCRI/GPI/恐怖主义/军费等免费源不可得，已诚实标注缺口。"
        ),
    }


# ---------------------------------------------------------------------------
# 全球货币（GlobalMoneyView）—— 技术面 3 块 + 基本面 4 块
# ---------------------------------------------------------------------------
# 数据源：复用 v2.1 三轨制已落地的货币流动性表（us_money_supply_monthly /
#   us_fed_balance_weekly / us_money_market_daily / eu_money_supply_monthly /
#   cn_liquidity_monthly / cn_cb_balance_monthly / cn_reserve_monthly /
#   global_liquidity_bis / cb_policy_rate / global_usd_index_daily / vix_daily /
#   futures_spot_price / global_macro_annual）。
# 诚实标注缺口：TED/LIBOR-OIS（LIBOR 停发）、SOFR-IORB（IORB 受阻）、XCCY/MOVE
#   （商业授权）、EMBI/新兴资本流（IIF/JPM 订阅）、COFER 币种构成（IMF 季频暂缓）、
#   日 M2 / 欧日央行资产负债表（库内缺）。


def _usd_cny_latest() -> float | None:
    """人民币兑美元中间价（元/美元），用于中国 M2/央行资产折美元。"""
    r = query_all(
        "SELECT mid_price FROM currency_boc_daily WHERE currency='USD' "
        "AND mid_price IS NOT NULL ORDER BY trade_date DESC LIMIT 1"
    )
    return float(r[0]["mid_price"]) if r else None


def global_money() -> dict:
    """全球货币（投资环境·全球货币章节）：技术面 3 块 + 基本面 4 块。

    只读数据 + 组装响应，不做结论。合成口径（全球 M2 汇总、美元净流动性、
    超额流动性）一律标「合成口径·非官方」；免费源不可得的利差类指标诚实标注缺口。
    单位统一折「十亿美元」/「%」，前端只透传不换算。
    """
    usd_cny = _usd_cny_latest() or 7.0  # 兜底汇率（正常不会走到）

    # ===================================================================
    # 技术面 ① 数量指标 —— 货币供应「总水位」
    # ===================================================================
    # 美国 M2（十亿美元，DBnomics FED/H6 主口径）
    us_m2 = _env_monthly_series(
        "SELECT stat_month, m2 AS v FROM us_money_supply_monthly "
        "WHERE m2 IS NOT NULL ORDER BY stat_month", col="v")
    us_m2_yoy = _env_monthly_series(
        "SELECT stat_month, m2_yoy AS v FROM us_money_supply_monthly "
        "WHERE m2_yoy IS NOT NULL ORDER BY stat_month", col="v")
    # 欧元区 M2（百万欧元，DBnomics ECB/BSI）
    eu_m2_raw = query_all(
        "SELECT stat_month, m2 FROM eu_money_supply_monthly "
        "WHERE m2 IS NOT NULL ORDER BY stat_month")
    # 中国 M2（亿元，人行统计）
    cn_m2_raw = query_all(
        "SELECT stat_month, m2 FROM cn_liquidity_monthly "
        "WHERE m2 IS NOT NULL ORDER BY stat_month")
    cn_m2_yoy = _env_monthly_series(
        "SELECT stat_month, m2_yoy AS v FROM cn_liquidity_monthly "
        "WHERE m2_yoy IS NOT NULL ORDER BY stat_month", col="v")
    # 欧元/人民币兑美元汇率（用于折算）
    eur_usd = query_all(
        "SELECT trade_date, eur_usd FROM global_usd_index_daily "
        "WHERE eur_usd IS NOT NULL ORDER BY trade_date")
    cny_usd = query_all(
        "SELECT trade_date, mid_price FROM currency_boc_daily WHERE currency='USD' "
        "AND mid_price IS NOT NULL ORDER BY trade_date")

    def _align_month_fx(month_rows, fx_rows, fx_col):
        """按月对齐汇率（用月末/当月最后一个可得汇率折算月度 M2）。"""
        fx = {}
        for r in fx_rows:
            fx[str(r["trade_date"])[:7]] = float(r[fx_col])
        out = {}
        for r in month_rows:
            m = str(r["stat_month"])[:7]
            if m in fx:
                out[m] = fx[m]
        return out

    fx_eur = _align_month_fx(eu_m2_raw, eur_usd, "eur_usd")
    fx_cny = _align_month_fx(cn_m2_raw, cny_usd, "mid_price")

    # 全球 M2 汇总（折十亿美元）：美(十亿) + 欧(百万欧×eur_usd/1000) + 中(亿元/cny/10)
    # 只在三大经济体均有数据的「交集月份」汇总，避免某经济体滞后导致末月骤降失真。
    us_m2_month = {str(r["stat_month"])[:7]: float(r["m2"])
                   for r in query_all("SELECT stat_month, m2 FROM us_money_supply_monthly "
                                      "WHERE m2 IS NOT NULL ORDER BY stat_month")}
    eu_m2_month = {str(r["stat_month"])[:7]: float(r["m2"]) for r in eu_m2_raw}
    cn_m2_month = {str(r["stat_month"])[:7]: float(r["m2"]) for r in cn_m2_raw}
    common_months = sorted(set(us_m2_month) & set(eu_m2_month) & set(cn_m2_month)
                           & set(fx_cny) & set(fx_eur))
    gbl_m2_dates, gbl_m2_vals = [], []
    for m in common_months:
        total = (us_m2_month[m]                                    # 美：十亿，直接用
                 + eu_m2_month[m] * fx_eur[m] / 1000.0            # 百万欧×eur_usd/1000
                 + cn_m2_month[m] / fx_cny[m] / 10.0)             # 亿元/cny/10
        gbl_m2_dates.append(m + "-01")
        gbl_m2_vals.append(round(total, 1))

    gbl_m2 = {
        "dates": gbl_m2_dates,
        "values": gbl_m2_vals,
        "latest": gbl_m2_vals[-1] if gbl_m2_vals else None,
        "latest_date": gbl_m2_dates[-1] if gbl_m2_dates else None,
    }
    # 各经济体 M2 分列（供前端分列展示，统一折十亿美元）
    cn_m2_bn = {
        "dates": [str(r["stat_month"]) for r in cn_m2_raw],
        "values": [round(float(r["m2"]) / fx_cny.get(str(r["stat_month"])[:7], usd_cny) / 10.0, 1)
                   if fx_cny.get(str(r["stat_month"])[:7]) else None for r in cn_m2_raw],
        "latest": None,
        "latest_date": None,
    }
    if cn_m2_bn["values"]:
        _v = [v for v in cn_m2_bn["values"] if v is not None]
        cn_m2_bn["latest"] = _v[-1] if _v else None
    eu_m2_bn = {
        "dates": [str(r["stat_month"]) for r in eu_m2_raw],
        "values": [round(float(r["m2"]) * fx_eur.get(str(r["stat_month"])[:7], 1.0) / 1000.0, 1)
                   if fx_eur.get(str(r["stat_month"])[:7]) else None for r in eu_m2_raw],
        "latest": None,
        "latest_date": None,
    }
    if eu_m2_bn["values"]:
        _v = [v for v in eu_m2_bn["values"] if v is not None]
        eu_m2_bn["latest"] = _v[-1] if _v else None

    # 全球央行资产负债表（美联储 + 人行，折十亿美元）
    fed_bal = _env_series(
        "SELECT trade_date, total_assets AS v FROM us_fed_balance_weekly "
        "WHERE total_assets IS NOT NULL ORDER BY trade_date", n=2000)
    # 源单位是百万美元 → 折十亿美元（÷1000）
    fed_bal["values"] = [None if v is None else round(v / 1000.0, 1) for v in fed_bal["values"]]
    fed_bal["latest"] = round(fed_bal["latest"] / 1000.0, 1) if fed_bal["latest"] is not None else None
    cn_cb_raw = query_all(
        "SELECT stat_month, total_assets FROM cn_cb_balance_monthly "
        "WHERE total_assets IS NOT NULL ORDER BY stat_month")
    fx_cny_cb = _align_month_fx(cn_cb_raw, cny_usd, "mid_price")
    cn_cb_bn = {
        "dates": [str(r["stat_month"]) for r in cn_cb_raw],
        "values": [round(float(r["total_assets"]) / fx_cny_cb.get(str(r["stat_month"])[:7], usd_cny) / 10.0, 1)
                   if fx_cny_cb.get(str(r["stat_month"])[:7]) else None for r in cn_cb_raw],
        "latest": None,
        "latest_date": None,
    }
    if cn_cb_bn["values"]:
        _v = [v for v in cn_cb_bn["values"] if v is not None]
        cn_cb_bn["latest"] = _v[-1] if _v else None

    # 美元净流动性 = 美联储总资产 − TGA − ON RRP（合成口径，周频对齐日频 TGA）
    fed_rows = query_all(
        "SELECT trade_date, total_assets, reverse_repo_total FROM us_fed_balance_weekly "
        "WHERE total_assets IS NOT NULL ORDER BY trade_date")
    tga_rows = query_all(
        "SELECT trade_date, tga FROM us_money_market_daily "
        "WHERE tga IS NOT NULL ORDER BY trade_date")
    tga_map = {r["trade_date"]: float(r["tga"]) for r in tga_rows}
    tga_dates = sorted(tga_map.keys())
    net_liq_dates, net_liq_vals = [], []
    for r in fed_rows:
        d = r["trade_date"]
        # 找最近一个 ≤ d 的 TGA
        tga = None
        for td in reversed(tga_dates):
            if td <= d:
                tga = tga_map[td]
                break
        if tga is None or r["reverse_repo_total"] is None:
            continue
        # 单位均为百万美元 → 折十亿美元（÷1000）
        net = (float(r["total_assets"]) - tga - float(r["reverse_repo_total"])) / 1000.0
        net_liq_dates.append(d)
        net_liq_vals.append(round(net, 1))
    net_liq = {
        "dates": [str(d) for d in net_liq_dates],
        "values": net_liq_vals,
        "latest": net_liq_vals[-1] if net_liq_vals else None,
        "latest_date": str(net_liq_dates[-1]) if net_liq_dates else None,
    }

    # 货币乘数 = 美 M2 ÷ 基础货币（us_money_supply_monthly 有 monetary_base）
    money_mult_raw = query_all(
        "SELECT stat_month, m2, monetary_base FROM us_money_supply_monthly "
        "WHERE m2 IS NOT NULL AND monetary_base IS NOT NULL AND monetary_base > 0 "
        "ORDER BY stat_month")
    money_mult = {
        "dates": [str(r["stat_month"]) for r in money_mult_raw],
        "values": [round(float(r["m2"]) / float(r["monetary_base"]), 2) for r in money_mult_raw],
        "latest": round(float(money_mult_raw[-1]["m2"]) / float(money_mult_raw[-1]["monetary_base"]), 2)
        if money_mult_raw else None,
        "latest_date": str(money_mult_raw[-1]["stat_month"]) if money_mult_raw else None,
    }

    quantity = {
        "gbl_m2": gbl_m2,
        "us_m2": us_m2,
        "eu_m2": eu_m2_bn,
        "cn_m2": cn_m2_bn,
        "us_m2_yoy": us_m2_yoy,
        "cn_m2_yoy": cn_m2_yoy,
        "fed_balance": fed_bal,
        "cn_cb_balance": cn_cb_bn,
        "net_liquidity": net_liq,
        "money_mult": money_mult,
        "cb_gdp": {
            "empty": False,
            "static": [
                {"central_bank": "美联储", "ratio": 20.7},
                {"central_bank": "欧央行", "ratio": 38.2},
                {"central_bank": "日央行", "ratio": 92.8},
                {"central_bank": "英格兰银行", "ratio": 28.7},
            ],
            "note": "央行资产/GDP 比例：库内无绝对 GDP（global_macro_annual 仅有 gdp_growth 增速），"
                    "无法自算历史序列。此处为 2026 年中静态参考值（eco3min 统计）。"
                    "扩展需补 IMF WEO 绝对 GDP 值。",
        },
        "note": (
            "全球 M2 汇总为合成口径（美欧中三大 M2 按汇率折美元加总，非官方「全球 M2」）；"
            "美元净流动性 = 美联储总资产 − TGA − ON RRP（合成口径·非官方）；"
            "货币乘数 = 美 M2 ÷ 基础货币。⚠️ 日 M2 与欧/日央行资产负债表库内缺失，标注缺口。"
            "单位统一折「十亿美元」；M2 同比为 %。"
        ),
    }

    # ===================================================================
    # 技术面 ② 价格指标 —— 资金「成本」
    # ===================================================================
    sofr = _env_series(
        "SELECT trade_date, sofr AS v FROM us_money_market_daily "
        "WHERE sofr IS NOT NULL ORDER BY trade_date", n=1000)
    vix = _env_series(
        "SELECT trade_date, close AS v FROM vix_daily ORDER BY trade_date", n=1000)

    # 央行政策利率（美/欧/日/英 最新）
    policy_rows = query_all(
        "SELECT country_name, rate, change_bp, event_date FROM cb_policy_rate "
        "WHERE (country_name, event_date) IN "
        "(SELECT country_name, MAX(event_date) FROM cb_policy_rate GROUP BY country_name) "
        "ORDER BY country_name")

    # 利率差（美 − 欧 / 美 − 日，bp）
    rate_map = {r["country_name"]: float(r["rate"]) for r in policy_rows if r["rate"] is not None}
    rate_diff = {
        "us_eu": round((rate_map.get("美国", 0) - rate_map.get("欧元区", 0)) * 100, 0)
        if "美国" in rate_map and "欧元区" in rate_map else None,
        "us_jp": round((rate_map.get("美国", 0) - rate_map.get("日本", 0)) * 100, 0)
        if "美国" in rate_map and "日本" in rate_map else None,
    }

    price = {
        "sofr": sofr,
        "vix": vix,
        "policy_rate": [
            {"country": r["country_name"], "rate": float(r["rate"]) if r["rate"] is not None else None,
             "change_bp": r["change_bp"], "event_date": str(r["event_date"])}
            for r in policy_rows
        ],
        "rate_diff": rate_diff,
        "ted": {
            "empty": True,
            "note": "TED 利差（3M LIBOR − 3M 国债）：美元 LIBOR 已于 2023-06-30 终止报价，"
                    "FRED TEDRATE 系列随之于 2022-01 停更（DISCONTINUED）。免费源不可得。",
        },
        "libor_ois": {
            "empty": True,
            "note": "LIBOR-OIS 利差：同因美元 LIBOR 停发，OIS 亦随 SOFR 转型重构。免费源不可得。",
        },
        "sofr_iorb": {
            "empty": True,
            "note": "SOFR-IORB 利差：SOFR 库内已有（纽约联储直连），但 IORB（准备金余额利率）"
                    "在 FRED（本项目被代理阻断，且 DBnomics 的 FRED 镜像已下架）。"
                    "仅展示 SOFR，IORB 侧标注缺口。",
        },
        "xccy": {
            "empty": True,
            "note": "跨货币互换基差（XCCY）：Bloomberg（EUBS/JYBS ticker）/ ICE 商业授权，"
                    "无免费全历史。免费源不可得。",
        },
        "move": {
            "empty": True,
            "note": "MOVE 美债波动率指数：ICE 商业授权产品（2019 年收购），无免费官方全历史。"
                    "免费源不可得。",
        },
        "note": (
            "SOFR（担保隔夜融资利率，LIBOR 官方替代）与 VIX 库内直读；政策利率表为美/欧/日/英。"
            "⚠️ 政策利率上游停更 2025-08，最后决议日 ≠ 当前利率。"
            "TED/LIBOR-OIS/SOFR-IORB/XCCY/MOVE 因 LIBOR 停发或商业授权，免费源不可得，已标注。"
        ),
    }

    # ===================================================================
    # 技术面 ③ 传导与市场反馈指标
    # ===================================================================
    dxy = _env_series(
        "SELECT trade_date, dxy_sina AS v FROM global_usd_index_daily "
        "WHERE dxy_sina IS NOT NULL ORDER BY trade_date", n=1000)
    gold = _env_series(
        "SELECT trade_date, main_contract_price AS v FROM futures_spot_price "
        "WHERE good_name='黄金' AND main_contract_price IS NOT NULL ORDER BY trade_date", n=1000)
    copper = _env_series(
        "SELECT trade_date, main_contract_price AS v FROM futures_spot_price "
        "WHERE good_name='铜' AND main_contract_price IS NOT NULL ORDER BY trade_date", n=1000)
    reserve = _env_monthly_series(
        "SELECT stat_month, fx_reserve_usd AS v FROM cn_reserve_monthly "
        "WHERE fx_reserve_usd IS NOT NULL ORDER BY stat_month", col="v")

    transmission = {
        "dxy": dxy,
        "gold": gold,
        "copper": copper,
        "reserve": reserve,
        "valuation": {
            "empty": False,
            "note": "全球股市估值中枢见「国际指数」章节基本面（Siblis PE-TTM/PE-Fwd/CAPE）。",
        },
        "em_flow": {
            "empty": True,
            "note": "新兴市场组合资金流向：IIF 订阅数据（月频新闻稿免费但无历史库）。"
                    "免费源不可得。",
        },
        "embi": {
            "empty": True,
            "note": "EMBI 主权外债利差：J.P. Morgan 商业产品。免费源不可得。",
        },
        "cofer": {
            "empty": True,
            "note": "全球外汇储备币种构成（COFER）：IMF 季频，需 Data Explorer 解析，本期暂缓。"
                    "参考：美元占已分配储备 56.7%（2026-Q2）。",
        },
        "note": (
            "美元指数 DXY（官方日线 ICE 口径）、黄金/铜（上期所主力）、中国外汇储备（亿美元）。"
            "原油库内无直接期货品种（仅有燃料油/沥青等，非原油），其波动率见国际环境章节 OVX。"
            "新兴资本流/EMBI/COFER 免费源不可得，已标注。"
        ),
    }

    # ===================================================================
    # 基本面 ① 央行政策与制度
    # ===================================================================
    central_bank = {
        "fed_balance": fed_bal,
        "cn_cb_balance": cn_cb_bn,
        "rate_diff": rate_diff,
        "judgements": [
            {"topic": "货币政策方向", "title": "全球主要央行加息/降息、QE/QT 方向",
             "states": ["宽松周期（降息/扩表）", "紧缩周期（加息/缩表）"]},
            {"topic": "央行独立性", "title": "货币政策是否受财政主导",
             "states": ["独立性稳固", "财政主导、赤字货币化"]},
            {"topic": "国际货币体系", "title": "美元本位 vs 储备货币多元化",
             "states": ["美元本位稳固", "去美元化加速、多元化"]},
        ],
        "note": (
            "扩表/缩表方向读美联储（us_fed_balance_weekly）与人民银行（cn_cb_balance_monthly）"
            "资产负债表；利率差读美欧日政策利率之差（bp）。央行独立性/国际货币体系为定性判读卡。"
        ),
    }

    # ===================================================================
    # 基本面 ② 财政政策与债务
    # ===================================================================
    # 政府债务/GDP（IMF WEO，年度，含预测）
    debt_rows = query_all(
        "SELECT country_code, stat_year, value FROM global_macro_annual "
        "WHERE indicator='debt_gdp' AND country_code IN ('USA','CHN','JPN','DEU','GBR','FRA') "
        "ORDER BY stat_year")
    debt_series = {}
    for r in debt_rows:
        c = r["country_code"]
        d = debt_series.setdefault(c, {"country_code": c, "years": [], "values": []})
        d["years"].append(r["stat_year"])
        d["values"].append(r["value"])

    fiscal = {
        "debt_gdp": {
            "empty": not debt_series,
            "countries": [{"country_code": c, "years": d["years"], "values": d["values"]}
                          for c, d in sorted(debt_series.items())],
            "note": "政府债务/GDP（IMF WEO，年度含预测）。美国联邦债务/GDP 已从 35% 以下升至 120% 以上，"
                    "长期侵蚀美元信用。",
        },
        "judgements": [
            {"topic": "财政赤字规模", "title": "主要经济体政府收支缺口",
             "states": ["赤字收敛", "赤字扩张、财政刺激"]},
            {"topic": "财政主导风险", "title": "财政对央行的压力",
             "states": ["财政纪律有序", "财政主导、迫使货币配合"]},
            {"topic": "MMT 实践", "title": "现代货币理论现实操作",
             "states": ["未启用", "财政赤字货币化倾向"]},
        ],
        "note": (
            "政府债务/GDP 读 global_macro_annual（IMF WEO）。财政赤字规模免费源为美财政部 "
            "fiscaldata（可采，本期暂缓）；财政主导/MMT 为定性判读卡。"
        ),
    }

    # ===================================================================
    # 基本面 ③ 国际收支与结构性因素
    # ===================================================================
    # 全球 GDP 增速（IMF WEO，主要经济体）
    gdp_rows = query_all(
        "SELECT country_code, stat_year, value FROM global_macro_annual "
        "WHERE indicator='gdp_growth' AND country_code IN ('USA','CHN','JPN','DEU','GBR','FRA','EUR') "
        "ORDER BY stat_year")
    gdp_series = {}
    for r in gdp_rows:
        c = r["country_code"]
        d = gdp_series.setdefault(c, {"country_code": c, "years": [], "values": []})
        d["years"].append(r["stat_year"])
        d["values"].append(r["value"])

    structure = {
        "reserve": reserve,
        "money_mult": money_mult,
        "gdp_growth": {
            "empty": not gdp_series,
            "countries": [{"country_code": c, "years": d["years"], "values": d["values"]}
                          for c, d in sorted(gdp_series.items())],
            "note": "全球 GDP 增速（IMF WEO，年度含预测），实体经济对货币的需求。",
        },
        "judgements": [
            {"topic": "经常账户余额", "title": "主要经济体贸易与收入净差额",
             "states": ["顺差扩大", "逆差扩大"]},
            {"topic": "资本账户流动", "title": "跨境投资与借贷方向",
             "states": ["资本净流入", "资本净流出"]},
            {"topic": "通胀预期", "title": "市场对未来物价的预期",
             "states": ["通胀预期锚定", "通胀预期脱锚上行"]},
        ],
        "note": (
            "外汇储备积累读 cn_reserve_monthly（中国外储/黄金）；货币乘数读美 M2 ÷ 基础货币；"
            "全球 GDP 读 IMF WEO。经常账户/资本账户（IMF BoP 季频滞后）与通胀预期（需 FRED breakeven）"
            "本期标注缺口，以定性判读卡呈现。"
        ),
    }

    # ===================================================================
    # 基本面 ④ 周期与长期趋势
    # ===================================================================
    # BIS GLI 离岸美元（严格 6 条件：3P × N × A × I × B × USD）
    gli_rows = query_all(
        "SELECT time_period, obs_value FROM global_liquidity_bis "
        "WHERE borrowers_cty='3P' AND borrowers_sector='N' AND lenders_sector='A' "
        "AND l_pos_type='I' AND l_instr='B' AND unit_measure='USD' "
        "ORDER BY time_period")
    gli = {
        "dates": [str(r["time_period"]) for r in gli_rows],
        # 源单位百万美元 → 折十亿美元（÷1000）
        "values": [round(float(r["obs_value"]) / 1000.0, 1) for r in gli_rows],
        "latest": round(float(gli_rows[-1]["obs_value"]) / 1000.0, 1) if gli_rows else None,
        "latest_date": str(gli_rows[-1]["time_period"]) if gli_rows else None,
    }

    cycle = {
        "gli": gli,
        "judgements": [
            {"topic": "全球流动性周期", "title": "GLI 约 65 个月（5.5 年）振荡周期位置",
             "states": ["扩张期", "扩张末端/边际下行"]},
            {"topic": "债务再融资大周期", "title": "全球债务到期与再融资压力",
             "states": ["再融资压力缓释", "集中到期、再融资压力大"]},
            {"topic": "去美元化进程", "title": "各国减持美元资产、储备多元化",
             "states": ["美元地位稳固", "去美元化加速"]},
            {"topic": "地缘政治与制裁", "title": "冲突/制裁/资产冻结对流动性传导",
             "states": ["传导渠道通畅", "渠道被切断、货币格局重塑"]},
        ],
        "note": (
            "全球流动性周期读 BIS GLI（离岸美元信贷，官方口径，严格 6 条件）。"
            "债务再融资/去美元化（COFER 季频佐证）/地缘制裁为定性判读卡。"
        ),
    }

    return {
        "quantity": quantity,
        "price": price,
        "transmission": transmission,
        "central_bank": central_bank,
        "fiscal": fiscal,
        "structure": structure,
        "cycle": cycle,
        "note": (
            "全球货币：技术面（数量指标/价格指标/传导与市场反馈）复用 v2.1 三轨制已落地的"
            "货币流动性表（美 M1M2/美联储资产负债表/纽约联储 SOFR/欧元区 M2/中国 M0M1M2/央行"
            "资产负债表/外储/BIS GLI + 政策利率/DXY/VIX/商品）。合成口径（全球 M2 汇总、美元净"
            "流动性）标「合成口径·非官方」；TED/LIBOR-OIS/SOFR-IORB/XCCY/MOVE/EMBI/新兴资本流/"
            "COFER/日 M2/欧日央行资产负债表等免费源不可得，已诚实标注缺口。单位统一折十亿美元。"
        ),
    }


def domestic_money() -> dict:
    """国内货币（投资环境·国内货币章节）：技术面 3 块 + 基本面 4 块。

    只读数据组装，不做结论。单位约定：货币总量统一「亿元」（与央行公布口径一致），
    利率统一「%」，比率统一「倍/百分点」。免费源停更（社融分项/信贷结构/存款总量）
    与季频披露（超额准备金率/五篇大文章）如实标注缺口，不伪装成有序列。
    """
    # ===================================================================
    # 技术面 ① 数量指标 —— 货币供应「总水位」
    # ===================================================================
    m2 = _env_monthly_series(
        "SELECT stat_month, m2 AS v FROM cn_liquidity_monthly "
        "WHERE m2 IS NOT NULL ORDER BY stat_month", col="v")
    m2_yoy = _env_monthly_series(
        "SELECT stat_month, m2_yoy AS v FROM cn_liquidity_monthly "
        "WHERE m2_yoy IS NOT NULL ORDER BY stat_month", col="v")
    m1 = _env_monthly_series(
        "SELECT stat_month, m1 AS v FROM cn_liquidity_monthly "
        "WHERE m1 IS NOT NULL ORDER BY stat_month", col="v")
    m1_yoy = _env_monthly_series(
        "SELECT stat_month, m1_yoy AS v FROM cn_liquidity_monthly "
        "WHERE m1_yoy IS NOT NULL ORDER BY stat_month", col="v")
    m0 = _env_monthly_series(
        "SELECT stat_month, m0 AS v FROM cn_liquidity_monthly "
        "WHERE m0 IS NOT NULL ORDER BY stat_month", col="v")
    m0_yoy = _env_monthly_series(
        "SELECT stat_month, m0_yoy AS v FROM cn_liquidity_monthly "
        "WHERE m0_yoy IS NOT NULL ORDER BY stat_month", col="v")
    m1_m2_gap = _env_monthly_series(
        "SELECT stat_month, m1_m2_gap AS v FROM cn_liquidity_monthly "
        "WHERE m1_m2_gap IS NOT NULL ORDER BY stat_month", col="v")

    # 货币乘数 = M2 ÷ 基础货币（派生口径，央行资产负债表 reserve_money）
    mm_rows = query_all(
        "SELECT l.stat_month, l.m2, c.reserve_money FROM cn_liquidity_monthly l "
        "JOIN cn_cb_balance_monthly c ON l.stat_month=c.stat_month "
        "WHERE l.m2 IS NOT NULL AND c.reserve_money IS NOT NULL AND c.reserve_money>0 "
        "ORDER BY l.stat_month")
    money_mult = {
        "dates": [str(r["stat_month"]) for r in mm_rows],
        "values": [round(float(r["m2"]) / float(r["reserve_money"]), 2) for r in mm_rows],
        "latest": round(float(mm_rows[-1]["m2"]) / float(mm_rows[-1]["reserve_money"]), 2)
        if mm_rows else None,
        "latest_date": str(mm_rows[-1]["stat_month"]) if mm_rows else None,
    }

    # 社融（akshare macro_china_shrzgm 实测停更在 2026-04，诚实标注）
    shrzgm = _env_monthly_series(
        "SELECT stat_month, shrzgm AS v FROM cn_liquidity_monthly "
        "WHERE shrzgm IS NOT NULL ORDER BY stat_month", col="v")

    quantity = {
        "m2": m2, "m2_yoy": m2_yoy,
        "m1": m1, "m1_yoy": m1_yoy,
        "m0": m0, "m0_yoy": m0_yoy,
        "m1_m2_gap": m1_m2_gap,
        "money_mult": money_mult,
        "shrzgm": shrzgm,
        "excess_reserve": {
            "empty": True,
            "note": "超额准备金率为季频披露（央行货币政策执行报告，最新 2026Q1 为 1.4%），"
                    "无免费日/月频稳定序列，本期以静态参考值呈现。",
        },
    }

    # ===================================================================
    # 技术面 ② 价格指标 —— 资金「成本」
    # ===================================================================
    dr001 = _env_series(
        "SELECT trade_date, dr001 AS v FROM dr_repo_daily "
        "WHERE dr001 IS NOT NULL ORDER BY trade_date", n=800)
    dr007 = _env_series(
        "SELECT trade_date, dr007 AS v FROM dr_repo_daily "
        "WHERE dr007 IS NOT NULL ORDER BY trade_date", n=800)
    shibor_on = _env_series(
        "SELECT trade_date, shibor_on AS v FROM interbank_rate_daily "
        "WHERE shibor_on IS NOT NULL ORDER BY trade_date", n=800)
    shibor_1w = _env_series(
        "SELECT trade_date, shibor_1w AS v FROM interbank_rate_daily "
        "WHERE shibor_1w IS NOT NULL ORDER BY trade_date", n=800)
    lpr_1y = _env_series(
        "SELECT trade_date, lpr_1y AS v FROM interbank_rate_daily "
        "WHERE lpr_1y IS NOT NULL ORDER BY trade_date", n=800)
    lpr_5y = _env_series(
        "SELECT trade_date, lpr_5y AS v FROM interbank_rate_daily "
        "WHERE lpr_5y IS NOT NULL ORDER BY trade_date", n=800)
    bond_10y = _env_series(
        "SELECT trade_date, cn_bond_10y AS v FROM bond_profit_daily "
        "WHERE cn_bond_10y IS NOT NULL ORDER BY trade_date", n=800)
    bond_2y = _env_series(
        "SELECT trade_date, cn_bond_2y AS v FROM bond_profit_daily "
        "WHERE cn_bond_2y IS NOT NULL ORDER BY trade_date", n=800)
    bond_spread = _env_series(
        "SELECT trade_date, cn_bond_10y_2y_spread AS v FROM bond_profit_daily "
        "WHERE cn_bond_10y_2y_spread IS NOT NULL ORDER BY trade_date", n=800)
    # 逆回购利率（利率走廊中枢，最新 1.40%）
    omo_rate_rows = query_all(
        "SELECT op_rate FROM cn_omo_daily WHERE op_type='reverse_repo' "
        "AND op_rate IS NOT NULL ORDER BY trade_date DESC LIMIT 1")

    price = {
        "dr001": dr001,
        "dr007": dr007,
        "shibor_on": shibor_on,
        "shibor_1w": shibor_1w,
        "lpr_1y": lpr_1y,
        "lpr_5y": lpr_5y,
        "bond_10y": bond_10y,
        "bond_2y": bond_2y,
        "bond_spread": bond_spread,
        "reverse_repo_rate": float(omo_rate_rows[0]["op_rate"]) if omo_rate_rows else None,
        "deposit_lending_benchmark": {
            "note": "存贷款基准利率自 2015-10 最后一次调整后已超 10 年未变，"
                    "市场预计将逐步退出历史舞台（由 LPR/政策利率取代）。",
        },
    }

    # ===================================================================
    # 技术面 ③ 传导与市场反馈
    # ===================================================================
    usd_mid = _env_series(
        "SELECT trade_date, mid_price AS v FROM currency_boc_daily "
        "WHERE currency='USD' AND mid_price IS NOT NULL ORDER BY trade_date", n=800)
    # 存款：库内仅有准货币 + 其他存款（「人民币存款余额总量」口径缺）
    other_deposit = _env_monthly_series(
        "SELECT stat_month, other_deposit AS v FROM cn_liquidity_monthly "
        "WHERE other_deposit IS NOT NULL ORDER BY stat_month", col="v")
    quasi_money = _env_monthly_series(
        "SELECT stat_month, quasi_money AS v FROM cn_liquidity_monthly "
        "WHERE quasi_money IS NOT NULL ORDER BY stat_month", col="v")
    fx_reserve_usd_t = _env_monthly_series(
        "SELECT stat_month, fx_reserve_usd AS v FROM cn_reserve_monthly "
        "WHERE fx_reserve_usd IS NOT NULL ORDER BY stat_month", col="v")

    transmission = {
        "usd_mid": usd_mid,
        "fx_reserve_usd": fx_reserve_usd_t,
        "other_deposit": other_deposit,
        "quasi_money": quasi_money,
        "credit_structure": {
            "empty": True,
            "note": "信贷结构（企业 vs 住户）分项依赖央行金融机构信贷收支表，"
                    "免费源无稳定月度序列；库内仅有信贷总量（credit_cum）。",
        },
        "direct_financing": {
            "empty": True,
            "note": "直接融资占比需社融分项派生，而社融分项免费源已停更（2026-04），"
                    "无法稳定计算，本期定性说明。",
        },
        "five_articles": {
            "empty": True,
            "note": "「五篇大文章」贷款（科技/绿色/普惠/养老/数字）增速为央行季度/半年度报告披露，"
                    "无免费月度稳定序列，本期定性呈现。",
        },
    }

    # ===================================================================
    # 基本面 ① 央行政策与制度
    # ===================================================================
    rrr_large = query_all(
        "SELECT rrr_large FROM cn_liquidity_monthly WHERE rrr_large IS NOT NULL "
        "ORDER BY stat_month DESC LIMIT 1")
    rrr_small = query_all(
        "SELECT rrr_small FROM cn_liquidity_monthly WHERE rrr_small IS NOT NULL "
        "ORDER BY stat_month DESC LIMIT 1")

    central_bank = {
        "stance": {
            "note": "货币政策基调为「适度宽松」，把促进经济稳定增长、物价合理回升作为重要考量，"
                    "发挥增量政策与存量政策的集成效应。",
        },
        "rrr_large": float(rrr_large[0]["rrr_large"]) if rrr_large else None,
        "rrr_small": float(rrr_small[0]["rrr_small"]) if rrr_small else None,
        "reverse_repo_rate": price["reverse_repo_rate"],
        "rate_corridor": {
            "note": "以 DR001 为核心、利率走廊收窄；2026-06 优化隔夜正逆回购操作机制，"
                    "当前隔夜利率已成债市短端定价基准。",
        },
        "structural_tools": {
            "note": "结构性货币政策工具支持重点领域与薄弱环节；2026 年初各类工具利率下调 25BP，"
                    "为银行体系节约年度利息成本约 142.5 亿元。",
        },
    }

    # ===================================================================
    # 基本面 ② 财政政策与债务
    # ===================================================================
    gov_deposit = _env_monthly_series(
        "SELECT stat_month, gov_deposit AS v FROM cn_cb_balance_monthly "
        "WHERE gov_deposit IS NOT NULL ORDER BY stat_month", col="v")

    fiscal = {
        "gov_deposit": gov_deposit,
        "coordination": {
            "note": "加强与财政政策在贴息、担保和风险成本分担等方面的协同配合，放大政策效果。",
        },
        "gov_bond": {
            "note": "政府债券是社融的重要支撑：前三季度政府债券净融资约 10.3 万亿元，"
                    "余额同比增长 13.7%。",
        },
        "debt_resolution": {
            "note": "持续推进金融支持融资平台债务风险化解，坚持市场化、法治化原则。",
        },
    }

    # ===================================================================
    # 基本面 ③ 国际收支与结构性因素
    # ===================================================================
    fx_reserve = _env_monthly_series(
        "SELECT stat_month, fx_reserve AS v FROM cn_cb_balance_monthly "
        "WHERE fx_reserve IS NOT NULL ORDER BY stat_month", col="v")
    fx_reserve_usd = _env_monthly_series(
        "SELECT stat_month, fx_reserve_usd AS v FROM cn_reserve_monthly "
        "WHERE fx_reserve_usd IS NOT NULL ORDER BY stat_month", col="v")

    structure = {
        "fx_reserve": fx_reserve,
        "fx_reserve_usd": fx_reserve_usd,
        "usd_mid": usd_mid,
        "money_creation": {
            "note": "货币创造模式正从信贷扩张转向直接融资：居民与房企加杠杆意愿偏弱，"
                    "传统信贷对货币派生的贡献收缩，政府债券扩容和企业直接融资成为新渠道。",
        },
        "economic_transition": {
            "note": "房地产、融资平台信贷需求回落；新质生产力领域倾向债券、股权等差异化融资方式。",
        },
    }

    # ===================================================================
    # 基本面 ④ 周期与长期趋势
    # ===================================================================
    cycle = {
        "m2_gdp": {
            "empty": True,
            "note": "M2/GDP 比率需 GDP 绝对值，库内 global_macro_annual 仅有增速/债务占比，"
                    "无绝对 GDP 水平，故无法直接序列化；该比率长期攀升反映银行主导的金融深化特征。",
        },
        "mpa": {
            "note": "M1/GDP、M2/GDP、M1-M2 增速差均可作为衡量金融「脱实向虚」的挂钩变量，"
                    "其中 M2/GDP 可测性、可控性最优（宏观审慎评估体系 MPA）。",
        },
        "velocity": {
            "note": "货币供应量多寡需结合物价、名义 GDP 增长、资产价格变化综合判断，"
                    "而非单一依赖货币数据（货币流通速度）。",
        },
        "digital_currency": {
            "note": "数字货币投放量的参考中枢区间为年度真实 GDP 的 (0, 12.55%)，"
                    "此区间内不会改变货币体系乘数机制。",
        },
    }

    return {
        "quantity": quantity,
        "price": price,
        "transmission": transmission,
        "central_bank": central_bank,
        "fiscal": fiscal,
        "structure": structure,
        "cycle": cycle,
        "note": (
            "国内货币：技术面（数量指标/价格指标/传导与市场反馈）复用 cn_liquidity_monthly/"
            "cn_cb_balance_monthly/interbank_rate_daily/bond_profit_daily/currency_boc_daily/"
            "cn_reserve_monthly + 新采集 dr_repo_daily（DR001/DR007）。货币总量单位「亿元」，"
            "利率「%」。社融分项/信贷结构/存款总量免费源停更（2026-04），超额准备金率/五篇大文章"
            "为季频披露，均已诚实标注缺口，不伪装成有序列。"
        ),
    }
