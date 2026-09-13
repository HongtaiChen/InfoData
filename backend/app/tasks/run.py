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
import sys
import threading
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
from ..analysis import concept_ai

logger = logging.getLogger("infodata.tasks")

# 线程局部：wrapper 里采集器 run() 返回的结构化快照（含 run_steps），run_task 结束时写入 task_runs.run_detail
_tls = threading.local()


def _set_run_detail(detail) -> None:
    _tls.run_detail = detail


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


def run_index_cons_sync(params: dict) -> int:
    """指数成分股快照同步（index_constituents：csindex 主源 → cni/members 降级）"""
    collector = IndexConsSyncCollector()
    result = _collector_run(collector)
    if result["error_count"] > 0:
        logger.warning(f"⚠️ 指数成分 {result['error_count']} 项异常（其余正常）: {result['errors'][:5]}")
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

    params: max_stocks(400 本轮上限) / sleep_sec(0.12) / timeout_sec(30) / full_sweep(全池重扫)
    注：只补「MAX(报告期) < max(本地全局 MAX, 披露日历推算最近期)」的滞后股票；
        候选池已排除退市股（其报告期恒滞后，会永久占满 max_stocks 名额）。
    """
    p = _task_params(params, {"max_stocks": 400, "sleep_sec": 0.12,
                              "timeout_sec": 30, "full_sweep": False})
    collector = FinancialAbstractSyncCollector(
        max_stocks=int(p.get("max_stocks", 400)),
        sleep_sec=float(p.get("sleep_sec", 0.12)),
        timeout_sec=float(p.get("timeout_sec", 30)),
        full_sweep=bool(p.get("full_sweep", False)),
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
    ⚠️ 2026-09-13 状态：**默认禁用**（task_config.enabled=0）。原因：东财域名在本机
       沙箱环境不可达，且「主力净流入 = 超大单 + 大单」仅 90~93.8% 成立，
       源口径未完全对齐本地表 → 先实现不启用，待网络放行 + 口径复核后再开。
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
}


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
    except Exception as e:
        detail = _take_run_detail()
        recorder.finish(records_written=0, error_message=str(e), run_detail=_dumps(detail))
        logger.error(f"❌ 任务 {task_name} 失败: {e}")
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
