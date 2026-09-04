#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
同花顺概念板块行情采集器

数据源（akshare，同花顺 10jqka）：
- stock_board_concept_name_ths()        当前全部概念（name + concept_code 309xxx）
- stock_board_concept_index_ths(名称)   单概念历史日线（中文列：日期/开盘价/最高价/最低价/收盘价/成交量/成交额）

写入：
- ths_concept_market  日线（唯一键 index_code+trade_date，INSERT IGNORE）
- ths_concept_info    概念清单（名称匹配继承库内 886xxx index_code；新概念用 309xxx 落库）

说明：
- 东财概念接口（stock_board_concept_hist_em）受风控不可用，同花顺为主力源
- 概念只增不减的极端情况：已下架概念保留在库中（名称匹配不到则跳过，不影响历史）
- 增量窗口：库内该概念最大交易日的次日 → 今天；无历史数据的新概念默认回补近 400 天
"""
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

import akshare as ak
import pandas as pd
import pymysql

from ..db import get_db_config

logger = logging.getLogger("infodata.concept_market_sync")

SOURCE = "同花顺"
DATA_SOURCE = "ths"
# 新概念（库内无历史）回补天数
FALLBACK_DAYS = 400
# 并发拉取线程数
WORKERS = 4
# 单概念拉取硬超时（秒）
FETCH_TIMEOUT = 40


def _to_num(x):
    """数值化：None/空/NaN → None（pymysql 不接受 nan）"""
    try:
        if x is None:
            return None
        f = float(x)
        if pd.isna(f):
            return None
        return f
    except (TypeError, ValueError):
        return None


class ConceptMarketSyncCollector:
    def __init__(self, start_date: str | None = None, days_back: int = 15):
        self.start_date = start_date  # 可选：统一起始日 YYYYMMDD（覆盖增量逻辑，用于手动补历史）
        self.days_back = days_back  # 当 start_date 给定时的回看天数（本次增量窗口兜底）

    # ---------- 拉取单概念历史 ----------

    def _fetch_hist(self, name: str, start: str, end: str):
        """返回 DataFrame（akshare 中文列）或抛异常"""
        return ak.stock_board_concept_index_ths(symbol=name, start_date=start, end_date=end)

    # ---------- 主流程 ----------

    def run(self) -> dict:
        conn = pymysql.connect(**get_db_config().to_dict())
        try:
            # 1. 当前概念列表（name + concept_code）
            logger.info("拉取同花顺概念列表…")
            list_df = ak.stock_board_concept_name_ths()
            concepts = []
            for _, r in list_df.iterrows():
                name = str(r["name"]).strip()
                code = str(r["code"]).strip()
                if name:
                    concepts.append({"concept_name": name, "concept_code": code})
            logger.info(f"当前概念 {len(concepts)} 个")

            # 2. 库内映射：concept_name -> {index_code, last_date}
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT i.concept_name, i.index_code,
                           (SELECT MAX(m.trade_date) FROM ths_concept_market m
                            WHERE m.index_code = i.index_code) AS last_date
                    FROM ths_concept_info i
                    """
                )
                db_map = {}
                for concept_name, index_code, last_date in cur.fetchall():
                    db_map[concept_name] = {
                        "index_code": index_code,
                        "last_date": str(last_date) if last_date else None,
                    }

            # 3. 计算每个概念的拉取窗口
            end = datetime.now()
            end_str = end.strftime("%Y%m%d")
            fallback_start = (end - timedelta(days=FALLBACK_DAYS)).strftime("%Y%m%d")
            tasks = []
            for c in concepts:
                name = c["concept_name"]
                existing = db_map.get(name)
                if existing and existing["last_date"]:
                    # 增量：库里最大交易日 + 1 天 → 今天
                    start_dt = datetime.strptime(existing["last_date"], "%Y-%m-%d") + timedelta(days=1)
                    start = start_dt.strftime("%Y%m%d")
                else:
                    start = self.start_date or fallback_start
                if start > end_str:
                    continue  # 已最新
                tasks.append(
                    {
                        **c,
                        "index_code": existing["index_code"] if existing else None,
                        "start": start,
                        "end": end_str,
                    }
                )
            logger.info(f"待拉取 {len(tasks)} 个概念（已最新跳过 {len(concepts) - len(tasks)} 个）")

            # 4. 并发拉取
            results = {}
            errors = []
            if tasks:
                with ThreadPoolExecutor(max_workers=WORKERS) as pool:
                    fut_map = {
                        pool.submit(self._fetch_hist, t["concept_name"], t["start"], t["end"]): t
                        for t in tasks
                    }
                    for fut in as_completed(fut_map):
                        t = fut_map[fut]
                        try:
                            df = fut.result(timeout=FETCH_TIMEOUT + 10)
                            if df is not None and len(df) > 0:
                                results[t["concept_name"]] = {"task": t, "df": df}
                        except Exception as e:
                            errors.append(f"{t['concept_name']}: {str(e)[:80]}")
                            logger.warning(f"✗ {t['concept_name']} 拉取失败: {str(e)[:80]}")

            logger.info(f"拉取完成：成功 {len(results)} 个，失败 {len(errors)} 个")

            # 5. 组装并批量写入
            rows_market = []      # (index_code, concept_code, name, date, o,h,l,c, vol, amt, chg_amt, chg_pct)
            rows_info_new = []    # 新概念: (concept_code, concept_name)
            now = datetime.now()
            for name, r in results.items():
                task, df = r["task"], r["df"]
                index_code = task["index_code"]
                if index_code is None:
                    # 库内无此概念 → 新概念：concept_code 兼作 index_code（309 段不与 886 冲突）
                    index_code = task["concept_code"]
                    if task["concept_code"] not in {x[0] for x in rows_info_new}:
                        rows_info_new.append((task["concept_code"], name))
                df = df.rename(
                    columns={
                        "日期": "date",
                        "开盘价": "open",
                        "最高价": "high",
                        "最低价": "low",
                        "收盘价": "close",
                        "成交量": "volume",
                        "成交额": "amount",
                    }
                )
                # 日期统一为 YYYY-MM-DD 字符串并过滤非法行
                df["date"] = pd.to_datetime(df["date"], errors="coerce")
                df = df[df["date"].notna()]
                prev_close = None
                for _, x in df.iterrows():
                    close = _to_num(x.get("close"))
                    if close is None:
                        continue
                    chg_amt = None
                    chg_pct = None
                    if prev_close:
                        chg_amt = round(close - prev_close, 4)
                        if prev_close:
                            chg_pct = round((close / prev_close - 1) * 100, 4)
                    prev_close = close
                    rows_market.append(
                        (
                            index_code,
                            task["concept_code"],
                            name,
                            x["date"].strftime("%Y-%m-%d"),
                            _to_num(x.get("open")),
                            close,
                            _to_num(x.get("high")),
                            _to_num(x.get("low")),
                            _to_num(x.get("volume")),
                            _to_num(x.get("amount")),
                            chg_amt,
                            chg_pct,
                            now,
                            DATA_SOURCE,
                        )
                    )

            with conn.cursor() as cur:
                # 新概念入库
                inserted_info = 0
                for concept_code, name in rows_info_new:
                    cur.execute(
                        "SELECT COUNT(*) FROM ths_concept_info WHERE concept_name=%s",
                        (name,),
                    )
                    if cur.fetchone()[0] == 0:
                        cur.execute(
                            "INSERT INTO ths_concept_info (index_code, concept_code, concept_name, source, update_time, data_source) "
                            "VALUES (%s, %s, %s, %s, %s, %s)",
                            (concept_code, concept_code, name, SOURCE, now, DATA_SOURCE),
                        )
                        inserted_info += 1
                # 行情批量写入（INSERT IGNORE：唯一键 index_code+trade_date）
                written = 0
                if rows_market:
                    sql = """
                        INSERT IGNORE INTO ths_concept_market
                        (index_code, concept_code, concept_name, trade_date,
                         open, close, high, low, volume, amount,
                         change_amount, change_pct, update_time, data_source)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """
                    cur.executemany(sql, rows_market)
                    written = cur.rowcount
                conn.commit()

            logger.info(
                f"✅ 概念行情同步完成：写入 {written} 行（新概念 {inserted_info} 个，失败 {len(errors)} 个）"
            )
            return {
                "records_written": written,
                "error_count": len(errors),
                "errors": errors,
                "note": f"概念数 {len(concepts)}，成功拉取 {len(results)}，新增概念 {inserted_info}",
            }
        finally:
            conn.close()
