#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
InvestBuddy 指数成分股同步（index_constituents 表快照重建）

数据源优先级（2026-09-20 按实测校准重排）：
1. 中证系（14 个）：akshare index_stock_cons_csindex（成分名单）
   + index_stock_cons_weight_csindex（权重，失败不阻塞，权重置 NULL）。
   实测 csindex 覆盖 000/399/899/932 各码段 —— 含北交所 899050（故从 cni 迁入）。
2. 深证系（399001/399006/399330/399673）：
   akshare index_detail_cni（当前版本该接口有 Excel 解析 bug，属预期性失败）
   → 失败自动降级 stock_info.index_members 反查（FIND_IN_SET 精确匹配逗号分隔名单，
   规避「深证100」误匹配「深证100R」的 LIKE 精度问题），权重为 NULL
3. 国证系（980017 国证芯片）：仅走 index_detail_cni（实测返回 30 只 + 权重，
   列名「样本代码/样本简称」正好匹配 _fetch_cni 的读取口径；csindex 不覆盖国证代码）
4. 上证指数（000001）：全市场指数，不入成分表——详情接口按「沪市全部上市股」实时派生
5. 中证转债（000832）：**债券指数，成分是可转换公司债券而非股票**，本表股票口径不适用，
   永不入表 —— 详情接口 market.py 的 NON_EQUITY_INDEXES 登记口径说明，
   前端据此展示「口径不适用」而不是笼统的「行业数据暂缺」

⚠️ 维护约定（2026-09-20 补）—— 「写死的清单 vs 动态的行情表」已经漂移两次
（上一次是 index_profile 释义 seed，本次是成分清单）：行情页指数由 dc_index_market
采集决定，而本清单是写死的。**新增指数上市场页时必须同步三处**：
  ① 本文件的 CSINDEX_CONS / CNINDEX_CONS / CNI_ONLY（成分来源，按 akshare 实测选源）
  ② setup_index_tables.py 的 SEED（释义档案）
  ③ 非股票指数（债券等）→ market.py 的 NON_EQUITY_INDEXES 登记口径说明
核对差集：`SELECT DISTINCT index_code FROM dc_index_market` 与本清单比对
（run() 末尾的巡检会把「档案里有、本快照日无成分」的指数记进 errors）。

调度契约：
- 指数每月定期调样，cron 建议月度（task_config: index_cons_sync '30 21 15 * *'）；
- **快照留档（2026-09-19 改造，报告 §7-⑧）**：`trade_date` 语义 = **快照日（本轮采集日）**，
  同一 (index_code, stock_code) 允许按快照日多份并存（唯一键 uk_index_stock_date）。
  每轮**只删除「本轮快照日」的行**后重建，历史快照永久保留 —— 这样才能做「指数级分组归因」
  的历史回溯（原实现每轮 DELETE 全表，只留最新一份，永远无法回答「上月这天是哪 300 只」）。
  源侧自己的样本日期另存 `sample_date`（中证官网返回的「日期」列），供核对调样是否生效；
  国证/members 兜底路径无该字段，留 NULL。
- 权重字段用于前端「行业分布」加权统计，NULL 时前端按等权处理。
"""
import logging
import threading
from datetime import date

import pymysql
import akshare as ak

from ..db import get_db_config
from ..index_meta import NON_EQUITY_INDEXES
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
    {"no": 1, "name": "构建同步清单",
     "params": "中证系 14 + 深证系 4 + 国证系 1（上证指数派生、中证转债债券口径 → 均不入表）"},
    {"no": 2, "name": "逐指数拉取成分", "params": "csindex 成分+权重 → cni 样本（列名 样本简称）→ index_members 精确反查"},
    {"no": 3, "name": "名称兜底回填", "params": "stock_name 为空时用 stock_info.short_name 补齐（防源列名变动）"},
    {"no": 4, "name": "按快照日留档重建", "params": "DELETE 本快照日旧行 → 批量 INSERT（uk_index_stock_date 幂等）；历史快照保留"},
    {"no": 5, "name": "结果巡检", "params": "逐指数行数汇总（按本次快照日过滤），失败项计入 errors"},
]

# 中证系：成分 + 权重双接口（csindex 覆盖 000/399/899/932 码段，实测校准）
CSINDEX_CONS = {
    "000016": "上证50",
    "000300": "沪深300",
    "000905": "中证500",
    "000852": "中证1000",
    "000688": "科创50",
    "000698": "科创100",
    "931775": "中证全指房地产指数",
    # ---- 2026-09-20 补入：行情页指数扩容到 21 个后，以下 6 个从未进过本清单 ----
    # （用户反馈「中证全指成分股与行业分布都是空的」）；实测 csindex 成分/权重双接口均可得
    "000985": "中证全指",      # 5121 只（全市场参照指数）
    "000922": "中证红利",      # 100 只
    "932000": "中证2000",      # 2000 只
    "399975": "证券公司",      # 49 只（中证全指证券公司）
    "399986": "中证银行",      # 42 只
    "399997": "中证白酒",      # 17 只
    # 899050 原先只在 CNI_ONLY 里走 cni、长期 error（cni 对该码不可用）——
    # 实测 csindex 支持北交所指数，迁到 csindex 即通（50 只）
    "899050": "北证50",
}
# 深证系：cni 主源（当前版本有 bug）→ index_members 兜底
CNINDEX_CONS = {
    "399001": "深证成指",
    "399006": "创业板指",
    "399330": "深证100",
    "399673": "创业板50",
}
# 国证系：仅 cni（csindex 不覆盖国证代码；980017 实测返回 30 只 + 权重）
CNI_ONLY = {"980017": "国证芯片"}

# 非股票指数（口径不适用，永不入表）与全市场派生指数清单见 app/index_meta.py ——
# API 层与本文件共用同一份常量（避免「两处清单各写各的」再次漂移）。


class IndexConsSyncCollector:
    """指数成分股快照同步"""

    # ---------- 中证系 ----------
    def _fetch_csindex(self, code: str, snapshot: date) -> tuple[list[dict] | None, str]:
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
            sd = str(r.get("日期") or "")[:10] or None
            rows.append({
                "stock_code": sc,
                "stock_name": str(r.get("成分券名称") or "") or None,
                "weight": weights.get(sc),
                # trade_date = 快照日（本轮采集日），保证三个来源口径一致、可做历史留档
                "trade_date": snapshot.isoformat(),
                "sample_date": sd,
            })
        return rows, "csindex"

    # ---------- 国证系 ----------
    def _fetch_cni(self, code: str, snapshot: date) -> list[dict] | None:
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
                # 国证接口实际列名为「样本简称」（2026-09-13 修复：原先读 单元格名称/样本名称 → 750 行名称全空）
                "stock_name": str(r.get("样本简称") or r.get("单元格名称") or r.get("样本名称") or "") or None,
                "weight": w,
                "trade_date": snapshot.isoformat(),
                "sample_date": None,     # 国证源不给样本日期
            })
        return rows

    def _fallback_members(self, cur, code: str, name: str, snapshot: date) -> list[dict]:
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
            {"stock_code": sc, "stock_name": sn, "weight": None,
             "trade_date": snapshot.isoformat(), "sample_date": None}
            for sc, sn in cur.fetchall()
        ]

    def _backfill_names(self, cur, rows: list[dict]) -> int:
        """名称兜底回填：源接口列名变动导致 stock_name 为空时，用本地 stock_info.short_name 补齐。
        返回回填条数（0 表示源名称完整）。"""
        missing = [r["stock_code"] for r in rows if not r.get("stock_name")]
        if not missing:
            return 0
        name_map: dict[str, str] = {}
        for i in range(0, len(missing), 900):
            chunk = missing[i: i + 900]
            ph = ",".join(["%s"] * len(chunk))
            cur.execute(
                f"SELECT stock_code, short_name FROM stock_info WHERE stock_code IN ({ph})",
                chunk,
            )
            for sc, sn in cur.fetchall():
                if sn:
                    name_map[sc] = sn
        filled = 0
        for r in rows:
            if not r.get("stock_name") and r["stock_code"] in name_map:
                r["stock_name"] = name_map[r["stock_code"]]
                filled += 1
        return filled

    # ---------- 主流程 ----------
    def run(self) -> dict:
        snapshot = date.today()          # 本轮快照日（三个来源统一口径）
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
                        rows, source = self._fetch_csindex(code, snapshot)
                    elif code in CNINDEX_CONS:
                        rows = self._fetch_cni(code, snapshot)
                        if rows is not None and rows:
                            source = "cni"
                        else:
                            rows = self._fallback_members(cur, code, name, snapshot)
                            source = "stock_info_members"
                    else:  # CNI_ONLY（国证系：csindex 不覆盖其代码段）
                        rows = self._fetch_cni(code, snapshot)
                        source = "cni"

                    if rows is None:
                        errors.append(f"{code} {name} 成分接口失败（本轮跳过，保留旧快照）")
                        continue
                    if not rows:
                        errors.append(f"{code} {name} 成分为空")
                        continue

                    filled = self._backfill_names(cur, rows)
                    if filled:
                        notes.append(f"{name} 名称兜底回填 {filled} 只")

                    # ⚠️ 只删「本快照日」的行：历史快照必须留下（§7-⑧ 的核心）
                    cur.execute(
                        "DELETE FROM index_constituents WHERE index_code=%s AND trade_date=%s",
                        (code, snapshot),
                    )
                    cur.executemany(
                        """
                        INSERT INTO index_constituents
                            (index_code, stock_code, stock_name, weight, trade_date,
                             sample_date, source, data_source)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        ON DUPLICATE KEY UPDATE
                            stock_name = VALUES(stock_name),
                            weight = VALUES(weight),
                            sample_date = VALUES(sample_date),
                            source = VALUES(source)
                        """,
                        [
                            (
                                code, r["stock_code"], r["stock_name"], r["weight"],
                                r["trade_date"], r.get("sample_date"), source, source,
                            )
                            for r in rows
                        ],
                    )
                    written += len(rows)
                    per_index[code] = len(rows)
                    w_tag = "含权重" if rows[0]["weight"] is not None else "无权重"
                    notes.append(f"{name} {len(rows)} 只({source}/{w_tag})")

                # 巡检：13 个指数在**本快照日**的成分覆盖情况
                cur.execute(
                    """
                    SELECT p.index_code, p.index_name, COUNT(c.id)
                    FROM index_profile p
                    LEFT JOIN index_constituents c
                           ON c.index_code = p.index_code AND c.trade_date = %s
                    GROUP BY p.index_code, p.index_name
                    """,
                    (snapshot,),
                )
                for c, n, cnt in cur.fetchall():
                    if c == "000001":
                        continue  # 全市场指数，按沪市派生
                    if c in NON_EQUITY_INDEXES:
                        continue  # 债券等非股票指数「口径不适用」，不是采集缺陷（勿报假警）
                    if cnt == 0:
                        errors.append(f"{c} {n} 无成分快照（详情页将提示暂缺）")
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

        msg = f"指数成分快照 {written} 行 / {len(per_index)} 个指数（快照日 {snapshot}）"
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
                4: f"快照日 {snapshot}（历史快照保留，仅重建当日）",
            },
        )
