#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
InvestBuddy MSCI 指数日线采集器（写入 overseas_index_daily）—— 2026-10-20 国际指数章节

为什么单独一个采集器（而非塞进 overseas_index_sync）：
  MSCI 数据走 **MSCI 官方公开 chart API**（`app2.msci.com/.../getLevelDataForGraph`，
  免 Key、无 WAF），只给**收盘 LEVEL**（无 OHLC/成交量），币种固定 USD；
  而 overseas_index_sync 的港美/环球指数都带 OHLC。两条通道字段口径不同，
  归一后共写同一张 `overseas_index_daily` 表（MSCI 的 open/high/low/volume 置 NULL）。

覆盖指数（官网权威 index code，2026-10-20 从 MSCI 官网索引页核实）：
  · MSCI ACWI              892400  全球可投资权益 ~85%
  · MSCI World            990100  发达市场
  · MSCI EAFE             990300  欧澳远东
  · MSCI Emerging Markets 891800  新兴市场

⚠️ 关键坑（实测）：
  · `msci-data` PyPI 包文档里的 code 有错位（它写 EM=664185、ACWI=891800），
    但 MSCI 官网索引列表的权威映射是 ACWI=892400 / World=990100 / EAFE=990300 / EM=891800。
    本项目直接用 requests 打官方 API，不依赖 msci-data 包的 code 常量。
  · MSCI China(735657) / MSCI Japan 标准指数在免费 API 返回空（MSCI 未开放单国标准指数日线），
    故「中国相关」用恒生国企 HSCEI、「日本」用日经225 N225 替代，见 overseas_index_sync。
  · 免费 API 数据回至 2000-01-01（DATE floor），STRD=价格收益、NETR=净总收益。
    本项目统一取 STRD（价格收益），与其它指数 close 口径一致（价格指数，不含红利再投）。

幂等：uk_code_date(index_code, trade_date) + 增量（起点 = 各指数本地 MAX(trade_date) 的次日）。
"""
import logging
from datetime import datetime, timedelta

import pymysql
import requests

from ..db import get_db_config
from ._common import with_steps

logger = logging.getLogger(__name__)

RUN_STEPS = [
    {"no": 1, "name": "读取本地水位", "params": "逐 MSCI 指数 MAX(trade_date)（首次无数据则回 2000-01-01）"},
    {"no": 2, "name": "拉取 MSCI 日线", "params": "MSCI 官方公开 chart API，STRD 价格收益，USD"},
    {"no": 3, "name": "增量过滤", "params": "仅保留 trade_date > 本地 MAX 的行"},
    {"no": 4, "name": "批量 Upsert", "params": "uk_code_date(index_code, trade_date) 幂等"},
]

# (index_code, 中文名, MSCI 官方 index code)
MSCI_INDICES = [
    ("ACWI", "MSCI ACWI", "892400"),
    ("MSWORLD", "MSCI World", "990100"),
    ("MSEAFE", "MSCI EAFE", "990300"),
    ("MSEM", "MSCI Emerging Markets", "891800"),
]

_API_URL = "https://app2.msci.com/products/service/index/indexmaster/getLevelDataForGraph"
_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.msci.com/",
}
_FLOOR = "20000101"


def _d(v) -> str | None:
    """YYYYMMDD → YYYY-MM-DD"""
    s = str(v).strip()
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return None


def _f(v, nd: int = 4):
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else round(f, nd)


def _fetch_msci(msci_code: str, start: str, end: str, timeout: float = 30.0):
    """MSCI 官方 chart API → list[(date_str, level)]，STRD 价格收益 USD。

    start/end 传 YYYYMMDD（含两端）。返回按日期升序的收盘 LEVEL 列表。
    """
    params = {
        "index_codes": msci_code,
        "currency_symbol": "USD",
        "index_variant": "STRD",
        "data_frequency": "DAILY",
        "start_date": start,
        "end_date": end,
    }
    r = requests.get(_API_URL, params=params, headers=_HEADERS, timeout=timeout)
    r.raise_for_status()
    js = r.json()
    levels = ((js.get("indexes") or {}).get("INDEX_LEVELS")) or []
    out = []
    for row in levels:
        d = _d(row.get("calc_date"))
        lv = _f(row.get("level_eod"))
        if d and lv is not None:
            out.append((d, lv))
    return out


class MsciIndexSyncCollector:
    """MSCI 指数日线增量同步（写 overseas_index_daily，OHLC 置 NULL）"""

    def __init__(self, timeout_sec: float = 60):
        self.timeout_sec = float(timeout_sec)

    def run(self) -> dict:
        conn = pymysql.connect(**get_db_config().to_dict(),
                               cursorclass=pymysql.cursors.DictCursor)
        written, errors, per = 0, [], {}
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT index_code, MAX(trade_date) AS d FROM overseas_index_daily "
                            "WHERE index_code IN (%s, %s, %s, %s) GROUP BY index_code",
                            tuple(c for c, _, _ in MSCI_INDICES))
                wm = {r["index_code"]: r["d"] for r in cur.fetchall()}

            end = datetime.now().strftime("%Y%m%d")
            for code, name, msci_code in MSCI_INDICES:
                try:
                    # 增量起点：本地 MAX 次日，首次则回 2000 全史
                    last = wm.get(code)
                    start = (last + timedelta(days=1)).strftime("%Y%m%d") if last else _FLOOR
                    rows = _fetch_msci(msci_code, start, end, self.timeout_sec)
                except Exception as e:  # noqa: BLE001
                    errors.append(f"{code} {name}: {type(e).__name__} {str(e)[:60]}")
                    continue
                if not rows:
                    per[code] = 0
                    logger.info("  %s %s：无增量（本地至 %s）", code, name, last)
                    continue

                insert_sql = (
                    "INSERT INTO overseas_index_daily "
                    "(index_code, index_name, trade_date, open, high, low, close, volume, amount, update_time, data_source) "
                    "VALUES (%s, %s, %s, NULL, NULL, NULL, %s, NULL, NULL, NOW(), 'MSCI') "
                    "ON DUPLICATE KEY UPDATE close=VALUES(close), update_time=NOW()"
                )
                vals = [(code, name, d, lv) for d, lv in rows]
                with conn.cursor() as cur:
                    cur.executemany(insert_sql, vals)
                conn.commit()
                written += len(vals)
                per[code] = len(vals)
                logger.info("  %s %s：MSCI %s 行 → 增量 %s 行（本地至 %s）",
                            code, name, len(rows), len(vals), last)
        finally:
            conn.close()

        msg = f"MSCI 指数新增 {written} 行（{len(per)}/{len(MSCI_INDICES)} 个指数）"
        if errors:
            msg += f"；异常 {len(errors)} 项"
        logger.info("✅ %s", msg)
        return with_steps(
            {"records_written": written, "error_count": len(errors), "errors": errors[:20], "note": msg},
            RUN_STEPS,
            {
                1: " · ".join(f"{c}至{wm.get(c) or '无'}" for c, _, _ in MSCI_INDICES),
                2: f"拉取 {len(per)}/{len(MSCI_INDICES)} 个",
                3: " · ".join(f"{c} +{per[c]}" for c in per) or "无增量",
                4: f"MSCI 官方 API，STRD 价格收益 USD，floor={_FLOOR}",
            },
        )
