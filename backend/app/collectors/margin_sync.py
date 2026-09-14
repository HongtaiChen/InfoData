#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
InvestBuddy 融资融券采集器（securities_margin，沪深北三市合计，恢复采集）

背景（2026-09-13）：该表为历史导入遗留，代码层无采集器，水位停于 2025-09-18。

口径（2026-09-13 调研阶段用本地 2025-09-16~18 三日逐字段反解确认）：
  rzye     = SSE.融资余额 + SZSE.融资余额 × 1e8 + Σ(BSE 明细.融资余额)
  rqye     = SSE.融券余量金额 + SZSE.融券余额 × 1e8 + Σ(BSE 明细.融券余额)
  rzrqye   = rzye + rqye
  rzrqyecz = rzye - rqye        （本地派生列，三日校验差值均为 0）
  三市求和的吻合度：融券侧 0.003%、融资侧 0.10%（残差来自深交所接口仅 2 位小数的亿元精度）

单位陷阱（务必注意）：
  - stock_margin_sse    返回「元」
  - stock_margin_szse   返回「亿元」  ← 需 ×1e8
  - stock_margin_detail_bse 明细返回「元」（求和即为元；而 stock_margin_bse() 聚合接口是「万元」，勿混用）

- 源：上交所/深交所/北交所官网（经 akshare），无需 token
- 策略：从本地 MAX(trade_date) 的次日起，逐交易日补齐到最近已收盘交易日
- 数据可用性：沪深两所均为 T+1 发布，当日查不到即跳过该日（下轮自动补）
- 幂等：主防线为按本地 trade_date 集合判重；DB 侧另有 UNIQUE INDEX `uk_trade_date`
  （2026-09-13 补，G9）配合 `ON DUPLICATE KEY UPDATE` 兜底并发竞态；重复执行安全
- 容错：SSE 缺失 → 跳过该日；SZSE 缺失 → 跳过该日（沪深为主体，缺一不可）；
        BSE 缺失 → 按 0 计入并记入 errors（占比 ~0.3%，不阻断主链路）

⚠️ 超时兜底（2026-09-13 事故后加固，务必保留）：
  akshare 各接口内部 `requests.get(...)` **不传 timeout**，对端不响应即永久阻塞。
  实测本采集器曾在首次 SZSE 调用处卡死 17 分钟（无日志、无自愈）。
  故所有外部调用统一经 `call_with_timeout` 包裹（默认 60s），超时抛 CollectorTimeout，
  该日按「未完成」跳过并在下一轮自动重试；BSE 另加 bse_retry 次重试（实测 www.bse.cn
  在代理环境下偶发 ProxyError，一次重试即可恢复）。
"""
import logging
import time
from datetime import datetime, date, timedelta

import pandas as pd
import pymysql

import akshare as ak

from ..db import get_db_config
from ._common import with_steps, CollectorTimeout, call_with_timeout

logger = logging.getLogger(__name__)

RUN_STEPS = [
    {"no": 1, "name": "确定补齐区间", "params": "本地 MAX(trade_date)+1 → trade_calendar 最近已收盘交易日"},
    {"no": 2, "name": "拉沪深北三市", "params": "stock_margin_sse(区间) + stock_margin_szse(逐日×1e8) + stock_margin_detail_bse(逐日求和)；均经 60s 超时兜底"},
    {"no": 3, "name": "三市求和与派生", "params": "rzye=沪+深+北；rqye=沪+深+北；rzrqye=rzye+rqye；rzrqyecz=rzye-rqye"},
    {"no": 4, "name": "批量写入", "params": "按 trade_date 判重后 INSERT … ON DUPLICATE KEY UPDATE（data_source=AKSHARE）；未成功日留待下轮补"},
]

_INSERT_COLS = ["trade_date", "rzye", "rqye", "rzrqye", "rzrqyecz", "update_time", "data_source"]


def _num(v) -> float | None:
    """akshare 可能返回 str/float/None；转 float，失败返回 None"""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f      # NaN


class MarginSyncCollector:
    """融资融券（沪+深+北三市合计）增量同步"""

    def __init__(self, max_days: int = 0, sleep_sec: float = 0.25,
                 timeout_sec: float = 60, bse_retry: int = 2):
        self.max_days = int(max_days or 0)
        self.sleep_sec = float(sleep_sec)
        self.timeout_sec = float(timeout_sec)
        self.bse_retry = int(bse_retry)

    # ---------- 区间与存量 ----------

    def _target_dates(self, conn, start: date) -> list[date]:
        """交易日历中 [start, 最近已收盘交易日] 的交易日列表"""
        with conn.cursor() as cur:
            cur.execute(
                "SELECT trade_date FROM trade_calendar "
                "WHERE is_trading_day = 1 AND trade_date >= %s AND trade_date <= CURDATE() "
                "ORDER BY trade_date",
                (start,),
            )
            rows = cur.fetchall()
        out = [r[0] for r in rows]
        return out[: self.max_days] if self.max_days > 0 else out

    def _existing_dates(self, conn) -> set:
        with conn.cursor() as cur:
            cur.execute("SELECT trade_date FROM securities_margin")
            return {r[0] for r in cur.fetchall()}

    def _max_date(self, conn) -> date | None:
        with conn.cursor() as cur:
            cur.execute("SELECT MAX(trade_date) FROM securities_margin")
            return cur.fetchone()[0]

    # ---------- 三市 ----------

    def _fetch_sse(self, start: date, end: date) -> dict[str, tuple[float, float]]:
        """上交所（元）：{YYYYMMDD: (融资余额, 融券余量金额)}

        无数据时 akshare 内部会抛 ValueError（'Length mismatch: Expected axis has 0
        elements, new values have 13 elements'，因响应 0 行但代码仍尝试赋列名）——
        语义等同「未发布/无数据」（两融 T+1，当日数据次日才发布），归一为 {}，
        不计入 errors，留待下轮自动补。
        """
        try:
            df = call_with_timeout(
                ak.stock_margin_sse, self.timeout_sec,
                start_date=start.strftime("%Y%m%d"), end_date=end.strftime("%Y%m%d"),
            )
        except ValueError:
            return {}
        if df is None or df.empty:
            return {}
        out = {}
        for _, r in df.iterrows():
            d = str(r.get("信用交易日期")).strip()
            rz, rq = _num(r.get("融资余额")), _num(r.get("融券余量金额"))
            if d and rz is not None:
                out[d] = (rz, rq or 0.0)
        return out

    def _fetch_szse(self, ds: str) -> tuple[float, float] | None:
        """深交所（亿元 → ×1e8 转元）：(融资余额, 融券余额)

        无数据时 akshare 内部会抛 ValueError（'Length mismatch: Expected axis has 0
        elements'，因响应 0 行但代码仍尝试赋 6 个列名）—— 语义等同「未发布/无数据」，
        此处归一为 None，不计入 errors，留待下轮自动补。
        """
        try:
            df = call_with_timeout(ak.stock_margin_szse, self.timeout_sec, date=ds)
        except ValueError:
            return None
        if df is None or df.empty:
            return None
        r = df.iloc[0]
        rz, rq = _num(r.get("融资余额")), _num(r.get("融券余额"))
        if rz is None:
            return None
        return rz * 1e8, (rq or 0.0) * 1e8

    def _fetch_bse(self, ds: str) -> tuple[float, float] | None:
        """北交所明细（元，逐股求和）：(Σ融资余额, Σ融券余额)

        www.bse.cn 在代理/弱网下偶发 ProxyError 或空返回，故带重试（bse_retry）。
        """
        last_err = None
        for attempt in range(self.bse_retry + 1):
            try:
                df = call_with_timeout(ak.stock_margin_detail_bse, self.timeout_sec, date=ds)
            except Exception as e:  # noqa: BLE001 - 网络抖动，重试
                last_err = e
                if attempt < self.bse_retry:
                    time.sleep(1.0)
                continue
            if df is None or df.empty:
                return None
            return _num(df["融资余额"].sum()) or 0.0, _num(df["融券余额"].sum()) or 0.0
        if last_err is not None:
            raise last_err
        return None

    # ---------- 主流程 ----------

    def run(self) -> dict:
        conn = pymysql.connect(**get_db_config().to_dict())
        try:
            max_d = self._max_date(conn)
            existing = self._existing_dates(conn)
            if max_d is None:
                start = date.today() - timedelta(days=30)
            else:
                start = max_d + timedelta(days=1)
            dates = self._target_dates(conn, start)
        finally:
            conn.close()

        dates = [d for d in dates if d not in existing]
        if not dates:
            logger.info("ℹ️ 融资融券无待补交易日（本地已至 %s）", max_d)
            return with_steps(
                {"records_written": 0, "error_count": 0, "errors": [], "note": "已是最新"},
                RUN_STEPS, {1: f"本地已至 {max_d}，无新增交易日"},
            )

        logger.info("融资融券：待补 %s 个交易日（%s ~ %s）", len(dates), dates[0], dates[-1])

        sse_map = self._fetch_sse(dates[0], dates[-1])
        logger.info("  上交所区间返回 %s 天", len(sse_map))

        conn = pymysql.connect(**get_db_config().to_dict())
        written, errors, pending = 0, [], 0
        placeholder = ", ".join(["%s"] * len(_INSERT_COLS))
        # DB 级幂等：本表已补 UNIQUE INDEX uk_trade_date(trade_date)（2026-09-13，G9）。
        # `_existing_dates` 过滤是主防线；此处的 ON DUPLICATE KEY UPDATE 是并发竞态的兜底
        # （无唯一索引时它等价于普通 INSERT，不影响旧库兼容）。
        update_clause = ", ".join(f"{c}=VALUES({c})" for c in _INSERT_COLS if c != "trade_date")
        insert_sql = (f"INSERT INTO securities_margin ({', '.join(_INSERT_COLS)}) "
                      f"VALUES ({placeholder}) ON DUPLICATE KEY UPDATE {update_clause}")
        now = datetime.now()
        total = len(dates)
        try:
            with conn.cursor() as cur:
                for i, d in enumerate(dates, 1):
                    ds = d.strftime("%Y%m%d")
                    sse = sse_map.get(ds)
                    if sse is None:
                        pending += 1          # 上交所未发布，下轮自动补
                        continue
                    try:
                        szse = self._fetch_szse(ds)
                    except CollectorTimeout as e:
                        errors.append(f"{ds}: 深交所超时（{self.timeout_sec:g}s）{str(e)[:40]}")
                        szse = None
                    except Exception as e:  # noqa: BLE001
                        errors.append(f"{ds}: 深交所 {type(e).__name__} {str(e)[:50]}")
                        szse = None
                    if szse is None:
                        pending += 1
                        continue
                    if self.sleep_sec > 0:
                        time.sleep(self.sleep_sec)
                    bse = None
                    try:
                        bse = self._fetch_bse(ds)
                    except Exception as e:  # noqa: BLE001
                        errors.append(f"{ds}: 北交所 {type(e).__name__} {str(e)[:50]}")
                    if bse is None:
                        errors.append(f"{ds}: 北交所无数据，按 0 计入（占比小）")
                        bse = (0.0, 0.0)
                    if self.sleep_sec > 0:
                        time.sleep(self.sleep_sec)

                    rzye = sse[0] + szse[0] + bse[0]
                    rqye = sse[1] + szse[1] + bse[1]
                    if rzye <= 0:
                        errors.append(f"{ds}: 合计融资余额异常({rzye})，跳过")
                        continue
                    cur.execute(insert_sql, (
                        d, round(rzye, 2), round(rqye, 2),
                        round(rzye + rqye, 2), round(rzye - rqye, 2),
                        now, "AKSHARE",
                    ))
                    written += 1
                    if i % 20 == 0:
                        conn.commit()
                        logger.info("  已处理 %s/%s 日，写入 %s 天（待补 %s，异常 %s）…",
                                    i, total, written, pending, len(errors))
                conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

        msg = f"融资融券补齐 {written} 个交易日（未发布/待补 {pending}，异常 {len(errors)}）"
        logger.info("✅ %s", msg)
        return with_steps(
            {"records_written": written, "error_count": len(errors), "errors": errors[:50], "note": msg},
            RUN_STEPS,
            {
                1: f"{dates[0]} ~ {dates[-1]} 共 {len(dates)} 个交易日",
                2: f"上交所 {len(sse_map)} 天 · 深/北逐日 {len(dates)} 次（超时兜底 {self.timeout_sec:g}s）",
                3: f"三市求和完成",
                4: f"写入 {written} 天（未发布 {pending}）",
            },
        )
