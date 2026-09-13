#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
InvestBuddy 期货现货价格采集器（futures_spot_price，新浪/生意社源经 akshare，恢复采集）

背景（2026-09-13）：该表为历史导入遗留，代码层无任何采集器，水位停于 2025-09-15。
本次按《死表恢复同步可行性调研报告》第 3 批（P2）恢复采集。

- 源：ak.futures_spot_price_daily(start_day, end_day) —— 逐交易日返回 54 个有现货报价的品种
- 写表：futures_spot_price
- 策略：**按区间分块增量补齐** —— 从本地 MAX(trade_date)+1 补到今日；
        每块 ≤ chunk_days 个自然日、每块独立提交，避免单点卡死丢全程
- 幂等：先读目标区间已有的 (trade_date, good_name) 集合，只插不存在的行
        （该表无唯一索引：同日同品种存在历史多快照行，加 UNIQUE 前需先定口径）
- 补数：from_date 可显式指定起点（运维补历史缺口用）；不传则从本地 MAX+1 续
- 容错：单块失败记 error 不中断，下轮自动补；连续多块失败即提前收尾

⚠️ 源的两个坑（实测，2026-09-13）：
  1. akshare 内部会去 www.100ppi.com（生意社）补现货价，**失败按日期重试最多 5 次**，
     长区间会被显著拖慢（实测 46 天区间耗时 165s）；故必须分块 + 单块超时。
  2. 生意社对高频访问会**封 IP**（返回「您的地址被网站墙了，请稍后从该日期起重试」），
     故 chunk_days 不宜过大、且失败块留待下轮重试（历史块不会丢，只是延后）。

⚠️ 已修正的历史口径缺陷（**单位不一致**，设计规范 §8 G16）：
  akshare 在 `_check_information` 内对 3 个品种做了单位归一后才计算基差：
  鸡蛋 ×500（元/公斤→元/500千克）、玻璃 ×80（元/平方米→元/吨）、生猪 ×1000（元/公斤→元/吨）。
  历史导入的行 **只把归一值用于算基差，却把原始值存进了 spot_price**，导致
  `spot_price` 与 `main_contract_basis` 互不兼容（例：鸡蛋 spot=7.35 而 basis=626）。
  本采集器统一存**归一后的元/吨口径**（与其余 51 个品种一致），
  即鸡蛋/玻璃/生猪的 spot_price 相比历史行会有 500×/80×/1000× 的量级跳变 —— 属**修正**而非回退。

⚠️ 派生列口径（main_basis_high/low/avg_180d）：
  历史导入的这 3 列由原导入器按「180 个自然日窗口」逐行滚动计算，但其 basis 取值来自
  另一份盘中快照（与本地存储的 main_contract_basis 略有出入），故**无法逐字段复现历史值**。
  本采集器采用口径：窗口 = [trade_date-180d, trade_date]，取值 = 本地 main_contract_basis
  序列 ∪ 本轮新拉取的源 basis；high/low 取极值、avg 取算术均值（2 位小数）。
  即：**新行自洽，与历史行存在一次性轻微口径差**，已在设计规范 §8 G15 记录。
"""
import logging
import re
import time
from datetime import datetime, date, timedelta

import pymysql

import akshare as ak

from ..db import get_db_config
from ._common import with_steps, call_with_timeout

logger = logging.getLogger(__name__)

# 运行步骤链模板（供前端「数据流·整链拓扑」展示运行逻辑）
RUN_STEPS = [
    {"no": 1, "name": "确定补齐区间", "params": "本地 MAX(trade_date)+1 → 今日；from_date 可显式指定起点（运维补数）；max_days>0 时限制单轮最多补多少个自然日"},
    {"no": 2, "name": "分块拉取源数据", "params": "ak.futures_spot_price_daily(start,end) 按 chunk_days(默认30) 切块，单块 180s 超时，逐块提交"},
    {"no": 3, "name": "品种代码映射", "params": "源 symbol(字母代码) → 本地 good_name(中文名)，54 项静态表（2026-09-13 用现货价+主力价双字段实证对齐）"},
    {"no": 4, "name": "算 180 日派生列", "params": "main_basis_high/low/avg_180d = [交易日-180d, 交易日] 窗口内 main_contract_basis 的极值/均值"},
    {"no": 5, "name": "差量写入", "params": "仅写入 trade_date > 本地 MAX 的行（data_source=AKSHARE）"},
]

# 源字母代码 → 本地中文品种名（54 项）
# 推导方式（2026-09-13）：以 2025-09-15 为锚点，用 (现货价 ±0.05) 双字段匹配本地行自动对齐
# 47 项；余 7 项（FG/J/JD/LH/NI/SN）因单位换算或本地当日无行，再用
# ak.futures_display_main_sina()/futures_symbol_mark() 的官方中文名交叉确认。
# PL(丙烯) 为郑商所 2025 新上市品种，本地表从未有过，本次一并纳入。
_CODE2NAME = {
    "A": "豆一", "AG": "白银", "AL": "铝", "AU": "黄金", "BR": "丁二烯橡胶",
    "BU": "石油沥青", "BZ": "纯苯", "C": "玉米", "CF": "棉花", "CU": "铜",
    "CY": "棉纱", "EB": "苯乙烯", "EG": "乙二醇", "FG": "玻璃", "FU": "燃料油",
    "HC": "热轧卷板", "I": "铁矿石", "J": "焦炭", "JD": "鸡蛋", "JM": "焦煤",
    "L": "聚乙烯", "LC": "碳酸锂", "LH": "生猪", "M": "豆粕", "MA": "甲醇MA",
    "NI": "镍", "OI": "菜籽油OI", "P": "棕榈油", "PB": "铅", "PF": "涤纶短纤",
    "PG": "液化石油气", "PL": "丙烯", "PP": "聚丙烯", "PR": "瓶片", "PS": "多晶硅",
    "PX": "PX", "RB": "螺纹钢", "RM": "菜籽粕", "RU": "天然橡胶", "SA": "纯碱",
    "SF": "硅铁", "SH": "烧碱", "SI": "工业硅", "SM": "锰硅", "SN": "锡",
    "SP": "纸浆", "SR": "白糖", "SS": "不锈钢", "TA": "PTA", "UR": "尿素",
    "V": "聚氯乙烯", "WR": "线材", "Y": "豆油", "ZN": "锌",
}

_INSERT_COLS = ["trade_date", "good_name", "spot_price", "main_contract_code",
                "main_contract_price", "main_contract_basis", "main_contract_change_pct",
                "main_basis_high_180d", "main_basis_low_180d", "main_basis_avg_180d",
                "update_time", "data_source"]

_DERIV_WINDOW_DAYS = 180


def _num(v):
    """宽松转 float；不可解析返回 None"""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    return f


def _contract_month(code: str | None) -> str | None:
    """主力合约代码 → 本地存储的 YYMM 四位数字。

    源返回形如 'c2601'(4 位) / 'CF601' / 'CY511'(3 位)，本地统一存 4 位 YYMM，
    3 位时按当前年代补前导 '2'（实测 2025-09-12 本地 棉纱=2511 ← CY511）。
    """
    if not code:
        return None
    digits = re.sub(r"\D", "", str(code))
    if len(digits) == 3:
        return "2" + digits
    return digits or None


class FuturesSpotSyncCollector:
    """期货现货价格增量同步（新浪/生意社 futures_spot_price_daily）"""

    def __init__(self, chunk_days: int = 30, max_days: int = 0,
                 sleep_sec: float = 0.5, timeout_sec: float = 180,
                 first_lookback_days: int = 365, from_date: str | None = None):
        self.chunk_days = max(int(chunk_days or 30), 1)
        self.max_days = int(max_days or 0)
        self.sleep_sec = float(sleep_sec)
        self.timeout_sec = float(timeout_sec)
        self.first_lookback_days = int(first_lookback_days or 365)
        self.from_date = self._parse_date(from_date)   # 运维补数用：显式起点

    @staticmethod
    def _parse_date(v) -> date | None:
        if not v:
            return None
        s = str(v).strip()[:10]
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
            try:
                return datetime.strptime(s, fmt).date()
            except ValueError:
                continue
        return None

    # ---------- 本地状态 ----------

    @staticmethod
    def _load_existing(conn, start: date, end: date) -> set[tuple[date, str]]:
        """目标区间内已存在的 (trade_date, good_name) 集合 —— 用于差量写入与补数"""
        out: set[tuple[date, str]] = set()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT trade_date, good_name FROM futures_spot_price "
                "WHERE trade_date BETWEEN %s AND %s", (start, end)
            )
            for d, n in cur.fetchall():
                out.add((d, str(n)))
        return out

    @staticmethod
    def _load_basis_history(conn, since: date) -> dict[str, list[tuple[date, float]]]:
        """窗口期历史 basis：{good_name: [(date, basis), ...]}"""
        out: dict[str, list[tuple[date, float]]] = {}
        with conn.cursor() as cur:
            cur.execute(
                "SELECT good_name, trade_date, main_contract_basis FROM futures_spot_price "
                "WHERE trade_date >= %s AND main_contract_basis IS NOT NULL "
                "ORDER BY good_name, trade_date", (since,)
            )
            for name, d, b in cur.fetchall():
                out.setdefault(str(name), []).append((d, float(b)))
        return out

    # ---------- 数据源 ----------

    def _fetch_chunk(self, start: date, end: date):
        return call_with_timeout(
            ak.futures_spot_price_daily, self.timeout_sec,
            start_day=start.strftime("%Y%m%d"), end_day=end.strftime("%Y%m%d"),
        )

    # ---------- 主流程 ----------

    def run(self) -> dict:
        conn = pymysql.connect(**get_db_config().to_dict())
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT MAX(trade_date) FROM futures_spot_price")
                row = cur.fetchone()
            max_date = row[0] if row and row[0] else None
        finally:
            conn.close()

        today = date.today()
        if self.from_date:
            # 运维补数：显式起点，允许回补历史缺口（已存在的行会被差量跳过）
            start = self.from_date
        elif max_date is None:
            start = today - timedelta(days=self.first_lookback_days)
        else:
            start = max_date + timedelta(days=1)
        if self.max_days > 0 and not self.from_date:
            start = max(start, today - timedelta(days=self.max_days))

        if start > today:
            msg = f"期货现货价格已是最新（本地 MAX={max_date}），无需补齐"
            logger.info("✅ %s", msg)
            return with_steps(
                {"records_written": 0, "error_count": 0, "errors": [], "note": msg},
                RUN_STEPS, {1: f"区间为空（本地最新 {max_date}）"},
            )

        total_days = (today - start).days + 1
        chunks = []
        c0 = start
        while c0 <= today:
            c1 = min(c0 + timedelta(days=self.chunk_days - 1), today)
            chunks.append((c0, c1))
            c0 = c1 + timedelta(days=1)
        logger.info("期货同步：%s ~ %s（%d 天，%d 块）", start, today, total_days, len(chunks))

        # 逐块拉取，累积 (日期, 品种) -> 记录
        collected: dict[tuple[date, str], dict] = {}
        errors: list[str] = []
        ok_chunks, fail_chunks = 0, 0
        consecutive_fail = 0
        for c0, c1 in chunks:
            try:
                df = self._fetch_chunk(c0, c1)
            except Exception as e:  # noqa: BLE001 - 限流/超时，跳过该块留待下轮
                fail_chunks += 1
                consecutive_fail += 1
                errors.append(f"{c0}~{c1}: {type(e).__name__} {str(e)[:80]}")
                logger.warning("  块 %s~%s 失败（%s）", c0, c1, type(e).__name__)
                if consecutive_fail >= 3:
                    errors.append("连续 3 块失败，提前收尾（疑似生意社限流，下轮继续）")
                    break
                continue
            consecutive_fail = 0
            ok_chunks += 1
            if df is None or df.empty:
                continue
            for _, r in df.iterrows():
                sym = str(r.get("symbol") or "").strip().upper()
                name = _CODE2NAME.get(sym)
                if not name:
                    continue
                ds = str(r.get("date") or "")[:8]
                try:
                    d = datetime.strptime(ds, "%Y%m%d").date()
                except ValueError:
                    continue
                if d < start or d > today:
                    continue
                spot = _num(r.get("spot_price"))
                mprice = _num(r.get("dominant_contract_price"))
                if spot is None or mprice is None:
                    continue
                collected[(d, name)] = {
                    "trade_date": d,
                    "good_name": name,
                    "spot_price": round(spot, 2),
                    "main_contract_code": _contract_month(r.get("dominant_contract")),
                    "main_contract_price": round(mprice, 2),
                    "main_contract_basis": round(spot - mprice, 2),
                    "main_contract_change_pct": round((spot - mprice) / spot * 100, 2) if spot else None,
                }
            if self.sleep_sec > 0:
                time.sleep(self.sleep_sec)

        if not collected:
            msg = f"期货同步未取到任何数据（成功块 {ok_chunks} / 失败块 {fail_chunks}）"
            logger.warning("⚠️ %s", msg)
            return with_steps(
                {"records_written": 0, "error_count": len(errors), "errors": errors[:50], "note": msg},
                RUN_STEPS,
                {1: f"{start} ~ {today} 共 {total_days} 天", 2: f"成功 {ok_chunks} 块 / 失败 {fail_chunks} 块"},
            )

        # 派生列：本地历史 basis ∪ 本轮新值，按品种取 180 天窗口
        conn = pymysql.connect(**get_db_config().to_dict())
        written = 0
        try:
            existing = self._load_existing(conn, start, today)
            fresh = [r for k, r in collected.items() if (r["trade_date"], r["good_name"]) not in existing]
            skipped_dup = len(collected) - len(fresh)
            if not fresh:
                msg = f"期货现货价格无需写入（取回 {len(collected)} 条，均已存在）"
                logger.info("✅ %s", msg)
                return with_steps(
                    {"records_written": 0, "error_count": len(errors), "errors": errors[:50], "note": msg},
                    RUN_STEPS,
                    {1: f"{start} ~ {today} 共 {total_days} 天",
                     2: f"成功 {ok_chunks} 块 / 失败 {fail_chunks} 块，取回 {len(collected)} 条",
                     5: f"全部已存在，写入 0 条"},
                )

            hist = self._load_basis_history(conn, start - timedelta(days=_DERIV_WINDOW_DAYS))
            series: dict[str, list[tuple[date, float]]] = {k: list(v) for k, v in hist.items()}
            seen: dict[str, set[date]] = {k: {d for d, _ in v} for k, v in series.items()}
            for row in fresh:
                key = row["good_name"]
                if row["trade_date"] in seen.get(key, set()):
                    continue      # 该日已有存量行，以存量为准（保持历史口径不被新值扰动）
                series.setdefault(key, []).append((row["trade_date"], float(row["main_contract_basis"])))
                seen.setdefault(key, set()).add(row["trade_date"])
            for k in series:
                series[k].sort(key=lambda x: x[0])

            for row in fresh:
                d = row["trade_date"]
                lo = d - timedelta(days=_DERIV_WINDOW_DAYS)
                vals = [b for dd, b in series[row["good_name"]] if lo <= dd <= d]
                if vals:
                    row["main_basis_high_180d"] = round(max(vals), 2)
                    row["main_basis_low_180d"] = round(min(vals), 2)
                    row["main_basis_avg_180d"] = round(sum(vals) / len(vals), 2)

            now = datetime.now()
            placeholder = ", ".join(["%s"] * len(_INSERT_COLS))
            insert_sql = (f"INSERT INTO futures_spot_price ({', '.join(_INSERT_COLS)}) "
                          f"VALUES ({placeholder})")
            payload = [
                tuple(row.get(c) for c in _INSERT_COLS[:-2]) + (now, "AKSHARE")
                for row in sorted(fresh, key=lambda x: (x["trade_date"], x["good_name"]))
            ]
            with conn.cursor() as cur:
                for i in range(0, len(payload), 2000):
                    cur.executemany(insert_sql, payload[i:i + 2000])
                written = len(payload)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

        dates = sorted({r["trade_date"] for r in fresh})
        msg = (f"期货现货价格补齐：{written} 条 · {len(dates)} 个交易日 · "
               f"{len({r['good_name'] for r in fresh})} 个品种"
               f"（{dates[0]} ~ {dates[-1]}），失败块 {fail_chunks}"
               + (f"，跳过已存在 {skipped_dup} 条" if skipped_dup else ""))
        logger.info("✅ %s", msg)
        return with_steps(
            {"records_written": written, "error_count": len(errors), "errors": errors[:50], "note": msg},
            RUN_STEPS,
            {
                1: f"{start} ~ {today} 共 {total_days} 天（{len(chunks)} 块）",
                2: f"成功 {ok_chunks} 块 / 失败 {fail_chunks} 块，取回 {len(collected)} 条",
                3: f"命中品种 {len({r['good_name'] for r in fresh})} 个",
                4: f"窗口 {_DERIV_WINDOW_DAYS} 天滚动计算完成",
                5: f"写入 {written} 条（{dates[0]} ~ {dates[-1]}）" + (f"，跳过已存在 {skipped_dup} 条" if skipped_dup else ""),
            },
        )
