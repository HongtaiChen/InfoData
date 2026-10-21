#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
投资环境模块 · 国际环境「情绪与不确定性」—— 全球不确定性/贸易不确定性指数采集器
（global_uncertainty_sync）

把三类低频（月/季）国际不确定性指标落库：
  · WUI  —— 世界不确定性指数（IMF/Ahir-Bloom-Furceri，71 国月频 2008 起 + 全球 GDP 加权）
  · TPU  —— 贸易政策不确定性指数（Caldara et al.，月频 1960 起）
  · FSI  —— Püttmann 报纸金融压力指数（美国，1889-2016，月频，超长期历史）

数据源（官方开放，免 Key）：
  · WUI : https://worlduncertaintyindex.com/data/ （解析当季 xlsx 直链，F1=全球 GDP 加权月频）
  · TPU : https://www.matteoiacoviello.com/tpu.htm （解析 tpu_web_latest.xlsx）
  · FSI : https://www.policyuncertainty.com/media/Financial_Stress.xlsx （sheet2=月频）

⚠️ 三源最新数据均为初值、会修订 ⇒ 全量覆盖（REPLACE）。
   FSI 已停更（止于 2016），一次性落库即可，后续任务仅兜底。

落库表：
  · wui_monthly    （世界不确定性指数月频，全球 GDP 加权）
  · tpu_monthly    （贸易政策不确定性月频）
  · fsi_monthly    （Püttmann 报纸金融压力指数月频）
"""
import logging
from datetime import date, datetime

import pymysql

from ..db import get_db_config
from ._common import with_steps

logger = logging.getLogger(__name__)

_H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120"}

_WUI_PAGE = "https://worlduncertaintyindex.com/data/"
_TPU_PAGE = "https://www.matteoiacoviello.com/tpu.htm"
_FSI_URL = "https://www.policyuncertainty.com/media/Financial_Stress.xlsx"


def _d(v) -> date | None:
    if v is None:
        return None
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


class WuiSyncCollector:
    """WUI 世界不确定性指数（全球 GDP 加权，月频）"""

    def __init__(self, timeout_sec: float = 60):
        self.timeout_sec = float(timeout_sec)

    @staticmethod
    def _ensure_table(conn):
        ddl = """
        CREATE TABLE IF NOT EXISTS wui_monthly (
          stat_month DATE NOT NULL COMMENT '月份（每月1日）',
          wui DECIMAL(14,4) DEFAULT NULL COMMENT 'WUI 全球 GDP 加权指数',
          update_time TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          data_source VARCHAR(100) DEFAULT NULL,
          PRIMARY KEY (stat_month)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        COMMENT='世界不确定性指数WUI月频（全球GDP加权，2026-10-21）'
        """
        with conn.cursor() as cur:
            cur.execute(ddl)
        conn.commit()

    def run(self) -> dict:
        import requests
        import pandas as pd
        import io
        import re

        conn = pymysql.connect(**get_db_config().to_dict(),
                               cursorclass=pymysql.cursors.DictCursor)
        written, errors = 0, []
        try:
            self._ensure_table(conn)
            r = requests.get(_WUI_PAGE, headers=_H, timeout=self.timeout_sec)
            r.raise_for_status()
            # 月频数据集文件名形如 WUI_M_dataset_YYYY_MM.xlsx
            links = re.findall(r'href="([^"]*WUI_M_dataset[^"]*\.xlsx)"', r.text)
            if not links:
                raise RuntimeError("WUI 月频 xlsx 直链未找到")
            xlsx_url = links[0]
            if xlsx_url.startswith("/"):
                xlsx_url = "https://worlduncertaintyindex.com" + xlsx_url
            r2 = requests.get(xlsx_url, headers=_H, timeout=self.timeout_sec)
            r2.raise_for_status()
            xl = pd.ExcelFile(io.BytesIO(r2.content))
            df = xl.parse("F1", header=1)  # 首行为列名
            date_col = "date" if "date" in df.columns else df.columns[0]
            wui_col = None
            for c in df.columns:
                if "WUI" in str(c) and "GDP" in str(c):
                    wui_col = c
                    break
            if wui_col is None:
                wui_col = df.columns[1]
            rows = []
            for _, row in df.iterrows():
                d = _d(row.get(date_col))
                v = _f(row.get(wui_col), 4)
                if d is None or v is None:
                    continue
                rows.append((d, v))
            if rows:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM wui_monthly")
                    cur.executemany(
                        "INSERT INTO wui_monthly (stat_month, wui, update_time, data_source) "
                        "VALUES (%s,%s,NOW(),'WUI')", rows)
                conn.commit()
            written = len(rows)
        except Exception as e:  # noqa: BLE001
            errors.append(f"WUI: {type(e).__name__} {str(e)[:80]}")
            logger.warning("⚠️ WUI 采集失败: %s", e)
        finally:
            conn.close()
        return with_steps(
            {"records_written": written, "error_count": len(errors), "errors": errors[:3],
             "note": f"WUI 全量覆盖 {written} 行"},
            [{"no": 1, "name": "解析 WUI 月频 xlsx 直链", "params": _WUI_PAGE},
             {"no": 2, "name": "全量覆盖落库", "params": "REPLACE 语义"}],
            {1: f"xlsx 定位成功", 2: f"wui_monthly 写入 {written} 行"},
        )


class TpuSyncCollector:
    """TPU 贸易政策不确定性指数（月频）"""

    def __init__(self, timeout_sec: float = 60):
        self.timeout_sec = float(timeout_sec)

    @staticmethod
    def _ensure_table(conn):
        ddl = """
        CREATE TABLE IF NOT EXISTS tpu_monthly (
          stat_month DATE NOT NULL COMMENT '月份（每月1日）',
          tpu DECIMAL(10,4) DEFAULT NULL COMMENT 'TPU 指数（100=1% 文章占比）',
          update_time TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          data_source VARCHAR(100) DEFAULT NULL,
          PRIMARY KEY (stat_month)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        COMMENT='贸易政策不确定性TPU月频（Caldara et al.，2026-10-21）'
        """
        with conn.cursor() as cur:
            cur.execute(ddl)
        conn.commit()

    def run(self) -> dict:
        import requests
        import pandas as pd
        import io
        import re

        conn = pymysql.connect(**get_db_config().to_dict(),
                               cursorclass=pymysql.cursors.DictCursor)
        written, errors = 0, []
        try:
            self._ensure_table(conn)
            r = requests.get(_TPU_PAGE, headers=_H, timeout=self.timeout_sec)
            r.raise_for_status()
            links = re.findall(r'href="([^"]*tpu[^"]*\.xlsx)"', r.text, re.I)
            if not links:
                raise RuntimeError("TPU xlsx 直链未找到")
            xlsx_url = links[0]
            if xlsx_url.startswith("/"):
                xlsx_url = "https://www.matteoiacoviello.com" + xlsx_url
            r2 = requests.get(xlsx_url, headers=_H, timeout=self.timeout_sec)
            r2.raise_for_status()
            xl = pd.ExcelFile(io.BytesIO(r2.content))
            df = xl.parse("TPU_MONTHLY")
            rows = []
            for _, row in df.iterrows():
                d = _d(row.get("DATE"))
                v = _f(row.get("TPU"), 4)
                if d is None or v is None:
                    continue
                rows.append((d, v))
            if rows:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM tpu_monthly")
                    cur.executemany(
                        "INSERT INTO tpu_monthly (stat_month, tpu, update_time, data_source) "
                        "VALUES (%s,%s,NOW(),'TPU')", rows)
                conn.commit()
            written = len(rows)
        except Exception as e:  # noqa: BLE001
            errors.append(f"TPU: {type(e).__name__} {str(e)[:80]}")
            logger.warning("⚠️ TPU 采集失败: %s", e)
        finally:
            conn.close()
        return with_steps(
            {"records_written": written, "error_count": len(errors), "errors": errors[:3],
             "note": f"TPU 全量覆盖 {written} 行"},
            [{"no": 1, "name": "解析 TPU xlsx 直链", "params": _TPU_PAGE},
             {"no": 2, "name": "全量覆盖落库", "params": "REPLACE 语义"}],
            {1: f"xlsx 定位成功", 2: f"tpu_monthly 写入 {written} 行"},
        )


class FsiSyncCollector:
    """Püttmann 报纸金融压力指数（美国，月频 1889-2016）"""

    def __init__(self, timeout_sec: float = 60):
        self.timeout_sec = float(timeout_sec)

    @staticmethod
    def _ensure_table(conn):
        ddl = """
        CREATE TABLE IF NOT EXISTS fsi_monthly (
          stat_month DATE NOT NULL COMMENT '月份（每月1日）',
          fsi DECIMAL(10,4) DEFAULT NULL COMMENT '金融压力指数（均值100）',
          update_time TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          data_source VARCHAR(100) DEFAULT NULL,
          PRIMARY KEY (stat_month)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        COMMENT='Püttmann报纸金融压力指数FSI月频（1889-2016，2026-10-21）'
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
            r = requests.get(_FSI_URL, headers=_H, timeout=self.timeout_sec)
            r.raise_for_status()
            xl = pd.ExcelFile(io.BytesIO(r.content))
            df = xl.parse(xl.sheet_names[1])  # sheet2 = 月频
            rows = []
            for _, row in df.iterrows():
                try:
                    d = date(int(row["year"]), int(row["month"]), 1)
                except (TypeError, ValueError, KeyError):
                    continue
                v = _f(row.get("indicator"), 4)
                if v is None:
                    continue
                rows.append((d, v))
            if rows:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM fsi_monthly")
                    cur.executemany(
                        "INSERT INTO fsi_monthly (stat_month, fsi, update_time, data_source) "
                        "VALUES (%s,%s,NOW(),'PUTTMANN')", rows)
                conn.commit()
            written = len(rows)
        except Exception as e:  # noqa: BLE001
            errors.append(f"FSI: {type(e).__name__} {str(e)[:80]}")
            logger.warning("⚠️ FSI 采集失败: %s", e)
        finally:
            conn.close()
        return with_steps(
            {"records_written": written, "error_count": len(errors), "errors": errors[:3],
             "note": f"FSI 全量覆盖 {written} 行"},
            [{"no": 1, "name": "拉取 Püttmann FSI xlsx", "params": _FSI_URL},
             {"no": 2, "name": "全量覆盖落库", "params": "REPLACE 语义"}],
            {1: f"{written} 行", 2: f"fsi_monthly 写入 {written} 行"},
        )
