#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
InvestBuddy 北交所标的同步（stock_info 的北交所名册重建 + 新旧代码对照，日更）

为什么需要单独一个采集器（2026-09-20 实测立）
================================================================================
北交所 **2025-10-09 全面切换到「920」独立代码段**（北证公告 + 央视/上证报），
旧 43/83/87 段（新三板时期遗留）作废。切换规则：旧码前三位改 920、后三位不变；
后三位撞车时，按上市时间先后对后上市公司递进第四位（例 837023→920123、
831305 海希通讯→920405）。

本库当时完全没跟上，留下三个真实缺陷：

1. **代码全部失效** —— stock_info 的北交所名册冻结在切换前：277 条 =
   242 只存量（旧码）+ 35 只增量（已是 920）。`update_time` 全是 2026-09-05
   的「宽表物理合并」迁移戳，并非数据刷新。旧码在交易所层面已不存在，
   于是所有按 stock_code 的 JOIN（指数成分 / 行情 / 财务）在北交所这一段全断：
   实测北证50 成分 50 只里 36 只对不上主表，中证全指 256 只缺档里 235 只是 920 段。
2. **新上市标的从未入库** —— 切换后新上市的 69 只（截至 2026-09-20 名册共 344 只，
   其中 2026 年内 58 只）一条都没进主表。
3. **行业字段整段缺失** —— `stock_company_sync` 走巨潮 `stock_profile_cninfo`，
   而巨潮不提供北交所档案（实测 `KeyError 'count'`），故 277 条 industry 全空。
   北交所官网口径的 `ak.stock_info_bj_name_code()` 恰好提供
   证券代码 / 简称 / 总股本 / 流通股本 / 上市日期 / **所属行业** / 地区
   （实测 344 行、行业 37 类、零空值），是唯一可用源。

写表
================================================================================
- `stock_info`          ：迁移代码 + UPSERT 简称 / 交易所 / 所属行业；退市标的登记上市状态
- `stock_info_ex`       ：**同步迁移代码**（只改 stock_code（不碰简称/人工列））
- `stock_code_mapping`  ：旧码 → 新码对照台账（权威资产，同时让本次迁移可逆、可审计）

为什么连扩展表一起迁（2026-09-20 实测发现）
--------------------------------------------------------------------------------
`stock_info_ex` 是同一份名册的影子表（承载人工 `is_gxlstock` 高股息标记）。
它的写方是 `stock_info_sync`，而该采集器取自东财 spot 名单 —— **东财不含北交所**，
故扩展表的北交所段**永远不会被刷新**：主表迁移后若不同步，242 条旧码会永久滞留
（实测其中 2 条 —— 同力股份 834599、贝特瑞 835185 —— 还挂着人工「高股息」标记，
等于把人工标注冻结在交易所层面已不存在的死码上）。
**边界**：本采集器只做「旧码 → 新码」的迁移，**不向扩展表补录名册里新增的标的** ——
该表以人工标注为主（data_source='人工'），批量灌入机器行属口径取舍，需另行决策。
当前扩展表北交所 276 条 vs 主表 346 条，差额即「未补录的新上市标的」。

与其他采集器的边界（互不干扰，勿越界）
================================================================================
- `stock_info_sync`    ：沪深名单 / 名称 / 交易所（东财 `stock_zh_a_spot_em` **不含北交所**）
- `stock_company_sync` ：沪深公司档案（巨潮，文件内已明确跳过北交所）
- `stock_status_sync`  ：Baostock 上市 / 退市状态（不覆盖北交所）
本采集器只碰 `exchange='BJ'` 的行 + `stock_code_mapping` 表。

口径约定（重要）
================================================================================
- **行业**：证监会行业分类（制造业大类，如「计算机、通信和其他电子设备制造业」），
  与巨潮「所属行业」同口径，可与沪深标的混算。**不要**与申万行业混用 ——
  `stock_industry_sw` 完全不覆盖北交所（实测 5,215 行里北交所 0 行）。
- **list_date 不写**：主表 `list_date` 的既有口径是「推断口径为唯一权威」
  （`MIN(stock_market_daily.trade_date)` + Baostock ipoDate）。北交所标的的
  MIN(daily) 其实是**新三板挂牌首日**而非北交所上市日，用名册值覆盖会静默混入
  第二套口径。故名册的「北交所上市日期」只落 `stock_code_mapping.list_date`。
- **简称**：剥掉除息 / 除权临时前缀（XD / XR / DR）后写入 —— 官网在这两天会给
  简称加前缀，原样写主表会污染简称列；`ST / *ST` 属实质信息，保留。

幂等与可重跑：第二轮起 `old_rows` 已全是 920 码，映射为空、只做名册 UPSERT 刷新
（扩展表同理由空映射驱动，0 迁移）。台账行数应恒为 **242**
（= 官方公告的存量切换只数），巡检据此拦截映射退化。
"""
import logging
import re
from datetime import date, datetime

import pandas as pd
import pymysql

import akshare as ak

from ..db import get_db_config
from ._common import call_with_timeout, with_steps

logger = logging.getLogger(__name__)

SOURCE = "bse"                       # 北交所官网（经 akshare stock_info_bj_name_code）
SWITCH_DATE = "2025-10-09"           # 北交所存量股票代码切换生效日
# 巡检不变式：官方公告的存量切换只数（242 = 277 只总数 − 35 只原本就用 920 的增量公司）
EXPECTED_SWITCHED = 242

TABLE = "stock_code_mapping"
DDL = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
    id INT AUTO_INCREMENT PRIMARY KEY,
    old_code VARCHAR(10) NOT NULL COMMENT '变更前代码（北交所新三板时期 43/83/87 等段）',
    new_code VARCHAR(10) DEFAULT NULL COMMENT '变更后代码（920 段）；已退市标的为 NULL',
    short_name VARCHAR(50) DEFAULT NULL COMMENT '证券简称（北交所名册口径）',
    list_date DATE DEFAULT NULL COMMENT '北交所上市日期（名册口径，非主表 list_date）',
    change_date DATE DEFAULT NULL COMMENT '代码切换生效日；退市标的为摘牌日',
    status VARCHAR(20) NOT NULL DEFAULT 'switched'
        COMMENT 'switched=已切换 920 段 / retired=标的已退市无新代码',
    match_rule VARCHAR(50) DEFAULT NULL COMMENT '映射依据：简称归一 / 人工核证 / 官方通报',
    evidence VARCHAR(255) DEFAULT NULL COMMENT '映射证据（人工核证与退市项必填）',
    source VARCHAR(50) NOT NULL DEFAULT 'bse',
    data_source VARCHAR(100) DEFAULT NULL,
    update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_old_code (old_code),
    KEY idx_new_code (new_code)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '证券代码变更对照（北交所 920 代码切换）'
"""

# ---------------------------------------------------------------------------
# 简称归一无法命中的极少数记录 —— 逐条核证，证据随行落库
#
# 269/277 靠「简称归一」即可命中；以下 6 条属于更名 / ST / 除息前缀，
# 归一后仍对不上，必须人工核证。**每一条都必须写明证据**，否则本表就是猜测。
# ---------------------------------------------------------------------------
MANUAL_MAPPING: dict[str, tuple[str, str]] = {
    "430090": ("920090", "简称变更 同辉信息→*ST同辉；后三位 090 无冲突"),
    "831305": ("920405", "920405 名册简称精确为「海希通讯」；305 段按上市时间递进"),
    "832023": ("920023", "北交所官方《新旧代码对照表》第 82 行：832023→920023"),
    "833575": ("920575", "北交所官方《新旧代码对照表》第 69 行：833575→920575"),
    "835174": ("920174", "公司更名 五新隧装→五新智能；后三位 174 名册内唯一"),
    "920445": ("920445", "代码未变（龙竹科技），仅官网简称临时带 XD 除息前缀"),
}

# 已退市、无新代码的存量标的（保留旧码行并登记上市状态，不留「在市」幻影）
RETIRED: dict[str, dict] = {
    "835305": {
        "short_name": "*ST云创", "delist_date": "2026-07-30",
        "evidence": "北交所 2026-06-09 决定终止上市，2026-07-30 摘牌（巨潮公告 1225388036）",
    },
    "839680": {
        "short_name": "*ST广道", "delist_date": "2026-01-05",
        "evidence": "北交所 2025-11-12 决定终止上市，2026-01-05 摘牌（2026 年 A 股首只摘牌公司）",
    },
}

RUN_STEPS = [
    {"no": 1, "name": "拉北交所名册",
     "params": "ak.stock_info_bj_name_code（北交所官网源）→ 344 只 × [代码/简称/上市日/所属行业]；行数 <200 视为源异常拒用"},
    {"no": 2, "name": "构建新旧代码映射",
     "params": "本地旧码按「简称归一」匹配名册 → 未命中者走人工核证表（6 条，证据随行落库）；仍无法映射即认定已退市"},
    {"no": 3, "name": "写代码对照台账",
     "params": f"UPSERT {TABLE}（uk_old_code）；台账行数应为 {EXPECTED_SWITCHED}（= 官方公告存量切换只数）"},
    {"no": 4, "name": "迁移主表代码",
     "params": "stock_info 旧码 UPDATE 为新码；目标码已被占用的行拒绝执行并记 error（防静默覆盖）"},
    {"no": 5, "name": "迁移扩展表代码",
     "params": "stock_info_ex 同一份名册的影子表，按台账迁移旧码；只改代码，人工列（is_gxlstock 等）不动"},
    {"no": 6, "name": "UPSERT 名册",
     "params": "简称（剥 XD/XR/DR）/ 交易所 / 所属行业；list_date 不写（口径归 mapping 表）"},
    {"no": 7, "name": "退市登记", "params": "已退市存量标的：list_status='退市' + delist_date，避免名册留在市幻影"},
    {"no": 8, "name": "结果巡检",
     "params": "名册覆盖率 / 行业非空率 / 旧码残留数（主表 + 扩展表）/ 台账行数不变式"},
]

# 简称归一：剥除息除权与新股前缀、S/ST 标记，仅用于匹配（不用于写入）
_TEMP_PREFIX = re.compile(r"^(XD|XR|DR)")
_NORM_PREFIX = re.compile(r"^(XD|XR|DR|N|C)")
_ST_PREFIX = re.compile(r"^(S?\*?ST)")


def _display_name(s: str) -> str:
    """写入主表的简称：剥掉除息/除权临时前缀，保留 ST/*ST"""
    return _TEMP_PREFIX.sub("", s).strip() or s


def _norm_name(s: str) -> str:
    """匹配用的简称键：再去掉 S/ST 标记与新股前缀"""
    return _ST_PREFIX.sub("", _NORM_PREFIX.sub("", (s or "").strip().upper())).strip()


def _clean(v) -> str | None:
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    s = str(v).strip()
    return s or None


def _to_date(v) -> date | None:
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


class BjStockSyncCollector:
    """北交所名册同步（stock_info 迁移 + 代码对照台账）"""

    # ---------- 数据源 ----------
    def _fetch_roster(self) -> list[dict]:
        """北交所官网名册（经 akshare）。返回 [{stock_code, short_name, industry, list_date}]"""
        df = call_with_timeout(ak.stock_info_bj_name_code, 90)
        if df is None or df.empty:
            raise RuntimeError("stock_info_bj_name_code 返回空")
        if "证券代码" not in df.columns or "证券简称" not in df.columns:
            raise RuntimeError(f"stock_info_bj_name_code 列异常: {list(df.columns)}")
        rows: list[dict] = []
        for _, r in df.iterrows():
            code = _clean(r.get("证券代码"))
            name = _clean(r.get("证券简称"))
            if not code or not name:
                continue
            rows.append({
                "stock_code": code,
                "short_name": _clean(_display_name(name)),
                "industry": _clean(r.get("所属行业")),
                "list_date": _to_date(r.get("上市日期")),
            })
        # 护栏：源侧若只返回一页/改动口径，宁可不写也不要用残缺名册覆盖主表
        if len(rows) < 200:
            raise RuntimeError(f"北交所名册仅 {len(rows)} 行（<200），疑似源异常，拒用")
        return rows

    # ---------- 映射 ----------
    def _build_mapping(self, old_rows: list[tuple], roster: list[dict]) -> tuple[dict, dict, list]:
        """返回 (mapping, unmatched, ambiguous)

        mapping: {old_code: (new_code, rule, evidence)}，含 old==new 的「已在名册」项；
        unmatched: [(code, name)] 名册里找不到、也非 RETIRED → 认定已退市；
        ambiguous: 归一键在名册内重复的键（不敢自动映射的）
        """
        code_of_key: dict[str, str] = {}
        ambiguous_keys: set[str] = set()
        for r in roster:
            k = _norm_name(r["short_name"])
            if not k:
                continue
            if k in code_of_key and code_of_key[k] != r["stock_code"]:
                ambiguous_keys.add(k)
            else:
                code_of_key[k] = r["stock_code"]

        roster_codes = {r["stock_code"] for r in roster}
        mapping: dict[str, tuple[str, str, str]] = {}
        unmatched: list[tuple[str, str]] = []
        for code, name in old_rows:
            if code in RETIRED:
                continue          # 已退市，无新码；另走「退市登记」步骤
            if code in MANUAL_MAPPING:
                new_code, ev = MANUAL_MAPPING[code]
                mapping[code] = (new_code, "官方通报" if "对照表" in ev else "人工核证", ev)
                continue
            if code in roster_codes:
                mapping[code] = (code, "已在名册", "代码已是 920 段，无需迁移")
                continue
            k = _norm_name(name or "")
            if k and k not in ambiguous_keys and k in code_of_key:
                mapping[code] = (code_of_key[k], "简称归一", f"简称「{name}」归一为「{k}」命中名册")
                continue
            unmatched.append((code, name or ""))
        return mapping, unmatched, sorted(ambiguous_keys)

    # ---------- 主流程 ----------
    def run(self) -> dict:
        roster = self._fetch_roster()
        roster_codes = {r["stock_code"] for r in roster}
        logger.info("北交所名册 %s 只", len(roster))

        conn = pymysql.connect(**get_db_config().to_dict())
        migrated = ex_migrated = inserted = updated = retired_marked = 0
        errors: list[str] = []
        notes: list[str] = []
        try:
            with conn.cursor() as cur:
                cur.execute(DDL)
                cur.execute(
                    "SELECT stock_code, short_name FROM stock_info WHERE exchange='BJ' "
                    "ORDER BY stock_code"
                )
                old_rows = cur.fetchall()
                cur.execute("SELECT stock_code FROM stock_info")
                all_codes = {r[0] for r in cur.fetchall()}

                # 1) 映射
                mapping, unmatched, ambiguous = self._build_mapping(old_rows, roster)
                if ambiguous:
                    notes.append(f"归一简称在名册内重复、未自动映射: {ambiguous[:5]}")
                switches = {o: n[0] for o, n in mapping.items() if n[0] != o}

                # 2) 对照台账（存量切换 + 已退市；行数不变式见 EXPECTED_SWITCHED）
                ledger = []
                for old, new in switches.items():
                    rule, ev = mapping[old][1], mapping[old][2]
                    nm = next((r for r in roster if r["stock_code"] == new), {})
                    ledger.append((
                        old, new, nm.get("short_name"), nm.get("list_date"),
                        SWITCH_DATE, "switched", rule, ev, SOURCE, SOURCE,
                    ))
                for old, meta in RETIRED.items():
                    ledger.append((
                        old, None, meta["short_name"], None,
                        meta["delist_date"], "retired", "官方通报", meta["evidence"],
                        SOURCE, SOURCE,
                    ))
                if ledger:
                    cur.executemany(
                        f"""
                        INSERT INTO {TABLE}
                            (old_code, new_code, short_name, list_date, change_date,
                             status, match_rule, evidence, source, data_source)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON DUPLICATE KEY UPDATE
                            new_code = VALUES(new_code), short_name = VALUES(short_name),
                            list_date = VALUES(list_date), change_date = VALUES(change_date),
                            status = VALUES(status), match_rule = VALUES(match_rule),
                            evidence = VALUES(evidence)
                        """,
                        ledger,
                    )
                cur.execute(f"SELECT COUNT(*) FROM {TABLE}")
                ledger_total = cur.fetchone()[0]

                # 3) 迁移主表代码（目标码已被占用则拒绝，绝不静默覆盖）
                for old, new in switches.items():
                    if new in all_codes:
                        errors.append(f"{old}→{new} 迁移中止：目标代码已被 stock_info 占用")
                        continue
                    cur.execute(
                        "UPDATE stock_info SET stock_code=%s, update_time=NOW() "
                        "WHERE stock_code=%s",
                        (new, old),
                    )
                    if cur.rowcount == 1:
                        all_codes.discard(old)
                        all_codes.add(new)
                        migrated += 1
                    else:
                        errors.append(f"{old} 迁移影响行数 {cur.rowcount}（期望 1）")

                # 3b) 迁移扩展表代码（stock_info_ex —— 同一份名册的影子表）
                #     为什么必须一起迁：stock_info_ex 的写方 stock_info_sync 取自东财 spot 名单，
                #     而**东财不含北交所**，故该表北交所段永远得不到刷新 —— 主表迁移后若不同步
                #     这张表，242 条旧码会永久滞留（实测其中 2 条还挂着人工「高股息」标记，
                #     等于把人工标注冻结在死码上）。2026-09-20 实测发现并补上。
                #     ⚠️ 驱动源必须是**台账**而不是本轮的 `switches`：stock_info 首轮迁完后旧码
                #     即消失，第二轮起 `switches` 恒为空；若挂在 switches 上，扩展表的旧码就
                #     **永远迁不动**（2026-09-20 实测踩到：连跑两次，扩展表 240 条岿然不动，
                #     巡检如实报了 error，才暴露这个假实现）。台账是持久权威，且天然幂等 ——
                #     迁完后再查 `old_code IN ex_codes` 就命中不了了。
                #     ⚠️ 该表 stock_code **无唯一索引**（仅普通 KEY idx_stock_code）：
                #     迁移前先确认目标码未被占用，占用即中止该条并记 error，绝不静默覆盖。
                #     ⚠️ 只改 stock_code（不碰简称/人工列），**刻意不碰** is_gxlstock（人工标记）、
                #     list_date（本地推断口径）、data_source —— 三列均不在本采集器写入范围内。
                #     2 只退市标的无 920 对应码，保持旧码（台账 status='retired' 可溯）。
                cur.execute(
                    "SELECT old_code, new_code FROM stock_code_mapping "
                    "WHERE status='switched' AND new_code IS NOT NULL"
                )
                switch_pairs = cur.fetchall()
                cur.execute("SELECT stock_code FROM stock_info_ex WHERE exchange='BJ'")
                ex_codes = {r[0] for r in cur.fetchall()}
                for old, new in switch_pairs:
                    if old not in ex_codes:
                        continue            # 扩展表本无此条 —— 名册补齐是另一件事，见文件头边界
                    if new in ex_codes:
                        errors.append(
                            f"{old}→{new} 扩展表迁移中止：目标代码已被 stock_info_ex 占用"
                        )
                        continue
                    cur.execute(
                        "UPDATE stock_info_ex SET stock_code=%s, update_time=NOW() "
                        "WHERE stock_code=%s AND exchange='BJ'",
                        (new, old),
                    )
                    if cur.rowcount == 1:
                        ex_codes.discard(old)
                        ex_codes.add(new)
                        ex_migrated += 1
                    else:
                        errors.append(f"{old} 扩展表迁移影响行数 {cur.rowcount}（期望 1）")

                # 4) 名册 UPSERT（简称 / 交易所 / 行业；list_date 刻意不写，见文件头口径约定）
                for r in roster:
                    if r["stock_code"] in all_codes:
                        cur.execute(
                            "UPDATE stock_info SET short_name=%s, exchange='BJ', "
                            "industry=%s, update_time=NOW() WHERE stock_code=%s",
                            (r["short_name"], r["industry"], r["stock_code"]),
                        )
                    else:
                        cur.execute(
                            "INSERT INTO stock_info (stock_code, short_name, exchange, "
                            "industry, update_time) VALUES (%s, %s, 'BJ', %s, NOW())",
                            (r["stock_code"], r["short_name"], r["industry"]),
                        )
                        all_codes.add(r["stock_code"])
                        inserted += 1
                # 主表 rowcount 只计「值真的变了」的行，用作刷新数会低估 → 直接按名册口径算
                updated = len(roster) - inserted

                # 5) 退市登记（不留「在市」幻影）
                for code, meta in RETIRED.items():
                    cur.execute(
                        "UPDATE stock_info SET list_status='退市', delist_date=%s, "
                        "update_time=NOW() WHERE stock_code=%s AND (list_status IS NULL "
                        "OR list_status <> '退市')",
                        (meta["delist_date"], code),
                    )
                    retired_marked += cur.rowcount

                # 6) 巡检
                #    ⚠️ 行业空值只统计**在市**记录：已退市标的（不在名册内）本就无行业源，
                #    算进来会把「正确结果」报成故障（2026-09-20 首跑实测踩过）。
                #    list_status 可能为 NULL，故一律 IFNULL 后再比，避免 NULL 比较被 SUM 吞掉。
                cur.execute(
                    "SELECT COUNT(*), "
                    "SUM(IFNULL(list_status,'')='退市'), "
                    "SUM(IFNULL(list_status,'')<>'退市' AND (industry IS NULL OR industry='')) "
                    "FROM stock_info WHERE exchange='BJ'"
                )
                bj_total, bj_delisted, bj_no_ind = cur.fetchone()
                cur.execute(
                    "SELECT COUNT(*) FROM stock_info WHERE exchange='BJ' "
                    "AND stock_code NOT LIKE '92%' AND IFNULL(list_status,'') <> '退市'"
                )
                stale_left = cur.fetchone()[0]

                # 扩展表旧码残留：该表**无 list_status 列**，主表那套「排除退市」在这里用不了
                # —— 2 只退市标的（无 920 对应码）只能按台账口径排除：台账 status='retired'
                # 的行即「本就无新码可迁」。除此之外任何非 920 码都算残留缺陷。
                # ⚠️ 本条**带参数**（retired_codes），pymysql 会对 SQL 做 `query % args`，
                #    故 where 里的字面量 `'92%'` 必须写成 `'92%%'` —— 否则 %' 被当成格式符，
                #    抛 `ValueError: unsupported format character`。无参数的同款查询（主表那条）
                #    不走 mogrify、反而不受影响，极易踩错。（2026-09-20 实测踩到，已回滚无损）
                retired_codes = tuple(RETIRED.keys())
                if retired_codes:
                    ph = ",".join(["%s"] * len(retired_codes))
                    cur.execute(
                        f"SELECT COUNT(*) FROM stock_info_ex WHERE exchange='BJ' "
                        f"AND stock_code NOT LIKE '92%%' AND stock_code NOT IN ({ph})",
                        retired_codes,
                    )
                    ex_stale_left = cur.fetchone()[0]
                else:
                    ex_stale_left = 0

                # 名册覆盖：应 344/344
                covered = 0
                codes = [r["stock_code"] for r in roster]
                for i in range(0, len(codes), 900):
                    chunk = codes[i: i + 900]
                    ph = ",".join(["%s"] * len(chunk))
                    cur.execute(
                        f"SELECT COUNT(*) FROM stock_info WHERE stock_code IN ({ph})", chunk
                    )
                    covered += cur.fetchone()[0]

                if unmatched:
                    notes.append(
                        "名册外记录（认定已退市，保留原码）: "
                        + ", ".join(f"{c}({n})" for c, n in unmatched[:5])
                    )
                if ledger_total != EXPECTED_SWITCHED:
                    errors.append(
                        f"对照台账 {ledger_total} 行 ≠ 官方存量切换 {EXPECTED_SWITCHED} 行（映射可能退化）"
                    )
                if bj_no_ind:
                    errors.append(f"北交所在市标的 {bj_no_ind} 条 industry 仍为空（源侧应有全覆盖）")
                if stale_left:
                    errors.append(f"{stale_left} 条非 920 码在市记录，代码口径仍不干净")
                if ex_stale_left:
                    errors.append(
                        f"扩展表 stock_info_ex 仍有 {ex_stale_left} 条非 920 码（退市除外），"
                        f"影子表未同步迁移"
                    )
                if covered < len(roster):
                    errors.append(f"名册覆盖不全：{covered}/{len(roster)} 只不在 stock_info")
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

        msg = (
            f"北交所名册同步：迁移 {migrated} 只（扩展表 {ex_migrated} 只）"
            f" / 新增 {inserted} 只 / 刷新 {updated} 只"
            f"（名册 {len(roster)} 只，台账 {ledger_total} 行）"
        )
        if errors:
            msg += f"；异常 {len(errors)} 项"
        logger.info(f"✅ {msg}")
        return with_steps(
            {"records_written": migrated + ex_migrated + inserted + updated,
             "error_count": len(errors), "errors": errors[:50], "note": msg},
            RUN_STEPS,
            {
                1: f"名册 {len(roster)} 只（行业非空 "
                   f"{sum(1 for r in roster if r['industry'])}/{len(roster)}）",
                2: f"可映射 {len(mapping)} 只（其中需迁移 {len(switches)}），未映射 {len(unmatched)} 只",
                3: f"台账 {ledger_total} 行（不变式 {EXPECTED_SWITCHED}）",
                4: f"迁移 {migrated} 只" + (f" · 失败 {len(errors)} 项" if errors else ""),
                5: f"扩展表迁移 {ex_migrated} 只（is_gxlstock 等人工列不动）",
                6: f"刷新 {updated} 只 · 新增 {inserted} 只（list_date 不写，口径归台账）",
                7: f"退市登记 {retired_marked} 只",
                8: f"名册覆盖 {covered}/{len(roster)} · 北交所共 {bj_total} 条"
                   f"（在市 {bj_total - (bj_delisted or 0)} / 退市 {bj_delisted or 0}）· "
                   f"在市行业空 {bj_no_ind} · 旧码残留 主表 {stale_left} / 扩展表 {ex_stale_left}",
            },
        )
