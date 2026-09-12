#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
One-shot manual runner for IndexMarketSyncCollector.backfill().

调用方式：
  python -m app.run_index_backfill                 # 默认回补 2024-01-01 ~ 今天
  python -m app.run_index_backfill 2025-01-01      # 指定起始日

作用：
- 用中证指数官网（沪/中证/北证 9 个）+ 国证指数（深证 4 个，成交额）重建 dc_index_market 区间数据
- 一次性补齐 2025-09-23 起因东财源不可达而缺失的成交额 / 涨跌幅，并回补 931775 停更缺口
- 顺带修同码多名的历史劈叉（399006「创业板」→「创业板指」）
"""
import logging
import sys

from .collectors.index_market_sync import IndexMarketSyncCollector


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    start = sys.argv[1] if len(sys.argv) > 1 else "2024-01-01"
    collector = IndexMarketSyncCollector()
    result = collector.backfill(start)
    print("结果:", result["note"])
    for d in result.get("detail", []):
        print("  -", d)
    if result.get("errors"):
        print("异常:")
        for e in result["errors"]:
            print("  !", e)
    return 0 if result["error_count"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
