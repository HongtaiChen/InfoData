#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
InvestBuddy 采集任务统一运行入口
- 从 task_config 读取任务配置（启停/cron/参数）
- 每个任务执行时记录 task_runs（running -> success/failed + 写入条数）
- 支持命令行手动触发：python -m app.tasks.run stock_daily_incr
- TASKS 注册表位于文件底部（所有 run_* 函数定义之后，避免模块加载时 NameError）
"""
import argparse
import json
import logging
import os
import sys
import threading
import traceback
from datetime import datetime

from ..db import get_db_config
from ..task_recorder import TaskRecorder
from ..collectors.stock_daily_incr import StockDailyIncrementalCollector
from ..collectors.market_current_sync import MarketCurrentSyncCollector
from ..collectors.trade_calendar_sync import TradeCalendarSyncCollector
from ..collectors.news_fetch import NewsFetchCollector
from ..collectors.concept_market_sync import ConceptMarketSyncCollector
from ..collectors.stock_info_sync import StockInfoSyncCollector
from ..collectors.concept_sync import ConceptSyncCollector
from ..collectors.fund_info_sync import FundInfoSyncCollector
from ..collectors.index_market_sync import IndexMarketSyncCollector
from ..collectors.index_cons_sync import IndexConsSyncCollector
# 2026-09-20 北交所名册（920 代码切换迁移 + 行业补采 + 新旧代码对照台账）
from ..collectors.bj_stock_sync import BjStockSyncCollector
from ..collectors.bond_profit_sync import BondProfitSyncCollector
from ..collectors.finance_calendar_sync import FinanceCalendarSyncCollector
from ..collectors.data_quality_check import DataQualityCheckCollector
from ..collectors.stock_status_sync import StockStatusSyncCollector
from ..collectors.stock_company_sync import StockCompanySyncCollector
from ..collectors.daily_recon import DailyReconCollector
from ..collectors.ths_dividend_sync import ThsDividendSyncCollector
from ..collectors.margin_sync import MarginSyncCollector
from ..collectors.jgdy_sync import JgdySyncCollector
# 2026-09-13 历史死表恢复采集 · 第 3/4 批（P2 行情域 + P3 逐股型域）
from ..collectors.futures_sync import FuturesSpotSyncCollector
from ..collectors.sw_industry_sync import SwIndustrySyncCollector
from ..collectors.financial_abstract_sync import FinancialAbstractSyncCollector
from ..collectors.stock_shares_sync import StockSharesSyncCollector
from ..collectors.capital_flow_sync import CapitalFlowSyncCollector
from ..collectors.market_style_sync import MarketStyleSyncCollector
# 2026-09-19 分析侧派生物化：交叉印证背离数（market_wind 卡片分位用）
from ..collectors.xcheck_sync import XcheckSyncCollector
# 2026-09-19 市场风向蓝图 P2/P3 落地（估值→ERP / 拆借利率 / 跨市场 / 汇率 / 新基金 / 回购）
from ..collectors.index_valuation_sync import IndexValuationSyncCollector
from ..collectors.interbank_rate_sync import InterbankRateSyncCollector
from ..collectors.overseas_index_sync import OverseasIndexSyncCollector
from ..collectors.currency_boc_sync import CurrencyBocSyncCollector
from ..collectors.fund_new_issue_sync import FundNewIssueSyncCollector
from ..collectors.stock_repurchase_sync import StockRepurchaseSyncCollector
from ..analysis import concept_ai

logger = logging.getLogger("infodata.tasks")


class TaskBlockedError(RuntimeError):
    """数据未就绪 → 本次按设计不执行（**非任务故障**）。

    与普通 RuntimeError 的区别：run_task 捕获它时把 task_runs.status 记为
    'blocked' 而非 'failed'。动机（2026-09-17）：快照护栏每天都会因「日线尚未
    跑完」拒绝一次，若记 failed，每个交易日都会产出一次假失败污染 DQ 失败率。
    见 app/task_recorder.py 的三态语义说明。
    """


# 线程局部：wrapper 里采集器 run() 返回的结构化快照（含 run_steps），run_task 结束时写入 task_runs.run_detail
_tls = threading.local()


def _set_run_detail(detail) -> None:
    """暂存 run_detail 快照（写入前统一展平 → _flatten_run_detail）"""
    _tls.run_detail = _flatten_run_detail(detail)


def _flatten_run_detail(detail):
    """把 with_steps 塞进 result["run_detail"] 的步骤链**提到顶层**。

    背景（2026-09-19 实测定位）：`with_steps` 把步骤链写成
        result["run_detail"]["run_steps"]
    而这里原先**整包**落库 → task_runs.run_detail 的顶层只有
        records_written / error_count / errors / note
    步骤链被压在第二层。前端「数据流·整链拓扑」读的是
        last.run_detail.run_steps          （第一层）
    于是 `real` 恒为空数组，每一步都渲染成「—」——
    **模板（步骤名/参数）有、当轮实录（value）全空**，44 张表全部中招。

    展平策略：外层键全保留（xcheck_sync 的 as_of/summary、
    stock_daily_incr 的 duration/source_stats/cutoff/skipped 都在外层，
    前端要用），再把内层 run_detail 的键并上来（run_steps 由此落到第一层）。
    """
    if not isinstance(detail, dict):
        return detail
    inner = detail.get("run_detail")
    flat = {k: v for k, v in detail.items() if k != "run_detail"}
    if isinstance(inner, dict):
        flat.update(inner)
    return flat


def _take_run_detail():
    d = getattr(_tls, "run_detail", None)
    _tls.run_detail = None
    return d


def _collector_run(collector):
    """调用采集器并暂存其结构化结果 → task_runs.run_detail（推广整链拓扑的统一入口）"""
    result = collector.run()
    if isinstance(result, dict):
        _set_run_detail(result)
    return result


def _setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


def run_stock_daily_incr(params: dict) -> int:
    collector = StockDailyIncrementalCollector(
        days_back=int(params.get("days_back", 15)),
        adjust=params.get("adjust", "qfq"),
        max_stocks=int(params.get("max_stocks", 0)),
        include_stale=bool(params.get("include_stale", False)),
        include_bj=bool(params.get("include_bj", False)),
    )
    result = _collector_run(collector)
    if result["error_count"] > 0 and result["records_written"] == 0:
        raise RuntimeError(f"{result['error_count']} 只股票采集失败（无任何写入）: {result['errors']}")
    if result["error_count"] > 0:
        # 部分失败（已成功写入部分数据）：不判整个任务失败，仅记录日志
        logger.warning(
            f"⚠️ {result['error_count']} 只股票失败（其余正常，任务记为成功）: {result['errors'][:3]}"
        )
    return result["records_written"]


def _task_params(params: dict, defaults: dict) -> dict:
    """合并任务参数（缺失字段用默认值）"""
    merged = dict(defaults)
    merged.update({k: v for k, v in (params or {}).items() if v is not None})
    return merged


def run_market_current_sync(params: dict) -> int:
    """行情快照聚合：stock_market_daily 最新交易日 → stock_market_current"""
    collector = MarketCurrentSyncCollector()
    result = _collector_run(collector)
    if result.get("blocked"):
        # 护栏拦截（日线未就绪）→ blocked 语义，不计入失败率
        raise TaskBlockedError("; ".join(result.get("errors") or ["数据未就绪"]))
    if result["error_count"] > 0:
        raise RuntimeError("; ".join(result["errors"]))
    return result["records_written"]


def run_trade_calendar_sync(params: dict) -> int:
    """交易日历补齐（默认当年+次年）"""
    p = _task_params(params, {})
    years = p.get("years")  # 可选: [2026, 2027]
    collector = TradeCalendarSyncCollector(years=years)
    result = _collector_run(collector)
    return result["records_written"]


def run_news_fetch(params: dict) -> int:
    """资讯采集：财联社 cls + 东财 em"""
    p = _task_params(params, {"sources": ["em"], "max_pages": 3})
    sources = p.get("sources") or ["em"]
    collector = NewsFetchCollector(sources=sources, max_pages=int(p.get("max_pages", 3)))
    result = _collector_run(collector)
    if result["error_count"] > 0:
        raise RuntimeError("; ".join(result["errors"]))
    return result["records_written"]


def run_concept_market_sync(params: dict) -> int:
    """同花顺概念板块行情增量同步（概念 K 线 / 概念排名数据源）"""
    p = _task_params(params, {})
    collector = ConceptMarketSyncCollector(start_date=p.get("start_date"))
    result = _collector_run(collector)
    if result["error_count"] > 0:
        logger.warning(f"⚠️ 概念行情 {result['error_count']} 个失败（其余正常）: {result['errors'][:3]}")
    return result["records_written"]


def run_ai_concept_analysis(params: dict) -> int:
    """批量分析最近 N 天日历事件 → 写入 finance_concept_analysis
    返回实际写入的概念分析条数。无 ARK_API_KEY 时降级为占位结果，仍会写 1 条 placeholder 记录。"""
    result = concept_ai.batch_analyze(
        limit=int(params.get("limit", 20)),
        days_back=int(params.get("days_back", 30)),
    )
    # 写入条数 = ai + placeholder 解析后的概念总数（这里以 analyzed 数为近似）
    analyzed = int(result.get("ai", 0)) + int(result.get("placeholder", 0))
    _set_run_detail(result)  # batch_analyze 结构化快照 → task_runs.run_detail
    if result.get("errors"):
        logger.warning(f"⚠️ 部分事件分析失败: {result['errors'][:3]}")
    return analyzed


# ============ 补齐 6 个“有配置无实现”的数据同步作业（2026-09-05） ============

def run_stock_info_sync(params: dict) -> int:
    """股票基础资料同步（stock_info / stock_info_ex，周更全市场名单）"""
    collector = StockInfoSyncCollector()
    result = _collector_run(collector)
    if result["error_count"] > 0:
        raise RuntimeError("; ".join(result["errors"]))
    return result["records_written"]


def run_concept_sync(params: dict) -> int:
    """概念成分同步（ths_stock_concepts：同花顺主源 → 新浪降级）"""
    collector = ConceptSyncCollector()
    result = _collector_run(collector)
    if result["error_count"] > 0:
        logger.warning(f"⚠️ 概念成分 {result['error_count']} 个失败（其余正常）: {result['errors'][:5]}")
    return result["records_written"]


def run_fund_info_sync(params: dict) -> int:
    """基金基础信息同步（fund_info，东财基金列表全量重建）"""
    collector = FundInfoSyncCollector()
    result = _collector_run(collector)
    if result["error_count"] > 0:
        raise RuntimeError("; ".join(result["errors"]))
    return result["records_written"]


def run_index_market_sync(params: dict) -> int:
    """指数日线同步（dc_index_market：东财主源 → 腾讯降级）"""
    collector = IndexMarketSyncCollector()
    result = _collector_run(collector)
    if result["error_count"] > 0:
        logger.warning(f"⚠️ 指数 {result['error_count']} 个失败（其余正常）: {result['errors'][:5]}")
    return result["records_written"]


def run_market_style_sync(params: dict) -> int:
    """市场风格日频物化表计算（market_style_daily：纯库内，挂 index_market_sync 之后）"""
    collector = MarketStyleSyncCollector()
    result = _collector_run(collector)
    return result["records_written"]


def run_xcheck_sync(params: dict) -> int:
    """交叉印证背离数物化（market_xcheck_daily，纯库内计算，22:30）

    market_wind 卡片要回答「4 项背离算多吗」，必须有一条历史序列可比 ——
    本任务每天记一行，逐日积累分布；一次性回填见 scripts/backfill_xcheck.py。
    说明与「为何不挂链式」见 collectors/xcheck_sync.py 文件头。
    params: as_of（仅运维回填用；日常不传 = 取全库最新）
    """
    p = _task_params(params, {"as_of": None})
    collector = XcheckSyncCollector(as_of=p.get("as_of"))
    result = _collector_run(collector)
    return result["records_written"]


def run_index_cons_sync(params: dict) -> int:
    """指数成分股快照同步（index_constituents：csindex 主源 → cni/members 降级）"""
    collector = IndexConsSyncCollector()
    result = _collector_run(collector)
    if result["error_count"] > 0:
        logger.warning(f"⚠️ 指数成分 {result['error_count']} 项异常（其余正常）: {result['errors'][:5]}")
    return result["records_written"]


def run_bj_stock_sync(params: dict) -> int:
    """北交所名册同步（stock_info 920 代码迁移 + 行业补采；台账写 stock_code_mapping）

    排期落在 stock_info_sync 之后、stock_daily_incr 之前（19:06）——日线候选池取自
    stock_info，名册不先修，北交所标的就永远进不了行情链。
    errors 非空即抛（含台账行数不变式、行业空值、旧码残留三类硬校验），
    避免「跑成功但口径仍脏」被静默放过。
    """
    collector = BjStockSyncCollector()
    result = _collector_run(collector)
    if result["error_count"] > 0:
        raise RuntimeError("; ".join(result["errors"][:5]))
    return result["records_written"]


def run_bond_profit_sync(params: dict) -> int:
    """中美国债收益率同步（bond_profit_daily 增量）"""
    collector = BondProfitSyncCollector()
    result = _collector_run(collector)
    if result["error_count"] > 0:
        raise RuntimeError("; ".join(result["errors"]))
    return result["records_written"]


def run_finance_calendar_sync(params: dict) -> int:
    """财经日历同步（finance_calendar：东财 RPT_CPH_FECALENDAR，替换失效 JY 源）"""
    collector = FinanceCalendarSyncCollector()
    result = _collector_run(collector)
    if result["error_count"] > 0:
        raise RuntimeError("; ".join(result["errors"]))
    return result["records_written"]


def run_data_quality_check(params: dict) -> int:
    """数据质量体检：读取 dq_rules 逐条执行 → 写 dq_report（每日盘后自动）

    params.groups 可选：["daily"]（默认任务配置）/ ["weekly"] / 省略=全部规则。
    daily 组为最新切片类（秒级），weekly 组为全史窗口扫描类（分钟级，独立任务）。
    """
    groups = (params or {}).get("groups")
    if isinstance(groups, str):
        groups = [groups]
    collector = DataQualityCheckCollector(groups=groups)
    result = _collector_run(collector)
    if result["error_count"] > 0 and result["records_written"] == 0:
        raise RuntimeError("; ".join(result["errors"]))
    if result["error_count"] > 0:
        logger.warning(f"⚠️ DQ {result['error_count']} 条规则执行异常（其余正常）: {result['errors'][:5]}")
    return result["records_written"]


def run_data_quality_check_weekly(params: dict) -> int:
    """数据质量体检 · 全史窗口组（每周一 21:30 独立任务）

    含 gap_scan（全史缺口，~196s）/ source_handoff（跨源衔接，~41s）/
    OHLC 自洽 / 涨跌幅衔接 / 近端覆盖率，实测合计 4~7 分钟，不与每日轮混跑。
    """
    collector = DataQualityCheckCollector(groups=["weekly"])
    result = _collector_run(collector)
    if result["error_count"] > 0 and result["records_written"] == 0:
        raise RuntimeError("; ".join(result["errors"]))
    if result["error_count"] > 0:
        logger.warning(f"⚠️ DQ(weekly) {result['error_count']} 条规则执行异常: {result['errors'][:5]}")
    return result["records_written"]


# ============ L3 修复闭环：日线定向回补（2026-09-10） ============

def _gap_ranges_from_dq(p: dict) -> list[tuple]:
    """从 dq_gap_detail 读 open 缺口 → 回补区间 [(code, start, end)]

    同一票的多个缺口合并为「最早 prev_date ~ 最晚 next_date」一个区间
    （中间已有数据由 INSERT IGNORE 自动去重，避免逐段多次请求）。
    """
    import pymysql
    from ..db import get_db_config

    conn = pymysql.connect(**get_db_config().to_dict())
    try:
        sql = ("SELECT stock_code, MIN(prev_date) AS s, MAX(next_date) AS e "
               "FROM dq_gap_detail WHERE status='open'")
        args: list = []
        if p.get("risk") == "high":
            sql += " AND risk='high'"
        sql += " GROUP BY stock_code ORDER BY stock_code"
        max_codes = int(p.get("max_codes", 0))
        if max_codes > 0:
            sql += " LIMIT %s"
            args.append(max_codes)
        with conn.cursor() as cur:
            cur.execute(sql, args)
            return [(r[0], r[1].strftime("%Y%m%d"), r[2].strftime("%Y%m%d")) for r in cur.fetchall()]
    finally:
        conn.close()


def run_daily_backfill(params: dict) -> int:
    """日线定向回补（L3 修复闭环）

    params:
      source   : "dq_gap"（默认，读 dq_gap_detail 中 status='open' 的疑似缺口）
                 | "ranges"（显式区间）
      ranges   : [["600519","20010827","20011231"], ...]（source=ranges 时必填）
      risk     : "all"（默认）| "high"（只补 ≥365 天高危缺口）
      max_codes: 0（0=不限；调试可用小值）
      adjust   : "qfq"（默认，与主表口径一致）
    回补成功后将该票缺口明细置 status='fixed'；n=0 的保持 open 待人工判定
    （可能是停牌/退市等合理缺失，应人工置 ignored）。
    """
    from datetime import datetime as _dt
    import pymysql
    from ..db import get_db_config

    p = _task_params(params, {"source": "dq_gap", "risk": "all", "max_codes": 0, "adjust": "qfq"})
    source = p.get("source", "dq_gap")
    if source == "ranges":
        ranges = [tuple(x) for x in (p.get("ranges") or [])]
    else:
        ranges = _gap_ranges_from_dq(p)
    if not ranges:
        logger.info("定向回补：无待补区间（dq_gap_detail 无 open 缺口，或未提供 ranges）")
        return 0

    collector = StockDailyIncrementalCollector(
        adjust=p.get("adjust", "qfq"),
        max_stocks=int(p.get("max_codes", 0)),
        backfill_ranges=ranges,
    )
    result = _collector_run(collector)
    ok_ranges = result.get("ok_ranges") or []
    if ok_ranges:
        # 按「区间」而非「股票」粒度标记 fixed：只标记本次真正补到数据的缺口段，
        # 避免同票其它未回补缺口（如长期停牌段）被误标（2026-09-10 实测发现并修正）。
        def _d(v) -> str:
            s = str(v)
            return f"{s[:4]}-{s[4:6]}-{s[6:8]}" if len(s) == 8 else s

        conn = pymysql.connect(**get_db_config().to_dict())
        try:
            with conn.cursor() as cur:
                for code, s, e in ok_ranges:
                    cur.execute(
                        "UPDATE dq_gap_detail SET status='fixed', "
                        "note=CONCAT(COALESCE(note,''), %s) "
                        "WHERE status='open' AND stock_code=%s AND prev_date >= %s AND next_date <= %s",
                        (f"[{_dt.now():%Y-%m-%d} 回补]", code, _d(s), _d(e)),
                    )
            conn.commit()
            logger.info(f"dq_gap_detail 置 fixed：{len(ok_ranges)} 个区间")
        finally:
            conn.close()
    if result["error_count"] > 0 and result["records_written"] == 0:
        raise RuntimeError("; ".join(result["errors"]))
    return result["records_written"]


# ============ L2 外部对账（2026-09-10） ============

def run_daily_recon_window(params: dict) -> int:
    """L2 外部对账 · 滚动窗口（腾讯源，独立血缘）

    params: days(默认5) / max_stocks(0=全市场) / tolerance_pct(0.5) / adjust(qfq)
    """
    p = _task_params(params, {"days": 5, "max_stocks": 0, "tolerance_pct": 0.5, "adjust": "qfq"})
    collector = DailyReconCollector(
        mode="window",
        days=int(p.get("days", 5)),
        max_stocks=int(p.get("max_stocks", 0)),
        tolerance_pct=float(p.get("tolerance_pct", 0.5)),
        adjust=p.get("adjust", "qfq"),
    )
    result = _collector_run(collector)
    if result["error_count"] > 0:
        logger.warning(f"⚠️ 窗口对账 {result['error_count']} 只失败（其余正常）: {result['errors'][:3]}")
    return result["records_written"]


def run_daily_recon_sample(params: dict) -> int:
    """L2 外部对账 · 全史抽样（东财源，查入库丢行/截断）

    params: sample_size(50) / seed(42) / adjust(qfq)
    """
    p = _task_params(params, {"sample_size": 50, "seed": 42, "adjust": "qfq"})
    collector = DailyReconCollector(
        mode="sample",
        sample_size=int(p.get("sample_size", 50)),
        seed=int(p.get("seed", 42)),
        adjust=p.get("adjust", "qfq"),
    )
    result = _collector_run(collector)
    if result["error_count"] > 0:
        logger.warning(f"⚠️ 抽样对账 {result['error_count']} 只失败: {result['errors'][:3]}")
    return result["records_written"]


# ============ 股票档案域（2026-09-05） ============

def run_stock_status_sync(params: dict) -> int:
    """上市/退市状态同步（stock_info.list_status/delist_date，Baostock 周更全量）"""
    collector = StockStatusSyncCollector()
    result = _collector_run(collector)
    if result["error_count"] > 0:
        raise RuntimeError("; ".join(result["errors"]))
    return result["records_written"]


def run_stock_company_sync(params: dict) -> int:
    """公司档案同步（stock_info 宽表档案列：巨潮官方源，差量逐只 UPDATE，列级不触碰证券列）"""
    p = _task_params(params, {"max_count": 200, "refresh_days": 30})
    collector = StockCompanySyncCollector(
        max_count=int(p.get("max_count", 200)),
        refresh_days=int(p.get("refresh_days", 30)),
    )
    result = _collector_run(collector)
    if result["error_count"] > 0:
        logger.warning(f"⚠️ 公司档案 {result['error_count']} 只失败（其余正常）: {result['errors'][:5]}")
    return result["records_written"]


# ============ 历史死表恢复采集（2026-09-13，P0+P1 批次） ============

def run_ths_dividend_sync(params: dict) -> int:
    """分红送配同步（ths_stock_dividend，同花顺逐股增量补齐）

    params: max_stocks(0=全部) / sleep_sec(0.12) / retry(1) / timeout_sec(30 单只调用超时)
    """
    p = _task_params(params, {"max_stocks": 0, "sleep_sec": 0.12, "retry": 1, "timeout_sec": 30})
    collector = ThsDividendSyncCollector(
        max_stocks=int(p.get("max_stocks", 0)),
        sleep_sec=float(p.get("sleep_sec", 0.12)),
        retry=int(p.get("retry", 1)),
        timeout_sec=float(p.get("timeout_sec", 30)),
    )
    result = _collector_run(collector)
    if result["error_count"] > 0:
        logger.warning(f"⚠️ 分红送配 {result['error_count']} 只失败（其余正常）: {result['errors'][:3]}")
    return result["records_written"]


def run_margin_sync(params: dict) -> int:
    """融资融券同步（securities_margin，沪+深+北三市合计）

    params: max_days(0=全部) / sleep_sec(0.25) / timeout_sec(60 单次调用超时) / bse_retry(2)
    """
    p = _task_params(params, {"max_days": 0, "sleep_sec": 0.25, "timeout_sec": 60, "bse_retry": 2})
    collector = MarginSyncCollector(
        max_days=int(p.get("max_days", 0)),
        sleep_sec=float(p.get("sleep_sec", 0.25)),
        timeout_sec=float(p.get("timeout_sec", 60)),
        bse_retry=int(p.get("bse_retry", 2)),
    )
    result = _collector_run(collector)
    if result["error_count"] > 0:
        logger.warning(f"⚠️ 融资融券 {result['error_count']} 项异常（其余正常）: {result['errors'][:3]}")
    return result["records_written"]


def run_jgdy_sync(params: dict) -> int:
    """机构调研同步（stock_jgdy_detail，东财按公告日期起点单次拉取）

    params: overlap_days(30) / first_lookback_days(365) / timeout_sec(60)
    注：stock_jgdy_tj_em 的 date 参数是「公告日期起点」而非接待日期，
        单次调用即返回该起点之后的全部记录，故无需逐日遍历。
    """
    p = _task_params(params, {"overlap_days": 30, "first_lookback_days": 365, "timeout_sec": 60})
    collector = JgdySyncCollector(
        overlap_days=int(p.get("overlap_days", 30)),
        first_lookback_days=int(p.get("first_lookback_days", 365)),
        timeout_sec=float(p.get("timeout_sec", 60)),
    )
    result = _collector_run(collector)
    if result["error_count"] > 0:
        logger.warning(f"⚠️ 机构调研 {result['error_count']} 项异常: {result['errors'][:3]}")
    return result["records_written"]


# ============ 历史死表恢复采集（2026-09-13，第 3 批 P2：行情域） ============

def run_futures_sync(params: dict) -> int:
    """期货现货价格与基差同步（futures_spot_price，akshare 100ppi 源）

    params: chunk_days(30) / max_days(0=补到最新) / sleep_sec(0.5) /
            timeout_sec(180 单块超时) / first_lookback_days(365) / from_date(可空，运维回补起点)
    注：源单次调用 60~165s（100ppi 重试 5 次），故按 chunk_days 分块 + 每块独立提交 + 单块超时；
        本表同日存在多快照且值不同，**不可建唯一索引**，幂等靠 MAX(trade_date)+1 续跑。
        派生列 main_basis_high/low/avg_180d 为日历 180 天滚动窗口。
    """
    p = _task_params(params, {"chunk_days": 30, "max_days": 0, "sleep_sec": 0.5,
                              "timeout_sec": 180, "first_lookback_days": 365,
                              "from_date": None})
    collector = FuturesSpotSyncCollector(
        chunk_days=int(p.get("chunk_days", 30)),
        max_days=int(p.get("max_days", 0)),
        sleep_sec=float(p.get("sleep_sec", 0.5)),
        timeout_sec=float(p.get("timeout_sec", 180)),
        first_lookback_days=int(p.get("first_lookback_days", 365)),
        from_date=p.get("from_date"),
    )
    result = _collector_run(collector)
    if result["error_count"] > 0:
        logger.warning(f"⚠️ 期货现货 {result['error_count']} 项异常: {result['errors'][:3]}")
    return result["records_written"]


def run_sw_industry_sync(params: dict) -> int:
    """申万行业分类快照同步（stock_industry_sw，31 一级 + 131 二级成分）

    params: sleep_sec(0.15) / timeout_sec(30) / min_coverage(0.85 覆盖率护栏)
    注：小事务 DELETE+INSERT 整体重建（分类会调样），覆盖率低于 min_coverage 直接判定源异常并回滚。
    """
    p = _task_params(params, {"sleep_sec": 0.15, "timeout_sec": 30, "min_coverage": 0.85})
    collector = SwIndustrySyncCollector(
        sleep_sec=float(p.get("sleep_sec", 0.15)),
        timeout_sec=float(p.get("timeout_sec", 30)),
        min_coverage=float(p.get("min_coverage", 0.85)),
    )
    result = _collector_run(collector)
    if result["error_count"] > 0:
        logger.warning(f"⚠️ 申万行业 {result['error_count']} 项异常: {result['errors'][:3]}")
    return result["records_written"]


# ============ 历史死表恢复采集（2026-09-13，第 4 批 P3：逐股型） ============

def run_financial_abstract_sync(params: dict) -> int:
    """财务关键指标同步（stock_financial_abstract_ths，同花顺逐股增量）

    params: max_stocks(400 本轮上限) / sleep_sec(0.12) / timeout_sec(30) /
            full_sweep(全池重扫) / retry(1 单股重试) / retry_backoff(2.0s)
    注：只补「MAX(报告期) < max(本地全局 MAX, 披露日历推算最近期)」的滞后股票；
        候选池已排除退市股（其报告期恒滞后，会永久占满 max_stocks 名额）。
        同花顺长跑会间歇限流（实测 34% 失败，停跑即恢复）→ 单股退避重试必备。
    """
    p = _task_params(params, {"max_stocks": 400, "sleep_sec": 0.12, "timeout_sec": 30,
                              "full_sweep": False, "retry": 1, "retry_backoff": 2.0})
    collector = FinancialAbstractSyncCollector(
        max_stocks=int(p.get("max_stocks", 400)),
        sleep_sec=float(p.get("sleep_sec", 0.12)),
        timeout_sec=float(p.get("timeout_sec", 30)),
        full_sweep=bool(p.get("full_sweep", False)),
        retry=int(p.get("retry", 1)),
        retry_backoff=float(p.get("retry_backoff", 2.0)),
    )
    result = _collector_run(collector)
    if result["error_count"] > 0:
        logger.warning(f"⚠️ 财务摘要 {result['error_count']} 只失败（其余正常）: {result['errors'][:3]}")
    return result["records_written"]


def run_stock_shares_sync(params: dict) -> int:
    """股本变动同步（stock_shares，巨潮逐股滚动）

    params: max_stocks(400 本轮上限) / refresh_days(30) / sleep_sec(0.12) /
            timeout_sec(30) / full_sweep(全池重扫)
    注：事件型表，data_col(change_date) 常年不变，故以 update_time 判「久未刷新」；
        退市股源侧无记录（akshare 抛 KeyError 公告日期）已归一为「无数据」，候选池亦排除。
    """
    p = _task_params(params, {"max_stocks": 400, "refresh_days": 30, "sleep_sec": 0.12,
                              "timeout_sec": 30, "full_sweep": False})
    collector = StockSharesSyncCollector(
        max_stocks=int(p.get("max_stocks", 400)),
        refresh_days=int(p.get("refresh_days", 30)),
        sleep_sec=float(p.get("sleep_sec", 0.12)),
        timeout_sec=float(p.get("timeout_sec", 30)),
        full_sweep=bool(p.get("full_sweep", False)),
    )
    result = _collector_run(collector)
    if result["error_count"] > 0:
        logger.warning(f"⚠️ 股本变动 {result['error_count']} 只失败（其余正常）: {result['errors'][:3]}")
    return result["records_written"]


def run_capital_flow_sync(params: dict) -> int:
    """资金流向同步（stock_capital_flow，东财逐股）

    params: max_stocks(400) / refresh_days(7) / sleep_sec(0.12) /
            timeout_sec(30) / full_sweep(False)
    ⚠️ 2026-09-19 定性：**已明确废弃**（不再是「待启用」）。task_config.enabled=0 且
       cron 已由 `15 22 * * *` 改为「手动」。三条独立理由（任一都足以否掉启用）：
       ① 网络：逐股打东财 push2his（400 只/轮）。该子域对本机是**间歇性 RST 风控**——
          关闭前实测首次直连可通、连续请求随即被拒，跨 4 分钟 5 次重试全败；
          同域的 datacenter-web 却稳定 200（说明不是整机断网，是该行情子域不欢迎批量爬取）。
          逐股批量调用正是最容易触发风控的模式，启用后大概率长期失败。
       ② 口径：「主力净流入 = 超大单 + 大单」仅 90~93.8% 成立，源口径未与本地表完全对齐,
          每行带 6~10% 不确定性，会污染下游「资金/杠杆」类判读。
       ③ cron `15 22 * * *` 与 financial_abstract_sync **完全撞车**（改为「手动」后消除）。
       处置：代码与 stock_capital_flow 表**保留为冻结态**（不 drop，748,019 行历史仍可读），
       仅去掉排期与「待启用」的模糊表述。若要复活，须先解决 ①② 两条。
    """
    p = _task_params(params, {"max_stocks": 400, "refresh_days": 7, "sleep_sec": 0.12,
                              "timeout_sec": 30, "full_sweep": False})
    collector = CapitalFlowSyncCollector(
        max_stocks=int(p.get("max_stocks", 400)),
        refresh_days=int(p.get("refresh_days", 7)),
        sleep_sec=float(p.get("sleep_sec", 0.12)),
        timeout_sec=float(p.get("timeout_sec", 30)),
        full_sweep=bool(p.get("full_sweep", False)),
    )
    result = _collector_run(collector)
    if result["error_count"] > 0:
        logger.warning(f"⚠️ 资金流向 {result['error_count']} 只失败（其余正常）: {result['errors'][:3]}")
    return result["records_written"]


# ============ 市场风向蓝图 P2/P3 落地（2026-09-19） ============
# 依据 docs/市场风向数据蓝图落地审计_2026-09-19.md §5「建议下一步」。
# 六个采集器覆盖报告 §5 实测可达的**全部** 11 个候选接口，并把蓝图 A/B/D/E 四块补齐。

def run_index_valuation_sync(params: dict) -> int:
    """指数估值同步（index_valuation_daily：中证官网 + 乐咕 + 全A 三源）

    蓝图 B「股债性价比」的估值分母 —— 没有它，ERP 永远做不出来（报告 §7-① 的根源）。
    params: timeout_sec(60) / lookback_days(0=不截断，**必须保留乐咕完整历史**，
            否则 ERP 分位样本只剩十几个点、失去意义)
    """
    p = _task_params(params, {"timeout_sec": 60, "lookback_days": 0})
    collector = IndexValuationSyncCollector(
        timeout_sec=float(p.get("timeout_sec", 60)),
        lookback_days=int(p.get("lookback_days", 0)),
    )
    result = _collector_run(collector)
    if result["error_count"] > 0:
        logger.warning(f"⚠️ 指数估值 {result['error_count']} 项异常（其余正常）: {result['errors'][:3]}")
    return result["records_written"]


def run_interbank_rate_sync(params: dict) -> int:
    """银行间拆借利率 + LPR 同步（interbank_rate_daily）

    蓝图 A「钱贵不贵」维度（报告 §6 蓝图 A 唯一的数据缺口）。
    params: timeout_sec(90)
    """
    p = _task_params(params, {"timeout_sec": 90})
    collector = InterbankRateSyncCollector(timeout_sec=float(p.get("timeout_sec", 90)))
    result = _collector_run(collector)
    if result["error_count"] > 0:
        logger.warning(f"⚠️ 拆借利率 {result['error_count']} 项异常: {result['errors'][:3]}")
    return result["records_written"]


def run_overseas_index_sync(params: dict) -> int:
    """海外/港股指数日线同步（overseas_index_daily：恒生 + 道指/标普/纳指）

    蓝图 E「跨市场与外部情绪」—— 没有外部参照物，「A 股抗跌」这个判断无从谈起。
    params: timeout_sec(60)
    """
    p = _task_params(params, {"timeout_sec": 60})
    collector = OverseasIndexSyncCollector(timeout_sec=float(p.get("timeout_sec", 60)))
    result = _collector_run(collector)
    if result["error_count"] > 0:
        logger.warning(f"⚠️ 海外指数 {result['error_count']} 项异常: {result['errors'][:3]}")
    return result["records_written"]


def run_currency_boc_sync(params: dict) -> int:
    """人民币外汇牌价同步（currency_boc_daily：美元/欧元/日元/港元）

    蓝图 E 汇率腿 —— 汇率破位 = 外资流出压力。
    params: timeout_sec(60) / first_lookback_days(1825 首次回扫 5 年) / from_date(运维显式起点)
    """
    p = _task_params(params, {"timeout_sec": 60, "first_lookback_days": 1825, "from_date": None})
    collector = CurrencyBocSyncCollector(
        timeout_sec=float(p.get("timeout_sec", 60)),
        first_lookback_days=int(p.get("first_lookback_days", 1825)),
        from_date=p.get("from_date"),
    )
    result = _collector_run(collector)
    if result["error_count"] > 0:
        logger.warning(f"⚠️ 外汇牌价 {result['error_count']} 项异常: {result['errors'][:3]}")
    return result["records_written"]


def run_fund_new_issue_sync(params: dict) -> int:
    """新基金发行同步（fund_new_issue：东财全量）

    蓝图 E 基金腿 —— 发行冰点 = 反向底部信号（散户情绪极值）。
    params: timeout_sec(120)
    """
    p = _task_params(params, {"timeout_sec": 120})
    collector = FundNewIssueSyncCollector(timeout_sec=float(p.get("timeout_sec", 120)))
    result = _collector_run(collector)
    return result["records_written"]


def run_stock_repurchase_sync(params: dict) -> int:
    """股票回购同步（stock_repurchase：东财全量）

    蓝图 D「机构与产业资本」的回购腿（另两腿为库内 stock_jgdy_detail / stock_shares）。
    params: timeout_sec(240 源需翻 12 页)
    """
    p = _task_params(params, {"timeout_sec": 240})
    collector = StockRepurchaseSyncCollector(timeout_sec=float(p.get("timeout_sec", 240)))
    result = _collector_run(collector)
    return result["records_written"]


# ============ 任务注册表（所有 run_* 函数定义之后） ============
TASKS = {
    "stock_daily_incr": run_stock_daily_incr,
    "ai_concept_analysis": run_ai_concept_analysis,
    "market_current_sync": run_market_current_sync,
    "trade_calendar_sync": run_trade_calendar_sync,
    "news_fetch": run_news_fetch,
    "concept_market_sync": run_concept_market_sync,
    # 2026-09-05 补齐的 6 个作业（原“有配置无实现”，调度器此前静默跳过）
    "stock_info_sync": run_stock_info_sync,
    "concept_sync": run_concept_sync,
    "fund_info_sync": run_fund_info_sync,
    "index_market_sync": run_index_market_sync,
    # 2026-09-12 指数成分股快照（月度调样刷新）
    "index_cons_sync": run_index_cons_sync,
    # 2026-09-20 北交所名册（920 代码切换迁移 + 行业补采；必须早于 stock_daily_incr）
    "bj_stock_sync": run_bj_stock_sync,
    "bond_profit_sync": run_bond_profit_sync,
    "finance_calendar_sync": run_finance_calendar_sync,
    # 2026-09-05 数据质量体检（读 dq_rules → 写 dq_report）
    "data_quality_check": run_data_quality_check,
    # 2026-09-10 数据质量体检 · 全史窗口组（weekly 规则，每周一 21:30）
    "data_quality_check_weekly": run_data_quality_check_weekly,
    # 2026-09-10 L3 修复闭环：日线定向回补（按需/手动触发）
    "daily_backfill": run_daily_backfill,
    # 2026-09-10 L2 外部对账（腾讯滚动窗口 + 东财全史抽样）
    "daily_recon_window": run_daily_recon_window,
    "daily_recon_sample": run_daily_recon_sample,
    # 2026-09-05 股票档案域（Baostock 上市/退市状态 + 巨潮公司档案）
    "stock_status_sync": run_stock_status_sync,
    "stock_company_sync": run_stock_company_sync,
    # 2026-09-13 历史死表恢复采集（分红送配 / 融资融券 / 机构调研）
    "ths_dividend_sync": run_ths_dividend_sync,
    "margin_sync": run_margin_sync,
    "jgdy_sync": run_jgdy_sync,
    # 2026-09-13 历史死表恢复采集 · 第 3 批 P2（期货现货 / 申万行业）
    "futures_sync": run_futures_sync,
    "sw_industry_sync": run_sw_industry_sync,
    # 2026-09-13 历史死表恢复采集 · 第 4 批 P3（财务摘要 / 股本变动 / 资金流向）
    "financial_abstract_sync": run_financial_abstract_sync,
    "stock_shares_sync": run_stock_shares_sync,
    "capital_flow_sync": run_capital_flow_sync,
    # 2026-09-13 分析研究框架 · 市场风向模块（风格物化表计算，挂 index_market_sync 之后）
    "market_style_sync": run_market_style_sync,
    # 2026-09-19 分析侧派生物化 · 交叉印证背离数（22:30，须晚于概念补班 22:00）
    "xcheck_sync": run_xcheck_sync,
    # 2026-09-19 市场风向蓝图 P2/P3 落地（6 个采集器，对应报告 §5 全部 11 个可达接口）
    "index_valuation_sync": run_index_valuation_sync,
    "interbank_rate_sync": run_interbank_rate_sync,
    "overseas_index_sync": run_overseas_index_sync,
    "currency_boc_sync": run_currency_boc_sync,
    "fund_new_issue_sync": run_fund_new_issue_sync,
    "stock_repurchase_sync": run_stock_repurchase_sync,
}


_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _error_trace(exc: BaseException, limit: int = 6) -> dict:
    """把异常现场压成结构化摘要，供 task_runs.run_detail.error_trace 留档。

    动机（2026-09-19 复盘踩坑）：run_task 原先**只把 str(e) 写进 error_message**，
    没有任何栈帧。`index_valuation_sync` 那次
        TypeError: tuple indices must be integers or slices, not str
    因为缺栈帧、无从定位，被当成「重试即好的偶发缺陷、根因未修」，
    白花一整轮排查（事后靠 git 提交时间才还原真因＝开发期未提交版本、已在 703ecba 修掉）。
    → 失败现场必须自带「哪个文件哪一行」，否则 4 秒就挂的偶发错误永远查不动。

    只保留 backend/ 内的帧（排除 pymysql/pandas 等 site-packages 噪音），
    取末尾 limit 帧（最靠近出错点）。附加字段，前端按可选字段读取，不影响现有渲染。
    """
    frames: list[dict] = []
    for fr in traceback.extract_tb(exc.__traceback__):
        fn = fr.filename or ""
        try:
            full = os.path.abspath(fn)
        except (OSError, ValueError):
            continue
        if not full.startswith(_BACKEND_ROOT):
            continue
        frames.append({
            "file": os.path.relpath(full, _BACKEND_ROOT).replace("\\", "/"),
            "line": fr.lineno,
            "func": fr.name,
            "code": (fr.line or "").strip()[:160],
        })
    return {
        "type": type(exc).__name__,
        "message": str(exc)[:500],
        "frames": frames[-limit:],
    }


def _with_error_trace(detail, exc: BaseException):
    """在既有 run_detail 上附加 error_trace，保留 run_steps 等原有字段（失败路径专用）"""
    merged = dict(detail) if isinstance(detail, dict) else {}
    merged["error_trace"] = _error_trace(exc)
    return merged


def run_task(task_name: str) -> int:
    """执行单个任务（带 task_runs 记录）"""
    import json
    import pymysql
    conn = pymysql.connect(**get_db_config().to_dict())
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT enabled, params FROM task_config WHERE task_name=%s",
                (task_name,),
            )
            row = cur.fetchone()
    finally:
        conn.close()

    if row is None:
        raise ValueError(f"任务 {task_name} 未在 task_config 中注册")
    enabled, params_raw = row
    if not enabled:
        logger.info(f"任务 {task_name} 已禁用，跳过")
        return 0
    if task_name not in TASKS:
        raise ValueError(f"任务 {task_name} 无执行实现")

    params = {}
    if params_raw:
        try:
            params = json.loads(params_raw) if isinstance(params_raw, str) else (params_raw or {})
        except (json.JSONDecodeError, TypeError):
            params = {}

    recorder = TaskRecorder(task_name)
    recorder.start()
    detail = None
    try:
        written = TASKS[task_name](params)
        detail = _take_run_detail()
        recorder.finish(records_written=written, run_detail=_dumps(detail))
        logger.info(f"✅ 任务 {task_name} 完成，写入 {written} 条")
        return written
    except TaskBlockedError as e:
        # 数据未就绪：记为 blocked（非 failed，不计失败率），但**仍然向上抛出**——
        # 让调用方（调度器）知道本次没有产出数据，不要据此接力下游任务。
        # ⚠️ blocked 是**设计内的拒绝**（护栏触发，每天都会发生），不附 error_trace：
        #    消息已点明是哪条护栏，附栈只会把「正常日路径」的 run_detail 撑噪。
        detail = _take_run_detail()
        recorder.finish(records_written=0, error_message=str(e), run_detail=_dumps(detail), status="blocked")
        logger.warning(f"⏸ 任务 {task_name} 未执行（数据未就绪，非故障）: {e}")
        raise
    except Exception as e:
        # 真失败：run_detail 追加 error_trace（异常类型 + backend/ 内栈帧），让偶发故障可定位
        detail = _with_error_trace(_take_run_detail(), e)
        recorder.finish(records_written=0, error_message=str(e), run_detail=_dumps(detail))
        # logger.exception（非 error）：保留完整堆栈。偶发错误往往 4 秒就挂，
        # 只有一行 message 时根本无从下手（2026-09-19 index_valuation_sync 的教训）。
        logger.exception(f"❌ 任务 {task_name} 失败: {e}")
        raise


def _dumps(detail) -> str | None:
    """将运行快照序列化为 JSON 字符串（可入 MySQL json 列）；非 JSON 可序列化时降级为 None"""
    if detail is None:
        return None
    try:
        return json.dumps(detail, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return None


def main():
    _setup_logging()
    parser = argparse.ArgumentParser(description="InvestBuddy 采集任务")
    parser.add_argument("task", nargs="?", help="任务名（如 stock_daily_incr）")
    parser.add_argument("--list", action="store_true", help="列出全部已注册任务")
    args = parser.parse_args()

    if args.list:
        for name in TASKS:
            print(name)
        return

    if not args.task:
        parser.print_help()
        return

    try:
        run_task(args.task)
    except Exception as e:
        logger.error(f"任务执行失败: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
