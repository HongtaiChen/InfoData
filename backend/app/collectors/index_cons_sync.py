#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
InvestBuddy 指数成分股同步（index_constituents 表快照重建）

数据源优先级：
1. 中证系（000016/000300/000905/000852/000688/000698/931775）：
   akshare index_stock_cons_csindex（成分名单）+ index_stock_cons_weight_csindex（权重，
   失败不阻塞，权重置 NULL）
2. 深证系（399001/399006/399330/399673）：
   akshare index_detail_cni（当前版本该接口有 Excel 解析 bug，属预期性失败）
   → 失败自动降级 stock_info.index_members 反查（FIND_IN_SET 精确匹配逗号分隔名单，
   规避「深证100」误匹配「深证100R」的 LIKE 精度问题），权重为 NULL
3. 北证50（899050）：仅走 index_detail_cni，失败则记 error（留待接口修复/直连国证）
4. 上证指数（000001）：全市场指数，不入成分表——详情接口按「沪市全部上市股」实时派生

调度契约：
- 指数每月定期调样，cron 建议月度（task_config: index_cons_sync '30 8 15 * *'）；
- 每轮对每个指数 DELETE 旧快照后批量重建，幂等；
- 权重字段用于前端「行业分布」加权统计，NULL 时前端按等权处理。
"""
import logging
import threading
from datetime import date

import pymysql
import akshare as ak

from ..db import get_db_config
from ._common import with_steps

logger = logging.getLogger(__name__)


def _call_with_timeout(fn, timeout_s: float):
    """akshare 内部请求普遍无 timeout，撞上不可达站点会无限挂起：
    用工作线程 + join(timeout) 兜底，超时返回 None（区分于正常结果）。"""
    box: dict = {}

    def _worker():
        try:
            box["r"] = fn()
        except Exception as e:  # noqa: BLE001 - 异常原样透传给调用方
            box["e"] = e

    th = threading.Thread(target=_worker, daemon=True)
    th.start()
    th.join(timeout_s)
    if th.is_alive():
        logger.warning(f"akshare 调用超时({timeout_s:.0f}s)，放弃等待")
        return None
    if "e" in box:
        raise box["e"]
    return box.get("r")

RUN_STEPS = [
    {"no": 1, "name": "构建同步清单", "params": "中证系 7 + 深证系 4 + 北证50（上证指数派生不入表）"},
    {"no": 2, "name": "逐指数拉取成分", "params": "csindex 成分+权重 → cni 样本 → index_members 精确反查"},
    {"no": 3, "name": "快照重建", "params": "DELETE 旧快照 → 批量 INSERT（uk_index_stock 幂等）"},
    {"no": 4, "name": "结果巡检", "params": "逐指数行数汇总，失败项计入 errors"},
]

# 中证系：成分 + 权重双接口
CSINDEX_CONS = {
    "000016": "上证50",
    "000300": "沪深300",
    "000905": "中证500",
    "000852": "中证1000",
    "000688": "科创50",
    "000698": "科创100",
    "931775": "中证全指房地产指数",
}
# 深证系：cni 主源（当前版本有 bug）→ index_members 兜底
CNINDEX_CONS = {
    "399001": "深证成指",
    "399006": "创业板指",
    "399330": "深证100",
    "399673": "创业板50",
}
# 北证50：仅 cni（失败即 error，不兜底）
CNI_ONLY = {"899050": "北证50"}


class IndexConsSyncCollector:
    """指数成分股快照同步"""

    # ---------- 中证系 ----------
    def _fetch_csindex(self, code: str) -> tuple[list[dict] | None, str]:
        """成分名单 + 权重（权重接口失败仅降级为无权重）。返回 (rows, source)"""
        try:
            df = _call_with_timeout(lambda: ak.index_stock_cons_csindex(symbol=code), 90)
        except Exception as e:
            logger.warning(f"指数 {code} csindex 成分接口失败: {str(e)[:90]}")
            return None, ""
        if df is None or df.empty:
            return None, ""
        weights: dict[str, float] = {}
        try:
            wdf = _call_with_timeout(lambda: ak.index_stock_cons_weight_csindex(symbol=code), 90)
            if wdf is not None and not wdf.empty:
                for _, r in wdf.iterrows():
                    c = str(r.get("成分券代码") or "")
                    w = r.get("权重") if "权重" in wdf.columns else r.get("权重(%)")
                    if c and w is not None:
                        try:
                            weights[c] = float(w)
                        except (TypeError, ValueError):
                            pass
        except Exception as e:
            logger.info(f"指数 {code} 权重接口失败（降级为无权重）: {str(e)[:90]}")
        rows = []
        for _, r in df.iterrows():
            sc = str(r.get("成分券代码") or "")
            if not sc:
                continue
            rows.append({
                "stock_code": sc,
                "stock_name": str(r.get("成分券名称") or "") or None,
                "weight": weights.get(sc),
                "trade_date": str(r.get("日期") or date.today().isoformat())[:10],
            })
        return rows, "csindex"

    # ---------- 国证系 ----------
    def _fetch_cni(self, code: str) -> list[dict] | None:
        """akshare 国证样本详情（当前版本 Excel 解析 bug，失败返回 None）"""
        try:
            df = _call_with_timeout(lambda: ak.index_detail_cni(symbol=code), 40)
        except Exception as e:
            logger.info(f"指数 {code} cni 样本接口不可用: {str(e)[:90]}")
            return None
        if df is None:
            logger.warning(f"指数 {code} cni 样本接口超时(40s)")
            return None
        if df is None or df.empty:
            return []
        rows = []
        for _, r in df.iterrows():
            sc = str(r.get("单元格代码") or r.get("样本代码") or "")
            if not sc:
                continue
            w = r.get("权重") if "权重" in df.columns else None
            try:
                w = float(w) if w is not None else None
            except (TypeError, ValueError):
                w = None
            rows.append({
                "stock_code": sc,
                "stock_name": str(r.get("单元格名称") or r.get("样本名称") or "") or None,
                "weight": w,
                "trade_date": date.today().isoformat(),
            })
        return rows

    def _fallback_members(self, cur, code: str, name: str) -> list[dict]:
        """stock_info.index_members 反查兜底：FIND_IN_SET 精确匹配（防 深证100/100R 误匹配）"""
        cur.execute(
            """
            SELECT stock_code, short_name FROM stock_info
            WHERE list_status = '上市'
              AND FIND_IN_SET(%s, REPLACE(index_members, ' ', '')) > 0
            """,
            (name,),
        )
        return [
            {"stock_code": sc, "stock_name": sn, "weight": None, "trade_date": date.today().isoformat()}
            for sc, sn in cur.fetchall()
        ]

    # ---------- 主流程 ----------
    def run(self) -> dict:
        conn = pymysql.connect(**get_db_config().to_dict())
        written = 0
        notes: list[str] = []
        errors: list[str] = []
        per_index: dict[str, int] = {}
        try:
            with conn.cursor() as cur:
                plan: list[tuple[str, str]] = [
                    *(CSINDEX_CONS.items()),
                    *(CNINDEX_CONS.items()),
                    *(CNI_ONLY.items()),
                ]

                for code, name in plan:
                    rows: list[dict] | None = None
                    source = ""
                    if code in CSINDEX_CONS:
                        rows, source = self._fetch_csindex(code)
                    elif code in CNINDEX_CONS:
                        rows = self._fetch_cni(code)
                        if rows is not None and rows:
                            source = "cni"
                        else:
                            rows = self._fallback_members(cur, code, name)
                            source = "stock_info_members"
                    else:  # 899050
                        rows = self._fetch_cni(code)
                        source = "cni"

                    if rows is None:
                        errors.append(f"{code} {name} 成分接口失败（本轮跳过，保留旧快照）")
                        continue
                    if not rows:
                        errors.append(f"{code} {name} 成分为空")
                        continue

                    cur.execute("DELETE FROM index_constituents WHERE index_code=%s", (code,))
                    cur.executemany(
                        """
                        INSERT INTO index_constituents
                            (index_code, stock_code, stock_name, weight, trade_date, source, data_source)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        ON DUPLICATE KEY UPDATE
                            stock_name = VALUES(stock_name),
                            weight = VALUES(weight),
                            trade_date = VALUES(trade_date)
                        """,
                        [
                            (
                                code, r["stock_code"], r["stock_name"], r["weight"],
                                r["trade_date"], source, source,
                            )
                            for r in rows
                        ],
                    )
                    written += len(rows)
                    per_index[code] = len(rows)
                    w_tag = "含权重" if rows[0]["weight"] is not None else "无权重"
                    notes.append(f"{name} {len(rows)} 只({source}/{w_tag})")

                # 巡检：13 个指数的成分覆盖情况
                cur.execute(
                    """
                    SELECT p.index_code, p.index_name, COUNT(c.id)
                    FROM index_profile p
                    LEFT JOIN index_constituents c ON c.index_code = p.index_code
                    GROUP BY p.index_code, p.index_name
                    """
                )
                for c, n, cnt in cur.fetchall():
                    if c == "000001":
                        continue  # 全市场指数，按沪市派生
                    if cnt == 0:
                        errors.append(f"{c} {n} 无成分快照（详情页将提示暂缺）")
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

        msg = f"指数成分快照 {written} 行 / {len(per_index)} 个指数"
        if errors:
            msg += f"；异常 {len(errors)} 项"
        logger.info(f"✅ {msg}")
        return with_steps(
            {"records_written": written, "error_count": len(errors), "errors": errors, "note": msg},
            RUN_STEPS,
            {
                1: f"清单 {len(CSINDEX_CONS) + len(CNINDEX_CONS) + len(CNI_ONLY)} 个",
                2: f"成功 {len(per_index)} 个（csindex / cni / members 兜底）",
                3: f"写入 {written} 行",
                4: f"异常 {len(errors)} 项",
            },
        )
