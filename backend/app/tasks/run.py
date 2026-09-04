#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
InfoData 采集任务统一运行入口
- 从 task_config 读取任务配置（启停/cron/参数）
- 每个任务执行时记录 task_runs（running -> success/failed + 写入条数）
- 支持命令行手动触发：python -m app.tasks.run stock_daily_incr
- TASKS 注册表位于文件底部（所有 run_* 函数定义之后，避免模块加载时 NameError）
"""
import argparse
import logging
import sys
from datetime import datetime

from ..db import get_db_config
from ..task_recorder import TaskRecorder
from ..collectors.stock_daily_incr import StockDailyIncrementalCollector
from ..collectors.market_current_sync import MarketCurrentSyncCollector
from ..collectors.trade_calendar_sync import TradeCalendarSyncCollector
from ..collectors.news_fetch import NewsFetchCollector
from ..collectors.concept_market_sync import ConceptMarketSyncCollector
from ..analysis import concept_ai

logger = logging.getLogger("infodata.tasks")


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
    result = collector.run()
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
    result = collector.run()
    if result["error_count"] > 0:
        raise RuntimeError("; ".join(result["errors"]))
    return result["records_written"]


def run_trade_calendar_sync(params: dict) -> int:
    """交易日历补齐（默认当年+次年）"""
    p = _task_params(params, {})
    years = p.get("years")  # 可选: [2026, 2027]
    collector = TradeCalendarSyncCollector(years=years)
    result = collector.run()
    return result["records_written"]


def run_news_fetch(params: dict) -> int:
    """资讯采集：财联社 cls + 东财 em"""
    p = _task_params(params, {"sources": ["em"], "max_pages": 3})
    sources = p.get("sources") or ["em"]
    collector = NewsFetchCollector(sources=sources, max_pages=int(p.get("max_pages", 3)))
    result = collector.run()
    if result["error_count"] > 0:
        raise RuntimeError("; ".join(result["errors"]))
    return result["records_written"]


def run_concept_market_sync(params: dict) -> int:
    """同花顺概念板块行情增量同步（概念 K 线 / 概念排名数据源）"""
    p = _task_params(params, {})
    collector = ConceptMarketSyncCollector(start_date=p.get("start_date"))
    result = collector.run()
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
    if result.get("errors"):
        logger.warning(f"⚠️ 部分事件分析失败: {result['errors'][:3]}")
    return analyzed


# ============ 任务注册表（所有 run_* 函数定义之后） ============
TASKS = {
    "stock_daily_incr": run_stock_daily_incr,
    "ai_concept_analysis": run_ai_concept_analysis,
    "market_current_sync": run_market_current_sync,
    "trade_calendar_sync": run_trade_calendar_sync,
    "news_fetch": run_news_fetch,
    "concept_market_sync": run_concept_market_sync,
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
    try:
        written = TASKS[task_name](params)
        recorder.finish(records_written=written)
        logger.info(f"✅ 任务 {task_name} 完成，写入 {written} 条")
        return written
    except Exception as e:
        recorder.finish(records_written=0, error_message=str(e))
        logger.error(f"❌ 任务 {task_name} 失败: {e}")
        raise


def main():
    _setup_logging()
    parser = argparse.ArgumentParser(description="InfoData 采集任务")
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
