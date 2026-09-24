#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
InvestBuddy 股票基础信息采集器（stock_info / stock_info_ex 表，日更）

名单源 = **多源取并集**（逐个容错，任一源挂掉不影响其余）：
  ① 交易所官方名单（权威、自带上市日期、简称无盘口前缀）
     · 沪：ak.stock_info_sh_name_code("主板A股") + ("科创板")  → 60xxxx / 68xxxx
     · 深：ak.stock_info_sz_name_code("A股列表")              → 00xxxx / 30xxxx
     · 京：ak.stock_info_bj_name_code()                        → 920xxx
  ② 东财代码名称表 ak.stock_info_a_code_name() —— 兜底补漏（官方名单尚未收录的最新上市股）
  ③ 降级：本地佐证并集（stock_info ∪ stock_market_current ∪ index_constituents）
     —— 外部源全挂时保住已知名册，不再因为「某一个行情接口被拒」就整轮漏收

上市日期 list_date 优先级：官方名单自带 > 存量已有值 > MIN(stock_market_daily.trade_date)
策略：不删退市/旧行 —— 名单内 UPDATE 名称与交易所，名单外保留；仅 INSERT 新上市
   （新增行显式写 list_status='上市'；stock_daily_incr / _common.load_universe /
     index_cons_sync / daily_recon 均按 list_status='上市' 建候选池，写成 NULL 会被静默排除）

🔴 2026-09-24 换源：原主源东财实时快照 ak.stock_zh_a_spot_em() 已弃用
   它是**实时行情快照**，不是名单源，实测有两类缺陷，均已在库内取到实证：
   1) 漏收 89 只 A 股 —— 快照只返回当时有报价的代码，近年次新股成片缺失。实测漏收含
      001280 中国铀业（沪深300 / 深证成指成分；申万行业表、机构调研表、指数成分表三处
      都能查到它，唯独名册没有）。而 stock_info 是 stock_daily_incr / market_current_sync
      的股票池**上游**，缺一只就级联缺日线、缺快照、缺行业分布 —— 中证全指「其他」桶里
      那 21 只「无行业」成分股即由此而来。
   2) （2026-09-24 换源后复测的**勘误**）原先判断「XD/XR/DR 前缀只有东财快照会写」，
      实测**交易所官方名单同样带**：沪/深/京三所官方简称在除权除息**当天**也写成 XDxxx
      （这是交易所自己的盘口约定），换源并不能消除它。它属**当日瞬态**而非长期污染：
      次日同步即被干净简称覆盖 —— 实测 09-24 13:49 这次运行后，09-23 写脏的
      "XD中国医"(600056) 已恢复为「中国医药」，而当日新除权的 600160 等 24 行仍带 XD，
      属正常现象。故此处**不做前缀剥离**：剥离反而偏离交易所原始口径。
   新源收益（实测）：缺失代码覆盖 21/21（无遗漏）；单轮 15s 跑完（原源还要逐只查日线回填）；
   官方名单自带上市日期，把「日线回填」从最多 600 次逐股查询降到 0 次。
"""
import logging
from datetime import datetime, date

import pymysql
import akshare as ak

from ..db import get_db_config
from ._common import with_steps, call_with_timeout

logger = logging.getLogger(__name__)

# 运行步骤链模板（供前端「数据流·整链拓扑」展示运行逻辑）
RUN_STEPS = [
    {"no": 1, "name": "取名册（多源并集）", "params": "交易所官方 沪主板+科创 / 深A / 京 → 东财代码名称表兜底补漏；逐个容错，合计 <3000 判不可用"},
    {"no": 2, "name": "读存量快照", "params": "stock_info / stock_info_ex 现有代码与名称"},
    {"no": 3, "name": "补上市日期", "params": "官方名单自带优先；仅无官方日期者回填 MIN(stock_market_daily.trade_date)（单次上限 600）"},
    {"no": 4, "name": "名单内更新", "params": "stock_info/stock_info_ex UPDATE 名称/交易所；名单外保留（不删退市旧行）"},
    {"no": 5, "name": "新上市补录", "params": "仅 INSERT 新代码（含 list_status='上市'）；stock_info_ex 保留人工 is_gxlstock"},
]

# 交易所官方名单四路：(源标签, 调用, 代码列, 名称列, 上市日期列)
_OFFICIAL_SOURCES = (
    ("交易所·沪主板", lambda: ak.stock_info_sh_name_code(symbol="主板A股"), "证券代码", "证券简称", "上市日期"),
    ("交易所·沪科创", lambda: ak.stock_info_sh_name_code(symbol="科创板"), "证券代码", "证券简称", "上市日期"),
    ("交易所·深A", lambda: ak.stock_info_sz_name_code(symbol="A股列表"), "A股代码", "A股简称", "A股上市日期"),
    ("交易所·北交所", ak.stock_info_bj_name_code, "证券代码", "证券简称", "上市日期"),
)

# 非 A 股代码段（B 股）：与 _common.is_a_share / stock_daily_incr 的排除口径保持一致
_B_SHARE_PREFIX = ("200", "201", "900", "901")


def _is_a_share_code(code: str) -> bool:
    """6 位数字且非 B 股代码段（B 股全线不纳入名册，与下游逐股采集口径一致）"""
    c = str(code).strip()
    return len(c) == 6 and c.isdigit() and c[:3] not in _B_SHARE_PREFIX


def _norm_date(v) -> date | None:
    """官方名单的上市日期统一成 date。

    容错目标：date / datetime / pandas Timestamp / '2025-12-03' / '20251203' /
    NaN / NaT / 'nan' / None —— 统一先转字符串再解析，避免 isinstance 判不住
    pandas 标量（NaT 是 datetime 子类，`.date()` 会返回 NaT 而非 date）。
    """
    if v is None:
        return None
    s = str(v).strip()
    if not s or s == "-" or s[:3].lower() in ("nat", "nan", "non"):
        return None
    s = s[:10].replace("/", "-")
    if len(s) == 8 and s.isdigit():
        s = f"{s[:4]}-{s[4:6]}-{s[6:]}"
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return None


def _exchange_of(code: str) -> str:
    """按 A 股代码前缀推断交易所"""
    if code.startswith(("60", "68", "90")):
        return "SH"
    if code.startswith(("00", "30", "20")):
        return "SZ"
    if code.startswith(("43", "83", "87", "88", "92", "82", "89")):
        return "BJ"
    if code.startswith(("4", "8")):
        return "BJ"
    return "SH" if code.startswith(("5", "6", "9")) else "SZ"


class StockInfoSyncCollector:
    """股票基础资料同步（日更）"""

    def __init__(self, max_backfill: int = 600, timeout: float = 90):
        # 单次最多为多少只缺 list_date 的股票回填（防止首跑全表扫描）
        self.max_backfill = max_backfill
        # 单个外部名单源调用超时（akshare 底层 requests 不传 timeout，须外层兜底）
        self.timeout = timeout

    # ---------- 数据源 ----------
    def _src_official(self) -> dict[str, dict]:
        """交易所官方名单（沪主板A + 沪科创 + 深A + 京）→ {code: {name, list_date, src}}

        四路互相独立：某一路失败只丢自己那一批，其余照常入库。
        """
        out: dict[str, dict] = {}
        for tag, fn, c_code, c_name, c_date in _OFFICIAL_SOURCES:
            try:
                df = call_with_timeout(fn, self.timeout)
                if df is None or getattr(df, "empty", True):
                    raise RuntimeError("返回空表")
                n = 0
                for _, r in df.iterrows():
                    code = str(r[c_code]).strip().zfill(6)
                    if not _is_a_share_code(code):
                        continue
                    out[code] = {
                        "name": str(r[c_name]).strip(),
                        "list_date": _norm_date(r.get(c_date)),
                        "src": tag,
                    }
                    n += 1
                logger.info(f"名单源[{tag}] {len(df)} 行 → 载入 {n} 只")
            except Exception as e:  # noqa: BLE001 - 单源失败不拖累其余源
                logger.warning(f"名单源[{tag}] 失败（跳过）: {type(e).__name__}: {str(e)[:90]}")
        return out

    def _src_em(self) -> dict[str, dict]:
        """东财代码名称表（**无**上市日期）—— 补官方名单尚未收录的最新上市股"""
        df = call_with_timeout(ak.stock_info_a_code_name, self.timeout)
        if df is None or getattr(df, "empty", True) or "code" not in getattr(df, "columns", []):
            raise RuntimeError("stock_info_a_code_name 返回异常")
        out: dict[str, dict] = {}
        for _, r in df.iterrows():
            code = str(r["code"]).strip().zfill(6)
            if _is_a_share_code(code):
                out[code] = {"name": str(r["name"]).strip(), "list_date": None, "src": "东财名单"}
        return out

    def _src_local(self) -> dict[str, dict]:
        """降级源：本地佐证并集（外部名单源全挂时的最后一道防线）

        并集来源：已入库名册 stock_info ∪ 行情快照 stock_market_current ∪ 指数成分
        index_constituents —— 后者来自中证指数官网（csindex），是**独立于东财**的
        第三方源，即便东财全线不可用它仍在库里。三表都有名称列，故不会写出
        「名称 = 代码」这种污染行。
        （申万行业表 stock_industry_sw 虽然覆盖更广，但**无名称列**，凑不出简称故不用它。）
        """
        sql = """
            SELECT stock_code, MAX(nm) AS nm FROM (
                SELECT stock_code, short_name AS nm FROM stock_info
                UNION ALL SELECT stock_code, stock_name AS nm FROM stock_market_current
                UNION ALL SELECT stock_code, stock_name AS nm FROM index_constituents
            ) t
            WHERE stock_code IS NOT NULL AND nm IS NOT NULL AND nm <> ''
            GROUP BY stock_code
        """
        conn = pymysql.connect(**get_db_config().to_dict())
        try:
            with conn.cursor() as cur:
                cur.execute(sql)
                rows = cur.fetchall()
        finally:
            conn.close()
        out: dict[str, dict] = {}
        for c, n in rows:
            code = str(c).strip().zfill(6)
            if _is_a_share_code(code) and n:
                out[code] = {"name": str(n).strip(), "list_date": None, "src": "本地佐证"}
        if len(out) < 3000:
            raise RuntimeError(f"本地佐证源仅 {len(out)} 只，不可作全市场名单")
        return out

    def _fetch_roster(self) -> tuple[dict[str, dict], str]:
        """多源取并集。返回 (roster, 源描述)；外部源合计不足 3000 只则抛异常走降级。"""
        roster: dict[str, dict] = {}
        tags: list[str] = []

        official = self._src_official()
        if official:
            roster.update(official)
            tags.append(f"交易所官方 {len(official)}")

        try:
            em = self._src_em()
            added = sum(1 for c in em if c not in roster)
            for c, v in em.items():
                roster.setdefault(c, v)      # 官方名单优先，东财只补它没有的
            tags.append(f"东财名单 {len(em)}(补漏 {added})")
        except Exception as e:  # noqa: BLE001 - 东财挂掉不影响官方名单
            logger.warning(f"名单源[东财名单] 失败: {type(e).__name__}: {str(e)[:90]}")

        if len(roster) < 3000:
            raise RuntimeError(f"外部名单源合计仅 {len(roster)} 只，判为不可用")
        return roster, " + ".join(tags)

    def _list_date_of(self, code: str) -> date | None:
        conn = pymysql.connect(**get_db_config().to_dict())
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT MIN(trade_date) FROM stock_market_daily WHERE stock_code=%s", (code,)
                )
                row = cur.fetchone()
        finally:
            conn.close()
        return (row[0].date() if isinstance(row[0], datetime) else row[0]) if row and row[0] else None

    # ---------- 主流程 ----------
    def run(self) -> dict:
        # 1. 取名册（多源并集 → 本地佐证降级）
        try:
            roster, source_tag = self._fetch_roster()
        except Exception as e:  # noqa: BLE001 - 外部源全挂，降级本地
            logger.warning(f"外部名单源全部不可用，降级本地佐证源: {str(e)[:100]}")
            roster, source_tag = self._src_local(), "本地佐证（外部源全挂）"

        conn = pymysql.connect(**get_db_config().to_dict())
        inserted = updated = backfilled = 0
        try:
            with conn.cursor() as cur:
                # 2. 现有 stock_info / stock_info_ex 快照
                cur.execute("SELECT stock_code, short_name, list_date FROM stock_info")
                exist = {str(c): {"name": n, "list_date": d} for c, n, d in cur.fetchall()}
                cur.execute("SELECT stock_code FROM stock_info_ex")
                exist_ex = {str(c) for (c,) in cur.fetchall()}

                # 3. 上市日期：官方名单自带 → 直接用；其余（新代码 / 存量缺值）回填日线首日
                list_date_map: dict[str, date | None] = {
                    c: v["list_date"] for c, v in roster.items() if v["list_date"] is not None
                }
                need_list = {
                    c for c in roster
                    if c not in list_date_map
                    and (c not in exist or exist[c]["list_date"] is None)
                }
                for code in sorted(need_list):
                    if backfilled >= self.max_backfill:
                        break
                    list_date_map[code] = self._list_date_of(code)
                    backfilled += 1

                # 4. stock_info：UPDATE 名单内 / INSERT 新
                #    data_source 不入 INSERT —— v1.5 起为表级来源构成常量
                #    （DEFAULT 'EM;BAOSTOCK;CNINFO'：东财名单;Baostock状态;巨潮档案三源共同维护）
                upd, ins = [], []
                for code, v in roster.items():
                    name, ex = v["name"], _exchange_of(code)
                    if code in exist:
                        # 存量非空 list_date 一律保留（改历史口径需人工拍板），仅缺值才填
                        ld = (exist[code]["list_date"]
                              if exist[code]["list_date"] is not None else list_date_map.get(code))
                        upd.append((name, ex, ld, code))
                    else:
                        ins.append((code, name, ex, list_date_map.get(code)))
                cur.executemany(
                    "UPDATE stock_info SET short_name=%s, exchange=%s, list_date=%s, update_time=NOW() "
                    "WHERE stock_code=%s",
                    upd,
                )
                updated = cur.rowcount
                cur.executemany(
                    "INSERT INTO stock_info (stock_code, short_name, exchange, list_date, list_status, update_time) "
                    "VALUES (%s, %s, %s, %s, '上市', NOW())",
                    ins,
                )
                inserted = len(ins)

                # 5. stock_info_ex：同名/交易所；新代码补入（保留人工 is_gxlstock）
                upd_ex, ins_ex = [], []
                for code, v in roster.items():
                    ex = _exchange_of(code)
                    if code in exist_ex:
                        upd_ex.append((v["name"], ex, code))
                    else:
                        ins_ex.append((code, v["name"], ex, list_date_map.get(code), source_tag))
                cur.executemany(
                    "UPDATE stock_info_ex SET short_name=%s, exchange=%s, update_time=NOW() WHERE stock_code=%s",
                    upd_ex,
                )
                cur.executemany(
                    "INSERT INTO stock_info_ex (stock_code, short_name, is_gxlstock, exchange, list_date, update_time, data_source) "
                    "VALUES (%s, %s, NULL, %s, %s, NOW(), %s)",
                    ins_ex,
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

        msg = (f"股票基础资料同步：{len(roster)} 只在名单（新增 {inserted} / 更新 {updated}，"
               f"日线回填 {backfilled}），源={source_tag}")
        logger.info(f"✅ {msg}")
        n_official_date = sum(1 for v in roster.values() if v["list_date"] is not None)
        return with_steps(
            {
                "records_written": inserted + updated,
                "error_count": 0,
                "errors": [],
                "note": msg,
            },
            RUN_STEPS,
            {
                1: f"{len(roster)} 只（{source_tag}）",
                2: f"存量 stock_info {len(exist)} 只 / stock_info_ex {len(exist_ex)} 只",
                3: f"官方自带 {n_official_date} 只 / 日线回填 {backfilled} 只",
                4: f"更新 {updated} 只",
                5: f"新增 {inserted} 只",
            },
        )
