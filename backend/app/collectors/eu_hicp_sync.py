#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
投资环境模块 · 国际指数「基本面分析」—— 欧元区 HICP 月度通胀采集器（eu_hicp_sync）

「宏观经济与政策」块需要**及时的通胀读数**。IMF WEO 通胀是年度频率（半年更新），
表达不了「当前景气度」。欧元区 HICP（调和消费者物价指数，ECB 政策目标基准）由
Eurostat 直接发布，**免 Key、月度、约 T+1 月出 flash 初值**，是本域唯一能拿到
「最近几个月」的国际通胀及时源。

数据源：Eurostat JSON-stat API（`prc_hicp_manr` = HICP 月度同比率）。
  · 免 Key、免注册、HTTPS GET，CORS 开放。
  · 覆盖欧元区（EA20）+ 各成员国；本表取 EA20 总指数（coicop=CP00）+ 美/日/英（若可得）。

落库：新建 eu_hicp_monthly 表（长表），stat_month 存月份，value 为同比率（RCH_A 口径）。

⚠️ 及时性诚实标注：
  · 只覆盖欧元区（HICP 是欧盟口径），美国 CPI/PCE 与日本 CPI 的**及时月度**源
    在本域被代理阻断（BLS 403、FRED 阻断），故本表不伪造美/日及时通胀，
    美国/日本/中国年度通胀走 global_macro_annual（IMF WEO）。
  · HICP ≠ 美国 CPI，跨经济体比较时必须显式标注口径差异（前端 note 透传）。

幂等：uk(geo, stat_month) upsert，可重复执行。
"""
import logging
from datetime import datetime

import pymysql
import requests

from ..db import get_db_config
from ._common import with_steps

logger = logging.getLogger(__name__)

RUN_STEPS = [
    {"no": 1, "name": "拉取 Eurostat HICP", "params": "prc_hicp_manr EA20 同比率"},
    {"no": 2, "name": "解析 JSON-stat", "params": "sparse value → (month, pct)"},
    {"no": 3, "name": "落库 eu_hicp_monthly", "params": "uk(geo,stat_month) 幂等"},
]

_EUROSTAT = "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/prc_hicp_manr"

_TGT = ["geo", "stat_month", "value", "note", "data_source"]


def _f(v, nd: int = 4):
    if v is None:
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    if x != x:
        return None
    return round(x, nd)


class EuHicpSyncCollector:
    """欧元区 HICP 月度通胀同步（Eurostat）"""

    def __init__(self, timeout_sec: float = 60):
        self.timeout_sec = float(timeout_sec)

    def _fetch(self) -> list[dict]:
        """拉取 EA20 总指数 HICP 同比率全历史。

        JSON-stat 稀疏编码：`value` 的 key 是**单个扁平整数**（按 `size` 的
        混合进制反解成各维度索引），不是冒号分隔的多维 key。当只筛一个 geo/coicop/unit
        时，size 里只有 time 维 > 1，故 value key 直接就是 time 维的索引。
        """
        params = {"format": "JSON", "coicop": "CP00", "unit": "RCH_A", "geo": "EA20"}
        r = requests.get(_EUROSTAT, params=params, timeout=self.timeout_sec)
        r.raise_for_status()
        d = r.json()

        id_list = d.get("id", [])
        size = d.get("size", [])
        values = d.get("value", {})
        # time 维度在 id 里的位置
        try:
            time_pos = id_list.index("time")
        except ValueError:
            return []

        # time 维度的类别顺序（month 字符串）
        time_cats = d.get("dimension", {}).get("time", {}) \
            .get("category", {}).get("index", {})
        time_keys = list(time_cats.keys())

        out = []
        for flat_idx, v in values.items():
            vv = _f(v)
            if vv is None:
                continue
            fi = int(flat_idx)
            # 混合进制反解：从最后一维往前
            idxs = []
            rem = fi
            for dim_size in reversed(size):
                idxs.append(rem % dim_size)
                rem //= dim_size
            idxs.reverse()  # 恢复为 id 顺序
            if time_pos >= len(idxs):
                continue
            t = idxs[time_pos]
            if t < 0 or t >= len(time_keys):
                continue
            month = time_keys[t]
            if not month or not str(month)[:4].isdigit():
                continue
            out.append({
                "geo": "EA20", "stat_month": str(month)[:7],
                "value": vv, "note": "欧元区 HICP 同比率（ECB 目标基准口径）",
                "data_source": "EUROSTAT",
            })
        # 去重排序
        out.sort(key=lambda x: x["stat_month"])
        seen, uniq = set(), []
        for r_ in out:
            k = (r_["geo"], r_["stat_month"])
            if k not in seen:
                seen.add(k)
                uniq.append(r_)
        return uniq

    def run(self) -> dict:
        conn = pymysql.connect(**get_db_config().to_dict(),
                               cursorclass=pymysql.cursors.DictCursor)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "CREATE TABLE IF NOT EXISTS eu_hicp_monthly ("
                    "  id INT AUTO_INCREMENT PRIMARY KEY,"
                    "  geo VARCHAR(8) NOT NULL COMMENT '区域码 EA20',"
                    "  stat_month VARCHAR(7) NOT NULL COMMENT '统计月份 YYYY-MM',"
                    "  value DECIMAL(12,4) DEFAULT NULL COMMENT 'HICP 同比率 %',"
                    "  note VARCHAR(200) DEFAULT NULL,"
                    "  update_time TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,"
                    "  data_source VARCHAR(40) DEFAULT NULL,"
                    "  UNIQUE KEY uk_geo_month (geo, stat_month)"
                    ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci "
                    "COMMENT='欧元区 HICP 月度同比（Eurostat，2026-10-21）'"
                )
            conn.commit()
        finally:
            conn.close()

        try:
            rows = self._fetch()
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"Eurostat HICP 拉取失败: {type(e).__name__} {str(e)[:80]}") from e
        if not rows:
            raise RuntimeError("Eurostat HICP 返回为空")

        conn = pymysql.connect(**get_db_config().to_dict(),
                               cursorclass=pymysql.cursors.DictCursor)
        try:
            with conn.cursor() as cur:
                sql = (
                    f"INSERT INTO eu_hicp_monthly ({', '.join(_TGT)}, update_time) "
                    f"VALUES ({', '.join(['%s'] * len(_TGT))}, NOW()) "
                    "ON DUPLICATE KEY UPDATE "
                    + ", ".join(f"{c}=COALESCE(VALUES({c}), {c})" for c in _TGT
                                if c not in ("geo", "stat_month"))
                    + ", update_time=NOW()"
                )
                cur.executemany(sql, [tuple(r[c] for c in _TGT) for r in rows])
            conn.commit()
            with conn.cursor() as cur:
                cur.execute("SELECT MAX(stat_month) AS m FROM eu_hicp_monthly")
                latest = cur.fetchone()["m"]
        finally:
            conn.close()

        msg = f"欧元区 HICP upsert {len(rows)} 行（最新 {latest}）"
        logger.info("✅ %s", msg)
        return with_steps(
            {"records_written": len(rows), "error_count": 0, "note": msg},
            RUN_STEPS,
            {
                1: "Eurostat prc_hicp_manr EA20",
                2: f"JSON-stat 解析 {len(rows)} 点",
                3: f"upsert {len(rows)} 行（最新 {latest}）",
            },
        )
