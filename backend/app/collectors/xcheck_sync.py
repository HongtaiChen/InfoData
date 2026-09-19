#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""交叉印证背离数日频物化（market_xcheck_daily）

一、为什么需要这张表（2026-09-19 立）
  market_wind 卡片判读条此前写「7 项中 4 项背离」，但实测近 16 个交易日的背离数序列为
  `[4,3,4,3,2,3,3,4,4,4,4,4]` —— **中位即 4、最近连续 5 日为 4**：它是常态，不是信号。
  每天把 4 当头条喊，等于每天喊狼来了。要让「4」重新有信息量，必须能回答
  「**相对自己的历史算不算多**」，即需要一条背离数的历史序列 → 只能靠逐日物化积累。

二、为什么不把这一列加到 market_style_daily
  该表由 `market_style_sync` **整表 `DELETE` + 全量 `INSERT`** 重建（见该文件「三、性能设计」
  末条与 run() 第 6 步），任何由**其它任务**写入的列都会在下一次重建时被清空。
  故独立建表、独立写入，与市场风格物化解耦。

三、为什么由独立任务在 22:30 跑，而不是链在 market_style_sync（21:30）之后
  交叉印证第 ④ 项「口径互证」用的是同花顺概念板块，而概念源**分批发布**：
  21:30 时只有 ~190/375 个概念，22:00 补班才齐（见 `cross_check._concept_anchor` 的实测记录）。
  以 21:30 的数据算出来的背离数会**被写死进历史、事后无法修正**，污染整条分布。
  22:30 在概念补班（22:00）之后，且仍落在本机在线窗口（工作日 19:00–23:00）内。
  ⚠️ 因此本任务**不设 CHAIN_NEXT 上游**：若挂在 `concept_market_sync` 上，
     21:00 那次补班也会把它带上，而那时概念数据不全。

四、为什么不在这里预计算 250 日历史
  `cross_checks(as_of=D)` 单次实测 **330ms**（7 项各自独立取数），250 日需 ~83 秒。
  一次性回填可接受（见 `scripts/backfill_xcheck.py`），但绝不能放进请求路径或每日任务里。
  本任务每天只算「当前这一天」，耗时 ~0.3s。

五、幂等
  以 `trade_date` 为主键 UPSERT，同日重复跑直接覆盖（catchup 补跑 / 手动重跑均安全）。
"""
import logging
from datetime import datetime

import pymysql

from ..db import get_db_config
from ._common import with_steps

logger = logging.getLogger(__name__)

# 允许的项数上限（交叉印证当前 7 项；留余量，新增项不需要改表）
MAX_ITEMS = 12

RUN_STEPS = [
    {"no": 1, "name": "计算 7 项交叉印证",
     "params": "复用 analysis.cross_check（杠杆/股债/股商/口径/微观/量价/估值），逐项 fail-soft"},
    {"no": 2, "name": "写入背离数",
     "params": "market_xcheck_daily UPSERT（trade_date 主键，同日重复跑覆盖，幂等）"},
]

DDL = """
CREATE TABLE IF NOT EXISTS market_xcheck_daily (
  trade_date   DATE             NOT NULL COMMENT '与 market_style_daily.trade_date 对齐的交易日',
  items_n      TINYINT UNSIGNED NULL     COMMENT '参与判定的项数（当前 7）',
  agree_n      TINYINT UNSIGNED NULL     COMMENT '一致项数',
  diverge_n    TINYINT UNSIGNED NULL     COMMENT '背离项数 —— 本表的核心产出',
  neutral_n    TINYINT UNSIGNED NULL     COMMENT '中性项数（两维都在死区内）',
  nodata_n     TINYINT UNSIGNED NULL     COMMENT '数据缺失项数',
  diverge_keys VARCHAR(255)     NULL     COMMENT '背离项 key，逗号分隔（供「哪些项在背离」比对）',
  computed_at  DATETIME         NOT NULL COMMENT '写入时刻',
  PRIMARY KEY (trade_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='交叉印证背离数日频物化（market_wind 卡片分位用）'
"""

UPSERT = """
INSERT INTO market_xcheck_daily
  (trade_date, items_n, agree_n, diverge_n, neutral_n, nodata_n, diverge_keys, computed_at)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
ON DUPLICATE KEY UPDATE
  items_n=VALUES(items_n), agree_n=VALUES(agree_n), diverge_n=VALUES(diverge_n),
  neutral_n=VALUES(neutral_n), nodata_n=VALUES(nodata_n),
  diverge_keys=VALUES(diverge_keys), computed_at=VALUES(computed_at)
"""


class XcheckSyncCollector:
    """把某一交易日的「交叉印证背离数」写入 market_xcheck_daily"""

    def __init__(self, as_of: str | None = None):
        # as_of 仅回填脚本使用；None = 取全库最新（日常任务走这条）
        self.as_of = as_of

    def _ensure_table(self, conn) -> None:
        with conn.cursor() as cur:
            cur.execute(DDL)
        conn.commit()

    def run(self) -> dict:
        # 延迟导入：避免 collectors 包在导入期拉入 analysis 整个包
        from ..analysis import cross_check

        res = cross_check.cross_checks(self.as_of)
        d = res.get("as_of")
        if not d:
            # market_style_daily 尚无数据 → 本表无从计算，属「上游未就绪」而非故障
            raise RuntimeError("market_style_daily 无可用交易日，无法计算交叉印证背离数")

        summary = res["summary"]
        items = res["items"]
        diverge_keys = ",".join(i["key"] for i in items if i["level"] == "diverge")
        diverge_keys = diverge_keys[:255]
        row = (d, len(items), summary.get("agree"), summary.get("diverge"),
               summary.get("neutral"), summary.get("nodata"), diverge_keys,
               datetime.now())

        conn = pymysql.connect(**get_db_config().to_dict())
        try:
            self._ensure_table(conn)
            with conn.cursor() as cur:
                cur.execute(UPSERT, row)
            conn.commit()
        finally:
            conn.close()

        logger.info(f"交叉印证背离数 {d}：{summary.get('diverge')}/{len(items)} 项背离"
                    f"（{diverge_keys or '无'}）")
        return with_steps(
            {
                "records_written": 1,
                "as_of": str(d),
                "summary": summary,
                "diverge_keys": diverge_keys,
            },
            RUN_STEPS,
            {
                1: f"{len(items)} 项：一致 {summary.get('agree')} / 背离 {summary.get('diverge')}"
                   f" / 中性 {summary.get('neutral')} / 缺数据 {summary.get('nodata')}",
                2: f"{d} → 背离 {summary.get('diverge')} 项",
            },
        )
