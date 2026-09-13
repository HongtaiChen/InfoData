#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
InvestBuddy 财务关键指标采集器（stock_financial_abstract_ths，同花顺源经 akshare，恢复采集）

背景（2026-09-13）：该表为历史导入遗留，代码层无任何采集器，水位停于 2025-06-30
（本地 34.4 万行 / 5,850 只）。本次按调研报告第 4 批（P3）恢复采集。

- 源：ak.stock_financial_abstract_ths(symbol, indicator='按报告期') —— 单只返回全历史期
- 写表：stock_financial_abstract_ths（8 个业务列，映射已用 600519/920819 逐字段实证）
- 策略：**只补滞后股票 + 逐股增量 upsert**
  滞后判定 floor = max(本地全局 MAX(报告期), 按披露日历推算的最近报告期)；
  候选池 = (本地已有股票 ∪ stock_info 在册 A 股) − 退市股（见 _common.load_refresh_targets）
  ⚠️ 必须排除退市股：其报告期永远停留在退市时刻 → 永远命中「滞后」→
  331 只退市股会永久占满 max_stocks 名额，在册股票永远轮不到刷新（2026-09-13 实测修正）
- 幂等：DB 侧 UNIQUE(stock_code, report_date) + ON DUPLICATE KEY UPDATE
  （财务数据会被追溯调整，故用 upsert 覆盖而非 INSERT IGNORE）
- 性能：实测单只 0.45s；单轮上限由 max_stocks 控制（0 = 不限）；
  每 200 只分批提交 → 中途失败已写入的部分不回滚，具备断点续传效果
- 容错：单只失败记 error 不中断，下轮自动重试（仍滞后 → 仍入选）

⚠️ 源值清洗：akshare 对缺失值返回的是 Python 布尔 `False`（不是 NaN），
   直接入库会写成字符串 'False'；本采集器统一清洗为 NULL。
   `--` 同样按 NULL 处理。
"""
import logging
import time
from datetime import datetime, date

import pymysql

import akshare as ak

from ..db import get_db_config
from ._common import (with_steps, call_with_timeout,
                      load_refresh_targets, latest_expected_report_period)

logger = logging.getLogger(__name__)

RUN_STEPS = [
    {"no": 1, "name": "构建候选池与滞后集", "params": "候选 = (本地已有股票 ∪ stock_info 在册 A 股) − 退市股；滞后 = MAX(报告期) < max(本地全局 MAX, 披露日历推算最近期)"},
    {"no": 2, "name": "逐股拉财务摘要", "params": "ak.stock_financial_abstract_ths(symbol, '按报告期')；限流 sleep 0.12s；单只 30s 超时；失败记 error 不中断"},
    {"no": 3, "name": "源值清洗", "params": "akshare 对缺失返回布尔 False（非 NaN）→ 统一清洗为 NULL；'--' 同处理"},
    {"no": 4, "name": "批量 upsert", "params": "ON DUPLICATE KEY UPDATE 覆盖（财务数据会追溯调整）；每 200 只提交，具备断点续传"},
]

# 源列 → 目标列（2026-09-13 用 600519 / 920819 逐字段实证一致）
_COL_MAP = {
    "报告期": "report_date",
    "净利润": "net_profit",
    "净利润同比增长率": "net_profit_yoy_gr",
    "营业总收入": "total_operating_revenue",
    "营业总收入同比增长率": "total_operating_yoy_gr",
    "基本每股收益": "basic_eps",
    "每股净资产": "net_asset_ps",
    "净资产收益率": "roe",
}
_NULL_TOKENS = {"--", "-", "", "nan", "none", "nat", "false", "true"}
_INSERT_COLS = ["stock_code", "stock_name", "report_date", "net_profit", "net_profit_yoy_gr",
                "total_operating_revenue", "total_operating_yoy_gr", "basic_eps",
                "net_asset_ps", "roe", "update_time", "data_source"]
_UPDATE_COLS = ["stock_name", "net_profit", "net_profit_yoy_gr", "total_operating_revenue",
                "total_operating_yoy_gr", "basic_eps", "net_asset_ps", "roe", "update_time"]


def _clean_str(v) -> str | None:
    """源值 → 字符串或 None（布尔 False / NaN / '--' 一律 None）"""
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


def _to_dec(v, scale: int = 2):
    """数值列（basic_eps / net_asset_ps 为 DECIMAL）→ float 或 None"""
    s = _clean_str(v)
    if s is None:
        return None
    try:
        return round(float(s.replace(",", "")), scale)
    except ValueError:
        return None


class FinancialAbstractSyncCollector:
    """财务关键指标逐股增量同步（同花顺 stock_financial_abstract_ths）"""

    TABLE = "stock_financial_abstract_ths"

    def __init__(self, max_stocks: int = 0, sleep_sec: float = 0.12,
                 timeout_sec: float = 30, full_sweep: bool = False,
                 retry: int = 1, retry_backoff: float = 2.0):
        self.max_stocks = int(max_stocks or 0)
        self.sleep_sec = float(sleep_sec)
        self.timeout_sec = float(timeout_sec)
        self.full_sweep = bool(full_sweep)
        self.retry = max(int(retry or 0), 0)
        self.retry_backoff = float(retry_backoff)

    # ---------- 数据源 ----------

    def _fetch_once(self, code: str):
        return call_with_timeout(ak.stock_financial_abstract_ths, self.timeout_sec,
                                 symbol=code, indicator="按报告期")

    def _fetch(self, code: str):
        """单股重试：同花顺在长跑中会间歇限流（实测全量 5,379 只跑到后半程 34% 失败，
        停跑后单测同样的股票立即恢复正常——301076/301568 复测 39/33 行），
        失败退避后重试即可恢复；真无页面（如部分新上市股）重试后仍失败则记 error。"""
        last = None
        for attempt in range(self.retry + 1):
            try:
                return self._fetch_once(code)
            except Exception as e:  # noqa: BLE001 - 重试后仍失败向上抛
                last = e
                if attempt < self.retry:
                    time.sleep(self.retry_backoff)
        raise last

    # ---------- 主流程 ----------

    def run(self) -> dict:
        conn = pymysql.connect(**get_db_config().to_dict())
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT MAX(report_date) FROM %s" % self.TABLE)
                row = cur.fetchone()
            local_max = row[0] if row and row[0] else None
            expected = latest_expected_report_period()
            # 关键：floor 与「按披露日历推算的最近报告期」取大 —— 若本地整体停在旧期，
            # 只用本地全局 MAX 会让滞后集为空、永远补不上（2026-09-13 实测踩过这个坑）
            floor = max([d for d in (local_max, expected) if d is not None])
            targets, total = load_refresh_targets(
                conn, self.TABLE, data_col="report_date", floor_date=floor,
                name_col="stock_name",
                max_stocks=0 if self.full_sweep else self.max_stocks,
                skip_delisted=True,
            )
        finally:
            conn.close()

        if not targets:
            msg = (f"财务摘要已全部最新（本地 MAX={local_max}，应披露至 {expected}，"
                   f"滞后 0 只 / 候选 {total} 只）")
            logger.info("✅ %s", msg)
            return with_steps(
                {"records_written": 0, "error_count": 0, "errors": [], "note": msg},
                RUN_STEPS,
                {1: f"候选 {total} 只 · 滞后 0 只（本地 MAX={local_max}，应至 {expected}）"},
            )
        logger.info("财务摘要同步：候选 %s 只，本轮待刷新 %s 只（本地 MAX=%s，应至 %s，floor=%s）",
                    total, len(targets), local_max, expected, floor)

        conn = pymysql.connect(**get_db_config().to_dict())
        written, scanned, errors, no_name = 0, 0, [], 0
        placeholder = ", ".join(["%s"] * len(_INSERT_COLS))
        upsert_sql = (f"INSERT INTO {self.TABLE} ({', '.join(_INSERT_COLS)}) VALUES ({placeholder}) "
                      f"ON DUPLICATE KEY UPDATE "
                      + ", ".join("`%s`=VALUES(`%s`)" % (c, c) for c in _UPDATE_COLS))
        now = datetime.now()
        try:
            with conn.cursor() as cur:
                for code, name in targets:
                    scanned += 1
                    try:
                        df = self._fetch(code)
                    except Exception as e:  # noqa: BLE001 - 单只失败不中断
                        errors.append(f"{code}: {type(e).__name__} {str(e)[:70]}")
                        continue
                    if df is None or df.empty or "报告期" not in df.columns:
                        continue
                    if not name:
                        name = code
                        no_name += 1
                    rows = []
                    for _, r in df.iterrows():
                        rd = _to_date(r.get("报告期"))
                        if rd is None:
                            continue
                        rows.append((
                            code, name, rd,
                            _clean_str(r.get("净利润")),
                            _clean_str(r.get("净利润同比增长率")),
                            _clean_str(r.get("营业总收入")),
                            _clean_str(r.get("营业总收入同比增长率")),
                            _to_dec(r.get("基本每股收益")),
                            _to_dec(r.get("每股净资产")),
                            _clean_str(r.get("净资产收益率")),
                            now, "AKSHARE",
                        ))
                    if rows:
                        cur.executemany(upsert_sql, rows)
                        written += len(rows)
                    if scanned % 200 == 0:
                        conn.commit()
                        logger.info("  已扫描 %s 只，写入 %s 条…", scanned, written)
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
                f"全部 {scanned} 只均失败（疑似源不可达）：{errors[0] if errors else ''}")

        msg = (f"财务关键指标：扫描 {scanned} 只（滞后股票），upsert {written} 条，"
               f"失败 {len(errors)}")
        logger.info("✅ %s", msg)
        return with_steps(
            {"records_written": written, "error_count": len(errors), "errors": errors[:50], "note": msg},
            RUN_STEPS,
            {
                1: f"候选 {total} 只 · 本轮待刷新 {len(targets)} 只（本地 MAX={local_max}，应至 {expected}）",
                2: f"请求 {scanned} 只 · 失败 {len(errors)}",
                3: f"清洗完成（缺失值 False/-- → NULL）" + (f"；{no_name} 只用代码兜底名称" if no_name else ""),
                4: f"upsert {written} 条",
            },
        )
