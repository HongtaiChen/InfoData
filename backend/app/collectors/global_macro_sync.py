#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
投资环境模块 · 国际指数「基本面分析」—— 全球宏观采集器（global_macro_sync）

把「宏观经济与政策」块的三大结构性指标落库到 global_macro_annual 表：
  1. gdp_growth  各国实际 GDP 增速（%）          —— IMF WEO NGDP_RPCH
  2. inflation   各国通胀 CPI 年均增速（%）       —— IMF WEO PCPIPCH
  3. debt_gdp    政府总债务 / GDP（%）            —— IMF WEO GGXWDG_NGDP

数据源：IMF WEO（World Economic Outlook）datamapper API（免 Key、HTTPS GET、权威一手源）。
  · 半年更新一次（4 月 / 10 月），含**历史 + 预测**（约到 T+5 年）。
  · 覆盖 200+ 经济体，本表只取**主要经济体**（与「国际指数」对应的市场）：
    美国/日本/中国/德国/英国/法国/意大利/加拿大/澳大利亚/韩国/印度/巴西/欧元区代理。

口径（刻意不跨指标混比）：
  · GDP 增速是**实际（不变价）**同比 %；通胀是**年均 CPI** %；债务是**一般政府总债务**占名义 GDP %。
  · 三者频率统一为**年度**（IMF WEO 原生年度），stat_year 存年份。
  · 预测值（超出最新实际发布年）通过 note 标注「WEO 预测」，不在库里混入「实际」口径。

⚠️ 及时性诚实标注：
  · IMF WEO 是年度频率，**无法表达「当前月度景气度」**；月度高频（PMI/月度 CPI/月度 GDP）
    由 env_macro_monthly（中国）与 Eurostat HICP（欧元区，见同批采集）覆盖，
    本表专供「区域经济基本面 / 债务可持续性」的中长期结构判断。

幂等：uk(country_code, indicator, stat_year) upsert，可重复执行。
"""
import logging

import pymysql
import requests

from ..db import get_db_config
from ._common import with_steps

logger = logging.getLogger(__name__)

RUN_STEPS = [
    {"no": 1, "name": "拉取 IMF WEO", "params": "NGDP_RPCH/PCPIPCH/GGXWDG_NGDP × 主要经济体"},
    {"no": 2, "name": "归一落库", "params": "长表 uk(country_code,indicator,stat_year)"},
]

_IMF = "https://www.imf.org/external/datamapper/api/v1"

# 指标 → (IMF code, 落库 indicator, 单位说明)
INDICATORS = [
    ("NGDP_RPCH", "gdp_growth", "实际 GDP 增速 %"),
    ("PCPIPCH", "inflation", "年均 CPI 通胀 %"),
    ("GGXWDG_NGDP", "debt_gdp", "一般政府总债务占 GDP %"),
]

# 主要经济体（与「国际指数」对应市场）
COUNTRIES = [
    ("USA", "美国"), ("JPN", "日本"), ("CHN", "中国"), ("DEU", "德国"),
    ("GBR", "英国"), ("FRA", "法国"), ("ITA", "意大利"), ("CAN", "加拿大"),
    ("AUS", "澳大利亚"), ("KOR", "韩国"), ("IND", "印度"), ("BRA", "巴西"),
]

# IMF 国家码 → 国家码（本表直接用 IMF 三位码）
_TGT = ["country_code", "indicator", "stat_year", "value", "note", "data_source"]


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


class GlobalMacroSyncCollector:
    """全球宏观年度序列同步（IMF WEO datamapper）"""

    def __init__(self, timeout_sec: float = 60):
        self.timeout_sec = float(timeout_sec)

    def _fetch(self) -> tuple[list[dict], list[str]]:
        rows: list[dict] = []
        errors: list[str] = []
        ctry_codes = [c[0] for c in COUNTRIES]
        for imf_code, indicator, _unit in INDICATORS:
            try:
                r = requests.get(f"{_IMF}/{imf_code}/{'/'.join(ctry_codes)}",
                                 timeout=self.timeout_sec)
                r.raise_for_status()
                vals = r.json().get("values", {}).get(imf_code, {})
                for code, cname in COUNTRIES:
                    years = vals.get(code)
                    if not years:
                        continue
                    for y, v in years.items():
                        vv = _f(v)
                        if vv is None:
                            continue
                        rows.append({
                            "country_code": code, "indicator": indicator,
                            "stat_year": int(y), "value": vv,
                            "note": f"{cname}·{_unit}", "data_source": "IMF_WEO",
                        })
            except Exception as e:  # noqa: BLE001
                errors.append(f"{imf_code}: {type(e).__name__} {str(e)[:60]}")
        return rows, errors

    def run(self) -> dict:
        conn = pymysql.connect(**get_db_config().to_dict(),
                               cursorclass=pymysql.cursors.DictCursor)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "CREATE TABLE IF NOT EXISTS global_macro_annual ("
                    "  id INT AUTO_INCREMENT PRIMARY KEY,"
                    "  country_code VARCHAR(8) NOT NULL COMMENT 'IMF 三位国家码',"
                    "  indicator VARCHAR(24) NOT NULL COMMENT 'gdp_growth/inflation/debt_gdp',"
                    "  stat_year SMALLINT NOT NULL COMMENT '统计年份',"
                    "  value DECIMAL(12,4) DEFAULT NULL,"
                    "  note VARCHAR(200) DEFAULT NULL,"
                    "  update_time TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,"
                    "  data_source VARCHAR(40) DEFAULT NULL,"
                    "  UNIQUE KEY uk_country_ind_year (country_code, indicator, stat_year)"
                    ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci "
                    "COMMENT='全球宏观年度序列（IMF WEO，2026-10-21）'"
                )
            conn.commit()
        finally:
            conn.close()

        rows, errors = self._fetch()
        if not rows:
            raise RuntimeError("IMF WEO 全部失败：" + "; ".join(errors[:5]))

        conn = pymysql.connect(**get_db_config().to_dict(),
                               cursorclass=pymysql.cursors.DictCursor)
        try:
            with conn.cursor() as cur:
                sql = (
                    f"INSERT INTO global_macro_annual ({', '.join(_TGT)}, update_time) "
                    f"VALUES ({', '.join(['%s'] * len(_TGT))}, NOW()) "
                    "ON DUPLICATE KEY UPDATE "
                    + ", ".join(f"{c}=COALESCE(VALUES({c}), {c})" for c in _TGT
                                if c not in ("country_code", "indicator", "stat_year"))
                    + ", update_time=NOW()"
                )
                cur.executemany(sql, [tuple(r[c] for c in _TGT) for r in rows])
            conn.commit()
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS n FROM global_macro_annual")
                total = cur.fetchone()["n"]
                cur.execute("SELECT MAX(stat_year) AS y FROM global_macro_annual")
                latest = cur.fetchone()["y"]
        finally:
            conn.close()

        msg = (f"全球宏观 upsert {len(rows)} 行（表内 {total} 行 / 最新 {latest} 年）｜"
               f"gdp_growth/inflation/debt_gdp × {len(COUNTRIES)} 经济体")
        logger.info("✅ %s", msg)
        if errors:
            logger.warning("⚠️ 全球宏观 %s 项异常: %s", len(errors), errors[:3])
        return with_steps(
            {"records_written": len(rows), "error_count": len(errors),
             "errors": errors[:10], "note": msg},
            RUN_STEPS,
            {
                1: f"3 指标 × {len(COUNTRIES)} 经济体",
                2: f"upsert {len(rows)} 行（表内 {total} 行）",
            },
        )
