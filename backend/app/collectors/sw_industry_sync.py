#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
InvestBuddy 申万行业归属采集器（stock_industry_sw，申万宏源研究源经 akshare，恢复采集）

背景（2026-09-13）：该表为历史导入遗留（数据源「百度股市通」，2025-08-16 停更），
覆盖仅 3,406 只股票，且 industry_type 存在「申万一级 / 一级」两代命名混杂
（设计规范 §8 G10）。本次按调研报告第 3 批（P2）恢复采集，同时一并修正命名。

- 源：ak.sw_index_first_info()（31 个一级）+ ak.sw_index_second_info()（131 个二级）
      → 逐行业 ak.index_component_sw(symbol) 取成分股（162 次调用）
- 写表：stock_industry_sw
- 策略：**全量重建** —— 162 个行业成分先全部拉到内存，覆盖率达标后
        在单个事务内 DELETE 全表 + INSERT，天然完成命名统一与陈旧行清理
- 幂等：重复执行结果一致（全量覆盖）；DB 侧另有 UNIQUE(stock_code, industry_type) 保障
- 容错：单行业失败记 error；成功行业数低于 min_coverage 比例即**中止且不写库**
        （防止源故障时把好数据覆盖成残缺数据）

⚠️ 口径变化（有意为之）：
  1. industry_type 由两代混杂（申万一级/申万二级/一级/二级）统一为「申万一级 / 申万二级」
  2. sw_code 由百度自有编码（如 480000）改为申万标准代码（如 801780）
  3. 覆盖从 3,406 只提升到申万全成份（实测一级 5,220 / 二级 5,220）
  4. source 由「百度股市通」改为「申万宏源研究(akshare)」，
     旧行不保留 —— 新数据完整覆盖旧数据的信息量（均为「股票→行业」归属）
"""
import logging
import time
from datetime import datetime, date

import pymysql

import akshare as ak

from ..db import get_db_config
from ._common import with_steps, call_with_timeout

logger = logging.getLogger(__name__)

# 运行步骤链模板（供前端「数据流·整链拓扑」展示运行逻辑）
RUN_STEPS = [
    {"no": 1, "name": "拉行业目录", "params": "ak.sw_index_first_info() 31 个一级 + ak.sw_index_second_info() 131 个二级"},
    {"no": 2, "name": "逐行业拉成分", "params": "ak.index_component_sw(symbol) × 162；限流 sleep 0.15s；单次 30s 超时；单行业失败记 error"},
    {"no": 3, "name": "覆盖率护栏", "params": "成功行业数 < min_coverage(0.85) × 目录数 → 中止且不写库，防源故障覆盖好数据"},
    {"no": 4, "name": "全量重建", "params": "单事务 DELETE 全表 + INSERT 新成分（统一 industry_type 命名，见 G10）"},
]

_LEVEL1 = "申万一级"
_LEVEL2 = "申万二级"
_SOURCE = "申万宏源研究(akshare)"
_INSERT_COLS = ["stock_code", "sw_code", "industry_name", "industry_type",
                "source", "update_time", "data_source"]


def _sw_code(raw) -> str | None:
    """'801010.SI' → '801010'"""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    return s.split(".")[0]


def _stock_code(raw) -> str | None:
    """成分股证券代码归一为 6 位字符串"""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s or s.lower() in ("nan", "none"):
        return None
    return s.zfill(6) if s.isdigit() else s


class SwIndustrySyncCollector:
    """申万行业归属全量重建（申万宏源研究 index_component_sw）"""

    def __init__(self, sleep_sec: float = 0.15, timeout_sec: float = 30,
                 min_coverage: float = 0.85):
        self.sleep_sec = float(sleep_sec)
        self.timeout_sec = float(timeout_sec)
        self.min_coverage = float(min_coverage)

    # ---------- 数据源 ----------

    def _fetch_catalog(self) -> list[tuple[str, str, str]]:
        """→ [(sw_code, industry_name, industry_type)]"""
        out = []
        for fn, level in ((ak.sw_index_first_info, _LEVEL1), (ak.sw_index_second_info, _LEVEL2)):
            df = call_with_timeout(fn, self.timeout_sec)
            if df is None or df.empty:
                continue
            for _, r in df.iterrows():
                code = _sw_code(r.get("行业代码"))
                name = r.get("行业名称")
                if code and name is not None and str(name).strip():
                    out.append((code, str(name).strip(), level))
        return out

    def _fetch_members(self, sw_code: str) -> list[str]:
        df = call_with_timeout(ak.index_component_sw, self.timeout_sec, symbol=sw_code)
        if df is None or df.empty:
            return []
        codes = []
        for v in df["证券代码"].tolist():
            c = _stock_code(v)
            if c:
                codes.append(c)
        return codes

    # ---------- 主流程 ----------

    def run(self) -> dict:
        errors: list[str] = []

        try:
            catalog = self._fetch_catalog()
        except Exception as e:  # noqa: BLE001
            msg = f"申万行业目录获取失败：{type(e).__name__} {str(e)[:120]}"
            logger.error("❌ %s", msg)
            return with_steps(
                {"records_written": 0, "error_count": 1, "errors": [msg], "note": msg},
                RUN_STEPS, {1: "目录获取失败，终止"},
            )
        if not catalog:
            msg = "申万行业目录为空，终止（不写库）"
            return with_steps(
                {"records_written": 0, "error_count": 0, "errors": [], "note": msg},
                RUN_STEPS, {1: "目录 0 条，终止"},
            )
        n_l1 = sum(1 for _, _, lv in catalog if lv == _LEVEL1)
        n_l2 = len(catalog) - n_l1
        logger.info("申万行业：目录一级 %s + 二级 %s = %s 个", n_l1, n_l2, len(catalog))

        rows: list[tuple[str, str, str, str]] = []   # (stock_code, sw_code, name, type)
        ok, failed, last_err = 0, 0, None
        for idx, (code, name, level) in enumerate(catalog, 1):
            try:
                codes = self._fetch_members(code)
            except Exception as e:  # noqa: BLE001 - 单行业失败不中断
                failed += 1
                last_err = f"{code}({name}): {type(e).__name__} {str(e)[:60]}"
                errors.append(last_err)
                continue
            if not codes:
                failed += 1
                errors.append(f"{code}({name}): 成分股为空")
                continue
            ok += 1
            for sc in codes:
                rows.append((sc, code, name, level))
            if self.sleep_sec > 0:
                time.sleep(self.sleep_sec)
            if idx % 40 == 0:
                logger.info("  已拉 %s/%s 个行业，累计成分 %s 条…", idx, len(catalog), len(rows))

        coverage = ok / len(catalog) if catalog else 0.0
        if coverage < self.min_coverage:
            msg = (f"申万行业覆盖率不足（成功 {ok}/{len(catalog)} = {coverage:.0%} < "
                   f"{self.min_coverage:.0%}），**中止且不写库**以防覆盖好数据")
            logger.error("❌ %s", msg)
            return with_steps(
                {"records_written": 0, "error_count": len(errors), "errors": errors[:50], "note": msg},
                RUN_STEPS,
                {1: f"目录 {n_l1} 一级 + {n_l2} 二级", 2: f"成功 {ok} / 失败 {failed}", 3: f"覆盖率 {coverage:.0%} → 中止"},
            )

        # 去重（同股同级只保留一条；源自不同行业重复归属时以先到者为准）
        uniq: dict[tuple[str, str], tuple[str, str, str, str]] = {}
        for r in rows:
            uniq.setdefault((r[0], r[3]), r)
        payload_rows = list(uniq.values())

        conn = pymysql.connect(**get_db_config().to_dict())
        now = datetime.now()
        try:
            placeholder = ", ".join(["%s"] * len(_INSERT_COLS))
            insert_sql = (f"INSERT INTO stock_industry_sw ({', '.join(_INSERT_COLS)}) "
                          f"VALUES ({placeholder})")
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM stock_industry_sw")
                before = cur.fetchone()[0]
                cur.execute("DELETE FROM stock_industry_sw")
                payload = [(sc, c, nm, lv, _SOURCE, now, "AKSHARE")
                           for sc, c, nm, lv in payload_rows]
                for i in range(0, len(payload), 1000):
                    cur.executemany(insert_sql, payload[i:i + 1000])
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

        n_stock = len({r[0] for r in payload_rows})
        msg = (f"申万行业全量重建：{len(payload_rows)} 条 / {n_stock} 只股票 / "
               f"{ok} 个行业（旧表 {before} 条已替换），失败 {failed}")
        logger.info("✅ %s", msg)
        return with_steps(
            {"records_written": len(payload_rows), "error_count": len(errors),
             "errors": errors[:50], "note": msg},
            RUN_STEPS,
            {
                1: f"目录 {n_l1} 一级 + {n_l2} 二级",
                2: f"成功 {ok} / 失败 {failed} 个行业，累计 {len(rows)} 条",
                3: f"覆盖率 {coverage:.0%} ≥ {self.min_coverage:.0%} → 放行",
                4: f"DELETE {before} 条 → INSERT {len(payload_rows)} 条（{n_stock} 只股票）",
            },
        )
