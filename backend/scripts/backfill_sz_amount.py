#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
回填 399001（深证成指）/ 399006（创业板指）成交额为东财「板块全市场」口径。

背景（2026-10-24）：国证官网 index_hist_cni 对这两只指数给的是「指数成分股成交额」
（约为主流口径一半，且倍率随成分股占比浮动），与同花顺/腾讯「板块全市场成交额」不一致。
采集器 _fetch_sz 已改走东财原始 socket 通道（EM+TENCENT），本脚本据此全史重建 amount。

幂等：backfill 内部先 DELETE 窗口内旧行再 INSERT，可重复执行；数据源标记为 EM+TENCENT。
"""
import sys

sys.path.insert(0, ".")
from app.collectors.index_market_sync import IndexMarketSyncCollector  # noqa: E402

CODES = ["399001", "399006"]  # 深证成指（1991 起）、创业板指（2010 起）
START = "1991-01-01"


def main():
    c = IndexMarketSyncCollector()
    for code in CODES:
        print(f"=== 回填 {code} 全史成交额（东财口径）===")
        r = c.backfill(start=START, codes=[code])
        print(f"  {code}: 写入 {r['records_written']} 行, 错误 {r['error_count']} 条")
        if r["errors"]:
            for e in r["errors"]:
                print(f"    ❌ {e}")
    print("=== 回填完成 ===")


if __name__ == "__main__":
    main()
