#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
市场风格日频物化表（market_style_daily）计算任务

一、指数风格指标（数据源 dc_index_market，21 个指数日线，六分类见 index_market_sync.INDEX_META）
- 口径（与《分析研究模块交互设计规范》§4.1 一一对应）：
  · 六组等权 N 日收益 ret_*_20/60：组内各指数 (close_t/close_{t-N}-1)×100 的等权平均，
    组内要求全部成员有值（min_count=全组），否则该日记 NULL——保证口径纯净
  · 大小盘剪刀差 scissors_20/60：(中证1000+中证2000 等权) − (上证50+沪深300 等权) N 日收益
  · 风偏分数 risk_appetite_20：科技成长组 − 股息防守组（20 日）
  · 情绪温度 sentiment_20：证券公司 − 中证全指（20 日超额）
  · 政策敏感 policy_excess_20：中证全指房地产 − 中证全指（20 日超额）
  · 大势位置 bench_pos_pct：中证全指收盘在近 250 日高低区间的分位(%)

二、市场宽度指标（数据源 stock_market_daily 全市场个股日线）
- 为什么需要：上面 19 列全部是「指数之间比收益」，**完全没有「多少只股票在涨」**这一维。
  指数由权重股主导，宽度才能回答「上涨是不是有普遍性」（指数涨但 3000 只跌 = 虚涨）。
- 口径：
  · breadth_up / down / breadth_up_ratio：当日有收盘价个股的涨跌家数与上涨占比
  · breadth_adl：腾落线（Σ(上涨家数 − 下跌家数) 的累计值），全史连续累计
  · limit_up / limit_down：涨停/跌停家数。⚠️ 近似口径：主板阈值 ±9.8%、创业板(30*)/科创板(68*)
    阈值 ±19.8%（未单独识别 ST 的 5% 与北交所 30%，属可接受的近似）
  · above_ma20_pct / above_ma60_pct：收盘价站上 MA20 / MA60 的个股占比
    （MA60 仅统计当日已有 60 个交易日历史的个股，分母单独取 v60，不把次新股算成「跌破」）
  · new_high60 / new_low60 / hl_diff60：创 60 日新高 / 新低家数及差值（情绪拐点的先行信号）

二·补、量能指标（同源于 stock_market_daily，与宽度共用一次取数，零额外成本）
  · market_amount：全市场成交额（亿元，个股 amount 求和）——即「两市成交额」
  · amount_ratio_20：成交额 ÷ 20 日均值 ×100%（>100 放量 / <100 缩量），
    与「站上均线占比」配合可区分「放量下跌」与「缩量止跌」

三、性能设计（2026-09-14 实测选型，重要）
- stock_market_daily 有 1,800 万行 / 2.2GB。最初用单条 SQL 窗口函数（ROW_NUMBER/AVG/MAX/MIN
  OVER）计算宽度，实测在 1.2M 行窗口下就要 20~62 秒，且**窗口函数个数从 3 增到 4 时耗时从
  8s 跳到 62s**——根因是 MySQL 会话缓冲偏小（tmp_table_size 33MB / max_heap_table_size 16MB），
  窗口结果集 materialize 溢出到磁盘。调大到 1GB 只能缓解（4 窗口 21.8s，5 窗口仍 57s）。
- 最终改为 **pandas 计算**：抓 (stock_code, trade_date, close, change_pct) 四列后在内存里
  用 groupby.rolling 算 MA/极值。实测增量窗口 1.18M 行「抓数 6.5s + 计算 3.3s ≈ 10s」，
  比 SQL 快 2~6 倍，且数值与 SQL 口径一致（仅临界等值处有 1 只股票的浮点舍入差）。
- 取数依赖覆盖索引 idx_breadth_cover (stock_code, trade_date, close, change_pct)：
  EXPLAIN 确认优化器会走 `Using index for skip scan`，全索引覆盖免回表；缺索引只打警告。
- **增量重算**：日常只重算「表内最新日 − 10 天」之后的目标区间（预热另取 200 天），
  旧行沿用表内既有宽度列，不重复扫全史。首次运行（表内无宽度数据）才分块回填全史。
- 幂等：指数列每次全量重建（小表）；宽度列增量重算后与旧值合并，再随全表 DELETE + INSERT 落库
"""
import logging
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pymysql

from ..db import get_db_config
from ._common import with_steps

logger = logging.getLogger(__name__)

RUN_STEPS = [
    {"no": 1, "name": "加载指数收盘矩阵", "params": "dc_index_market 全史 close → pivot(index_code×trade_date)"},
    {"no": 2, "name": "计算 N 日收益", "params": "N=20/60；六组等权（要求组内全员有值）"},
    {"no": 3, "name": "派生风格指标", "params": "剪刀差 / 风偏分数 / 情绪温度 / 政策敏感 / 大势位置(250日分位)"},
    {"no": 4, "name": "计算市场宽度与量能", "params": "个股涨跌家数/涨停跌停/站上MA20·MA60占比/60日新高新低/成交额放缩量；pandas 增量重算"},
    {"no": 5, "name": "全量重建写入", "params": "market_style_daily DELETE + INSERT 单事务"},
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

# 宽度列（个股家数 / 均线参与度 / 新高新低）
BREADTH_COLS = [
    "breadth_total", "breadth_up", "breadth_down", "breadth_up_ratio",
    "breadth_adl", "limit_up", "limit_down",
    "above_ma20_pct", "above_ma60_pct", "new_high60", "new_low60", "hl_diff60",
]

# 量能列（2026-09-14 批次 3 新增）：与宽度同源于 stock_market_daily 的同一次取数，
# 只是把 amount 也带上按日求和，零额外取数成本。
VOLUME_COLS = ["market_amount", "amount_ratio_20"]

# 全部「个股派生」列：同一次抓数 → 同一次合并 → 同一次写入
MARKET_COLS = BREADTH_COLS + VOLUME_COLS

BREADTH_DDL = """
  breadth_total INT NULL COMMENT '当日有效个股数（有收盘价）',
  breadth_up INT NULL COMMENT '上涨家数',
  breadth_down INT NULL COMMENT '下跌家数',
  breadth_up_ratio DECIMAL(6,2) NULL COMMENT '上涨家数占比%',
  breadth_adl BIGINT NULL COMMENT '腾落线ADL累计值（Σ上涨-下跌）',
  limit_up INT NULL COMMENT '涨停家数（主板≥9.8%，创业板/科创板≥19.8%，近似口径）',
  limit_down INT NULL COMMENT '跌停家数',
  above_ma20_pct DECIMAL(6,2) NULL COMMENT '收盘站上MA20个股占比%',
  above_ma60_pct DECIMAL(6,2) NULL COMMENT '收盘站上MA60个股占比%（分母为有60日历史个股）',
  new_high60 INT NULL COMMENT '创60日新高家数',
  new_low60 INT NULL COMMENT '创60日新低家数',
  hl_diff60 INT NULL COMMENT '创60日新高-新低家数差',
  market_amount DECIMAL(18,2) NULL COMMENT '全市场成交额（亿元，个股 amount 求和）',
  amount_ratio_20 DECIMAL(8,2) NULL COMMENT '成交额 / 20日均值 ×100%（>100 放量，<100 缩量）',
"""

# 宽度覆盖索引（优化取数路径：全索引覆盖免回表）；由 scripts/seed_indexes.py 创建
BREADTH_INDEX = "idx_breadth_cover"

# 预热天数：MA60 需要 60 个交易日 ≈ 84 自然日，取 200 天留足冗余（含长假/停牌）
BREADTH_WARMUP_DAYS = 200
# 增量重算的重叠天数：从「表内最新日 − N 天」起算，便于自愈补上漏跑的日子
BREADTH_OVERLAP_DAYS = 10
# 全史回填起点（market_style_daily 起于 2005-02，留出 MA60 预热）
BREADTH_HISTORY_START = date(2005, 2, 1)
# 全史回填分块跨度（年）——实测 3 年块约 320 万行 / 1.1GB 内存，2 年块更稳
BREADTH_CHUNK_YEARS = 2


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
  {breadth_ddl}
  update_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (trade_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='市场风格日频物化表（分析研究·市场风向模块数据源，口径见交互设计规范 §4.1）'
"""


class MarketStyleSyncCollector:
    """市场风格日频物化表计算（纯库内，无外部源）"""

    def __init__(self, windows: tuple[int, ...] = (20, 60), bench_lookback: int = 250,
                 full_refresh: bool = False):
        self.windows = windows
        self.bench_lookback = bench_lookback
        # full_refresh=True 强制走全史回填：新增派生列（如批次 3 的 market_amount）后，
        # 表内旧行的新列为 NULL，而增量门控只看 breadth_up_ratio，会误判为「已有数据」，
        # 导致历史新列永远为空 —— 此时必须显式全量重算一次。
        self.full_refresh = full_refresh

    # ---------- 表结构维护 ----------

    def _ensure_table(self, conn) -> None:
        with conn.cursor() as cur:
            cur.execute(DDL.format(breadth_ddl=BREADTH_DDL))

    def _ensure_columns(self, conn) -> list[str]:
        """对已存在的表补齐缺失的宽度列（MySQL 无 ADD COLUMN IF NOT EXISTS，须查 information_schema）"""
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'market_style_daily'"
            )
            have = {r[0] for r in cur.fetchall()}
            added = []
            for col in MARKET_COLS:
                if col in have:
                    continue
                ddl_line = next(
                    (ln.strip().rstrip(",") for ln in BREADTH_DDL.strip().splitlines()
                     if ln.strip().startswith(col + " ")),
                    None,
                )
                if not ddl_line:
                    continue
                cur.execute(f"ALTER TABLE market_style_daily ADD COLUMN {ddl_line}")
                added.append(col)
        if added:
            logger.info(f"market_style_daily 补齐宽度列：{added}")
        return added

    def _has_breadth_index(self, conn) -> bool:
        with conn.cursor() as cur:
            cur.execute("SHOW INDEX FROM stock_market_daily WHERE Key_name = %s", (BREADTH_INDEX,))
            return bool(cur.fetchall())

    # ---------- 宽度计算 ----------

    def _load_existing_breadth(self, conn) -> dict:
        """读表内**已有宽度值**的行（用于增量合并）。

        ⚠️ 必须过滤 `breadth_up_ratio IS NOT NULL`：表在宽度列刚加上时是「有行但宽度全 NULL」，
        若把这些行也算作已有数据，就会误走增量路径、只重算最近 10 天，
        导致 5,000 多个历史交易日的宽度永远为空（首跑必须走全史回填）。
        ⚠️ 若本次是新增派生列后的首次运行，需用 full_refresh=True 强制全量（见 __init__）。
        """
        cols = ", ".join(MARKET_COLS)
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute(
                f"SELECT trade_date, {cols} FROM market_style_daily "
                f"WHERE breadth_up_ratio IS NOT NULL"
            )
            return {r["trade_date"]: {c: r[c] for c in MARKET_COLS} for r in cur.fetchall()}

    def _max_stock_date(self, conn) -> date | None:
        with conn.cursor() as cur:
            cur.execute("SELECT MAX(trade_date) AS d FROM stock_market_daily")
            row = cur.fetchone()
            return row[0] if row else None

    def _fetch_raw(self, conn, start: date, end: date) -> pd.DataFrame:
        """抓个股日线（5 列：含 amount 供量能派生）；走 idx_breadth_cover 覆盖索引"""
        with conn.cursor() as cur:
            cur.execute(
                "SELECT stock_code, trade_date, close, change_pct, amount FROM stock_market_daily "
                "WHERE close IS NOT NULL AND trade_date BETWEEN %s AND %s "
                "ORDER BY stock_code, trade_date",
                (start, end),
            )
            rows = cur.fetchall()
        if not rows:
            return pd.DataFrame(columns=["stock_code", "trade_date", "close", "change_pct", "amount"])
        df = pd.DataFrame(rows, columns=["stock_code", "trade_date", "close", "change_pct", "amount"])
        df["close"] = pd.to_numeric(df["close"], errors="coerce")
        df["change_pct"] = pd.to_numeric(df["change_pct"], errors="coerce")
        df["amount"] = pd.to_numeric(df["amount"], errors="coerce")
        df["stock_code"] = df["stock_code"].astype(str)
        return df

    @staticmethod
    def _aggregate_breadth(df: pd.DataFrame, target_start: date) -> pd.DataFrame:
        """在内存里算宽度与量能（df 须已按 stock_code, trade_date 排序，且含 target_start 之前的预热段）。

        ⚠️ 按日聚合与 20 日均量都必须在**完整 df**（含预热段）上做，最后才切出 target_start 之后的行；
        若先切再算，目标区首日的 20 日均量会缺 19 天、并把错误值写回表（增量重叠区尤其危险）。
        """
        g = df.groupby("stock_code", sort=False)["close"]
        df = df.assign(
            ma20=g.rolling(20, min_periods=20).mean().reset_index(level=0, drop=True),
            ma60=g.rolling(60, min_periods=60).mean().reset_index(level=0, drop=True),
            hh60=g.rolling(60, min_periods=60).max().reset_index(level=0, drop=True),
            ll60=g.rolling(60, min_periods=60).min().reset_index(level=0, drop=True),
            rn=df.groupby("stock_code", sort=False).cumcount() + 1,
        )
        # 涨停阈值：创业板(30*)/科创板(68*) 为 20cm，其余按 10cm（近似口径）
        thr = np.where(df["stock_code"].str.startswith(("30", "68")), 19.8, 9.8)
        v60 = df["rn"] >= 60
        df = df.assign(
            _up=df["change_pct"] > 0,
            _dn=df["change_pct"] < 0,
            _lu=df["change_pct"] >= thr,
            _ld=df["change_pct"] <= -thr,
            _a20=(df["rn"] >= 20) & (df["close"] > df["ma20"]),
            _a60=v60 & (df["close"] > df["ma60"]),
            _v60=v60,
            _nh=v60 & (df["close"] >= df["hh60"]),
            _nl=v60 & (df["close"] <= df["ll60"]),
        )
        agg = df.groupby("trade_date").agg(
            breadth_total=("close", "size"),
            breadth_up=("_up", "sum"),
            breadth_down=("_dn", "sum"),
            limit_up=("_lu", "sum"),
            limit_down=("_ld", "sum"),
            _a20=("_a20", "sum"),
            _a60=("_a60", "sum"),
            _v60=("_v60", "sum"),
            new_high60=("_nh", "sum"),
            new_low60=("_nl", "sum"),
            _amount=("amount", "sum"),
        )
        out = pd.DataFrame(index=agg.index)
        out["breadth_total"] = agg["breadth_total"].astype("Int64")
        out["breadth_up"] = agg["breadth_up"].astype("Int64")
        out["breadth_down"] = agg["breadth_down"].astype("Int64")
        out["breadth_up_ratio"] = (agg["breadth_up"] / agg["breadth_total"] * 100).round(2)
        out["limit_up"] = agg["limit_up"].astype("Int64")
        out["limit_down"] = agg["limit_down"].astype("Int64")
        out["above_ma20_pct"] = (agg["_a20"] / agg["breadth_total"] * 100).round(2)
        out["above_ma60_pct"] = (agg["_a60"] / agg["_v60"].replace(0, np.nan) * 100).round(2)
        out["new_high60"] = agg["new_high60"].astype("Int64")
        out["new_low60"] = agg["new_low60"].astype("Int64")
        out["hl_diff60"] = (agg["new_high60"] - agg["new_low60"]).astype("Int64")
        # 量能：全市场成交额（元 → 亿元）与其 20 日均值比（>100 放量 / <100 缩量）
        out["market_amount"] = (agg["_amount"] / 1e8).round(2)
        amt20 = agg["_amount"].rolling(20, min_periods=20).mean()
        out["amount_ratio_20"] = (agg["_amount"] / amt20 * 100).round(2)
        # 最后才切目标区间（预热段只用于 MA / 均量计算）
        return out[out.index >= target_start]

    @staticmethod
    def _date_chunks(start: date, end: date, years: int):
        """按 N 年切分 [start, end]（目标区间连续不重叠；预热由调用方另行前推）"""
        cur = start
        while cur <= end:
            try:
                nxt = cur.replace(year=cur.year + years)
            except ValueError:          # 2/29 等边界
                nxt = cur.replace(year=cur.year + years, day=28)
            last = min(nxt - timedelta(days=1), end)
            yield cur, last
            cur = nxt

    def _breadth_frame(self, conn) -> pd.DataFrame:
        """宽度列 → DataFrame(index=trade_date 的 date)。增量重算 + 与旧值合并 + ADL 全史累计"""
        import time
        existing = {} if self.full_refresh else self._load_existing_breadth(conn)
        if not self._has_breadth_index(conn):
            logger.warning(
                f"⚠️ 宽度覆盖索引 {BREADTH_INDEX} 不存在，取数将退化为回表扫描（慢）；"
                f"建议执行 scripts/seed_indexes.py --apply"
            )
        max_d = self._max_stock_date(conn)
        if max_d is None:
            return pd.DataFrame()

        t0 = time.time()
        parts: list[pd.DataFrame] = []
        if existing:
            # 增量：目标从「表内最新日 − 重叠天数」起，预热另取 200 天
            target_start = max(existing) - timedelta(days=BREADTH_OVERLAP_DAYS)
            fetch_start = target_start - timedelta(days=BREADTH_WARMUP_DAYS)
            parts.append(self._aggregate_breadth(self._fetch_raw(conn, fetch_start, max_d), target_start))
            note = f"增量重算（目标起 {target_start}）"
        else:
            # 首次：分块回填全史（每块独立预热，避免一次性载入千万行）。
            # ⚠️ 每块结束必须 del + gc.collect()：pandas 中间列多（ma/rn/各布尔列），
            #    Python 不立即把内存还给 OS，实测不回收时后段块耗时从 10s 劣化到 127s（换页）。
            import gc
            for cs, ce in self._date_chunks(BREADTH_HISTORY_START, max_d, BREADTH_CHUNK_YEARS):
                fs = cs - timedelta(days=BREADTH_WARMUP_DAYS)
                raw = self._fetch_raw(conn, fs, ce)
                part = self._aggregate_breadth(raw, cs)
                del raw
                parts.append(part)
                gc.collect()
                logger.info(f"  宽度回填块 {cs} ~ {ce} 完成")
            note = "全史回填（分块）"

        fresh = pd.concat(parts) if parts else pd.DataFrame()
        if not fresh.empty:
            fresh.index = [d if isinstance(d, date) else d.date() for d in fresh.index]
            fresh = fresh[~fresh.index.duplicated(keep="last")].sort_index()
            logger.info(f"宽度{note}：{len(fresh)} 个交易日，耗时 {time.time() - t0:.1f}s")

        # 合并：新值覆盖旧值（同口径，幂等）；旧值补齐预热/未覆盖区间
        # ⚠️ 遍历 row.index 而非 MARKET_COLS：fresh 里没有 breadth_adl（ADL 要拿合并后的
        #    完整序列才能累计），按列名硬取会 KeyError。
        merged = {d: dict(v) for d, v in existing.items()}
        for d, row in fresh.iterrows():
            merged[d] = {c: (None if pd.isna(row[c]) else row[c]) for c in row.index}
        if not merged:
            return pd.DataFrame()

        # ADL：只有拿合并后的完整序列才能保证腾落线连续
        adl = 0
        for d in sorted(merged):
            adl += int(merged[d].get("breadth_up") or 0) - int(merged[d].get("breadth_down") or 0)
            merged[d]["breadth_adl"] = adl

        df = pd.DataFrame.from_dict(merged, orient="index").reindex(columns=MARKET_COLS)
        df.index = pd.to_datetime(df.index)
        return df.sort_index()

    # ---------- 主流程 ----------

    def run(self) -> dict:
        conn = pymysql.connect(**get_db_config().to_dict())
        try:
            self._ensure_table(conn)
            self._ensure_columns(conn)

            # 1) 指数收盘矩阵
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

            # 5) 市场宽度（增量重算 + 合并）
            bdf = self._breadth_frame(conn)
            if not bdf.empty:
                bdf.index = bdf.index.date
                out.index = [d.date() if hasattr(d, "date") else d for d in out.index]
                out = out.join(bdf, how="left")

            out = out.dropna(how="all")
            if out.empty:
                raise RuntimeError("dc_index_market 数据不足，无法计算风格指标")

            # 6) 全量重建
            cols = list(out.columns)
            payload = [
                tuple(None if pd.isna(row[c]) else self._round(row[c]) for c in cols) + (idx,)
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
            n_breadth = int(out["breadth_total"].notna().sum()) if "breadth_total" in out else 0
            msg = (f"市场风格物化 {len(payload)} 行（{cols[0]}~{cols[-1]}），截至 {as_of}；"
                   f"其中宽度列 {n_breadth} 行")
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
                    4: f"宽度/量能 {n_breadth} 行（{len(MARKET_COLS)} 列，pandas 增量）",
                    5: f"重建 {len(payload)} 行",
                },
            )
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _round(v) -> float:
        """统一保留 4 位小数（整数列 round 后仍是等值浮点，MySQL 会按列类型落整）"""
        return round(float(v), 4)
