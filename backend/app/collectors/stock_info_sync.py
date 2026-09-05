#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
InvestBuddy 股票基础信息采集器（stock_info / stock_info_ex 表，周更）
- 主源：ak.stock_zh_a_spot_em()（东财沪深京 A 股当前名单，含代码/名称）
- 降级：本地 stock_market_current 最新快照（东财挂时仍可周更，名称以最新行情为准）
- 上市日期 list_date：从 stock_market_daily 按 MIN(trade_date) 回填（仅对缺失/新增代码，索引查询）
- 策略：不删退市/旧行 —— 名单内 UPDATE 名称与交易所，名单外保留；仅 INSERT 新上市
- stock_info_ex 同步同名/交易所，保留人工字段 is_gxlstock（如「高股息」标记）
"""
import logging
from datetime import datetime, date

import pymysql
import akshare as ak

from ..db import get_db_config

logger = logging.getLogger(__name__)


def _exchange_of(code: str) -> str:
    """按 A 股代码前缀推断交易所"""
    if code.startswith(("60", "68", "90")):
        return "SH"
    if code.startswith(("00", "30", "20")):
        return "SZ"
    if code.startswith(("43", "83", "87", "88", "92", "82", "89")):
        return "BJ"
    if code.startswith(("4", "8")):
        return "BJ"
    return "SH" if code.startswith(("5", "6", "9")) else "SZ"


class StockInfoSyncCollector:
    """股票基础资料同步（周更）"""

    def __init__(self, max_backfill: int = 600):
        # 单次最多为多少只缺 list_date 的股票回填（防止首跑全表扫描）
        self.max_backfill = max_backfill

    # ---------- 数据源 ----------
    def _fetch_primary(self) -> dict[str, str]:
        """东财沪深京 A 股全量代码→名称"""
        df = ak.stock_zh_a_spot_em()
        if df is None or df.empty:
            raise RuntimeError("stock_zh_a_spot_em 返回空")
        if not {"代码", "名称"}.issubset(df.columns):
            raise RuntimeError(f"stock_zh_a_spot_em 列异常: {list(df.columns)}")
        return {str(r["代码"]).strip(): str(r["名称"]).strip() for _, r in df.iterrows()}

    def _fetch_fallback(self) -> dict[str, str]:
        """本地最新行情快照（stock_market_current）作代码→名称源"""
        conn = pymysql.connect(**get_db_config().to_dict())
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT stock_code, stock_name FROM stock_market_current")
                rows = cur.fetchall()
        finally:
            conn.close()
        m = {str(c).strip(): str(n).strip() for c, n in rows}
        if len(m) < 3000:
            raise RuntimeError(f"本地快照仅 {len(m)} 只，不可作全市场名单")
        return m

    def _list_date_of(self, code: str) -> date | None:
        conn = pymysql.connect(**get_db_config().to_dict())
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT MIN(trade_date) FROM stock_market_daily WHERE stock_code=%s", (code,)
                )
                row = cur.fetchone()
        finally:
            conn.close()
        return (row[0].date() if isinstance(row[0], datetime) else row[0]) if row and row[0] else None

    # ---------- 主流程 ----------
    def run(self) -> dict:
        # 1. 取全市场名单（主源 → 本地降级）
        try:
            name_map = self._fetch_primary()
            source_tag = "AKSHARE"
        except Exception as e:
            logger.warning(f"东财名单失败，降级本地快照: {str(e)[:80]}")
            name_map = self._fetch_fallback()
            source_tag = "LOCAL"

        conn = pymysql.connect(**get_db_config().to_dict())
        inserted = updated = 0
        try:
            with conn.cursor() as cur:
                # 2. 现有 stock_info / stock_info_ex 快照
                cur.execute("SELECT stock_code, short_name, list_date FROM stock_info")
                exist = {str(c): {"name": n, "list_date": d} for c, n, d in cur.fetchall()}
                cur.execute("SELECT stock_code FROM stock_info_ex")
                exist_ex = {str(c) for (c,) in cur.fetchall()}

                # 3. 需要回填上市日期的代码 = 新代码 + 已有但缺 list_date 的
                need_list = {c for c in name_map if c not in exist} | {
                    c for c, v in exist.items() if v["list_date"] is None and c in name_map
                }
                backfilled = 0
                list_date_map: dict[str, date | None] = {}
                for code in sorted(need_list):
                    if backfilled >= self.max_backfill:
                        break
                    list_date_map[code] = self._list_date_of(code)
                    backfilled += 1

                # 4. stock_info：UPDATE 名单内 / INSERT 新
                upd, ins = [], []
                for code, name in name_map.items():
                    ex = _exchange_of(code)
                    ld = list_date_map.get(code)
                    if ld is None and code in exist:
                        ld = exist[code]["list_date"]
                    if code in exist:
                        upd.append((name, ex, ld, code))
                    else:
                        ins.append((code, name, ex, ld, source_tag))
                cur.executemany(
                    "UPDATE stock_info SET short_name=%s, exchange=%s, list_date=%s, update_time=NOW() "
                    "WHERE stock_code=%s",
                    upd,
                )
                updated = cur.rowcount
                cur.executemany(
                    "INSERT INTO stock_info (stock_code, short_name, exchange, list_date, update_time, data_source) "
                    "VALUES (%s, %s, %s, %s, NOW(), %s)",
                    ins,
                )
                inserted = len(ins)

                # 5. stock_info_ex：同名/交易所；新代码补入（保留人工 is_gxlstock）
                upd_ex, ins_ex = [], []
                for code, name in name_map.items():
                    ex = _exchange_of(code)
                    if code in exist_ex:
                        upd_ex.append((name, ex, code))
                    else:
                        ins_ex.append((code, name, ex, source_tag))
                cur.executemany(
                    "UPDATE stock_info_ex SET short_name=%s, exchange=%s, update_time=NOW() WHERE stock_code=%s",
                    upd_ex,
                )
                cur.executemany(
                    "INSERT INTO stock_info_ex (stock_code, short_name, is_gxlstock, exchange, list_date, update_time, data_source) "
                    "VALUES (%s, %s, NULL, %s, %s, NOW(), %s)",
                    [(c, n, e, list_date_map.get(c), s) for c, n, e, s in ins_ex],
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

        msg = f"股票基础资料同步：{len(name_map)} 只在名单（新增 {inserted} / 更新 {updated}，回填 list_date {backfilled}），源={source_tag}"
        logger.info(f"✅ {msg}")
        return {
            "records_written": inserted + updated,
            "error_count": 0,
            "errors": [],
            "note": msg,
        }
