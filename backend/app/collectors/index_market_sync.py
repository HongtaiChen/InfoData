#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
InvestBuddy 指数日线采集器（dc_index_market 表，13 个主流指数增量）
- 主源：ak.index_zh_a_hist()（东财，全字段：成交量/成交额/涨跌额/涨跌幅/换手率）
- 降级：腾讯 ifzq.gtimg.cn 日 K JSON（仅 OHLCV，amount/turnover/change 为 NULL 或自算）
- 策略：逐指数从本地 MAX(trade_date) 次日增量拉到最新；东财失败自动切腾讯；两者都失败则该指数本轮跳过
"""
import json
import logging
from datetime import date, datetime, timedelta

import pymysql
import requests
import akshare as ak

from ..db import get_db_config

logger = logging.getLogger(__name__)

# 指数代码 → (腾讯市场前缀, 指数名)。生产以东财 index_zh_a_hist 为准；腾讯仅作降级。
INDEX_MAP: dict[str, tuple[str, str]] = {
    "000001": ("sh", "上证指数"),
    "000016": ("sh", "上证50"),
    "000300": ("sh", "沪深300"),
    "000688": ("sh", "科创50"),
    "000698": ("sh", "科创100"),
    "000852": ("sh", "中证1000"),
    "000905": ("sh", "中证500"),
    "399001": ("sz", "深证成指"),
    "399006": ("sz", "创业板指"),
    "399330": ("sz", "深证100"),
    "399673": ("sz", "创业板50"),
    "899050": ("bj", "北证50"),
    "931775": ("csi", "中证全指房地产指数"),
}

EM_COLS = {"日期", "开盘", "收盘", "最高", "最低", "成交量", "成交额", "涨跌额", "涨跌幅", "换手率"}


def _today_str() -> str:
    return date.today().isoformat()


class IndexMarketSyncCollector:
    """主流指数日线增量同步"""

    def __init__(self, days_init: int = 500):
        # days_init：某指数本地无记录时的初始回补天数
        self.days_init = days_init

    # ---------- 数据源 ----------
    def _fetch_em(self, code: str, start: str, end: str) -> list[dict] | None:
        """东财指数历史（主源，全字段）"""
        try:
            df = ak.index_zh_a_hist(symbol=code, period="daily", start_date=start.replace("-", ""), end_date=end.replace("-", ""))
        except Exception as e:
            logger.warning(f"指数 {code} 东财源失败: {str(e)[:90]}")
            return None
        if df is None or df.empty:
            return []
        if not EM_COLS.issubset(df.columns):
            logger.warning(f"指数 {code} 东财返回列异常: {list(df.columns)}")
            return None
        rows = []
        for _, r in df.iterrows():
            rows.append({
                "trade_date": str(r["日期"])[:10],
                "open": r["开盘"], "high": r["最高"], "low": r["最低"], "close": r["收盘"],
                "volume": r["成交量"], "amount": r["成交额"],
                "change_amount": r["涨跌额"], "change_pct": r["涨跌幅"],
                "turnover_ratio": r["换手率"],
            })
        return rows

    def _fetch_tencent(self, code: str, market: str, start: str, end: str) -> list[dict] | None:
        """腾讯日 K（降级，仅 OHLCV；change 自算；amount/turnover 缺失为 NULL）"""
        try:
            url = f"https://ifzq.gtimg.cn/appstock/app/fqkline/get?param={market}{code},day,{start},{end},800,qfq"
            resp = requests.get(url, timeout=15)
            data = resp.json()
            day = ((data.get("data") or {}).get(f"{market}{code}") or {}).get("day") or []
        except Exception as e:
            logger.warning(f"指数 {code} 腾讯源失败: {str(e)[:90]}")
            return None
        if not day:
            return []
        rows = []
        prev_close = None
        for it in day:
            try:
                d, o, c, h, l, v = it[0], float(it[1]), float(it[2]), float(it[3]), float(it[4]), float(it[5])
            except (ValueError, IndexError, TypeError):
                continue
            chg_amt = (c - prev_close) if prev_close is not None else None
            chg_pct = (100.0 * (c - prev_close) / prev_close) if prev_close is not None and prev_close else None
            rows.append({
                "trade_date": d, "open": o, "high": h, "low": l, "close": c,
                "volume": int(v) if v else None, "amount": None,
                "change_amount": round(chg_amt, 3) if chg_amt is not None else None,
                "change_pct": round(chg_pct, 4) if chg_pct is not None else None,
                "turnover_ratio": None,
            })
            prev_close = c
        return rows

    # ---------- 主流程 ----------
    def run(self) -> dict:
        conn = pymysql.connect(**get_db_config().to_dict())
        written = skipped = 0
        notes = []
        errors = []
        end = _today_str()
        try:
            with conn.cursor() as cur:
                # DB 中已有的指数集合与名称（可含内置外的增补）
                cur.execute("SELECT DISTINCT index_code, index_name FROM dc_index_market")
                db_map = {r[0]: r[1] for r in cur.fetchall()}
                index_map = dict(INDEX_MAP)
                for code, name in db_map.items():
                    index_map.setdefault(code, ("", name))

                for code, (market, name) in index_map.items():
                    cur.execute("SELECT COALESCE(MAX(trade_date), NULL) FROM dc_index_market WHERE index_code=%s", (code,))
                    row = cur.fetchone()
                    max_d = row[0]
                    if max_d is not None:
                        start = (max_d + timedelta(days=1)).isoformat()
                        if start >= end:  # 已是最新（同一天或未来）
                            skipped += 1
                            continue
                    else:
                        start = (date.today() - timedelta(days=self.days_init)).isoformat()

                    rows = self._fetch_em(code, start, end)
                    source = "AKSHARE"
                    if rows is None:  # 东财异常（非空返回）→ 降级腾讯
                        rows = self._fetch_tencent(code, market, start, end) if market else []
                        source = "TENCENT"
                    if rows is None:
                        errors.append(code)
                        logger.warning(f"指数 {code} 双源均失败，本轮跳过（保留旧数据）")
                        continue
                    if not rows:
                        skipped += 1
                        continue

                    # 清理窗口内旧行（异常残留防重复）+ 写入
                    cur.execute(
                        "DELETE FROM dc_index_market WHERE index_code=%s AND trade_date>=%s AND data_source IN ('AKSHARE','TENCENT')",
                        (code, start),
                    )
                    payload = []
                    for r in rows:
                        payload.append((
                            code, name, r["trade_date"], r["open"], r["high"], r["low"], r["close"],
                            r["volume"], r["amount"], r["change_amount"], r["change_pct"],
                            r["turnover_ratio"], source,
                        ))
                    cur.executemany(
                        "INSERT INTO dc_index_market "
                        "(index_code, index_name, trade_date, open, high, low, close, volume, amount, "
                        " change_amount, change_pct, turnover_ratio, update_time, data_source) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), %s)",
                        payload,
                    )
                    written += len(payload)
                    notes.append(f"{name} +{len(payload)}({source})")
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

        msg = f"指数日线新增 {written} 行 / {len(notes)} 个指数更新" + (f"；失败 {errors}" if errors else "")
        logger.info(f"✅ {msg}")
        return {
            "records_written": written,
            "error_count": len(errors),
            "errors": errors,
            "note": msg,
        }
