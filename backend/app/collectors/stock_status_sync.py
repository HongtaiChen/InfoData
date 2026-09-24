#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
InvestBuddy 上市/退市状态采集器（stock_status_sync，Baostock 权威源，周更）

- 源：bs.query_stock_basic() —— 一次返回沪深全部股票（含退市 300+ 只）
  status: 1=上市 0=退市；outDate 非空即退市日期；ipoDate 为官方上市日期
- 写表 stock_info：
  1. list_status（'上市'/'退市'）与 delist_date 全量覆盖（按 stock_code）
  2. list_date 仅补 NULL（用官方 ipoDate，不覆盖已有值）
- 边界：Baostock 仅覆盖沪深 A 股；B 股/北交所代码不在名单 → 保持 NULL 不误标
- 幂等：整轮可安全重复执行

⚠️ 容错契约（2026-09-14 补）：Baostock 为**登录态会话**，网络抖动常表现为
   「登录失败: 网络接收错误」/「query_stock_basic 失败: 网络接收错误」并在数秒内快速失败
   （2026-09-14 一天内实测失败 2 次，均在重跑后立刻成功）。故本采集器加**重试 + 退避**：
   整段「登录 → 拉取 → 登出」作为一次尝试，失败则退避后重来（而不是只重试单个接口）。
   刻意不用 `call_with_timeout` 的守护线程超时——被放弃的线程仍持有全局 baostock 会话，
   其 logout() 会踩掉重试会话，反而制造更诡异的行为。
"""
import logging
import time

import pymysql

import baostock as bs

from ..db import get_db_config
from ._common import with_steps

logger = logging.getLogger(__name__)

# 运行步骤链模板（供前端「数据流·整链拓扑」展示运行逻辑）
RUN_STEPS = [
    {"no": 1, "name": "Baostock 全量拉取", "params": "query_stock_basic（沪深全部，含退市 300+ 只）；登录态会话，失败退避重试 2 次"},
    {"no": 2, "name": "数量护栏校验", "params": "返回 < 4000 只拒绝覆盖（防接口异常）"},
    {"no": 3, "name": "状态全量覆盖", "params": "逐只 UPDATE stock_info.list_status（上市/退市）"},
    {"no": 4, "name": "日期字段补充", "params": "outDate → delist_date；官方 ipoDate 仅补空 list_date（不覆盖本地推断）"},
    {"no": 5, "name": "B 段状态补充", "params": "Baostock 不返回 B 股 → 改读新浪 B 股实时行情（护栏 <50 只则整段跳过）；只把在市名单置「上市」，其余保持原值不臆断退市"},
]

_PREFIX = ("sh.", "sz.")
# ⚠️ 这里**看起来**已覆盖 B 段（sh.900xxx / sz.200xxx），但实测 Baostock 根本不返回 B 股
# （2026-09-24：8975 行中 B 股 0 行）⇒ B 段状态改由 _fetch_b_share() 独立补充。
# 下面的常量为 B 股在市数量下限护栏：低于它视为源异常、整段跳过（实测当日在市 79 只）。
_B_SHARE_MIN = 50
# Baostock 网络抖动重试（见模块头「容错契约」）
RETRY = 2
BACKOFF = 3.0


class StockStatusSyncCollector:
    """上市/退市状态同步（Baostock）"""

    def _fetch_once(self) -> list[dict]:
        lg = bs.login()
        if lg.error_code != "0":
            raise RuntimeError(f"Baostock 登录失败: {lg.error_msg}")
        try:
            rs = bs.query_stock_basic()
            if rs.error_code != "0":
                raise RuntimeError(f"query_stock_basic 失败: {rs.error_msg}")
            out = []
            while rs.error_code == "0" and rs.next():
                row = rs.get_row_data()  # [code, code_name, ipoDate, outDate, type, status]
                if row[4] != "1":  # 只要股票
                    continue
                if not row[0].startswith(_PREFIX):
                    continue
                out.append(
                    {
                        "stock_code": row[0][3:],
                        "ipo_date": row[2] or None,
                        "out_date": row[3] or None,
                        "status": "退市" if row[5] == "0" else "上市",
                    }
                )
            return out
        finally:
            bs.logout()

    def _fetch_all_stocks(self) -> list[dict]:
        """带退避重试的全量拉取（登录态会话整体重来，见模块头「容错契约」）"""
        last: Exception | None = None
        for i in range(RETRY + 1):
            try:
                return self._fetch_once()
            except Exception as e:  # noqa: BLE001 - 重试后仍失败则原样抛出
                last = e
                if i < RETRY:
                    delay = BACKOFF * (2 ** i)
                    logger.warning(f"Baostock 拉取失败（第 {i + 1}/{RETRY + 1} 次），{delay:g}s 后重试: {e}")
                    time.sleep(delay)
        assert last is not None
        raise last

    def _fetch_b_share(self) -> list[str]:
        """B 股「在市」代码列表（2026-09-24 新增的补充源）。

        为什么必须另开一个源：主源 Baostock `query_stock_basic()` **不返回 B 股** ——
        实测 2026-09-24 返回 8975 行，其中 `sh.900xxx` / `sz.200xxx` **0 行**。
        而 `_PREFIX = ("sh.", "sz.")` 看上去已覆盖 B 段，实际是
        「过滤条件正确、数据源缺席」的隐式缺口 ⇒ 名册里 114 行 B 股的 list_status
        长期为 NULL（A 股主逻辑一次都没触碰过它们）。

        改用新浪 B 股实时行情 `stock_zh_b_spot()` 作为「在市」证据（实测 79 只）。

        ⚠️ 口径（2026-09-24 修正）：**只把在市列表里的置为 `'上市'`，其余保持原值不动，
        绝不整段反推「退市」**。首版实现曾「先整段置退市、再把在市置回上市」，实测
        产生 35 行「`list_status='退市'` 但 `delist_date IS NULL`」——直接踩坏
        v1.3 既有规则 `stock_info_delist_date`（该规则规定「退市 ⇒ 必须有退市日」），
        本轮体检据此 fail(35 行)。根因是**证据不足却下断言**：`stock_zh_b_spot()` 只
        提供「在市」名单，缺席等于「无报价」，无法区分退市/长停/源缺，更没有退市日；
        而 Baostock 不返回 B 股（也无 B 股退市日源）⇒ 无源可证。
        故本字段对 B 段只能**单调升级（NULL → 上市）**，「未知」就诚实留 NULL——
        这也与 v2.3 已写明的「B股/北交所不在源内保持 NULL 不误标」一致。
        影响面为零：该字段只被 `='上市'` 过滤消费，而 B 段本就被 A 股池排除
        （`_common.is_a_share` / `get_stock_list` 的 200%/201%/900% 排除）。
        """
        import akshare as ak

        df = ak.stock_zh_b_spot()
        if df is None or df.empty:
            return []
        codes = []
        for raw in df["代码"].astype(str):
            c = raw.strip()
            if len(c) > 6:          # 形如 sh900901 / sz200002 → 取末 6 位数字
                c = c[-6:]
            if c.isdigit():
                codes.append(c)
        return sorted(set(codes))

    def run(self) -> dict:
        stocks = self._fetch_all_stocks()
        logger.info("Baostock 返回沪深股票 %s 只", len(stocks))
        if len(stocks) < 4000:
            raise RuntimeError(f"Baostock 仅返回 {len(stocks)} 只，疑似异常，拒绝覆盖")

        conn = pymysql.connect(**get_db_config().to_dict())
        upd_status = upd_delist = upd_listdate = b_listed = 0
        try:
            with conn.cursor() as cur:
                for s in stocks:
                    cur.execute(
                        "UPDATE stock_info SET list_status=%s WHERE stock_code=%s",
                        (s["status"], s["stock_code"]),
                    )
                    upd_status += cur.rowcount
                    if s["out_date"]:
                        cur.execute(
                            "UPDATE stock_info SET delist_date=%s WHERE stock_code=%s",
                            (s["out_date"], s["stock_code"]),
                        )
                        upd_delist += cur.rowcount
                    if s["ipo_date"]:
                        cur.execute(
                            "UPDATE stock_info SET list_date=%s WHERE stock_code=%s AND list_date IS NULL",
                            (s["ipo_date"], s["stock_code"]),
                        )
                        upd_listdate += cur.rowcount

                # ---- B 段补充（Baostock 不返回 B 股，见 _fetch_b_share 注释）----
                # 只做「单调升级」：把在市名单里的 B 股置为「上市」，其余原值不动。
                # 护栏：在市列表少于 _B_SHARE_MIN 视为源异常，整段跳过并保留原值，
                # 否则一次接口抖动就会把 79 只在市 B 股整体误标为退市。
                # ⚠️ 切勿改成「整段置退市 + 在市置回上市」——B 股无退市日源，
                #    会产生「退市但无退市日」的行，踩坏规则 stock_info_delist_date。
                try:
                    b_codes = self._fetch_b_share()
                    if len(b_codes) < _B_SHARE_MIN:
                        logger.warning(
                            "B 股在市仅 %s 只（< %s），疑似源异常，跳过 B 段状态更新（保留原值）",
                            len(b_codes), _B_SHARE_MIN,
                        )
                    else:
                        fmt = ",".join(["%s"] * len(b_codes))
                        cur.execute(
                            f"UPDATE stock_info SET list_status='上市' WHERE "
                            f"(list_status IS NULL OR list_status <> '上市') "
                            f"AND stock_code IN ({fmt})",
                            b_codes,
                        )
                        b_listed = cur.rowcount
                        logger.info("B 段状态升级：源在市 %s 只 → 置上市 %s 行", len(b_codes), b_listed)
                except Exception as e:  # noqa: BLE001 - B 股为辅源，失败不得阻断 A 股主流程
                    logger.warning("B 股状态补充失败（不影响 A 股主流程）：%s", str(e)[:160])
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

        msg = (
            f"上市/退市状态同步：覆盖 {upd_status} 只 / 写退市日期 {upd_delist} 只 / "
            f"补上市日期 {upd_listdate} 只 / B 段置上市 {b_listed} 行"
        )
        logger.info("✅ %s", msg)
        return with_steps(
            {
                "records_written": upd_status + b_listed,
                "error_count": 0,
                "errors": [],
                "note": msg,
            },
            RUN_STEPS,
            {
                1: f"Baostock 返回 {len(stocks)} 只",
                2: f"{len(stocks)} ≥ 4000 通过",
                3: f"覆盖 {upd_status} 只",
                4: f"退市日 {upd_delist} 只 · 补上市日 {upd_listdate} 只",
                5: f"B 段置上市 {b_listed} 行",
            },
        )
