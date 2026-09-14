#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
InvestBuddy 上市/退市状态采集器（stock_status_sync，Baostock 权威源，周更）

- 源：bs.query_stock_basic() —— 一次返回沪深全部股票（含退市 300+ 只）
  status: 1=上市 0=退市；outDate 非空即退市日期；ipoDate 为官方上市日期
- 写表 stock_info：
  1. list_status（'上市'/'退市'）与 delist_date 全量覆盖（按 stock_code）
  2. list_date 仅补 NULL（用官方 ipoDate，不覆盖已有值）
- 边界：Baostock 仅覆盖沪深 A 股；B 股/北交所代码不在名单 → 保持 NULL 不误标
- 幂等：整轮可安全重复执行

⚠️ 容错契约（2026-09-14 补）：Baostock 为**登录态会话**，网络抖动常表现为
   「登录失败: 网络接收错误」/「query_stock_basic 失败: 网络接收错误」并在数秒内快速失败
   （2026-09-14 一天内实测失败 2 次，均在重跑后立刻成功）。故本采集器加**重试 + 退避**：
   整段「登录 → 拉取 → 登出」作为一次尝试，失败则退避后重来（而不是只重试单个接口）。
   刻意不用 `call_with_timeout` 的守护线程超时——被放弃的线程仍持有全局 baostock 会话，
   其 logout() 会踩掉重试会话，反而制造更诡异的行为。
"""
import logging
import time

import pymysql

import baostock as bs

from ..db import get_db_config
from ._common import with_steps

logger = logging.getLogger(__name__)

# 运行步骤链模板（供前端「数据流·整链拓扑」展示运行逻辑）
RUN_STEPS = [
    {"no": 1, "name": "Baostock 全量拉取", "params": "query_stock_basic（沪深全部，含退市 300+ 只）；登录态会话，失败退避重试 2 次"},
    {"no": 2, "name": "数量护栏校验", "params": "返回 < 4000 只拒绝覆盖（防接口异常）"},
    {"no": 3, "name": "状态全量覆盖", "params": "逐只 UPDATE stock_info.list_status（上市/退市）"},
    {"no": 4, "name": "日期字段补充", "params": "outDate → delist_date；官方 ipoDate 仅补空 list_date（不覆盖本地推断）"},
]

_PREFIX = ("sh.", "sz.")
# Baostock 网络抖动重试（见模块头「容错契约」）
RETRY = 2
BACKOFF = 3.0


class StockStatusSyncCollector:
    """上市/退市状态同步（Baostock）"""

    def _fetch_once(self) -> list[dict]:
        lg = bs.login()
        if lg.error_code != "0":
            raise RuntimeError(f"Baostock 登录失败: {lg.error_msg}")
        try:
            rs = bs.query_stock_basic()
            if rs.error_code != "0":
                raise RuntimeError(f"query_stock_basic 失败: {rs.error_msg}")
            out = []
            while rs.error_code == "0" and rs.next():
                row = rs.get_row_data()  # [code, code_name, ipoDate, outDate, type, status]
                if row[4] != "1":  # 只要股票
                    continue
                if not row[0].startswith(_PREFIX):
                    continue
                out.append(
                    {
                        "stock_code": row[0][3:],
                        "ipo_date": row[2] or None,
                        "out_date": row[3] or None,
                        "status": "退市" if row[5] == "0" else "上市",
                    }
                )
            return out
        finally:
            bs.logout()

    def _fetch_all_stocks(self) -> list[dict]:
        """带退避重试的全量拉取（登录态会话整体重来，见模块头「容错契约」）"""
        last: Exception | None = None
        for i in range(RETRY + 1):
            try:
                return self._fetch_once()
            except Exception as e:  # noqa: BLE001 - 重试后仍失败则原样抛出
                last = e
                if i < RETRY:
                    delay = BACKOFF * (2 ** i)
                    logger.warning(f"Baostock 拉取失败（第 {i + 1}/{RETRY + 1} 次），{delay:g}s 后重试: {e}")
                    time.sleep(delay)
        assert last is not None
        raise last

    def run(self) -> dict:
        stocks = self._fetch_all_stocks()
        logger.info("Baostock 返回沪深股票 %s 只", len(stocks))
        if len(stocks) < 4000:
            raise RuntimeError(f"Baostock 仅返回 {len(stocks)} 只，疑似异常，拒绝覆盖")

        conn = pymysql.connect(**get_db_config().to_dict())
        upd_status = upd_delist = upd_listdate = 0
        try:
            with conn.cursor() as cur:
                for s in stocks:
                    cur.execute(
                        "UPDATE stock_info SET list_status=%s WHERE stock_code=%s",
                        (s["status"], s["stock_code"]),
                    )
                    upd_status += cur.rowcount
                    if s["out_date"]:
                        cur.execute(
                            "UPDATE stock_info SET delist_date=%s WHERE stock_code=%s",
                            (s["out_date"], s["stock_code"]),
                        )
                        upd_delist += cur.rowcount
                    if s["ipo_date"]:
                        cur.execute(
                            "UPDATE stock_info SET list_date=%s WHERE stock_code=%s AND list_date IS NULL",
                            (s["ipo_date"], s["stock_code"]),
                        )
                        upd_listdate += cur.rowcount
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

        msg = (
            f"上市/退市状态同步：覆盖 {upd_status} 只 / 写退市日期 {upd_delist} 只 / 补上市日期 {upd_listdate} 只"
        )
        logger.info("✅ %s", msg)
        return with_steps(
            {
                "records_written": upd_status,
                "error_count": 0,
                "errors": [],
                "note": msg,
            },
            RUN_STEPS,
            {
                1: f"Baostock 返回 {len(stocks)} 只",
                2: f"{len(stocks)} ≥ 4000 通过",
                3: f"覆盖 {upd_status} 只",
                4: f"退市日 {upd_delist} 只 · 补上市日 {upd_listdate} 只",
            },
        )
