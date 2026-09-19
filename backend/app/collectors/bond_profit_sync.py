#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
InvestBuddy 中美国债收益率采集器（bond_profit_daily 表）

- 数据源：ak.bond_zh_us_rate()（中债信息网 + 美债，1990 至今日频，已实测可用）
- 字段映射：源列「中国/美国国债收益率 X 年」→ cn_/us_bond_Xy；spread 直接取源列

⚠️ 2026-09-19 修复「美债腿断供 10 天」（根因与推演见 docs/市场风向数据蓝图落地审计_2026-09-19.md §4）

  旧实现有两处结构性缺陷，两者叠加造成**永久性数据空洞**：

    ① 增量起点写死为「表里最新到哪天」（`MAX(trade_date)`），而非「每条腿各自到哪天」。
       美债当日数据在**美国市场收盘后**才发布，对应北京时间已是次日凌晨；本任务排在
       晚间 19:15，拉取时源里该日美债列仍是 NaN。于是当晚写下一行「中债有值、美债全空」；
       第二天起点已经越过该日（`d <= max_date` 直接 continue），**那一行的 NULL 永远不会再被碰**。

    ② 写入是裸 `executemany(INSERT)`：即便重新拉到该日期也插不进去（唯一索引冲突），
       而当时表上又没有唯一索引 —— 属于「要么写不进、要么写重复」的两难。

  新实现 = **两腿独立水位线 + UPSERT**，与 `news_fetch` 的回看补采是同一个思路（每维用自己的水位线）：

    ① 起点 = `min(中债腿最后非空日, 美债腿最后非空日)` —— 哪条腿落后就从那条腿之后重扫。
       并以 `lookback_days` 兜底：万一某条腿长期为空（如源侧改版），也只回扫最近这么多天，
       不会把整张表拖进来重扫。
    ② 写入改 `INSERT … ON DUPLICATE KEY UPDATE`，且**每列**用 `COALESCE(VALUES(col), col)`：
       源有值才覆盖、源是 NaN 就保留库内旧值。这样「当晚缺美债」不会再冲掉已回填的历史值，
       也不会用 NULL 去覆盖中债。

  净效果：美债腿最多滞后 **1 个交易日**（当日发布前），次日自动补上；DQ 规则
  `bond_us_notnull`（check_type=column_watermark）负责在它再次掉队时立刻报警 ——
  此前这次断供 10 天**没有任何规则覆盖**，纯靠人工复测才发现。
"""
import logging
from datetime import datetime, date, timedelta

import pymysql
import akshare as ak

from ..db import get_db_config
from ._common import with_steps

logger = logging.getLogger(__name__)

# 运行步骤链模板（供前端「数据流·整链拓扑」展示运行逻辑）
RUN_STEPS = [
    {"no": 1, "name": "拉取中美收益率", "params": "ak.bond_zh_us_rate()（中债信息网+美债，1990~今日频）"},
    {"no": 2, "name": "源列校验", "params": "所需 10 列缺失任一则中断（防字段漂移）"},
    {"no": 3, "name": "两腿独立水位线", "params": "起点 = min(中债腿最后非空日, 美债腿最后非空日)；下限 lookback_days 防整表重扫"},
    {"no": 4, "name": "Upsert 回填", "params": "INSERT … ON DUPLICATE KEY UPDATE，每列 COALESCE(VALUES(col), col)：源有值才覆盖、NaN 保留旧值"},
]

# 源列名 → 目标列名
_COL_MAP = {
    "中国国债收益率2年": "cn_bond_2y",
    "中国国债收益率5年": "cn_bond_5y",
    "中国国债收益率10年": "cn_bond_10y",
    "中国国债收益率30年": "cn_bond_30y",
    "中国国债收益率10年-2年": "cn_bond_10y_2y_spread",
    "美国国债收益率2年": "us_bond_2y",
    "美国国债收益率5年": "us_bond_5y",
    "美国国债收益率10年": "us_bond_10y",
    "美国国债收益率30年": "us_bond_30y",
    "美国国债收益率10年-2年": "us_bond_10y_2y_spread",
}
_TGT_COLS = list(_COL_MAP.values())

# 水位线探针列：每条腿各取一个「必然有值」的代表列
_WM_CN = "cn_bond_10y"
_WM_US = "us_bond_10y"

# 回扫下限（自然日）：即使某条腿长期为空，也只回扫最近这么多天（防全表重扫）
DEFAULT_LOOKBACK_DAYS = 120


def _to_date(v) -> date | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()[:10]
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return None


def _is_nan(v) -> bool:
    return v is None or (isinstance(v, float) and v != v)


class BondProfitSyncCollector:
    """中美国债收益率增量同步（两腿独立水位线 + Upsert 回填）"""

    def __init__(self, lookback_days: int = DEFAULT_LOOKBACK_DAYS, from_date=None):
        # lookback_days<=0 表示不限下限（谨慎：会在某腿长期为空时重扫全表）
        self.lookback_days = int(lookback_days if lookback_days is not None else DEFAULT_LOOKBACK_DAYS)
        # 运维用：显式指定回扫起点（如源侧修正历史后做一次全量重建）
        self.from_date = from_date

    # ---------- 水位线 ----------

    @staticmethod
    def _watermarks(cur) -> dict:
        """三条水位线：整表最新日 / 中债腿最后非空日 / 美债腿最后非空日"""
        cur.execute(
            "SELECT MAX(trade_date) AS max_all,"
            f"       MAX(CASE WHEN {_WM_CN} IS NOT NULL THEN trade_date END) AS wm_cn,"
            f"       MAX(CASE WHEN {_WM_US} IS NOT NULL THEN trade_date END) AS wm_us"
            "  FROM bond_profit_daily"
        )
        r = cur.fetchone()
        return {"max_all": _to_date(r[0]), "wm_cn": _to_date(r[1]), "wm_us": _to_date(r[2])}

    def _start_date(self, wm: dict) -> date:
        """回扫起点：两腿取其早者；再受 lookback_days 下限约束"""
        if self.from_date:
            d = _to_date(self.from_date)
            if d:
                return d
        legs = [d for d in (wm["wm_cn"], wm["wm_us"]) if d is not None]
        start = min(legs) if legs else date(1900, 1, 1)
        if self.lookback_days > 0 and wm["max_all"] is not None:
            floor = wm["max_all"] - timedelta(days=self.lookback_days)
            if start < floor:
                start = floor
        return start

    @staticmethod
    def _gap_count(cur, col: str, start: date) -> int:
        """[start, ∞) 区间内该列为 NULL 的行数（= 这条腿的缺口）"""
        cur.execute(
            f"SELECT COUNT(*) FROM bond_profit_daily WHERE trade_date >= %s AND `{col}` IS NULL",
            (start,),
        )
        return int(cur.fetchone()[0])

    @staticmethod
    def _existing_dates(cur, start: date) -> set:
        cur.execute("SELECT trade_date FROM bond_profit_daily WHERE trade_date >= %s", (start,))
        return {r[0] for r in cur.fetchall()}

    # ---------- 主流程 ----------

    def run(self) -> dict:
        df = ak.bond_zh_us_rate()
        if df is None or df.empty or "日期" not in df.columns:
            raise RuntimeError("bond_zh_us_rate 返回为空（接口风控或变更）")

        missing = [c for c in _COL_MAP if c not in df.columns]
        if missing:
            raise RuntimeError(f"bond_zh_us_rate 列缺失: {missing}，实际列 {list(df.columns)}")

        conn = pymysql.connect(**get_db_config().to_dict())
        try:
            with conn.cursor() as cur:
                wm = self._watermarks(cur)
            start = self._start_date(wm)
            logger.info(
                "国债收益率：整表至 %s · 中债腿至 %s · 美债腿至 %s → 回扫起点 %s",
                wm["max_all"], wm["wm_cn"], wm["wm_us"], start,
            )

            # 组装增量行（d > start 才取；全空行不入库）
            rows, skipped_allna = [], 0
            for _, r in df.iterrows():
                d = _to_date(r["日期"])
                if d is None or d <= start:
                    continue
                vals, all_none = [], True
                for src in _COL_MAP:
                    v = r.get(src)
                    if _is_nan(v):
                        vals.append(None)
                    else:
                        vals.append(float(v))
                        all_none = False
                if all_none:
                    skipped_allna += 1
                    continue
                rows.append((d, *vals))

            with conn.cursor() as cur:
                exist = self._existing_dates(cur, start)
                new_rows = sum(1 for r in rows if r[0] not in exist)
                # 回填前缺口（以美债 10Y 为代表列；四条美债列同源同缺）
                gap_before_us = self._gap_count(cur, _WM_US, start)
                gap_before_cn = self._gap_count(cur, _WM_CN, start)

            if not rows:
                logger.info("ℹ️ 国债收益率无新增（回扫起点 %s，源无可入库行）", start)
                return with_steps(
                    {"records_written": 0, "error_count": 0, "errors": [],
                     "note": "已是最新，无新增"},
                    RUN_STEPS,
                    {1: f"接口 {len(df)} 天", 2: "列齐全",
                     3: f"整表至 {wm['max_all']}；中债腿至 {wm['wm_cn']}；美债腿至 {wm['wm_us']}",
                     4: "无 > 起点的新行，终止"},
                )

            # Upsert：源有值才覆盖，NaN 保留库内旧值（关键：不会用 NULL 冲掉已回填值）
            set_clause = ", ".join(f"{c}=COALESCE(VALUES({c}), {c})" for c in _TGT_COLS)
            sql = (
                "INSERT INTO bond_profit_daily "
                f"(trade_date, {', '.join(_TGT_COLS)}, update_time, data_source) "
                f"VALUES ({', '.join(['%s'] * (len(_TGT_COLS) + 1))}, NOW(), 'AKSHARE') "
                f"ON DUPLICATE KEY UPDATE {set_clause}, update_time=NOW()"
            )
            with conn.cursor() as cur:
                cur.executemany(sql, rows)
            conn.commit()

            with conn.cursor() as cur:
                gap_after_us = self._gap_count(cur, _WM_US, start)
                gap_after_cn = self._gap_count(cur, _WM_CN, start)

            filled_us = max(0, gap_before_us - gap_after_us)
            filled_cn = max(0, gap_before_cn - gap_after_cn)
            written = new_rows + filled_us + filled_cn

            logger.info(
                "✅ 国债收益率：upsert %s 天（新增 %s · 补美债缺口 %s 行 · 补中债缺口 %s 行）；"
                "美债腿残留缺口 %s 行",
                len(rows), new_rows, filled_us, filled_cn, gap_after_us,
            )
            note = (f"upsert {len(rows)} 天：新增 {new_rows} 天，回填美债 {filled_us} 行、中债 {filled_cn} 行"
                    f"（美债腿残留缺口 {gap_after_us} 行，通常为「当日尚未发布」）")
            return with_steps(
                {"records_written": written, "error_count": 0, "errors": [], "note": note},
                RUN_STEPS,
                {
                    1: f"接口 {len(df)} 天（{rows[0][0]} ~ {rows[-1][0]}）",
                    2: "列齐全",
                    3: (f"整表至 {wm['max_all']}；中债腿至 {wm['wm_cn']}；美债腿至 {wm['wm_us']}；"
                        f"回扫起点 {start}（区间 {start} ~ {rows[-1][0]}）"),
                    4: (f"upsert {len(rows)} 天 · 新增 {new_rows} · 回填美债 {filled_us} / 中债 {filled_cn}"
                        f"（残留缺口 美债 {gap_after_us} / 中债 {gap_after_cn}）"),
                },
            )
        finally:
            conn.close()
