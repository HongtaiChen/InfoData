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

from ..db import get_db_config, query_all
from ..task_recorder import TaskRecorder
from ..collectors.stock_daily_incr import StockDailyIncrementalCollector
from ..collectors.stock_market_daily_ex_sync import StockDailyExCollector
from ..collectors.fund_hold_sync import FundHoldCollector
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
from ..collectors.msci_index_sync import MsciIndexSyncCollector
from ..collectors.currency_boc_sync import CurrencyBocSyncCollector
from ..collectors.fund_new_issue_sync import FundNewIssueSyncCollector
from ..collectors.stock_repurchase_sync import StockRepurchaseSyncCollector
# 2026-09-25 货币流动性批次 2（中国数量维度 + 多国央行利率 + 美元指数自算）
# 对应《货币流动性观测体系设计_2026-09-25.md》§6 批次 2 的三张新表
from ..collectors.cn_liquidity_sync import CnLiquiditySyncCollector
from ..collectors.cn_cb_balance_sync import CnCbBalanceSyncCollector
from ..collectors.cb_policy_rate_sync import CbPolicyRateSyncCollector
from ..collectors.usd_index_sync import UsdIndexSyncCollector
# 2026-09-25 货币流动性补源 v2.0（无 FRED 密钥版）§7 批次 A/B/C/D —— 6 个新采集器
#   批次 A（美国层双轨）：us_fed_balance_weekly / us_money_supply_monthly / us_money_market_daily
#   批次 B（欧元区）：    eu_money_supply_monthly
#   批次 C（中国层）：    cn_omo_daily（人行 OMO 日度；cn_liquidity 的 M2 结构已在既有采集器内扩展）
#   批次 D（全球层）：    global_liquidity_bis
from ..collectors.us_fed_balance_sync import UsFedBalanceSyncCollector
from ..collectors.us_money_supply_sync import UsMoneySupplySyncCollector
from ..collectors.us_money_market_sync import UsMoneyMarketSyncCollector
from ..collectors.eu_money_supply_sync import EuMoneySupplySyncCollector
from ..collectors.cn_omo_sync import CnOmoSyncCollector
from ..collectors.cn_reserve_sync import CnReserveSyncCollector
from ..collectors.global_liquidity_sync import GlobalLiquiditySyncCollector
# 2026-10-18 投资环境模块：技术/基本面物化 + 宏观序列（E6）
from ..collectors.env_derive import EnvDeriveCollector
from ..collectors.env_macro_sync import EnvMacroSyncCollector
# 2026-10-20 筹码分布（B 方案）：个股筹码分布物化（纯库内，读 stock_market_daily 算 CYQ）
from ..collectors.stock_cyq_sync import StockCyqSyncCollector
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
        # adjust 已废弃（口径固定为实际价 + 后复权因子列），仅保留传参兼容
        adjust=params.get("adjust", ""),
        max_stocks=int(params.get("max_stocks", 0)),
        include_stale=bool(params.get("include_stale", False)),
        # 2026-09-20 由 False 改为 True：实测新浪对 bj920xxx 完全可用，
        # 原先「四级源均不支持北交所」的结论已不成立；继续跳过会让北交所行情再次停更。
        include_bj=bool(params.get("include_bj", True)),
        # 2026-10-17：单票回看上限由硬编码 500 抽出。默认 500 保持原行为；
        # 想要空库直出全史请用 daily_backfill 的 source=all_stocks，而不是把它调成 0
        # （增量路径没有分批概念，一次拉全史会让上游单次响应过大且无法断点续跑）。
        max_history_days=int(params.get("max_history_days", 500)),
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


def run_stock_market_daily_ex_sync(params: dict) -> int:
    """归档扩展行情同步（stock_market_daily_ex，不复权原始价）

    2026-10-17 新增：该表此前 `table_meta.writers=[]`（1690 万行无写入方），
    属「本机损毁 + 无备份即永久丢失」的最大单点风险。补上后 clone 可重建其 98.52%。

    params:
      source    : "auto"（默认，逐票 MAX(trade_date)+1 → 今天）
                  | "all"（无本地数据者从 list_date 起，即空库全史自举）
      max_codes : 0 = 本轮不限；>0 = 只处理前 N 只（分批推进，幂等可重跑）
      start_date: 显式回补起点（YYYYMMDD，可选）
      end_date  : 截止日（默认今天）
      workers   : 并发数（默认 4；V8 预热失败会自动退回 1）

    ⚠️ 已知不可重建域：老北交所代码（430/83x/87x）新浪返回空响应，
    共 245 只 / 249,999 行，采集器会自动跳过并计入 skipped_old_bj。
    """
    p = _task_params(params, {"source": "auto", "max_codes": 0})
    source = (p.get("source") or "auto").lower()
    collector = StockDailyExCollector(
        max_codes=int(p.get("max_codes") or 0),
        ranges=p.get("ranges") or None,
        start_date=p.get("start_date"),
        end_date=p.get("end_date"),
        workers=int(p.get("workers") or 4),
        # source=all：无视库内水位线，一律从 list_date 全量重拉（UPSERT 幂等，慢但彻底）
        full_sweep=(source == "all"),
    )
    result = _collector_run(collector)
    if result["error_count"] > 0:
        logger.warning(
            f"⚠️ 归档行情 {result['error_count']} 只失败（其余正常）: {result['errors'][:3]}"
        )
    return result["records_written"]


def run_fund_hold_sync(params: dict) -> int:
    """基金持仓同步（stock_hold_by_fund，按报告期全市场一次拉齐）

    2026-10-17 新增：该表此前 `table_meta.writers=[]`，11.3 万行无写入方。
    接口实测可回溯到 2012-03-31（比本地最早的 2016-03-31 更早），故重建可向前补齐 16 个报告期。

    params:
      periods    : 显式报告期列表 ["20250630", ...]（给定时忽略 start/end）
      start_period: 起始报告期（默认 20120331）
      end_period : 截止报告期（默认今天）
      overwrite  : True = UPSERT（会把历史快照改写成当前修订值，**慎用**）

    ⚠️ 默认 INSERT IGNORE：基金季报会被持续修订，同一报告期「当年存的」与「现在查的」
    行数和数值都不同（实测 2025-06-30：本地 2,943 行 vs 接口 5,216 行），
    UPSERT 会静默改写历史结论。
    """
    p = _task_params(params, {})
    collector = FundHoldCollector(
        periods=p.get("periods") or None,
        start_period=p.get("start_period"),
        end_period=p.get("end_period"),
        overwrite=bool(p.get("overwrite", False)),
    )
    result = _collector_run(collector)
    if result["error_count"] > 0:
        logger.warning(f"⚠️ 基金持仓 {result['error_count']} 期失败: {result['errors'][:3]}")
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


def run_env_derive(params: dict) -> int:
    """投资环境物化（env_index_tech_daily / env_fundamental_daily / env_fundamental_quarter /
    env_threshold）：纯库内计算，挂 market_current_sync 之后链式触发（依赖当日指数日线）。"""
    collector = EnvDeriveCollector(
        tech_days=int(params.get("tech_days") or 0),
        lookback_years=int(params.get("lookback_years") or 3),
    )
    result = _collector_run(collector)
    return result["records_written"]


def run_env_macro_sync(params: dict) -> int:
    """投资环境·宏观序列（env_macro_monthly：GDP/PMI/CPI/PPI，akshare macro_china 系列）"""
    collector = EnvMacroSyncCollector(timeout_sec=float(params.get("timeout_sec") or 60))
    result = _collector_run(collector)
    if result["error_count"] > 0:
        logger.warning(f"⚠️ 宏观序列 {result['error_count']} 项异常（其余正常）: {result['errors'][:3]}")
    return result["records_written"]


def run_stock_cyq_sync(params: dict) -> int:
    """个股筹码分布物化（stock_cyq_daily，纯库内，读 stock_market_daily 算 CYQ 三角分布）。

    挂 stock_daily_incr 之后链式触发。params: codes（逗号分隔定向回填，缺省全量）、
    limit（最多处理只数，缺省全量）。
    """
    collector = StockCyqSyncCollector(
        codes=str(params.get("codes") or ""),
        limit=int(params.get("limit") or 0),
    )
    result = _collector_run(collector)
    if result["error_count"] > 0:
        logger.warning(f"⚠️ 筹码计算 {result['error_count']} 只异常（其余正常）: {result['errors'][:3]}")
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


# ============ 启动收敛（自愈闭环 ·「启动即收敛」2026-10-08） ============

def _weekly_scan_due(min_interval_days: float) -> bool:
    """weekly 体检是否到期（距上次成功 >= min_interval_days 天）

    为什么必须节流：`data_quality_check_weekly` 是全史窗口扫描，实测 808~2318 秒。
    若每次启动都跑、而用户一天启动若干次，启动窗口会被反复占用十几分钟 ——
    而它的 8 条规则全部属**不可自愈类**（gap_scan / factor_link / pct_limit /
    where_count / source_handoff / per_key_coverage），重跑不解决任何问题。
    故按周节流（默认 6 天，与 weekly 的自然周期一致）。
    `min_interval_days <= 0` 表示不节流（每次都跑）。
    """
    if min_interval_days <= 0:
        return True
    rows = query_all(
        "SELECT MAX(finished_at) AS m FROM task_runs "
        "WHERE task_name = 'data_quality_check_weekly' AND status = 'success'"
    )
    last = rows[0].get("m") if rows else None
    if last is None:
        return True                       # 从未跑过 → 该跑
    return (datetime.now() - last).total_seconds() >= min_interval_days * 86400


def run_startup_converge(params: dict) -> int:
    """启动收敛：巡检 → 分诊 → 合并补偿队列 → 串行修复 → 复检 → 报告

    这是「自愈能力」的编排入口，设计依据与三条铁律见 `app/selfheal.py` 文件头。
    由 SchedulerManager 在**每次进程启动时**自动调用（见 `scheduler._boot_pipeline`），
    也可在前端手动触发做一次全量收敛（task_config.cron='手动'）。

    ⚠️ 为什么触发点是「启动」而不是「每小时」：本机服务是间歇运行的
    （实测近 38 天在线率 32%、断线 41 次、最长离线 260 小时），任何按墙上时钟
    触发的机制都只在「在线的那几小时」有效，而 90% 的问题发生在离线期间。

    params:
      max_repairs : 单次最多额外重跑几个任务（默认 10）。**只约束由 DQ 缺口触发的
                    修复**；时间维度的启动补偿不受此限（那是原有语义，补齐关机
                    期间错过的班次）
      max_seconds : 单次修复的**时间预算**（默认 900s）。与 max_repairs 是**两个维度**，
                    任一触发即停 —— 单任务耗时实测跨度 1s~1722s，光卡个数防不住
                    「10 个任务跑了 5 小时」
      groups      : 启动巡检纳入**分诊视野**的规则组（默认 ["daily", "weekly"]）
      rescan_weekly : 是否**重跑** weekly 体检（默认 False = 只读最近一轮结果）。
                    ⚠️ 实测依据（2026-10-08）：`data_quality_check_weekly` 耗时
                    **808~2318 秒**，而它的 8 条规则（全指向 stock_market_daily）
                    check_type 是 gap_scan / factor_link / pct_limit / where_count /
                    source_handoff / per_key_coverage —— **全部属不可自愈类**，
                    重跑一条都修不了。故默认只读、不重跑；确需重跑请显式打开。
      weekly_min_interval_days : rescan_weekly=True 时的节流天数（默认 6，与 weekly
                    的自然周期一致，避免每次重启都白烧十几分钟）
      verify      : 修复后是否复检（默认 True）
      dry_run     : 只分诊不执行（默认 False，排障用）
    """
    from .. import selfheal  # noqa: PLC0415 - 延迟 import，避免与 scheduler 循环
    from ..scheduler import manager as sm  # noqa: PLC0415

    p = _task_params(params, {
        "max_repairs": selfheal.DEFAULT_MAX_REPAIRS,
        "max_seconds": selfheal.DEFAULT_MAX_SECONDS,
        "groups": ["daily", "weekly"],
        "rescan_weekly": False,
        "weekly_min_interval_days": 6,
        "verify": True,
        "dry_run": False,
    })
    started = datetime.now()
    conv = selfheal.StartupConverger(
        max_repairs=int(p.get("max_repairs") or 0),
        max_seconds=int(p.get("max_seconds") or 0),
        groups=tuple(p.get("groups") or ["daily"]),
        dry_run=bool(p.get("dry_run")),
    )

    # ① 感知：跑一轮体检（失败**不阻断** —— 沿用最近一轮结果继续收敛）
    #   · daily  必跑（199 条，秒级）
    #   · weekly 默认只读不重跑（实测十几分钟且修不了任何一条，见 docstring）
    scan_tasks = ["data_quality_check"]
    if p.get("rescan_weekly"):
        if _weekly_scan_due(float(p.get("weekly_min_interval_days") or 0)):
            scan_tasks.append("data_quality_check_weekly")
        else:
            logger.info("启动收敛：weekly 体检未到节流间隔，跳过重跑（沿用最近一轮结果）")
    written = 0
    for t in scan_tasks:
        try:
            res = sm._execute(t)
            if res.get("started"):
                written += int(res.get("records_written") or 0)
            else:
                logger.info(f"启动收敛：{t} 未重跑（{res.get('reason')}），沿用最近一轮结果")
        except Exception as e:
            logger.exception(f"启动收敛：{t} 执行异常，沿用最近一轮结果: {e}")

    before = conv.scan()
    # ②③ 定位 + 分诊（表 → writers → 可自愈 / 待发布 / 需人工判定）
    plan = conv.plan(before)
    # 合并「时间遗漏班次」（原有启动补偿语义）与「DQ 缺口」（本次新增的按水位修复）
    try:
        # ⚠️ 必须解包：_find_missed() 返回 (清单, 被检查任务数) 二元组。
        # 漏写 `, _` 会让 missed 变成整个元组，merge_todo 里 `for m in missed`
        # 首个元素就是那个 list ⇒ `'list' object has no attribute 'get'`
        # （2026-10-08 首次端到端启动时真实踩到，34 项离线验收未覆盖此集成路径）。
        missed, _checked = sm._find_missed()
    except Exception as e:
        logger.warning(f"启动收敛：遗漏班次判定异常，本次仅按 DQ 缺口修复: {e}")
        missed = []
    todo = conv.merge_todo(missed, plan)
    # ④⑤ 串行执行（预算只约束 source='dq' 的额外修复）
    repair_result = conv.repair(todo, execute=sm._execute)

    # ⑥ 验证：复检一轮（与首轮同一批扫描任务）并逐规则 diff
    after = None
    if p.get("verify", True) and not p.get("dry_run"):
        try:
            for t in scan_tasks:
                res2 = sm._execute(t)
                if res2.get("started"):
                    written += int(res2.get("records_written") or 0)
            after = conv.scan()
        except Exception as e:
            logger.exception(f"启动收敛：复检异常: {e}")

    # ⑦ 交代：报告落 task_runs.run_detail（前端「作业监控」自动可见）
    report = conv.build_report(
        before=before, plan=plan, repair_result=repair_result, after=after,
        missed=missed, started_at=started, finished_at=datetime.now(),
    )
    _set_run_detail(report)
    logger.info(
        f"🩺 启动收敛完成：可自愈 {len(plan.targets)} 个任务 → 实际执行 "
        f"{repair_result['repaired_tasks']} 个；待发布 {report['pending_count']} 条；"
        f"复检已修复 {report['healed_count']} 条 / 仍不合格 {report['still_failing_count']} 条"
        f"（其中尝试后未修好 {report['still_failing_attempted_count']} 条）；"
        f"需人工 {report['needs_human_count']} 项"
    )
    # ⑦b 对外交代：只推「需要人动手/需要人知道」的结论，无结论则静默
    try:
        from ..notifier import notify_converge  # noqa: PLC0415
        notify_converge(report)
    except Exception as e:  # noqa: BLE001 - 推送失败绝不能影响收敛本身
        logger.warning(f"启动收敛：外部告警推送异常（不影响本次收敛）: {e}")
    return written or report["issues_total"]


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


def _market_is_empty() -> bool:
    """stock_market_daily 是否为零行（全新环境 / 刚跑完 schema.sql 的特征）"""
    import pymysql
    from ..db import get_db_config

    conn = pymysql.connect(**get_db_config().to_dict())
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM stock_market_daily LIMIT 1")
            return cur.fetchone() is None
    finally:
        conn.close()


def _bootstrap_ranges(p: dict) -> list[tuple]:
    """全史自举区间：[(code, 上市日, 截止日)]，只返回「起点尚未覆盖上市日」的股票

    为什么需要它（2026-10-17 新增）
    ------------------------------
    `source=dq_gap` 依赖 `dq_gap_detail`，而缺口是 `gap_scan` 用 `LAG()` 在**已有行之间**
    算出来的 —— **空表零行 ⇒ 零缺口 ⇒ 定向回补永远 return 0**。也就是说 L3 回补闭环
    无法自举历史，全新环境想补齐全史，原先只能手工拼 6000 多个区间（data-bootstrap.md
    路径 C 的写法），实际没人会那么干。

    **幂等 & 可轮次推进**：`NOT EXISTS` 只挑「日线最早日 > 上市日 + 5 天」的票，
    跑完一轮后这批自然不再入选，下一轮自动推进到下一批。所以 `max_codes` 可以当
    「本轮批次大小」安全使用，反复触发即可把全市场推完，不会每次都重拉同一批。

    口径：B 股（200/900 段）排除，与 `StockDailyIncrementalCollector.get_stock_list` 一致
    （数据源不支持）；退市股**保留**——它们的历史同样不可再生，能补一只是一只。
    """
    import pymysql
    from datetime import datetime as _dt
    from ..db import get_db_config

    p = p or {}
    end = str(p.get("end_date") or _dt.now().strftime("%Y%m%d"))
    max_codes = int(p.get("max_codes", 0))

    # 刻意不用 LIKE：带参数的 SQL 会走 pymysql 的 `query % args`，'200%' 里的 % 会被
    # 当成格式符抛 ValueError（本项目 2026-09 踩过），LEFT(...) 无此问题。
    sql = (
        "SELECT a.stock_code, a.list_date FROM stock_info a "
        "WHERE LEFT(a.stock_code, 3) NOT IN ('200', '900') "
        "  AND NOT EXISTS ("
        "      SELECT 1 FROM stock_market_daily b "
        "      WHERE b.stock_code = a.stock_code "
        "        AND b.trade_date <= DATE_ADD(COALESCE(a.list_date, '1990-12-19'), INTERVAL 5 DAY)"
        "  ) "
        "ORDER BY a.stock_code"
    )
    args: list = []
    if max_codes > 0:
        sql += " LIMIT %s"
        args.append(max_codes)

    conn = pymysql.connect(**get_db_config().to_dict())
    try:
        with conn.cursor() as cur:
            cur.execute(sql, args)
            rows = cur.fetchall()
    finally:
        conn.close()
    return [(r[0], r[1].strftime("%Y%m%d") if r[1] else "19901219", end) for r in rows]


def run_daily_backfill(params: dict) -> int:
    """日线定向回补 / 全史自举

    params:
      source   : "dq_gap"（默认，读 dq_gap_detail 中 status='open' 的疑似缺口）
                 | "ranges"（显式区间，自己给）
                 | "all_stocks"（全市场全史自举：按上市日→今天，自动分批、幂等推进）
      ranges   : [["600519","20010827","20011231"], ...]（source=ranges 时必填）
      risk     : "all"（默认）| "high"（只补 ≥365 天高危缺口；仅对 dq_gap 生效）
      max_codes: 0（0=不限；all_stocks 下即「本轮批次大小」，反复触发可持续推进）
      end_date : all_stocks 的截止日（默认今天）
      adjust   : "qfq"（默认，与主表口径一致）

    回补成功后将该票缺口明细置 status='fixed'；n=0 的保持 open 待人工判定
    （可能是停牌/退市等合理缺失，应人工置 ignored）。

    ⚠️ 空库自动自举（2026-10-17）：`source=dq_gap` 在 `stock_market_daily` 为空时
    **必然无区间** —— 缺口靠 `gap_scan` 的 `LAG()` 在已有行之间算，空表零缺口。
    若不自动切换，全新环境跑这个任务永远是「无待补区间，return 0」，历史永远长不出来。
    故此处判定：日线空表 + dq_gap_detail 无 open 缺口 ⇒ 自动改走 all_stocks。
    """
    from datetime import datetime as _dt
    import pymysql
    from ..db import get_db_config

    p = _task_params(params, {"source": "dq_gap", "risk": "all", "max_codes": 0, "adjust": "qfq"})
    source = p.get("source", "dq_gap")
    if source == "ranges":
        ranges = [tuple(x) for x in (p.get("ranges") or [])]
    elif source == "all_stocks":
        ranges = _bootstrap_ranges(p)
        logger.info(f"全史自举：本轮 {len(ranges)} 个区间（max_codes={p.get('max_codes', 0)}）")
    else:
        ranges = _gap_ranges_from_dq(p)
        if not ranges and _market_is_empty():
            # 全新环境：日线空表 + 无缺口明细 ⇒ dq_gap 永远补不动，自动降级为全史自举
            logger.warning("定向回补：日线表为空且无缺口明细，自动切换为 all_stocks 全史自举")
            ranges = _bootstrap_ranges(p)
    if not ranges:
        logger.info("定向回补：无待补区间（dq_gap_detail 无 open 缺口，或未提供 ranges）")
        return 0

    collector = StockDailyIncrementalCollector(
        adjust=p.get("adjust", ""),
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

    params: sample_size(50) / seed(42) / adjust(""=实际价，同主表新口径)
    """
    p = _task_params(params, {"sample_size": 50, "seed": 42, "adjust": ""})
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


def run_msci_index_sync(params: dict) -> int:
    """MSCI 指数日线同步（写 overseas_index_daily：ACWI/World/EAFE/EM，USD 收盘 LEVEL）

    2026-10-20 国际指数章节 —— MSCI 官方公开 chart API，免 Key、无 WAF，回 2000 年。
    字段口径与海外指数不同（无 OHLC，只有收盘），故独立采集器、共写一张表。
    params: timeout_sec(60)
    """
    p = _task_params(params, {"timeout_sec": 60})
    collector = MsciIndexSyncCollector(timeout_sec=float(p.get("timeout_sec", 60)))
    result = _collector_run(collector)
    if result["error_count"] > 0:
        logger.warning(f"⚠️ MSCI 指数 {result['error_count']} 项异常: {result['errors'][:3]}")
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


def run_account_open_sync(params: dict) -> int:
    """新增开户同步（stock_account_monthly：上交所官方 + 东财历史拼接）

    杠杆与资金行为·开户腿 —— 上交所月报接口逐月积累（官方口径）。
    params: timeout_sec(90)
    """
    from ..collectors.account_open_sync import AccountOpenSyncCollector
    p = _task_params(params, {"timeout_sec": 90})
    collector = AccountOpenSyncCollector(timeout_sec=float(p.get("timeout_sec", 90)))
    result = _collector_run(collector)
    return result["records_written"]


def run_etf_flow_sync(params: dict) -> int:
    """ETF 资金流向同步（etf_share_daily：东财全市场 ETF 份额快照，逐日积累）

    杠杆与资金行为·ETF 腿 —— 份额变化 × 价格 = 净流入估算。
    params: timeout_sec(120)
    """
    from ..collectors.etf_flow_sync import EtfFlowSyncCollector
    p = _task_params(params, {"timeout_sec": 120})
    collector = EtfFlowSyncCollector(timeout_sec=float(p.get("timeout_sec", 120)))
    result = _collector_run(collector)
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


def run_cash_flow_sync(params: dict) -> int:
    """经营性现金流同步（stock_cash_flow_ths：同花顺现金流量表，逐股增量）

    盈利质量与成长·现金流腿 —— 经营活动现金流量净额，逐成分股聚合→指数级现金流趋势。
    params: max_stocks(0=全量)、timeout_sec(30)
    """
    from ..collectors.cash_flow_sync import CashFlowSyncCollector
    p = _task_params(params, {"max_stocks": 0, "timeout_sec": 30})
    collector = CashFlowSyncCollector(
        max_stocks=int(p.get("max_stocks", 0)),
        timeout_sec=float(p.get("timeout_sec", 30)),
    )
    result = _collector_run(collector)
    return result["records_written"]


def run_stock_pb_sync(params: dict) -> int:
    """个股 PB 快照同步（stock_pb_daily：东财实时行情，双通道降级）

    估值水平·PB 离散度腿 —— 个股市净率横截面，逐日积累。
    params: timeout_sec(120)
    """
    from ..collectors.stock_pb_sync import StockPbSyncCollector
    p = _task_params(params, {"timeout_sec": 120})
    collector = StockPbSyncCollector(timeout_sec=float(p.get("timeout_sec", 120)))
    result = _collector_run(collector)
    return result["records_written"]


def run_overseas_tech_derive(params: dict) -> int:
    """国际指数技术面物化（env_overseas_tech_daily，纯库内，读 overseas_index_daily）

    国际指数章节·技术面 4 块的数据底座：均线/MACD/RSI/波动率/回撤/相对强弱/Beta/相关性。
    挂 msci_index_sync / overseas_index_sync 之后链式触发。
    """
    from ..collectors.overseas_tech_derive import OverseasTechDeriveCollector
    collector = OverseasTechDeriveCollector(
        lookback_years=int(params.get("lookback_years") or 0))
    result = _collector_run(collector)
    return result["records_written"]


def run_vix_sync(params: dict) -> int:
    """VIX 恐慌指数同步（vix_daily：新浪 gi 直连）

    国际指数章节·波动率与风险块：标普500 隐含波动率。
    params: timeout_sec(20)
    """
    from ..collectors.vix_sync import VixSyncCollector
    p = _task_params(params, {"timeout_sec": 20})
    collector = VixSyncCollector(timeout_sec=float(p.get("timeout_sec", 20)))
    result = _collector_run(collector)
    return result["records_written"]


def run_cftc_position_sync(params: dict) -> int:
    """CFTC 货币期货非商业持仓同步（cftc_position_weekly：akshare，周频）

    国际指数章节·资金流与仓位块：机构多空敞口。
    params: timeout_sec(60)
    """
    from ..collectors.cftc_position_sync import CftcPositionSyncCollector
    p = _task_params(params, {"timeout_sec": 60})
    collector = CftcPositionSyncCollector(timeout_sec=float(p.get("timeout_sec", 60)))
    result = _collector_run(collector)
    return result["records_written"]


def run_index_valuation_intl_sync(params: dict) -> int:
    """国际指数估值同步（index_valuation_intl：Siblis 免费 API，稀疏快照）

    国际指数章节·基本面块：pe_ttm/pe_fwd/cape 三指标（PB/股息率免费源不可得）。
    params: timeout_sec(20)
    """
    from ..collectors.index_valuation_intl_sync import IndexValuationIntlSyncCollector
    p = _task_params(params, {"timeout_sec": 20})
    collector = IndexValuationIntlSyncCollector(timeout_sec=float(p.get("timeout_sec", 20)))
    result = _collector_run(collector)
    return result["records_written"]


def run_index_fundamental_intl_sync(params: dict) -> int:
    """国际指数估值扩展同步（标普500 股息率，Shiller 学术长历史）

    国际指数章节·基本面块：补 dividend_yield 指标（回溯 12 个月股息÷月末价）。
    ⚠️ Shiller 数据更新滞后（完整到 2023-06），PB/其余指数股息率免费源不可得，如实留空。
    params: timeout_sec(90)
    """
    from ..collectors.index_fundamental_intl_sync import IndexFundamentalIntlSyncCollector
    p = _task_params(params, {"timeout_sec": 90})
    collector = IndexFundamentalIntlSyncCollector(timeout_sec=float(p.get("timeout_sec", 90)))
    result = _collector_run(collector)
    return result["records_written"]


def run_global_macro_sync(params: dict) -> int:
    """全球宏观年度序列同步（global_macro_annual：IMF WEO，gdp_growth/inflation/debt_gdp）

    国际指数章节·宏观块：12 主要经济体 × 3 指标，含历史+预测（半年更新）。
    params: timeout_sec(120)
    """
    from ..collectors.global_macro_sync import GlobalMacroSyncCollector
    p = _task_params(params, {"timeout_sec": 120})
    collector = GlobalMacroSyncCollector(timeout_sec=float(p.get("timeout_sec", 120)))
    result = _collector_run(collector)
    return result["records_written"]


def run_eu_hicp_sync(params: dict) -> int:
    """欧元区 HICP 月度通胀同步（eu_hicp_monthly：Eurostat，及时月度）

    国际指数章节·宏观块：本域唯一可得的及时月度国际通胀源（ECB 目标基准口径）。
    params: timeout_sec(60)
    """
    from ..collectors.eu_hicp_sync import EuHicpSyncCollector
    p = _task_params(params, {"timeout_sec": 60})
    collector = EuHicpSyncCollector(timeout_sec=float(p.get("timeout_sec", 60)))
    result = _collector_run(collector)
    return result["records_written"]


def run_ovx_sync(params: dict) -> int:
    """OVX 原油波动率同步（ovx_daily：Cboe 官方 CSV，日频）

    国际环境章节·技术面①市场波动块。全量覆盖（REPLACE）。
    params: timeout_sec(30)
    """
    from ..collectors.global_risk_sync import OvxSyncCollector
    p = _task_params(params, {"timeout_sec": 30})
    collector = OvxSyncCollector(timeout_sec=float(p.get("timeout_sec", 30)))
    result = _collector_run(collector)
    return result["records_written"]


def run_gpr_sync(params: dict) -> int:
    """GPR 地缘政治风险指数同步（gpr_daily：Caldara & Iacoviello，日频）

    国际环境章节·技术面① + 基本面①地缘块。全量覆盖（REPLACE）。
    params: timeout_sec(60)
    """
    from ..collectors.global_risk_sync import GprSyncCollector
    p = _task_params(params, {"timeout_sec": 60})
    collector = GprSyncCollector(timeout_sec=float(p.get("timeout_sec", 60)))
    result = _collector_run(collector)
    return result["records_written"]


def run_epu_sync(params: dict) -> int:
    """美国 EPU 经济政策不确定性同步（epu_daily：Baker/Bloom/Davis，日频）

    国际环境章节·技术面④情绪不确定性块。全量覆盖（REPLACE）。
    ⚠️ policyuncertainty.com 响应慢，timeout 默认 90s。
    params: timeout_sec(90)
    """
    from ..collectors.global_risk_sync import EpuSyncCollector
    p = _task_params(params, {"timeout_sec": 90})
    collector = EpuSyncCollector(timeout_sec=float(p.get("timeout_sec", 90)))
    result = _collector_run(collector)
    return result["records_written"]


def run_wui_sync(params: dict) -> int:
    """WUI 世界不确定性指数同步（wui_monthly：IMF 编制，月频）

    国际环境章节·技术面④情绪不确定性块。全量覆盖（REPLACE）。
    params: timeout_sec(60)
    """
    from ..collectors.global_uncertainty_sync import WuiSyncCollector
    p = _task_params(params, {"timeout_sec": 60})
    collector = WuiSyncCollector(timeout_sec=float(p.get("timeout_sec", 60)))
    result = _collector_run(collector)
    return result["records_written"]


def run_tpu_sync(params: dict) -> int:
    """TPU 贸易政策不确定性同步（tpu_monthly：Caldara et al.，月频）

    国际环境章节·技术面④ + 基本面②经贸块。全量覆盖（REPLACE）。
    params: timeout_sec(60)
    """
    from ..collectors.global_uncertainty_sync import TpuSyncCollector
    p = _task_params(params, {"timeout_sec": 60})
    collector = TpuSyncCollector(timeout_sec=float(p.get("timeout_sec", 60)))
    result = _collector_run(collector)
    return result["records_written"]


def run_fsi_sync(params: dict) -> int:
    """Püttmann 报纸金融压力指数同步（fsi_monthly：1889-2016，已停更）

    国际环境章节·技术面①市场波动块。一次性落库，后续仅兜底。
    params: timeout_sec(60)
    """
    from ..collectors.global_uncertainty_sync import FsiSyncCollector
    p = _task_params(params, {"timeout_sec": 60})
    collector = FsiSyncCollector(timeout_sec=float(p.get("timeout_sec", 60)))
    result = _collector_run(collector)
    return result["records_written"]


def run_dr_repo_sync(params: dict) -> int:
    """DR001/DR007 存款类机构质押式回购利率同步（dr_repo_daily，日频）

    国内货币章节·技术面②价格指标块（短端资金「温度计」）。
    ⚠️ 源侧硬限制：repo_rate_hist 单次请求最长约 300 天，采集器按 300 天/段自
       2025-01-01 回溯分段拉取后全量 upsert。
    params: timeout_sec(60)
    """
    from ..collectors.dr_repo_sync import DrRepoSyncCollector
    p = _task_params(params, {"timeout_sec": 60})
    collector = DrRepoSyncCollector(timeout_sec=float(p.get("timeout_sec", 60)))
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


def run_cn_liquidity_sync(params: dict) -> int:
    """中国货币供应 / 社融 / 信贷 / 准备金率同步（cn_liquidity_monthly，月度）

    货币流动性批次 2A「数量维度」——价格只反映边际供需，数量反映水位。
    ⚠️ 上游时效实测：M0/M1/M2 与信贷至 2026-08，**社融源停更于 2026-04**
      （`macro_china_shrzgm` 出自商务数据中心，东财 datacenter 无对应报告名），
      故社融列允许整体为空，逐列水位由采集器 run_steps 分别暴露。
    params: timeout_sec(120)
    """
    p = _task_params(params, {"timeout_sec": 120})
    collector = CnLiquiditySyncCollector(timeout_sec=float(p.get("timeout_sec", 120)))
    result = _collector_run(collector)
    if result["error_count"] > 0:
        logger.warning(f"⚠️ 中国流动性 {result['error_count']} 项异常: {result['errors'][:3]}")
    return result["records_written"]


def run_cn_cb_balance_sync(params: dict) -> int:
    """中国央行资产负债表同步（cn_cb_balance_monthly，月度，28 科目）

    货币流动性批次 2A「央行行为」——「对其他存款性公司债权」就是央行通过
    MLF/逆回购/PSL 投给银行的资金：扩张 = 放水、收缩 = 收水（中国式 QE 的直接观测）。
    ⚠️ 该表**只镜像源值、不存派生列**；源列名缺失即 raise（防拼错静默写 NULL）。
    params: timeout_sec(90)
    """
    p = _task_params(params, {"timeout_sec": 90})
    collector = CnCbBalanceSyncCollector(timeout_sec=float(p.get("timeout_sec", 90)))
    result = _collector_run(collector)
    return result["records_written"]


def run_cb_policy_rate_sync(params: dict) -> int:
    """多国央行政策利率决议同步（cb_policy_rate，美/欧/日/英）

    货币流动性批次 2B「央行行为」——全球央行方向共振度。
    ⚠️ **上游整体停更**：`macro_bank_{usa,euro,japan,english}_interest_rate` 全族
      最后一条有效「今值」统一停在 2025-07~08（表内更晚的日期行今值为空）。
      采集器只写入「今值非空」的有效决议行，并在 note 里强制标注，**不改判成功**。
    params: timeout_sec(90)
    """
    p = _task_params(params, {"timeout_sec": 90})
    collector = CbPolicyRateSyncCollector(timeout_sec=float(p.get("timeout_sec", 90)))
    result = _collector_run(collector)
    if result["error_count"] > 0:
        logger.warning(f"⚠️ 央行利率 {result['error_count']} 项异常: {result['errors'][:3]}")
    return result["records_written"]


def run_usd_index_sync(params: dict) -> int:
    """美元指数自算 + DINIW 快照标定（global_usd_index_daily）

    货币流动性批次 2B「外部约束」——G1 全球美元总闸门。
    官方历史源全数不可用（东财 push2his 域名不通、新浪 hq/daily 不支持 UDI/DINIW），
    故用 `currency_boc_safe` 的 6 个 DXY 成分货币按 ICE 标准公式自算；
    另用新浪实时快照逐日累积做标定基准（实测偏差 +0.085%）。
    ⚠️ 该表不能与 `market_current_sync` 下的**实时行情表**混用同一份 TTL；
      本任务写的是「按日的历史值」，快照缺失时降级为仅有自算列。
    params: timeout_sec(90) / first_lookback_days(3650 首次回扫 10 年)
    """
    p = _task_params(params, {"timeout_sec": 90, "first_lookback_days": 3650})
    collector = UsdIndexSyncCollector(
        timeout_sec=float(p.get("timeout_sec", 90)),
        first_lookback_days=int(p.get("first_lookback_days", 3650)),
    )
    result = _collector_run(collector)
    if result["error_count"] > 0:
        logger.warning(f"⚠️ 美元指数 {result['error_count']} 项异常: {result['errors'][:3]}")
    return result["records_written"]


# ============ 货币流动性补源 v2.0（无 FRED 密钥版）批次 A/B/C/D ============

def run_us_fed_balance_sync(params: dict) -> int:
    """美国·美联储资产负债表周度（us_fed_balance_weekly，H.4.1）

    批次 A「美国层双轨」主口径之一。DBnomics（免 Key）历史完整（2002-12 起 1241 行），
    官网 H.4.1 只做最新一期交叉校验（实测偏差 0.000000%）。
    ⚠️ 采集器内置 `session.trust_env=False` 绕过本机代理（代理会阻断国际源）。
    params: timeout_sec(150)
    """
    p = _task_params(params, {"timeout_sec": 150})
    collector = UsFedBalanceSyncCollector(timeout_sec=float(p.get("timeout_sec", 150)))
    result = _collector_run(collector)
    if result["error_count"] > 0:
        logger.warning(f"⚠️ 美联储资产负债表 {result['error_count']} 项异常: {result['errors'][:3]}")
    return result["records_written"]


def run_us_money_supply_sync(params: dict) -> int:
    """美国·货币供应量月度（us_money_supply_monthly，H.6：M1/M2/基础货币/准备金）

    批次 A「美国层双轨」主口径之一。DBnomics 历史完整（1959-01 起 812 行），
    官网 H.6 只做最新一期交叉校验（实测偏差 0.0000%）。
    params: timeout_sec(120)
    """
    p = _task_params(params, {"timeout_sec": 120})
    collector = UsMoneySupplySyncCollector(timeout_sec=float(p.get("timeout_sec", 120)))
    result = _collector_run(collector)
    if result["error_count"] > 0:
        logger.warning(f"⚠️ 美国货币供应 {result['error_count']} 项异常: {result['errors'][:3]}")
    return result["records_written"]


def run_us_money_market_sync(params: dict) -> int:
    """美国·货币市场日度（us_money_market_daily：SOFR/EFFR/ON RRP/TGA/国债）

    批次 A「美国层」短端抓手。NY Fed（SOFR/EFFR/ON RRP）+ 财政部（TGA/债务）零 Key 直连。
    ⚠️ 多源并表，增量水位按列取（否则只有快照的行会顶前水位、静默跳过另一条腿）。
    params: timeout_sec(60)
    """
    p = _task_params(params, {"timeout_sec": 60})
    collector = UsMoneyMarketSyncCollector(timeout_sec=float(p.get("timeout_sec", 60)))
    result = _collector_run(collector)
    if result["error_count"] > 0:
        logger.warning(f"⚠️ 美国货币市场 {result['error_count']} 项异常: {result['errors'][:3]}")
    return result["records_written"]


def run_eu_money_supply_sync(params: dict) -> int:
    """欧元区·货币供应量月度（eu_money_supply_monthly，ECB BSI：M1/M2/M3）

    批次 B「欧元区」。DBnomics 免 Key（1980-01 起 559 行）。源单一 ⇒ 三条全失败才 raise，
    单条失败记 error 继续。
    params: timeout_sec(120)
    """
    p = _task_params(params, {"timeout_sec": 120})
    collector = EuMoneySupplySyncCollector(timeout_sec=float(p.get("timeout_sec", 120)))
    result = _collector_run(collector)
    if result["error_count"] > 0:
        logger.warning(f"⚠️ 欧元区货币供应 {result['error_count']} 项异常: {result['errors'][:3]}")
    return result["records_written"]


def run_cn_omo_sync(params: dict) -> int:
    """中国·央行公开市场操作日度（cn_omo_daily，人行「公开市场业务」各栏目）

    批次 C「中国层」短端抓手（M2 是月度存量、OMO 是日度流量）。
    含交易公告 + 买断式逆回购两个栏目；零操作日也发公告（操作量=0）。
    ⚠️ 人行官网有反爬限流，采集器内置全局请求节流 + 退避（并发 4 会触发 403）。
    params: timeout_sec(900) / max_pages(25) / full(false)
    """
    p = _task_params(params, {"timeout_sec": 900, "max_pages": 25, "full": False})
    collector = CnOmoSyncCollector(
        timeout_sec=float(p.get("timeout_sec", 900)),
        max_pages=int(p.get("max_pages", 25)),
        workers=int(p.get("workers", 4)),
        full=bool(p.get("full", False)),
    )
    result = _collector_run(collector)
    if result["error_count"] > 0:
        logger.warning(f"⚠️ 央行 OMO {result['error_count']} 项异常: {result['errors'][:3]}")
    return result["records_written"]


def run_global_liquidity_sync(params: dict) -> int:
    """全球·BIS 全球流动性指标季度（global_liquidity_bis，WS_GLI 美元计价跨境信贷）

    批次 D「全球层」。BIS SDMX API 免 Key（2000-03 起 6720 行、19 个借款人国家/地区）。
    离岸美元 = borrowers_cty<>'US' 且 sector='N' 且 l_pos_type='I'。
    params: timeout_sec(90)
    """
    p = _task_params(params, {"timeout_sec": 90})
    collector = GlobalLiquiditySyncCollector(timeout_sec=float(p.get("timeout_sec", 90)))
    result = _collector_run(collector)
    if result["error_count"] > 0:
        logger.warning(f"⚠️ 全球流动性 {result['error_count']} 项异常: {result['errors'][:3]}")
    return result["records_written"]


def run_cn_reserve_sync(params: dict) -> int:
    """中国·官方外储（亿美元）+ 黄金储备（万盎司）月度（cn_reserve_monthly）

    货币流动性补源 v2.1 批次 **C2**「中国层·央行对外资产」的**国际可比口径**。
    ⚠️ 先勘误：`cn_cb_balance_monthly` **已有**人民币表内口径的 fx_reserve / monetary_gold
      （亿元，到 2026-08 未停更）⇒ 本表只补 akshare 独有的两项（亿美元 / 万盎司），
      **新建而非扩列**，避免与既有表重复且口径打架（三套口径严禁相加）。
    ⚠️ 源 `统计时间` 是字符串 `'YYYY.M'`：**字符串序 ≠ 时间序**（2025.10 < 2025.2），
      akshare 返回的 df 本身就是错序的 ⇒ 采集器按 (年,月) 元组排序（`parse_cn_month`），
      **禁止 iloc[-1]**。
    ⚠️ 双口径隐含折算率的软断言**在采集器内**（跨表、DQ 无 JOIN），且只做区间 + 变动，
      **不按市价互校**（该比率是历史成本口径，同期市场汇率约 7.0~7.3，按市价会稳定误报）。
    params: timeout_sec(90)
    """
    p = _task_params(params, {"timeout_sec": 90})
    collector = CnReserveSyncCollector(timeout_sec=float(p.get("timeout_sec", 90)))
    result = _collector_run(collector)
    if result["error_count"] > 0:
        logger.warning(f"⚠️ 中国外储/黄金 {result['error_count']} 项异常: {result['errors'][:3]}")
    xc = result.get("xcheck") or {}
    if xc.get("out_of_range") or xc.get("jumps"):
        logger.warning(f"⚠️ 外储双口径软断言命中: 越界 {len(xc.get('out_of_range') or [])} / "
                       f"跳变 {len(xc.get('jumps') or [])}")
    return result["records_written"]


# ============ 任务注册表（所有 run_* 函数定义之后） ============
TASKS = {
    "stock_daily_incr": run_stock_daily_incr,
    "ai_concept_analysis": run_ai_concept_analysis,
    "market_current_sync": run_market_current_sync,
    "trade_calendar_sync": run_trade_calendar_sync,
    "news_fetch": run_news_fetch,
    "concept_market_sync": run_concept_market_sync,
    # 2026-10-17 归档扩展行情（stock_market_daily_ex 原无写入方，1690 万行）
    "stock_market_daily_ex_sync": run_stock_market_daily_ex_sync,
    # 2026-10-17 基金持仓（stock_hold_by_fund 原无写入方，11.3 万行）
    "fund_hold_sync": run_fund_hold_sync,
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
    "msci_index_sync": run_msci_index_sync,
    "currency_boc_sync": run_currency_boc_sync,
    "fund_new_issue_sync": run_fund_new_issue_sync,
    "stock_repurchase_sync": run_stock_repurchase_sync,
    # 2026-09-25 货币流动性批次 2（三张新表 + 一个自算派生表）
    "cn_liquidity_sync": run_cn_liquidity_sync,
    "cn_cb_balance_sync": run_cn_cb_balance_sync,
    "cb_policy_rate_sync": run_cb_policy_rate_sync,
    "usd_index_sync": run_usd_index_sync,
    # 2026-09-25 货币流动性补源 v2.0（无 FRED 密钥版）批次 A/B/C/D
    "us_fed_balance_sync": run_us_fed_balance_sync,
    "us_money_supply_sync": run_us_money_supply_sync,
    "us_money_market_sync": run_us_money_market_sync,
    "eu_money_supply_sync": run_eu_money_supply_sync,
    "cn_omo_sync": run_cn_omo_sync,
    "global_liquidity_sync": run_global_liquidity_sync,
    # 2026-09-26 货币流动性补源 v2.1 批次 C2（中国层·央行对外资产：外储/黄金）
    "cn_reserve_sync": run_cn_reserve_sync,
    # 2026-10-08 启动收敛（自愈闭环 ·「启动即收敛」）—— 由 scheduler._boot_pipeline
    # 在每次进程启动时自动调用，也可前端手动触发一次全量收敛
    "startup_converge": run_startup_converge,
    # 2026-10-18 投资环境模块（国内指数页）：技术/基本面物化 + 宏观序列
    "env_derive": run_env_derive,
    "env_macro_sync": run_env_macro_sync,
    # 2026-10-20 筹码分布（B 方案）：个股筹码分布物化（纯库内，读 stock_market_daily）
    "stock_cyq_sync": run_stock_cyq_sync,
    # 2026-10-20 杠杆与资金行为（三指标之开户/ETF）：上交所官方开户 + 东财 ETF 份额
    "account_open_sync": run_account_open_sync,
    "etf_flow_sync": run_etf_flow_sync,
    # 2026-10-20 估值水平/盈利质量章节补齐：经营性现金流 + 个股 PB 离散度
    "cash_flow_sync": run_cash_flow_sync,
    "stock_pb_sync": run_stock_pb_sync,
    # 2026-10-21 国际指数章节：技术面物化 + VIX + CFTC 持仓 + 估值
    "overseas_tech_derive": run_overseas_tech_derive,
    "vix_sync": run_vix_sync,
    "cftc_position_sync": run_cftc_position_sync,
    "index_valuation_intl_sync": run_index_valuation_intl_sync,
    # 2026-10-21 国际指数基本面：估值扩展 + 全球宏观 + 欧元区 HICP
    "index_fundamental_intl_sync": run_index_fundamental_intl_sync,
    "global_macro_sync": run_global_macro_sync,
    "eu_hicp_sync": run_eu_hicp_sync,
    # 2026-10-21 国际环境：波动率/地缘/不确定性指数（OVX/GPR/EPU/WUI/TPU/FSI）
    "ovx_sync": run_ovx_sync,
    "gpr_sync": run_gpr_sync,
    "epu_sync": run_epu_sync,
    "wui_sync": run_wui_sync,
    "tpu_sync": run_tpu_sync,
    "fsi_sync": run_fsi_sync,
    # 2026-10-21 国内货币：DR001/DR007 存款类机构质押式回购利率
    "dr_repo_sync": run_dr_repo_sync,
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
