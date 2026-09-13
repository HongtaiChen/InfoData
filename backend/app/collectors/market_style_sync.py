#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
市场风格日频物化表（market_style_daily）计算任务
- 数据源：dc_index_market（21 个指数日线，index_group 六分类见 index_market_sync.INDEX_META）
- 口径（与《分析研究模块交互设计规范》§4.1 一一对应）：
  · 六组等权 N 日收益 ret_*_20/60：组内各指数 (close_t/close_{t-N}-1)×100 的等权平均，
    组内要求全部成员有值（min_count=全组），否则该日记 NULL——保证口径纯净
  · 大小盘剪刀差 scissors_20/60：(中证1000+中证2000 等权) − (上证50+沪深300 等权) N 日收益
  · 风偏分数 risk_appetite_20：科技成长组 − 股息防守组（20 日）
  · 情绪温度 sentiment_20：证券公司 − 中证全指（20 日超额）
  · 政策敏感 policy_excess_20：中证全指房地产 − 中证全指（20 日超额）
  · 大势位置 bench_pos_pct：中证全指收盘在近 250 日高低区间的分位(%)
- 幂等：每次全量重建（小表，~5,300 行），DELETE + INSERT 单事务
"""
import logging

import pandas as pd
import pymysql

from ..db import get_db_config
from ._common import with_steps

logger = logging.getLogger(__name__)

RUN_STEPS = [
    {"no": 1, "name": "加载指数收盘矩阵", "params": "dc_index_market 全史 close → pivot(index_code×trade_date)"},
    {"no": 2, "name": "计算 N 日收益", "params": "N=20/60；六组等权（要求组内全员有值）"},
    {"no": 3, "name": "派生风格指标", "params": "剪刀差 / 风偏分数 / 情绪温度 / 政策敏感 / 大势位置(250日分位)"},
    {"no": 4, "name": "全量重建写入", "params": "market_style_daily DELETE + INSERT 单事务"},
]

# 分组成员（与 index_market_sync.INDEX_META 六分类保持一致；改动需两处同步）
GROUP_MEMBERS = {
    "bench": ["000001", "399001", "000985", "399330", "899050"],   # 市场基准
    "size": ["000016", "000300", "000905", "000852", "932000"],    # 市值风格
    "tech": ["000688", "000698", "399006", "399673", "980017"],    # 科技成长
    "sent": ["399975"],                                            # 情绪温度
    "div": ["000922", "000832", "399986", "399997"],               # 股息防守
    "pol": ["931775"],                                             # 政策周期
}

RET_COLS = [(g, n) for g in GROUP_MEMBERS for n in (20, 60)]  # 12 列 ret_*_N

DDL = """
CREATE TABLE IF NOT EXISTS market_style_daily (
  trade_date DATE NOT NULL COMMENT '交易日',
  ret_bench_20 DECIMAL(10,4) NULL COMMENT '市场基准组等权20日收益%',
  ret_bench_60 DECIMAL(10,4) NULL COMMENT '市场基准组等权60日收益%',
  ret_size_20 DECIMAL(10,4) NULL COMMENT '市值风格组等权20日收益%',
  ret_size_60 DECIMAL(10,4) NULL COMMENT '市值风格组等权60日收益%',
  ret_tech_20 DECIMAL(10,4) NULL COMMENT '科技成长组等权20日收益%',
  ret_tech_60 DECIMAL(10,4) NULL COMMENT '科技成长组等权60日收益%',
  ret_sent_20 DECIMAL(10,4) NULL COMMENT '情绪温度组等权20日收益%',
  ret_sent_60 DECIMAL(10,4) NULL COMMENT '情绪温度组等权60日收益%',
  ret_div_20 DECIMAL(10,4) NULL COMMENT '股息防守组等权20日收益%',
  ret_div_60 DECIMAL(10,4) NULL COMMENT '股息防守组等权60日收益%',
  ret_pol_20 DECIMAL(10,4) NULL COMMENT '政策周期组等权20日收益%',
  ret_pol_60 DECIMAL(10,4) NULL COMMENT '政策周期组等权60日收益%',
  scissors_20 DECIMAL(10,4) NULL COMMENT '大小盘剪刀差20日：小盘组-大盘组',
  scissors_60 DECIMAL(10,4) NULL COMMENT '大小盘剪刀差60日',
  risk_appetite_20 DECIMAL(10,4) NULL COMMENT '风偏分数：科技成长-股息防守(20日)',
  sentiment_20 DECIMAL(10,4) NULL COMMENT '情绪温度：券商-中证全指(20日超额)',
  policy_excess_20 DECIMAL(10,4) NULL COMMENT '政策敏感：地产-中证全指(20日超额)',
  bench_pos_pct DECIMAL(6,2) NULL COMMENT '大势位置：中证全指近250日高低区间分位%',
  update_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (trade_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='市场风格日频物化表（分析研究·市场风向模块数据源，口径见交互设计规范 §4.1）'
"""


class MarketStyleSyncCollector:
    """市场风格日频物化表计算（纯库内，无外部源）"""

    def __init__(self, windows: tuple[int, ...] = (20, 60), bench_lookback: int = 250):
        self.windows = windows
        self.bench_lookback = bench_lookback

    def _ensure_table(self, conn) -> None:
        with conn.cursor() as cur:
            cur.execute(DDL)

    def run(self) -> dict:
        conn = pymysql.connect(**get_db_config().to_dict())
        try:
            self._ensure_table(conn)

            # 1) 收盘矩阵
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT index_code, trade_date, close FROM dc_index_market "
                    "WHERE close IS NOT NULL ORDER BY trade_date"
                )
                rows = cur.fetchall()
            df = pd.DataFrame(rows, columns=["code", "date", "close"])
            df["close"] = df["close"].astype(float)
            pivot = df.pivot(index="date", columns="code", values="close").sort_index()

            # 2) 各指数 N 日收益(%)
            rets = {n: pivot.pct_change(n) * 100 for n in self.windows}

            # 3) 组内等权（要求组内全员有值，口径纯净）
            def group_ret(group: str, n: int) -> pd.Series:
                cols = [c for c in GROUP_MEMBERS[group] if c in pivot.columns]
                if len(cols) != len(GROUP_MEMBERS[group]):
                    return pd.Series(dtype=float)
                return rets[n][cols].dropna().mean(axis=1)

            out = pd.DataFrame(index=pivot.index)
            for g, n in RET_COLS:
                out[f"ret_{g}_{n}"] = group_ret(g, n)

            # 4) 派生指标
            small = rets[self.windows[0]][["000852", "932000"]].dropna().mean(axis=1)
            large = rets[self.windows[0]][["000016", "000300"]].dropna().mean(axis=1)
            out["scissors_20"] = small - large
            small60 = rets[60][["000852", "932000"]].dropna().mean(axis=1)
            large60 = rets[60][["000016", "000300"]].dropna().mean(axis=1)
            out["scissors_60"] = small60 - large60
            out["risk_appetite_20"] = out["ret_tech_20"] - out["ret_div_20"]
            out["sentiment_20"] = out["ret_sent_20"] - out["ret_bench_20"]
            out["policy_excess_20"] = out["ret_pol_20"] - out["ret_bench_20"]

            bench = pivot["000985"].dropna()
            roll_min = bench.rolling(self.bench_lookback, min_periods=60).min()
            roll_max = bench.rolling(self.bench_lookback, min_periods=60).max()
            out["bench_pos_pct"] = ((bench - roll_min) / (roll_max - roll_min) * 100).round(2)

            out = out.dropna(how="all")
            if out.empty:
                raise RuntimeError("dc_index_market 数据不足，无法计算风格指标")

            # 5) 全量重建
            cols = list(out.columns)
            payload = [
                tuple(None if pd.isna(row[c]) else round(float(row[c]), 4) for c in cols) + (idx,)
                for idx, row in out.iterrows()
            ]
            with conn.cursor() as cur:
                cur.execute("DELETE FROM market_style_daily")
                cur.executemany(
                    f"INSERT INTO market_style_daily ({', '.join(cols)}, trade_date) "
                    f"VALUES ({', '.join(['%s'] * (len(cols) + 1))})",
                    payload,
                )
            conn.commit()

            as_of = str(out.index[-1])
            msg = f"市场风格物化 {len(payload)} 行（{cols[0]}~{cols[-1]}），截至 {as_of}"
            logger.info(f"✅ {msg}")
            return with_steps(
                {
                    "records_written": len(payload),
                    "error_count": 0,
                    "errors": [],
                    "note": msg,
                },
                RUN_STEPS,
                {
                    1: f"{len(pivot.columns)} 个指数 / {len(pivot)} 个交易日",
                    2: f"N={self.windows}",
                    3: "剪刀差/风偏/情绪/政策/大势位置",
                    4: f"重建 {len(payload)} 行",
                },
            )
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
