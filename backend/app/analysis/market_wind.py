#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""分析模块：市场风向（首发跟踪型模块，数据源 market_style_daily + dc_index_market）

KPI 结论规则：
- 风偏分数：与 5 个交易日前对比 → 走扩/收敛；正=偏进攻，负=偏防守
- 剪刀差：>0 小盘占优 / <0 大盘占优（叠加走扩/收敛）
- 情绪温度：证券公司 − 中证全指 20 日超额；正=情绪偏暖
- 政策敏感：中证全指房地产 − 中证全指 20 日超额；正=政策板块占优
- 大势位置（250 日分位）：>=80 高位 / >=60 偏高 / 40~60 中位 / 20~40 偏低 / <=20 低位

分位与极值（2026-09-14 新增）：
- 每个 KPI 附 `pct` = 当前值在近 250 个交易日中的分位（0=区间最低，100=最高）。
  绝对 pp 跨期不可比（2005 年的 5pp 与现在的 5pp 意义不同），只有分位才能判断「是否极端」。
- `highlight` = 分位进入极值区（<=10 或 >=90），前端用金色渲染（「蓝骨金魂」体系里金色专供亮点信号）。
- `bench_pos_pct` 自身即 250 日分位，不再二次求分位（否则是重复信息）。

风险调整（2026-09-15 新增）：
- 「风偏分数」与「大小盘剪刀差」两个 KPI 另附 `adj` = 收益差 ÷ 其自身近 250 日滚动标准差。
- 与 `pct` 分工不同：`pct` 答「在近一年排第几」（纯相对排位，会被区间选择影响），
  `adj` 答「偏离自身风险尺度几个单位」（含幅度、可跨期比较）。口径见 ADJ_NOTE。
- 数据来自 market_style_daily 的 `risk_appetite_adj20` / `scissors_adj20`。

滞后检测：
- `stale_sessions` = 本表 as_of 之后还走出了几个交易日（用 stock_market_daily 当日历）。
  0 = 最新；>0 说明风格表落后于行情，前端把「数据截至」标成琥珀色（设计规范 §2.2）。

市场宽度（2026-09-14 新增）：
- `breadth`：个股涨跌家数/涨停跌停/站上 MA20·MA60 占比/60 日新高新低/腾落线 ADL，
  以及 `score`（三个占比型指标的均值，0~100）与 `pct`（上涨占比的近一年分位）。
- `breadth_trend`：上述指标的近 N 日序列，供前端画宽度走势。
- 为什么需要：原有 19 列全是「指数之间比收益」，测不到「上涨是否普遍」——
  指数被权重股主导，指数涨而多数个股跌即为虚涨。
- 列由 market_style_sync 计算；首次采集前不存在，本模块用 `_breadth_ready()` 优雅降级为 None。

交叉印证（2026-09-15 新增，P1）：
- `cross_checks`：6 项「股票市场内维度 × 另一个独立维度」的比对（杠杆/股债/股商/口径/微观/量价），
  用来发现**背离**——五类参照系里唯一此前完全空白的一类（详见 cross_check.py docstring）。
- `cross_note`：交叉印证框架说明，随响应下发供前端直接展示（前端只透传不手抄）。
"""
from __future__ import annotations

import logging

from ..db import query_all
from . import cross_check
from ._cache import ttl_cache

logger = logging.getLogger(__name__)

# ERP 卡片口径说明（随响应下发给前端做 tooltip，前端只透传不手抄）
ERP_HINT = (
    "ERP（股权风险溢价）= 盈利收益率 − 无风险利率 = 100 ÷ 沪深300 滚动市盈率 − 10Y 国债收益率。"
    "回答「买股票相对买国债多拿到的补偿够不够」——单看利率或单看 PE 都答不了这个问题。"
    "分位 ≥90% 通常对应中长期底部区域，≤10% 对应泡沫区。"
    "⚠️ 分位窗口是**近 250 个月末**（乐咕月末序列，约 20 年），不是 250 个交易日。"
    "⚠️ 估值腿用乐咕口径；中证官网口径同日滚动 PE 系统性更高（实测 16.92 vs 12.68），两套只可同源纵向比。"
)

# 风险调整口径说明（随响应下发给前端做 tooltip，避免前后端各抄一份口径）
ADJ_NOTE = (
    "风险调整 = 收益差 ÷ 其自身近 250 日滚动标准差（0 = 两腿同收益，±1 = 偏离自身一个典型波动单位）。"
    "两条腿波动率并不对称——实测 σ(科技成长)/σ(股息防守) 中位约 2.2 倍，差值波动被高波动腿主导，"
    "故绝对 pp 跨期不可比：2024 初小盘股灾 diff −16.1（σ 4.9）与当前 diff −12.0（σ 12.1）看似相当，"
    "风险调整后分别为 −3.26 与 −0.99，信号强度差 3 倍。"
)

# 六组展示顺序与中文名
GROUP_LABELS = [
    ("bench", "市场基准"),
    ("size", "市值风格"),
    ("tech", "科技成长"),
    ("sent", "情绪温度"),
    ("div", "股息防守"),
    ("pol", "政策周期"),
]

SIZE_INDICES = [
    ("000016", "上证50", "超大盘"),
    ("000300", "沪深300", "大盘"),
    ("000905", "中证500", "中盘"),
    ("000852", "中证1000", "小盘"),
    ("932000", "中证2000", "微盘"),
]


def _num(v) -> float | None:
    return None if v is None else round(float(v), 2)


def _latest_style_row(as_of: str | None = None) -> dict | None:
    if as_of:
        rows = query_all(
            "SELECT * FROM market_style_daily WHERE trade_date <= %s "
            "ORDER BY trade_date DESC LIMIT 1", [as_of]
        )
    else:
        rows = query_all("SELECT * FROM market_style_daily ORDER BY trade_date DESC LIMIT 1")
    return rows[0] if rows else None


def _style_history(days: int, as_of: str | None = None) -> list[dict]:
    """近 days 个交易日的风格序列（含六组收益，供轮动时序与热力矩阵共用一次查询）"""
    sel = ("SELECT trade_date, scissors_20, risk_appetite_20, "
           + ", ".join(f"ret_{g}_20" for g, _ in GROUP_LABELS))
    if as_of:
        return query_all(
            f"{sel} FROM market_style_daily WHERE trade_date <= %s "
            "ORDER BY trade_date DESC LIMIT %s", [as_of, days]
        )
    return query_all(
        f"{sel} FROM market_style_daily ORDER BY trade_date DESC LIMIT %s", [days]
    )


def _offset_style_row(offset: int, as_of: str | None = None) -> dict | None:
    if as_of:
        rows = query_all(
            "SELECT * FROM market_style_daily WHERE trade_date <= %s "
            "ORDER BY trade_date DESC LIMIT 1 OFFSET %s", [as_of, offset]
        )
    else:
        rows = query_all(
            "SELECT * FROM market_style_daily ORDER BY trade_date DESC LIMIT 1 OFFSET %s",
            [offset],
        )
    return rows[0] if rows else None


def _latest_indices(as_of: str | None = None) -> list[dict]:
    """21 只指数快照 + 20 日收益（as_of 非空时取该日及之前，用于历史回放）

    2026-09-14：由「逐行相关子查询 + OFFSET 19」改为窗口函数。原写法每行都要重跑一次
    子查询取「当日之前第 20 个交易日收盘」，SQL 又脆又难读；LAG(close, 20) 与之等价
    （当前行往前数 20 行 = 当日之前第 20 个交易日），MySQL 8.2 支持。
    """
    cond = "WHERE i.trade_date <= %s" if as_of else ""
    return query_all(
        f"""
        SELECT t.index_code, t.index_name, t.index_group, t.group_desc,
               t.trade_date, t.close, t.change_pct,
               CASE WHEN t.c20 > 0 THEN (t.close / t.c20 - 1) * 100 END AS ret_20
        FROM (
            SELECT i.index_code, i.index_name, i.index_group, i.group_desc,
                   i.trade_date, i.close, i.change_pct,
                   LAG(i.close, 20) OVER (PARTITION BY i.index_code ORDER BY i.trade_date) AS c20,
                   ROW_NUMBER() OVER (PARTITION BY i.index_code ORDER BY i.trade_date DESC) AS rn
            FROM dc_index_market i
            {cond}
        ) t
        WHERE t.rn = 1
        ORDER BY t.index_group, t.index_code
        """,
        [as_of] if as_of else None,
    )


BREADTH_COLS = [
    "breadth_total", "breadth_up", "breadth_down", "breadth_up_ratio",
    "breadth_adl", "limit_up", "limit_down",
    "above_ma20_pct", "above_ma60_pct", "new_high60", "new_low60", "hl_diff60",
]


def _breadth_ready() -> bool:
    """market_style_daily 是否已具备宽度列（首次采集前不存在，须优雅降级）"""
    rows = query_all(
        "SELECT COUNT(*) AS n FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'market_style_daily' "
        "AND COLUMN_NAME = 'breadth_up_ratio'"
    )
    return bool(rows and rows[0]["n"])


def _breadth_history(days: int, as_of: str | None = None) -> list[dict]:
    """宽度与量能的近 N 日序列（供宽度走势图）。

    as_of 非空时只取该日及之前（历史回放）；否则取全表最新。
    amount_ratio_20 亦在此取，与宽度同图展示「放量下跌 / 缩量止跌」的区别。
    """
    cond = "breadth_up_ratio IS NOT NULL"
    args: list = []
    if as_of:
        cond += " AND trade_date <= %s"
        args.append(as_of)
    args.append(days)
    return query_all(
        "SELECT trade_date, breadth_up_ratio, breadth_adl, above_ma20_pct, "
        "       above_ma60_pct, hl_diff60, market_amount, amount_ratio_20 "
        f"FROM market_style_daily WHERE {cond} "
        "ORDER BY trade_date DESC LIMIT %s",
        args,
    )


def _standardize(cols: list[str], window: int = 250, as_of: str | None = None) -> dict:
    """对若干列做「近 window 日」标准化：分位(%) 与 z-score。

    为什么需要：绝对 pp / % 跨时间不可比 —— 2005 年的 5pp 与现在的 5pp 意义完全不同。
    只有分位（「现在处于近一年的什么位置」）和 z-score（「偏离均值几个标准差」）才能判断极端。
    cols 由本模块内部白名单传入，不接受外部输入，故列名可拼接。
    """
    existing = {
        r["COLUMN_NAME"] for r in query_all(
            "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'market_style_daily'"
        )
    }
    usable = [c for c in cols if c in existing]
    if not usable:
        return {}
    sel = ", ".join(usable)
    cond = "trade_date <= %s" if as_of else "trade_date IS NOT NULL"
    cond += " AND trade_date <= (SELECT MAX(trade_date) FROM market_style_daily)"
    args: list = [as_of] if as_of else []
    args.append(window)
    rows = query_all(
        f"SELECT {sel} FROM market_style_daily "
        f"WHERE {cond} ORDER BY trade_date DESC LIMIT %s",
        args,
    )
    out: dict = {}
    for c in usable:
        vals = [float(r[c]) for r in rows if r[c] is not None]
        if len(vals) < 20:
            out[c] = {"pct": None, "z": None}
            continue
        cur_v = vals[0]                       # 行是按日期倒序取的，第 0 行即最新
        le = sum(1 for v in vals if v <= cur_v)
        mean = sum(vals) / len(vals)
        var = sum((v - mean) ** 2 for v in vals) / len(vals)
        std = var ** 0.5
        out[c] = {
            "pct": round(le / len(vals) * 100, 1),
            "z": round((cur_v - mean) / std, 2) if std > 1e-12 else None,
        }
    return out


def _breadth_status(up_ratio: float | None) -> str:
    """上涨家数占比 → 市场宽度结论词"""
    if up_ratio is None:
        return "--"
    if up_ratio >= 70:
        return "普涨"
    if up_ratio >= 55:
        return "偏多"
    if up_ratio >= 45:
        return "分化"
    if up_ratio >= 30:
        return "偏空"
    return "普跌"


def _pctile(col: str, window: int = 250, as_of: str | None = None) -> float | None:
    """当前值在近 window 个交易日中的分位（0~100）

    ⚠️ col 由本模块内部以字面量传入（白名单列名），不接受外部输入，故可用 f-string 拼接。

    2026-09-15 修复：原实现把同一个 {cond} 在 SQL 里拼了 3 处（子查询 a / 取当日值 /
    子查询 b），带 as_of 时每处各产生 1 个 %s，共 5 个占位符，而只传了 4 个参数 →
    历史回放必抛 `TypeError: not enough arguments for format string`（不带 as_of 时
    cond 无 %s 恰好凑巧能跑，属典型的「只在某个分支炸」）。
    现改为「取窗口序列 → Python 内算分位」，占位符数量与参数一一对应，不再随 cond 复用而漂移。
    """
    cond = f"{col} IS NOT NULL"
    args: list = []
    if as_of:
        cond += " AND trade_date <= %s"
        args.append(as_of)
    rows = query_all(
        f"SELECT {col} AS v FROM market_style_daily WHERE {cond} "
        "ORDER BY trade_date DESC LIMIT %s",
        args + [window],
    )
    vals = [float(r["v"]) for r in rows if r["v"] is not None]
    if not vals:
        return None
    cur_v = vals[0]  # ORDER BY trade_date DESC → 首行即最新（或 as_of 当日）
    le = sum(1 for v in vals if v <= cur_v)
    return round(le / len(vals) * 100, 1)


def _is_extreme(pct: float | None) -> bool:
    """分位进入极值区（<=10 或 >=90）→ 前端用金色高亮"""
    return pct is not None and (pct <= 10 or pct >= 90)


def _scale(pct: float | None, label: str) -> dict | None:
    """KPI 数字的分位刻度（2026-09-19 新增）

    为什么需要：绝对 pp 跨期不可比（2008 年的 5pp 与现在的 5pp 意义完全不同），
    分位才能回答「是否极端」—— 但只给一个「12%」的数字，读者仍要自己换算成高低。
    刻度条把「这个数在 0~100 轴上的位置」直接画出来，数字与位置的对应关系一眼可见。

    ⚠️ `label` 必须随分位一起下发，**不能由前端写死「近一年」**：ERP 的分位窗口是
       「近 250 个月末」（乐咕月末序列，约 20 年），前端写死会把月频分位说成日频 ——
       这是实打实的口径错误，不是文案问题。
    ⚠️ 极值配色不在后端重复定义：KPI 上已有 `highlight`（分位 <=10 或 >=90），
       前端把 highlight 映射成金即可，避免同一个口径两处维护。
    """
    if pct is None:
        return None
    return {"pct": pct, "label": label}


def _stale_sessions(as_of) -> int:
    """as_of 之后还走出了几个交易日（0 = 最新）。用日线表当日历，避免周末误判。"""
    rows = query_all(
        "SELECT COUNT(DISTINCT trade_date) AS n FROM stock_market_daily WHERE trade_date > %s",
        [as_of],
    )
    return int(rows[0]["n"]) if rows else 0


def _risk_status(cur: float | None, prev: float | None) -> str:
    if cur is None:
        return "--"
    if prev is None:
        return "偏进攻" if cur > 0 else "偏防守"
    delta = cur - prev
    if cur > 0:
        return "风偏升温" if delta > 0 else "进攻放缓"
    return "防守加深" if delta < 0 else "防守松动"


def _scissors_status(cur: float | None, prev: float | None) -> str:
    if cur is None:
        return "--"
    if prev is None:
        return "小盘占优" if cur > 0 else "大盘占优"
    delta = cur - prev
    side = "小盘占优" if cur > 0 else "大盘占优"
    trend = "走扩" if (delta > 0) == (cur > 0) and delta != 0 else "收敛"
    return f"{side}·{trend}" if cur != 0 else "均衡"


def _pos_status(v: float | None) -> str:
    if v is None:
        return "--"
    if v >= 80:
        return "高位"
    if v >= 60:
        return "偏高"
    if v >= 40:
        return "中位"
    if v >= 20:
        return "偏低"
    return "低位"


def _sent_status(cur: float | None, prev: float | None) -> str:
    """情绪温度（证券公司 − 中证全指 20 日超额）：正 = 市场情绪偏暖"""
    if cur is None:
        return "--"
    if prev is None:
        return "情绪偏暖" if cur > 0 else "情绪偏冷"
    rising = cur - prev > 0
    if cur > 0:
        return "情绪升温" if rising else "热度回落"
    return "情绪修复" if rising else "情绪转冷"


def _policy_status(cur: float | None, prev: float | None) -> str:
    """政策敏感（中证全指房地产 − 中证全指 20 日超额）：正 = 政策敏感板块占优

    用词注意：「政策降温」易被读成「政策收紧」，而此处描述的是**政策敏感板块的相对超额在收窄**，
    故改用「政策退潮」，与 _sent_status 的「热度回落」保持同一层语义。
    """
    if cur is None:
        return "--"
    if prev is None:
        return "政策占优" if cur > 0 else "政策拖累"
    rising = cur - prev > 0
    if cur > 0:
        return "政策走强" if rising else "政策退潮"
    return "政策企稳" if rising else "政策拖累"


def _volume_status(r: float | None) -> str:
    """成交额 / 20 日均值（%）→ 量能结论词"""
    if r is None:
        return "--"
    if r >= 130:
        return "显著放量"
    if r >= 110:
        return "温和放量"
    if r >= 90:
        return "量能平稳"
    if r >= 70:
        return "温和缩量"
    return "显著缩量"


def _heat_matrix(hist: list[dict], buckets: int = 12) -> dict:
    """六组 × 时间 热力矩阵（批次 4）

    只有一个时点的六条横条看不出「哪一组在持续走强/走弱」。把 trend_days 等分成 buckets 段，
    每段取组内 20 日收益的均值，就能读出趋势的持续性（单时点条 → 时间×分组矩阵）。
    hist 须为按日期升序的列表。
    """
    n = len(hist)
    if n < buckets * 2:
        return {"cols": [], "rows": []}
    size = n / buckets
    spans = []
    for b in range(buckets):
        s = int(round(b * size))
        e = max(int(round((b + 1) * size)), s + 1)
        e = min(e, n)
        spans.append((s, e))
    cols = [f"{hist[s]['trade_date']}~{hist[e - 1]['trade_date']}" for s, e in spans]
    rows = []
    for g, label in GROUP_LABELS:
        vals = []
        for s, e in spans:
            seg = [float(r[f"ret_{g}_20"]) for r in hist[s:e] if r.get(f"ret_{g}_20") is not None]
            vals.append(round(sum(seg) / len(seg), 2) if seg else None)
        rows.append({"group": label, "values": vals})
    return {"cols": cols, "rows": rows}


def _sign_bands(dates: list[str], values: list, min_len: int = 3) -> list[dict]:
    """把「剪刀差正负」转成连续区间带（批次 4）

    双线图上只看曲线很难一眼数出「小盘占优持续了多久」。转成区间后可以直接铺底色。
    短于 min_len 的翻转视为噪声丢弃（否则 250 日会有几十段碎带，反而看不清）。
    """
    bands: list[dict] = []
    cur: dict | None = None
    for d, v in zip(dates, values):
        if v is None:
            if cur:
                bands.append(cur); cur = None
            continue
        side = 1 if v > 0 else -1
        if cur and cur["side"] == side:
            cur["end"] = d
            cur["days"] += 1
        else:
            if cur:
                bands.append(cur)
            cur = {"side": side, "start": d, "end": d, "days": 1}
    if cur:
        bands.append(cur)
    out = [b for b in bands if b["days"] >= min_len]
    for b in out:
        b["label"] = "小盘占优" if b["side"] > 0 else "大盘占优"
    return out


def _xcheck_series(days: int = 250, as_of: str | None = None) -> list[int]:
    """背离数的历史序列（**升序**），来自 `market_xcheck_daily`。

    ⚠️ 该表由 `xcheck_sync`（每工作日 22:30）与 `scripts/backfill_xcheck.py` 写入，
       **首次部署或回填前不存在** —— 此时必须优雅降级为「不给分位」，
       绝不能让卡片整块挂掉。故整体 fail-soft（表不存在 / 权限异常一律返回空序列）。
    """
    cond = "WHERE trade_date <= %s" if as_of else ""
    args: list = [as_of] if as_of else []
    try:
        rows = query_all(
            f"SELECT diverge_n FROM market_xcheck_daily {cond} "
            "ORDER BY trade_date DESC LIMIT %s",
            args + [days],
        )
    except Exception as e:                      # noqa: BLE001 —— 刻意 fail-soft，见 docstring
        logger.info("market_xcheck_daily 不可用，背离数分位降级：%s: %s", type(e).__name__, e)
        return []
    return [int(r["diverge_n"]) for r in reversed(rows) if r["diverge_n"] is not None]


def _diverge_stats(cur: int | None, series: list[int]) -> dict | None:
    """当前背离数在自身历史中的位置 —— 让「4 项背离」从计数变成信号。

    **为什么必须有这一步**（2026-09-19 实测，纠正了一个错误判断）：
      卡片原先直接报「7 项中 4 项背离」。用**近 16 个交易日**回放，得到序列
      `[4,3,4,3,2,3,3,4,4,4,4,4]` —— 看起来「4 就是常态」。但把窗口拉到 **250 日**：

          0 项 23 日 | 1 项 77 日 | 2 项 90 日（中位）| 3 项 43 日 | 4 项 16 日 | 5 项 1 日

      4 项实为 **99.6% 分位**（250 日中仅 1 日更多）—— 是**极值**，不是常态。
      短窗口回放恰好整段落在高位区，于是得出了完全相反的结论。
      → 教训：判「常态/异常」的窗口不能凭手感取，至少要覆盖一个完整市场周期（一年）。

    series 少于 20 个点时不判（样本不足以谈分位）。
    """
    if cur is None or len(series) < 20:
        return None
    le = sum(1 for v in series if v <= cur)
    return {
        "pct": round(le / len(series) * 100, 1),
        "days": len(series),
        "higher": sum(1 for v in series if v > cur),
        "median": sorted(series)[len(series) // 2],
    }


def _div_note(pct: float | None) -> str:
    """分位的定性词（让「99.6%」不用读者自己换算成高低）"""
    if pct is None:
        return ""
    if pct >= 90:
        return "，处极值区"
    if pct >= 75:
        return "，偏高"
    if pct <= 10:
        return "，处低位"
    if pct <= 25:
        return "，偏低"
    return "，属常态"


# 卡片判读条（2026-09-19）：卡片墙的「一句话结论」，由后端生成。
# 为什么放后端：本项目铁律是「口径随响应下发，前端只透传不手抄」（见 registry.py 第一原则）；
# 结论句若在前端拼装，会立刻产生第二份口径，日后必然漂移。
#
# 四个「别凭直觉改」的实测依据（2026-09-19，最后一条经 250 日全窗口复核）：
#   ✅ 背离项计数**加上自身历史分位后**可以进结论：计数本身（0~7）没有可比性，
#      放进近一年分布就变成信号 —— 实测 250 日分布为中位 2、当前 4 项处 **99.6% 分位**
#      （250 日中仅 1 日更多，见 _diverge_stats）。
#      ⚠️ 早先「4 是常态」的结论是用**16 日窗口**回放得出的，**已被推翻**：短窗口恰好整段
#      落在高位区。判常态/异常的窗口至少要覆盖一个完整市场周期，这是本次的教训。
#   ❌ ERP 分位不做唯一头条：估值分母是乐咕「近 250 个月末」序列（月频），实测 71.6 连续
#      14 个交易日不动 —— 在日频卡片上它几乎恒定，会退化成「永远偏便宜」。
#   ✅ 大势位置分位是日频活信号：实测近 16 日 18.2~39.4、极差 21.2pp。
# 故结论 = 位置（日频信号）+ 风险偏好方向（往哪走）+ 估值（慢变量锚）+ 内部背离（是否互相打架）。
#
# tone：位置 <=20 → 金（低位机会）；位置 >=80 或**背离数进 90% 分位** → 琥珀（提醒）；
# 其余蓝。实测背离数这条规则在近 250 日只点亮 17 天（4 项 16 天 + 5 项 1 天，6.8%）
# —— 亮得少才算信号。
def _card_verdict(bp, ra, erp_pct, xcheck: dict, div: dict | None = None) -> dict:
    items = xcheck.get("items") or []
    n_all = len(items)
    n_div = (xcheck.get("summary") or {}).get("diverge") or 0
    div_labels = [i.get("label") for i in items
                  if i.get("level") == "diverge" and i.get("label")]

    if bp is None:
        head = "位置数据未就绪"
    else:
        head = f"近一年{_pos_status(bp)}（{bp:.0f}% 分位）"
    head += "、风险偏好" + ("待定" if ra is None else ("偏防守" if ra < 0 else "偏进攻"))
    if erp_pct is None:
        head += "、估值数据未就绪"
    elif erp_pct >= 60:
        head += "、估值偏便宜"
    elif erp_pct <= 40:
        head += "、估值偏贵"
    else:
        head += "、估值中性"

    # 背离数必须与**自身历史**比才有意义 —— 单说「4 项」读者无从判断多不多。
    # 分位不可得时（market_xcheck_daily 尚未回填）退回纯计数表述，不阻断判读条。
    joined = " · ".join(div_labels)
    if not n_all:
        detail = ""
    elif div:
        extra = ""
        if div.get("higher") is not None:
            extra = f"、更高仅 {div['higher']} 日" if div["higher"] <= 3 else ""
        detail = (f"交叉印证 {n_div}/{n_all} 项背离 · 近一年 {div['pct']}% 分位"
                  f"{_div_note(div['pct'])}（中位 {div['median']} 项{extra}）"
                  f"：{joined or '无'}")
    else:
        detail = (f"交叉印证 {n_all} 项中 {n_div} 项背离：{joined}"
                  if n_div and joined else f"交叉印证 {n_all} 项，当前无背离项")

    if bp is not None and bp <= 20:
        tone = "opportunity"
    elif (bp is not None and bp >= 80) or (div and (div.get("pct") or 0) >= 90):
        # 背离数进 90% 分位 = 各维度互相打架到了历史罕见程度 → 提醒级（琥珀）
        tone = "caution"
    else:
        tone = "normal"
    return {"headline": head, "detail": detail, "tone": tone}


# 进程内 TTL 缓存（2026-09-19）：与「板块轮动」同一策略（见 _cache.py 边界说明）。
# market_style_daily 是日频盘后物化的，同一交易日内结果确定不变；而本接口会被
# 「分析研究总览页卡片墙」和「市场风向详情页」重复请求，且每次要跑 36 条查询 +
# 7 项交叉印证。实测：无缓存 1.55s → 有缓存首次 0.92s、后续命中 <1ms。
# 参数 trend_days / as_of 参与 key，切窗口与历史回放互不污染。
@ttl_cache(600)
def market_wind(trend_days: int = 250, as_of: str | None = None) -> dict:
    """市场风向模块数据装配（/api/analysis/market-wind）

    参数
    - trend_days：轮动时序 / 宽度走势 / 热力矩阵的回看交易日数
    - as_of：**历史回放锚点**（YYYY-MM-DD）。非空时全部查询只取该日及之前的数据，
      用于复盘「那一天风格与宽度长什么样」；为空则取全表最新。回放模式下不再报滞后。
    """
    cur_row = _latest_style_row(as_of)
    prev_row = _offset_style_row(5, as_of)
    if not cur_row:
        return {"as_of": None, "note": "market_style_daily 尚无数据，等待 market_style_sync 首跑"}

    data_as_of = str(cur_row["trade_date"])
    is_replay = bool(as_of)
    prev5 = {k: _num(prev_row.get(k)) if prev_row else None
             for k in ("risk_appetite_20", "scissors_20", "sentiment_20", "policy_excess_20")}

    # ---- 标准化（批次 3）：近 250 日分位 + z-score ----
    # 绝对 pp 跨期不可比（2005 年的 5pp 与现在的 5pp 意义不同），只有分位/z-score 能判断极端。
    std_cols = ([f"ret_{g}_20" for g, _ in GROUP_LABELS]
                + ["scissors_20", "risk_appetite_20", "sentiment_20", "policy_excess_20"]
                + (["breadth_up_ratio", "amount_ratio_20"] if _breadth_ready() else []))
    std = _standardize(std_cols, 250, as_of)

    def z_of(k: str):
        return (std.get(k) or {}).get("z")

    def p_of(k: str):
        return (std.get(k) or {}).get("pct")

    # KPI 结论区
    # 2026-09-14：3 → 5 项。补入 sentiment_20（情绪温度）与 policy_excess_20（政策敏感）——
    # 这两列 market_style_sync 每个交易日都在算（见该文件口径表）与设计规范 §4.1 的口径表，
    # 但视图层一直没接入，属「白算」。同时每项附近 250 日分位、z-score 与极值标记。

    # card_rank（2026-09-19）：**卡片墙只放这 3 个**（总览页据此挑，不再取数组前 3 个）。
    # 病灶：原先前端 slice(0,3) 按声明顺序截断，恰好把带分位的 bench_pos / erp 截掉，
    # 只留三个同类的「20 日动量」（风偏/剪刀差/情绪温度），语义高度重叠。
    # 选取标准 = **与判读条结论正交、各自回答一个不同的问题**：
    #   bench_pos 答「我在哪」／risk_appetite 答「资金往哪走」／erp 答「贵还是便宜」。
    # 未标注的项（scissors / sentiment / policy）仍完整出现在详情页，不丢信息。
    ra, sc = _num(cur_row.get("risk_appetite_20")), _num(cur_row.get("scissors_20"))
    se, po = _num(cur_row.get("sentiment_20")), _num(cur_row.get("policy_excess_20"))
    bp = _num(cur_row.get("bench_pos_pct"))
    # 风险调整版（2026-09-15）：差值 ÷ 其自身近 250 日滚动标准差。
    # 与 `pct` 分工不同——`pct` 答「在近一年排第几」，`adj` 答「偏离自身风险尺度几个单位」，
    # 后者不受「近一年恰好是牛是熊」影响（实测：原始值相关的分位 7.6% ↔ adj -0.99）。
    ra_adj, sc_adj = _num(cur_row.get("risk_appetite_adj20")), _num(cur_row.get("scissors_adj20"))
    pct_ra, pct_sc = _pctile("risk_appetite_20", 250, as_of), _pctile("scissors_20", 250, as_of)
    pct_se, pct_po = _pctile("sentiment_20", 250, as_of), _pctile("policy_excess_20", 250, as_of)

    # 股债性价比 ERP（2026-09-19 新增，P2）：交叉印证第 ⑦ 项与本 KPI 卡片共用同一份计算。
    # 取不到就整项优雅降级（value=None → 前端渲染 "--"），绝不让单点缺数据拖垮整页。
    try:
        _erp = cross_check.erp_snapshot(as_of)
    except Exception as e:                          # noqa: BLE001 —— 刻意 fail-soft
        logger.warning("ERP 快照计算失败，KPI 卡片降级：%s: %s", type(e).__name__, e)
        _erp = None
    erp_val = _erp["erp"] if _erp else None
    erp_pct = _erp["erp_pct"] if _erp else None
    if _erp:
        # ⚠️ 不再把「ERP 分位」写进 status：分位已由刻度条承载（scale.label「近 250 个月末」
        #    + 刻度文字里的百分数），写两遍既冗余、又会让 status 超出 KPI 盒子宽度被省略号
        #    截断（整行卡 6 列时每列仅 ~185px，2026-09-19 实测截断）。status 只留「分级 + 利率」。
        erp_status = f"{_erp['level_text']}｜10Y {_erp['bond_10y']}%"
    else:
        erp_status = "估值数据未就绪"

    # KPI 配色分三类（2026-09-19 定稿，见 registry.py 卡片墙契约第 ④ 条）：
    #   tone='updown'  —— 真正的行情涨跌数字（指数涨跌幅），红涨绿跌
    #   tone='diff'    —— **组间收益差 / 相对强弱**（风偏、剪刀差、超额），它不是「某个资产在涨跌」，
    #                     故用主色蓝 + 保留正负号。染红绿会让「防守占优」看起来像一条独立警报，
    #                     且卡片上一蓝一绿会被误读成两类指标。方向交给符号、status 文案与刻度条。
    #   tone='neutral' —— 分位 / 占比 / 离散度等无量纲量，主色蓝且不带正号
    # 底层原则：**卡片墙的颜色只表达「异常程度」，不表达方向**（亮得少才算信号），与 verdict 同源。
    kpis = [
        {"key": "risk_appetite", "card_rank": 2, "label": "风偏分数（20日）", "value": ra, "unit": "pp", "tone": "diff",
         "status": _risk_status(ra, prev5.get("risk_appetite_20")),
         "pct": pct_ra, "scale": _scale(pct_ra, "近一年"),
         "z": z_of("risk_appetite_20"), "highlight": _is_extreme(pct_ra), "anchor": "mw-trend",
         "adj": ra_adj,
         "hint": "科技成长组 − 股息防守组 等权20日收益差；正=偏进攻，负=偏防守"},
        {"key": "scissors", "card_rank": 4, "label": "大小盘剪刀差（20日）", "value": sc, "unit": "pp", "tone": "diff",
         "status": _scissors_status(sc, prev5.get("scissors_20")),
         "pct": pct_sc, "scale": _scale(pct_sc, "近一年"),
         "z": z_of("scissors_20"), "highlight": _is_extreme(pct_sc), "anchor": "mw-gradient",
         "adj": sc_adj,
         "hint": "(中证1000+中证2000) − (上证50+沪深300) 等权20日收益差；正=小盘占优"},
        {"key": "sentiment", "card_rank": 5, "label": "情绪温度（20日超额）", "value": se, "unit": "pp", "tone": "diff",
         "status": _sent_status(se, prev5.get("sentiment_20")),
         "pct": pct_se, "scale": _scale(pct_se, "近一年"),
         "z": z_of("sentiment_20"), "highlight": _is_extreme(pct_se), "anchor": "mw-heat",
         "hint": "证券公司 − 中证全指 20 日超额；正=券商跑赢，视为市场情绪偏暖"},
        {"key": "policy", "card_rank": 6, "label": "政策敏感（20日超额）", "value": po, "unit": "pp", "tone": "diff",
         "status": _policy_status(po, prev5.get("policy_excess_20")),
         "pct": pct_po, "scale": _scale(pct_po, "近一年"),
         "z": z_of("policy_excess_20"), "highlight": _is_extreme(pct_po), "anchor": "mw-heat",
         "hint": "中证全指房地产 − 中证全指 20 日超额；正=政策敏感板块占优"},
        # 大势位置的 value 本身就是 250 日分位，故 scale.pct 与 value 同值 ——
        # 刻度条在此不是「再算一个分位」，而是把已有的分位画到 0~100 轴上（数字→位置的直接映射）。
        {"key": "bench_pos", "card_rank": 1, "label": "大势位置（250日分位）", "value": bp, "unit": "%", "tone": "neutral",
         "status": _pos_status(bp), "pct": None, "scale": _scale(bp, "近 250 日"),
         "z": None, "highlight": _is_extreme(bp), "anchor": "mw-detail",
         "hint": "中证全指在近 250 日高低区间的分位，80+ 高位 / 20- 低位（本身即分位，不再二次求分位）"},
        # 股债性价比 ERP（2026-09-19 P2 落地）：估值分母此前完全缺失（stock_market_current 的 PE/PB
        # 全表为空），故这项当时做不出来、也没出现在页面上。index_valuation_sync 补齐后成立。
        # ⚠️ 分位窗口是「近 250 个月末」而非 250 个交易日 —— 长期估值分位本就该用长窗口，
        #    但不能塞进通用 pct 字段（前端会固定渲染成「近一年 N% 分位」），故写进 status 文案。
        # ⚠️ 不设 anchor：ERP 的论据在交叉印证面板里，不是本页独立图表。
        # 刻度 label 取乐咕序列的实际点数（「近 250 个月末」），由后端下发 ——
        # 前端写死「近一年」会把月频分位说成日频（见 _scale 的 ⚠️）。
        {"key": "erp", "card_rank": 3, "label": "股债性价比 ERP", "value": erp_val, "unit": "pp", "tone": "neutral",
         "status": erp_status, "pct": None,
         "scale": _scale(erp_pct, f"近 {_erp['samples']} 个月末" if _erp and _erp.get("samples") else "月末序列"),
         "z": None,
         "highlight": bool(erp_pct is not None and erp_pct >= 90), "anchor": None,
         "hint": ERP_HINT},
    ]

    # 六组收益热力条（附各自的近 250 日分位与 z-score —— 让「+3.5%」有可比基准）
    groups = [
        {"group": label, "ret_20": _num(cur_row.get(f"ret_{g}_20")),
         "ret_60": _num(cur_row.get(f"ret_{g}_60")),
         "ret_20_pct": p_of(f"ret_{g}_20"), "ret_20_z": z_of(f"ret_{g}_20")}
        for g, label in GROUP_LABELS
    ]

    # 大小盘五档梯度（最新收盘 + 当日涨跌 + 20 日收益）
    idx = _latest_indices(as_of)
    by_code = {r["index_code"]: r for r in idx}
    size_gradient = [
        {"code": c, "name": n, "desc": d,
         "ret_20": _num(by_code[c]["ret_20"]) if c in by_code else None,
         "change_pct": _num(by_code[c]["change_pct"]) if c in by_code else None}
        for c, n, d in SIZE_INDICES
    ]

    # 轮动时序（近 trend_days 个交易日）+ 区间底色带 + 六组热力矩阵（共用同一次查询）
    hist = _style_history(trend_days, as_of)
    hist.reverse()
    trend_dates = [str(r["trade_date"]) for r in hist]
    trend = {
        "dates": trend_dates,
        "scissors": [_num(r.get("scissors_20")) for r in hist],
        "risk_appetite": [_num(r.get("risk_appetite_20")) for r in hist],
        "bands": _sign_bands(trend_dates, [_num(r.get("scissors_20")) for r in hist]),
    }

    # 明细：21 只指数
    detail = [
        {
            "index_code": r["index_code"], "index_name": r["index_name"],
            "index_group": r["index_group"], "group_desc": r["group_desc"],
            "close": _num(r["close"]), "change_pct": _num(r["change_pct"]),
            "ret_20": _num(r["ret_20"]),
        }
        for r in idx
    ]

    # 市场宽度（2026-09-14 新增）：回答「上涨有没有普遍性」——指数由权重股主导，
    # 指数涨但 3000 只跌 = 虚涨，这是原 19 列纯「指数间收益差」完全测不到的维度。
    breadth = None
    volume = None
    breadth_trend = {"dates": [], "up_ratio": [], "adl": [], "above_ma20_pct": [], "hl_diff60": [],
                     "amount": [], "amount_ratio": []}
    if _breadth_ready():
        up_r = _num(cur_row.get("breadth_up_ratio"))
        a20 = _num(cur_row.get("above_ma20_pct"))
        a60 = _num(cur_row.get("above_ma60_pct"))
        # 宽度健康度：三个「占比型」指标的简单均值（0~100）。
        # 语义直观且可解释：多少股票在涨 / 站在 MA20 / MA60 上方，不做加权避免「看起来很深奥但说不清」。
        parts = [v for v in (up_r, a20, a60) if v is not None]
        breadth = {
            "total": cur_row.get("breadth_total"),
            "up": cur_row.get("breadth_up"),
            "down": cur_row.get("breadth_down"),
            "up_ratio": up_r,
            "adl": cur_row.get("breadth_adl"),
            "limit_up": cur_row.get("limit_up"),
            "limit_down": cur_row.get("limit_down"),
            "above_ma20_pct": a20,
            "above_ma60_pct": a60,
            "new_high60": cur_row.get("new_high60"),
            "new_low60": cur_row.get("new_low60"),
            "hl_diff60": cur_row.get("hl_diff60"),
            "status": _breadth_status(up_r),
            "score": round(sum(parts) / len(parts), 1) if parts else None,
            "pct": _pctile("breadth_up_ratio", 250, as_of),
            "z": z_of("breadth_up_ratio"),
            "highlight": False,
            "anchor": "mw-breadth",
            "hint": "上涨家数占比 / 站上均线占比；指数涨但宽度差 = 少数权重股拉抬的虚涨",
        }
        breadth["highlight"] = _is_extreme(breadth["pct"])

        # 量能（批次 3）：与宽度同源，配合能区分「放量下跌」与「缩量止跌」
        volume = {
            "amount": _num(cur_row.get("market_amount")),
            "ratio_20": _num(cur_row.get("amount_ratio_20")),
            "status": _volume_status(_num(cur_row.get("amount_ratio_20"))),
            "pct": _pctile("amount_ratio_20", 250, as_of),
            "hint": "全市场成交额（个股 amount 求和，亿元）及其与 20 日均值之比；>100 放量 / <100 缩量",
        }

        bh = _breadth_history(trend_days, as_of)
        bh.reverse()
        breadth_trend = {
            "dates": [str(r["trade_date"]) for r in bh],
            "up_ratio": [_num(r.get("breadth_up_ratio")) for r in bh],
            "adl": [None if r.get("breadth_adl") is None else int(r["breadth_adl"]) for r in bh],
            "above_ma20_pct": [_num(r.get("above_ma20_pct")) for r in bh],
            "hl_diff60": [None if r.get("hl_diff60") is None else int(r["hl_diff60"]) for r in bh],
            "amount": [_num(r.get("market_amount")) for r in bh],
            "amount_ratio": [_num(r.get("amount_ratio_20")) for r in bh],
        }

    # 交叉印证（2026-09-15 P1）：五类参照系的第 ④ 类，此前完全空白。
    # 逐项 fail-soft（内部已处理），任一项数据源异常只降级自己，不影响本页其余部分。
    xcheck = cross_check.cross_checks(as_of)

    # 背离数的历史位置（2026-09-19）：把「4 项背离」从计数变成信号 —— 见 _diverge_stats。
    # 序列取自 market_xcheck_daily（xcheck_sync 每工作日 22:30 写入；历史由
    # scripts/backfill_xcheck.py 一次性回填 250 日）。
    div_cur = (xcheck.get("summary") or {}).get("diverge")
    div_stats = _diverge_stats(div_cur, _xcheck_series(250, as_of))

    return {"as_of": data_as_of, "is_replay": is_replay,
            # 卡片墙的一句话结论（卡片专用；详情页有自己的完整面板，不重复渲染）
            "verdict": _card_verdict(bp, ra, erp_pct, xcheck, div_stats),
            # 背离数及其历史位置（口径随响应下发，供详情页与前端直接使用）
            "cross_diverge": div_stats,
            # 回放模式下滞后无意义（数据天然落后于今天），固定报 0 避免误标琥珀
            "stale_sessions": 0 if is_replay else _stale_sessions(data_as_of),
            "kpis": kpis, "groups": groups,
            "size_gradient": size_gradient, "trend": trend, "detail": detail,
            "heat_matrix": _heat_matrix(hist),
            "breadth": breadth, "volume": volume, "breadth_trend": breadth_trend,
            "cross_checks": xcheck["items"], "cross_summary": xcheck["summary"],
            "cross_note": xcheck["note"],
            "erp": _erp, "erp_note": ERP_HINT,
            "adj_note": ADJ_NOTE}
