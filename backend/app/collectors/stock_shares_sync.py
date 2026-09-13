#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
InvestBuddy 股本变动采集器（stock_shares，巨潮资讯源经 akshare，恢复采集）

背景（2026-09-13）：该表为历史导入遗留（data_source=ADATA），代码层无任何采集器，
水位停于 2025-08-25（本地 15.7 万行 / 5,739 只）。本次按调研报告第 4 批（P3）恢复采集。

- 源：ak.stock_share_change_cninfo(symbol, start_date, end_date) —— 巨潮官方股本变动明细，
      单只返回全部历史事件（含「定期报告」型快照与各类股本变动事件）
- 写表：stock_shares
- 策略：**按「久未刷新」逐股滚动补**（事件型表，不能用数据日期判滞后）
  入选规则：MAX(update_time) < today - refresh_days；最久未刷新的排最前，
  再由 max_stocks 截断 → 每轮推进一批，全市场分批轮转完成（同 stock_company_sync 的 max_count 思路）
- 幂等：DB 侧已有 UNIQUE(stock_code, change_date)（**本表历史上就带唯一索引**）
  + ON DUPLICATE KEY UPDATE 覆盖（巨潮会回溯修订，故用 upsert 而非 IGNORE）
- 容错：单只失败记 error 不中断；每 200 只分批提交 → 断点续传
  ⚠️ 退市股（本地 331 只）源侧无记录，akshare 会抛 KeyError('公告日期')——
  已归一为「无数据」计 no_evt（不算 error），并在候选池中直接排除退市股，
  否则它们会永久占满 max_stocks 名额（占比 ~6%）。

⚠️ 单位换算（已用 000002/600519/900901 逐字段实证）：
  源「总股本 / 已流通股份 / 流通受限股份 / 人民币普通股」单位均为**万股**（4 位小数），
  本地存的是**股数**，故一律 ×10000 后取整。
  字段对应：total_shares ← 总股本；limit_shares ← 流通受限股份（空按 0）；
            list_a_shares ← **人民币普通股**（= A 股流通股，**不是**「已流通股份」）。
  验证：000002 源「人民币普通股 971693.5865 万股」×10000 = 9,716,935,865，
        与本地 change_date=2023-12-31 行的 list_a_shares 逐位一致；
        该股同时有 B 股，故 list_a_shares ≠ total_shares - limit_shares，
        证明本地口径确实是「A 股流通股」而非「总股本 - 限售」。

⚠️ 已知历史数据的日期对齐异常（不在本采集器职责内，仅记档）：
  本地 000002 行标注 change_date=2023-12-31 的 total/limit/list_a 三项，
  与**源当前 2025-12-31 期**的数值逐位一致 —— 疑似原导入器或巨潮侧存在年份错位。
  本采集器一律以**源返回的变动日期为准**，不复现历史错位。
"""
import logging
import time
from datetime import datetime, date

import pymysql

import akshare as ak

from ..db import get_db_config
from ._common import with_steps, call_with_timeout, load_refresh_targets

logger = logging.getLogger(__name__)

RUN_STEPS = [
    {"no": 1, "name": "构建待刷新名单", "params": "候选 = (本地已有股票 ∪ stock_info 在册 A 股) − 退市股；入选 = MAX(update_time) < today-refresh_days；最久未刷新排最前，max_stocks 截断"},
    {"no": 2, "name": "逐股拉股本变动", "params": "ak.stock_share_change_cninfo(symbol, 19900101, 今日)；限流 sleep 0.12s；单只 30s 超时；源无记录（KeyError 公告日期）计「无数据」不计 error；真失败记 error 不中断"},
    {"no": 3, "name": "单位换算与字段映射", "params": "源为万股 → ×10000 转股数；total←总股本 / limit←流通受限股份 / list_a←人民币普通股（实证口径）"},
    {"no": 4, "name": "批量 upsert", "params": "ON DUPLICATE KEY UPDATE 覆盖（巨潮会回溯修订）；每 200 只提交，具备断点续传"},
]

# 源列 → 目标列（数值列单位：万股 → 股）
_COL_MAP = {
    "总股本": ("total_shares", 1),
    "流通受限股份": ("limit_shares", 1),
    "人民币普通股": ("list_a_shares", 1),
}
_INSERT_COLS = ["stock_code", "change_date", "total_shares", "limit_shares",
                "list_a_shares", "change_reason", "update_time", "data_source"]
_UPDATE_COLS = ["total_shares", "limit_shares", "list_a_shares", "change_reason", "update_time"]
_NULL_TOKENS = {"--", "-", "", "nan", "none", "nat"}


def _clean_str(v) -> str | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        import pandas as pd
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    s = str(v).strip()
    return None if s.lower() in _NULL_TOKENS else s


def _to_date(v) -> date | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        import pandas as pd
        if pd.isna(v):
            return None
        if isinstance(v, pd.Timestamp):
            return v.date()
    except (TypeError, ValueError):
        pass
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()[:10]
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _to_shares(v) -> int | None:
    """源「万股」→ 整数股数（×10000）"""
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    return int(round(f * 10000))


class StockSharesSyncCollector:
    """股本变动逐股滚动同步（巨潮 stock_share_change_cninfo）"""

    TABLE = "stock_shares"

    def __init__(self, max_stocks: int = 400, refresh_days: int = 30,
                 sleep_sec: float = 0.12, timeout_sec: float = 30,
                 full_sweep: bool = False):
        self.max_stocks = int(max_stocks or 0)
        self.refresh_days = int(refresh_days or 0)
        self.sleep_sec = float(sleep_sec)
        self.timeout_sec = float(timeout_sec)
        self.full_sweep = bool(full_sweep)

    # ---------- 数据源 ----------

    def _fetch(self, code: str):
        """取单只股本变动明细；源无该股记录时返回 None。

        ⚠️ 2026-09-13 实测：退市股（如 000003 PT金田A / 000005 ST星源 / 000024 招商地产）
        在巨潮侧无任何记录，akshare `stock_share_change_cninfo` 内部会直接访问
        `df['公告日期']` 而抛 `KeyError: '公告日期'` —— 这是**源无数据**的语义，
        不是源故障，必须与真正的网络/服务错误区分开，否则
        (a) error_count 被污染、(b) 「源整体不可达」护栏无法识别真故障。
        已用 10 只活跃蓝筹（600519/000002/300750/688981…）反向验证：均正常返回
        47~175 行、最新变动日期 2026-09-10~09-11，源健康且新鲜。
        """
        try:
            return call_with_timeout(ak.stock_share_change_cninfo, self.timeout_sec,
                                     symbol=code, start_date="19900101",
                                     end_date=date.today().strftime("%Y%m%d"))
        except KeyError as e:
            if "公告日期" in str(e):
                return None
            raise

    # ---------- 主流程 ----------

    def run(self) -> dict:
        conn = pymysql.connect(**get_db_config().to_dict())
        try:
            targets, total = load_refresh_targets(
                conn, self.TABLE, data_col="change_date",
                refresh_days=0 if self.full_sweep else self.refresh_days,
                max_stocks=0 if self.full_sweep else self.max_stocks,
                skip_delisted=True,
            )
        finally:
            conn.close()

        if not targets:
            msg = (f"股本变动无需刷新（候选 {total} 只，全部在 {self.refresh_days} 天内已刷新）")
            logger.info("✅ %s", msg)
            return with_steps(
                {"records_written": 0, "error_count": 0, "errors": [], "note": msg},
                RUN_STEPS, {1: f"候选 {total} 只 · 待刷新 0 只"},
            )
        logger.info("股本变动同步：候选 %s 只，本轮待刷新 %s 只（refresh_days=%s）",
                    total, len(targets), self.refresh_days)

        conn = pymysql.connect(**get_db_config().to_dict())
        written, scanned, errors, no_evt = 0, 0, [], 0
        placeholder = ", ".join(["%s"] * len(_INSERT_COLS))
        upsert_sql = (f"INSERT INTO {self.TABLE} ({', '.join(_INSERT_COLS)}) VALUES ({placeholder}) "
                      f"ON DUPLICATE KEY UPDATE "
                      + ", ".join("`%s`=VALUES(`%s`)" % (c, c) for c in _UPDATE_COLS))
        now = datetime.now()
        try:
            with conn.cursor() as cur:
                for code, _name in targets:
                    scanned += 1
                    try:
                        df = self._fetch(code)
                    except Exception as e:  # noqa: BLE001 - 单只失败不中断
                        errors.append(f"{code}: {type(e).__name__} {str(e)[:70]}")
                        continue
                    if df is None or df.empty or "变动日期" not in df.columns:
                        no_evt += 1
                        continue

                    rows = []
                    for _, r in df.iterrows():
                        cd = _to_date(r.get("变动日期"))
                        if cd is None:
                            continue
                        total_sh = _to_shares(r.get("总股本"))
                        if total_sh is None or total_sh <= 0:
                            continue
                        limit_sh = _to_shares(r.get("流通受限股份"))
                        list_a = _to_shares(r.get("人民币普通股"))
                        rows.append((
                            code, cd, total_sh,
                            0 if limit_sh is None else limit_sh,
                            None if list_a is None else round(list_a, 2),
                            _clean_str(r.get("变动原因")),
                            now, "AKSHARE",
                        ))
                    if rows:
                        cur.executemany(upsert_sql, rows)
                        written += len(rows)
                    else:
                        no_evt += 1
                    if scanned % 200 == 0:
                        conn.commit()
                        logger.info("  已扫描 %s 只，upsert %s 条…", scanned, written)
                    if self.sleep_sec > 0:
                        time.sleep(self.sleep_sec)
                conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

        # 源整体不可达保护：不允许「全失败」被静默当成成功
        if scanned >= 20 and len(errors) >= scanned:
            raise RuntimeError(
                f"全部 {scanned} 只均失败（疑似巨潮源不可达）：{errors[0] if errors else ''}")

        msg = (f"股本变动：扫描 {scanned} 只，upsert {written} 条，失败 {len(errors)}，"
               f"无可用事件 {no_evt} 只")
        logger.info("✅ %s", msg)
        return with_steps(
            {"records_written": written, "error_count": len(errors), "errors": errors[:50], "note": msg},
            RUN_STEPS,
            {
                1: f"候选 {total} 只 · 本轮待刷新 {len(targets)} 只（refresh_days={self.refresh_days}）",
                2: f"请求 {scanned} 只 · 失败 {len(errors)}",
                3: f"换算完成（万股 → 股，×10000）",
                4: f"upsert {written} 条",
            },
        )
