#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
One-shot manual runner for MarketCurrentSyncCollector.

调用方式：
  INFO_DATA_DB_HOST=127.0.0.1 INFO_DATA_DB_PASSWORD=root INFO_DATA_DB_NAME=adata \
    python -m app.run_market_current

作用：
- 直接调用 MarketCurrentSyncCollector.run() 重建 stock_market_current
- 绕过 scheduler 的「今日已跑跳过」保护
- 用于 daily_incr 跑完后追加同步 current（如 9-11 故障后恢复）
"""
import logging
import sys

from .db import get_db_config
from .collectors.market_current_sync import MarketCurrentSyncCollector


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    # 强制忽略今日幂等：直接用 collector.run()，不走 scheduler
    # run() 里有 "WHERE DATE(update_time) = CURDATE()" 判断，需要先清掉当日数据
    import pymysql
    conn = pymysql.connect(**get_db_config().to_dict())
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM stock_market_current WHERE DATE(update_time) = CURDATE()")
            deleted = cur.rowcount
        conn.commit()
        print(f"已清理 stock_market_current 当日 {deleted} 行")
    finally:
        conn.close()

    collector = MarketCurrentSyncCollector()
    result = collector.run()
    print("结果:", result)
    return 0 if result["error_count"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
