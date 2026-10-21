#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
投资环境模块 · 国际环境「市场波动与风险偏好」—— 风险/波动率指数采集器（global_risk_sync）

把三类国际风险指标落库，作为「市场波动与风险偏好」块的核心量化数据：
  · OVX       —— 原油 ETF 隐含波动率（Cboe 官方 CSV，2007-05-10 起，日频）
  · GPR 日频  —— 地缘政治风险指数（Caldara & Iacoviello，10 报，1985 起，日频）
  · EPU 日频  —— 美国经济政策不确定性（Baker/Bloom/Davis，1985 起，日频）

数据源：
  · OVX : https://cdn-api.cboe.com/api/global/us_indices/daily_prices/OVX_History.csv
  · GPR : https://www.matteoiacoviello.com/gpr_files/data_gpr_daily_recent.xls
  · EPU : https://www.policyuncertainty.com/media/All_Daily_Policy_Data.csv

⚠️ 三源均为官方/学术开放数据（OVX 为 Cboe 官方、GPR 为 AER 论文配套、EPU 为
   QJE 论文配套），免费、无需 Key、可程序化采集。三者最新数据均为初值、
   会随报纸入库延迟而修订 ⇒ 采集器按「全量覆盖（REPLACE）」而非增量追加，
   确保本地永远镜像官方最新版本。

落库表：
  · ovx_daily     （OVX 原油波动率日线）
  · gpr_daily     （GPR 地缘政治风险日频）
  · epu_daily     （美国 EPU 日频）
"""
import logging
from datetime import date, datetime

import pymysql

from ..db import get_db_config
from ._common import with_steps

logger = logging.getLogger(__name__)

_H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120"}

_OVX_URL = "https://cdn-api.cboe.com/api/global/us_indices/daily_prices/OVX_History.csv"
_GPR_URL = "https://www.matteoiacoviello.com/gpr_files/data_gpr_daily_recent.xls"
_EPU_URL = "https://www.policyuncertainty.com/media/All_Daily_Policy_Data.csv"


def _d(v) -> date | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()[:10]
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%Y%m%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _f(v, nd: int = 4):
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return round(f, nd)


class OvxSyncCollector:
    """OVX 原油波动率日线（Cboe 官方 CSV）"""

    def __init__(self, timeout_sec: float = 30):
        self.timeout_sec = float(timeout_sec)

    @staticmethod
    def _ensure_table(conn):
        ddl = """
        CREATE TABLE IF NOT EXISTS ovx_daily (
          trade_date DATE NOT NULL COMMENT '交易日',
          close DECIMAL(10,4) DEFAULT NULL COMMENT 'OVX 收盘',
          update_time TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          data_source VARCHAR(100) DEFAULT NULL,
          PRIMARY KEY (trade_date)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        COMMENT='OVX 原油ETF隐含波动率日线（Cboe 官方 CSV，2026-10-21）'
        """
        with conn.cursor() as cur:
            cur.execute(ddl)
        conn.commit()

    def run(self) -> dict:
        import requests
        import pandas as pd

        conn = pymysql.connect(**get_db_config().to_dict(),
                               cursorclass=pymysql.cursors.DictCursor)
        written, errors = 0, []
        try:
            self._ensure_table(conn)
            r = requests.get(_OVX_URL, headers=_H, timeout=self.timeout_sec)
            r.raise_for_status()
            df = pd.read_csv(io_string(r.text))
            rows = []
            for _, row in df.iterrows():
                d = _d(row.get("DATE"))
                c = _f(row.get("OVX"), 4)
                if d is None or c is None:
                    continue
                rows.append((d, c))
            if rows:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM ovx_daily")
                    cur.executemany(
                        "INSERT INTO ovx_daily (trade_date, close, update_time, data_source) "
                        "VALUES (%s,%s,NOW(),'CBOE')", rows)
                conn.commit()
            written = len(rows)
        except Exception as e:  # noqa: BLE001
            errors.append(f"OVX: {type(e).__name__} {str(e)[:80]}")
            logger.warning("⚠️ OVX 采集失败: %s", e)
        finally:
            conn.close()
        return with_steps(
            {"records_written": written, "error_count": len(errors), "errors": errors[:3],
             "note": f"OVX 全量覆盖 {written} 行"},
            [{"no": 1, "name": "拉取 Cboe OVX 官方 CSV", "params": _OVX_URL},
             {"no": 2, "name": "全量覆盖落库", "params": "REPLACE 语义，镜像官方最新"}],
            {1: f"HTTP 200，{written} 行", 2: f"ovx_daily 写入 {written} 行"},
        )


class GprSyncCollector:
    """GPR 地缘政治风险日频（Caldara & Iacoviello）"""

    def __init__(self, timeout_sec: float = 60):
        self.timeout_sec = float(timeout_sec)

    @staticmethod
    def _ensure_table(conn):
        ddl = """
        CREATE TABLE IF NOT EXISTS gpr_daily (
          trade_date DATE NOT NULL COMMENT '日期',
          gprd DECIMAL(10,4) DEFAULT NULL COMMENT 'GPR 总指数',
          gprd_act DECIMAL(10,4) DEFAULT NULL COMMENT '实际行动子指数',
          gprd_threat DECIMAL(10,4) DEFAULT NULL COMMENT '威胁子指数',
          gprd_ma30 DECIMAL(10,4) DEFAULT NULL COMMENT '30日均值',
          event VARCHAR(255) DEFAULT NULL COMMENT '重大事件标注',
          update_time TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          data_source VARCHAR(100) DEFAULT NULL,
          PRIMARY KEY (trade_date)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        COMMENT='GPR 地缘政治风险指数日频（Caldara & Iacoviello，2026-10-21）'
        """
        with conn.cursor() as cur:
            cur.execute(ddl)
        conn.commit()

    def run(self) -> dict:
        import requests
        import pandas as pd
        import io

        conn = pymysql.connect(**get_db_config().to_dict(),
                               cursorclass=pymysql.cursors.DictCursor)
        written, errors = 0, []
        try:
            self._ensure_table(conn)
            r = requests.get(_GPR_URL, headers=_H, timeout=self.timeout_sec)
            r.raise_for_status()
            df = pd.read_excel(io.BytesIO(r.content))
            rows = []
            for _, row in df.iterrows():
                d = _d(row.get("date"))
                if d is None:
                    continue
                gprd = _f(row.get("GPRD"), 4)
                if gprd is None:
                    continue
                ev = row.get("event")
                if ev is None or (isinstance(ev, float) and ev != ev):
                    ev = None
                rows.append((d, gprd, _f(row.get("GPRD_ACT"), 4),
                             _f(row.get("GPRD_THREAT"), 4),
                             _f(row.get("GPRD_MA30"), 4),
                             str(ev)[:255] if ev is not None else None))
            if rows:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM gpr_daily")
                    cur.executemany(
                        "INSERT INTO gpr_daily (trade_date, gprd, gprd_act, gprd_threat, "
                        "gprd_ma30, event, update_time, data_source) "
                        "VALUES (%s,%s,%s,%s,%s,%s,NOW(),'IACOVIELLO')", rows)
                conn.commit()
            written = len(rows)
        except Exception as e:  # noqa: BLE001
            errors.append(f"GPR: {type(e).__name__} {str(e)[:80]}")
            logger.warning("⚠️ GPR 采集失败: %s", e)
        finally:
            conn.close()
        return with_steps(
            {"records_written": written, "error_count": len(errors), "errors": errors[:3],
             "note": f"GPR 全量覆盖 {written} 行"},
            [{"no": 1, "name": "拉取 GPR 日频 xls", "params": _GPR_URL},
             {"no": 2, "name": "全量覆盖落库", "params": "REPLACE 语义"}],
            {1: f"{written} 行", 2: f"gpr_daily 写入 {written} 行"},
        )


class EpuSyncCollector:
    """美国 EPU 经济政策不确定性日频（Baker/Bloom/Davis）"""

    def __init__(self, timeout_sec: float = 90):
        self.timeout_sec = float(timeout_sec)

    @staticmethod
    def _ensure_table(conn):
        ddl = """
        CREATE TABLE IF NOT EXISTS epu_daily (
          trade_date DATE NOT NULL COMMENT '日期',
          epu DECIMAL(10,4) DEFAULT NULL COMMENT 'EPU 日频指数',
          update_time TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          data_source VARCHAR(100) DEFAULT NULL,
          PRIMARY KEY (trade_date)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        COMMENT='美国EPU经济政策不确定性日频（Baker/Bloom/Davis，2026-10-21）'
        """
        with conn.cursor() as cur:
            cur.execute(ddl)
        conn.commit()

    def run(self) -> dict:
        import requests
        import pandas as pd

        conn = pymysql.connect(**get_db_config().to_dict(),
                               cursorclass=pymysql.cursors.DictCursor)
        written, errors = 0, []
        try:
            self._ensure_table(conn)
            r = requests.get(_EPU_URL, headers=_H, timeout=self.timeout_sec)
            r.raise_for_status()
            df = pd.read_csv(io_string(r.text))
            rows = []
            for _, row in df.iterrows():
                try:
                    y, m, dd = int(row.get("year")), int(row.get("month")), int(row.get("day"))
                    d = date(y, m, dd)
                except (TypeError, ValueError):
                    continue
                e = _f(row.get("daily_policy_index"), 4)
                if e is None:
                    continue
                rows.append((d, e))
            if rows:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM epu_daily")
                    cur.executemany(
                        "INSERT INTO epu_daily (trade_date, epu, update_time, data_source) "
                        "VALUES (%s,%s,NOW(),'POLICY_UNCERTAINTY')", rows)
                conn.commit()
            written = len(rows)
        except Exception as e:  # noqa: BLE001
            errors.append(f"EPU: {type(e).__name__} {str(e)[:80]}")
            logger.warning("⚠️ EPU 采集失败: %s", e)
        finally:
            conn.close()
        return with_steps(
            {"records_written": written, "error_count": len(errors), "errors": errors[:3],
             "note": f"EPU 全量覆盖 {written} 行"},
            [{"no": 1, "name": "拉取 EPU 日频 CSV", "params": _EPU_URL},
             {"no": 2, "name": "全量覆盖落库", "params": "REPLACE 语义"}],
            {1: f"{written} 行", 2: f"epu_daily 写入 {written} 行"},
        )


def io_string(s: str):
    """CSV 文本 → 文件对象（避免顶层 import io 的重复）"""
    import io
    return io.StringIO(s)
