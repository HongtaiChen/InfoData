#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
InvestBuddy 中美国债收益率采集器（bond_profit_daily 表）
- 数据源：ak.bond_zh_us_rate()（中债信息网 + 美债，1990 至今日频，已实测可用）
- 策略：增量补齐 —— 从本地 MAX(trade_date) 的次日拉到源最新，重复行不写入
- 字段映射：源列「中国/美国国债收益率 X 年」→ cn_/us_bond_Xy；spread 直接取源列
"""
import logging
from datetime import datetime, date

import pymysql
import akshare as ak

from ..db import get_db_config

logger = logging.getLogger(__name__)

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


class BondProfitSyncCollector:
    """中美国债收益率增量同步"""

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
                cur.execute("SELECT COALESCE(MAX(trade_date), '1900-01-01') FROM bond_profit_daily")
                max_d = cur.fetchone()[0]
            max_date = _to_date(max_d) or date(1900, 1, 1)

            rows = []
            for _, r in df.iterrows():
                d = _to_date(r["日期"])
                if d is None or d <= max_date:
                    continue
                vals = []
                all_none = True
                for src in _COL_MAP:
                    v = r.get(src)
                    if v is None or (isinstance(v, float) and v != v):  # NaN
                        vals.append(None)
                    else:
                        vals.append(float(v))
                        all_none = False
                if all_none:
                    continue  # 全空行（如早期只有美债、中国列全空）不入库
                rows.append((d, *vals))

            if not rows:
                logger.info(f"ℹ️ 国债收益率无新增（本地已至 {max_date}）")
                return {"records_written": 0, "error_count": 0, "errors": [], "note": "已是最新，无新增"}

            with conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO bond_profit_daily "
                    "(trade_date, cn_bond_2y, cn_bond_5y, cn_bond_10y, cn_bond_30y, cn_bond_10y_2y_spread, "
                    " us_bond_2y, us_bond_5y, us_bond_10y, us_bond_30y, us_bond_10y_2y_spread, "
                    " update_time, data_source) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), 'AKSHARE')",
                    rows,
                )
            conn.commit()
            logger.info(f"✅ 国债收益率增量 {len(rows)} 条（{rows[0][0]} ~ {rows[-1][0]}）")
            return {
                "records_written": len(rows),
                "error_count": 0,
                "errors": [],
                "note": f"中美国债收益率补齐 {len(rows)} 天（{rows[0][0]}~{rows[-1][0]}）",
            }
        finally:
            conn.close()
