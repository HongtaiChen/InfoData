#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
InvestBuddy 启动收敛器（自愈闭环 ·「启动即收敛」方案 B）

为什么是「启动」而不是「每小时」
--------------------------------
实测本机服务是**间歇运行**的：近 38 天在线率 32%、断线 41 次、最长离线 260 小时，
典型形态是「22:00 关机 → 次日 18:00~19:00 开机」（每天离线约 20 小时）。
因此任何「按墙上时钟触发」的机制（cron / 每小时复检）都与真实使用习惯错位 ——
它们只在**在线的那几小时**里有效，而 90% 的问题发生在离线期间。

⇒ 真正对齐习惯的触发点只有一个：**进程启动**。
   本模块把「启动」变成系统唯一的关键事件，每次启动都把数据收敛到一致状态。

七层自愈闭环在本模块的落地
--------------------------
  ① 感知  latest_scan()   —— 读体检结果，拿到「哪张表 / 哪条规则」不合格
  ② 定位  writers_of()    —— table_meta.writers 给出「这张表由哪些任务写」→ 该重跑谁
  ③ 决策  plan()          —— 按可自愈白名单分诊：能修的排进队列，不能修的升级「待人工」
  ④ 执行  repair()        —— 串行重跑 writers（执行器由调用方注入，复用调度器的双层锁）
  ⑤ 止损  MAX_REPAIRS     —— 单次上限 + 同任务只跑一次 + 修不好不重试
  ⑥ 验证  diff_rules()    —— 修复后复检，diff 出「修好了哪些 / 还剩哪些」
  ⑦ 交代  build_report()  —— 全流程落 task_runs.run_detail，前端可见

⚠️ 三条不可越界的铁律
--------------------
  1. **只修「数据缺失型」**
     值域越界、唯一性冲突、格式不符、跨表引用缺失这类问题**重跑修不好**，而且
     可能用上游的间歇性异常值覆盖真实数据（本项目有过 `ths_concept_market.change_pct`
     源侧空值率 99% 的先例）。这类一律**不自动修**，见 MANUAL_ONLY_CHECKERS。
  2. **映射缺失不猜测**
     writers 为空、或指向未注册/纯手动任务时，不做任何推断，直接升级「待人工」。
     猜测性重跑比不重跑更危险。
  3. **不做无上限重试**
     同一任务一次收敛内最多执行一次；修不好就交给人工，绝不进入
     「修 → 不过 → 再修」的死循环（那会把上游打爆，也会掩盖真实故障）。

与 `catchup_missed` 的分工（互补，不替代）
----------------------------------------
  · catchup 依据「**时间**」：cron 的最近一个触发点没被 success 覆盖 → 补跑
    （擅长覆盖 DQ 尚未配置规则的表，以及高频流如 news）
  · 本收敛器依据「**水位**」：DQ 说哪张表的哪个判据不合格 → 重跑它的 writers
    （精确到「哪张表、落后几天」，是 catchup 做不到的）
  两者合并成**一条串行队列**去重后执行，既不双跑也不遗漏。
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable
from zoneinfo import ZoneInfo

from apscheduler.triggers.cron import CronTrigger

from .db import query_all

logger = logging.getLogger(__name__)

# 与 scheduler.py 同一时区真源（评估 cron 触发点必须用本地时区）
TZ = ZoneInfo("Asia/Shanghai")

# task_config.cron 里表示「非排期任务」的标记
MANUAL_MARK = "手动"

# 当日触发次数超过此值 ⇒ 视为「高频流」（数据一直在更新），**不适用发布时刻门槛**。
# 实测本项目：按班次发布的任务当日触发 1~2 次；高频流（如 */30）当日数十次。
# 意义：门槛只针对「当天还没到它该发数据的点」的**日频任务**，
#       高频流随时可修，不能被门槛挡住（否则等于永不修复）。
HIGH_FREQ_DAILY_CAP = 6


def day_fires(cron: str, now: datetime) -> list[datetime]:
    """cron 在 now **当天**的全部触发时刻（升序，含未来）；非法 cron / 无触发返回空

    与 scheduler._recent_fire_times 的区别：后者只回看过去（用于判「班次有没有漏」），
    本函数要的是**当日最后一次**（用于判「今天的数据发完了没有」），故必须向未来迭代。
    """
    try:
        tr = CronTrigger.from_crontab(cron.strip(), timezone=TZ)
    except Exception:                     # noqa: BLE001 - 非法 cron 一律视为「不适用门槛」
        return []
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)
    fires: list[datetime] = []
    prev, cur = None, day_start - timedelta(seconds=1)
    for _ in range(2000):                 # 硬上限，防脏 cron 造成死循环
        nxt = tr.get_next_fire_time(prev, cur)
        if nxt is None or nxt >= day_end:
            break
        fires.append(nxt)
        prev, cur = nxt, nxt + timedelta(seconds=1)
    return fires

# ---------------------------------------------------------------------------
# 分诊白名单
# ---------------------------------------------------------------------------

# ✅ 可自愈：语义统一为「数据缺失 / 停更 / 行数不足」—— 重跑 writers 是对症的
SELF_HEALABLE_CHECKERS: frozenset[str] = frozenset({
    "freshness_daily",      # 表 MAX(日期) 落后交易日历
    "freshness_interval",   # 距当前时长过久（高频流）
    "date_floor",           # 日期下限不足（停更）
    "date_floor_where",     # 某条腿停更（子集日期下限）
    "column_watermark",     # 有列无值：某列不再有新值
    "row_count_slice",      # 最新切片行数不足
    "row_count_total",      # 总行数不足（清空 / 大面积缺失）
})

# ❌ 不可自愈：值域 / 唯一性 / 格式 / 自洽 / 结构 / 运行态 —— 重跑无益，且可能覆盖真实数据
MANUAL_ONLY_CHECKERS: frozenset[str] = frozenset({
    "unique_index",         # 结构：缺唯一索引 → 要执行 DDL，不是重跑
    "regex_count",          # 格式不符 → 解析逻辑问题
    "null_rate_slice",      # 空值率高 → 可能是源侧间歇不返回，重跑有覆盖风险
    "violation_count",      # 值域越界 → 需人判断是脏数据还是口径变了
    "where_count",          # 自洽约束（OHLC / 涨跌幅衔接）→ 重跑不修，需查因子或源
    "ref_missing",          # 跨表引用缺失 → 名册问题，需查采集口径
    "pct_limit",            # 涨跌幅超板块上限 → 物理约束，说明价格/因子写错
    "factor_link",          # 复权因子自洽 → 因子体系问题
    "date_ceiling",         # 日期落到未来 → 解析拼错，要改代码
    "gap_scan",             # 全史缺口 → 应由 daily_backfill 定向回补，且耗时极长
    "source_handoff",       # 跨源衔接 → 换源口径问题
    "per_key_coverage",     # 每票覆盖不足 → 均匀稀疏，需查采集范围
    "stale_running",        # 僵尸 running → 已由 start() 的 reap_orphan_runs() 处理
    "long_finished_run",    # 超长运行 → 物理离线信号，不是任务故障
})

# 逐检查器的「为什么不能自动修」——写给人看的原因，不是给机器看的分类
MANUAL_REASON: dict[str, str] = {
    "unique_index": "结构问题：需执行 DDL 补唯一索引，重跑无益",
    "regex_count": "格式不符：属解析逻辑问题，重跑只会把同样的错再写一遍",
    "null_rate_slice": "空值率高：可能是源侧间歇性不返回，重跑有覆盖真实数据的风险",
    "violation_count": "值域越界：需人工判断是脏数据还是口径变了",
    "where_count": "自洽约束不成立：需查复权因子或上游源，重跑不修",
    "ref_missing": "跨表引用缺失：属名册采集口径问题，需人工核对",
    "pct_limit": "涨跌幅超板块上限：物理约束被违反，说明价格或因子写错",
    "factor_link": "复权因子不自洽：属因子体系问题，重跑不修",
    "date_ceiling": "日期落到未来：日期解析/拼接写错，需改采集代码",
    "gap_scan": "全史缺口：应由 daily_backfill 定向回补（全史扫描耗时极长，不宜在启动期自动跑）",
    "source_handoff": "跨源衔接存在偏差：属换源口径问题，需人工核对",
    "per_key_coverage": "每票覆盖不足（均匀稀疏）：需查采集范围与源能力上限",
    "stale_running": "僵尸 running 记录：已由启动时的 reap_orphan_runs() 自动回收",
    "long_finished_run": "超长运行：这是「机器待机冻结进程」的物理离线信号，不是任务故障",
}

# 收敛器自身与体检任务永不作为修复目标（防自递归 / 防把巡检当修复）
NEVER_REPAIR_TASKS: frozenset[str] = frozenset({
    "startup_converge",
    "data_quality_check",
    "data_quality_check_weekly",
})

# 单次收敛的默认修复上限（防「重跑风暴」把上游打爆）
DEFAULT_MAX_REPAIRS = 10

# 单次收敛的默认**时间预算**（秒）—— 与 DEFAULT_MAX_REPAIRS 是**两个维度**，任一触发即停。
#
# 为什么光有「个数」不够（2026-10-08 实测）：单任务耗时跨度极大 ——
#   `concept_market_sync` 60~67s，而 `daily_recon_window` 全市场逐票拉取实测 1722s（28 分钟）。
# 「上限 10 个」在极端情况下可能是 10 秒，也可能是 5 小时，防不住「一次启动把整个
# 晚间窗口占满」。时间预算是**用户可感知的维度**：卡住 15 分钟就止损，剩下的转人工。
DEFAULT_MAX_SECONDS = 900


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class RepairTarget:
    """一个待重跑的任务，及其触发它的规则/表（合并同类项后）"""

    task: str
    rules: list[str] = field(default_factory=list)
    tables: list[str] = field(default_factory=list)
    checkers: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "task": self.task,
            "tables": sorted(set(self.tables)),
            "rules": sorted(set(self.rules)),
            "checkers": sorted(set(self.checkers)),
            "reason": "；".join(sorted(set(self.reasons))[:3]),
        }


@dataclass
class Plan:
    """分诊结果：能修的 / 需人工的 / 定位不到写入方的 / 当日数据还没发布的"""

    targets: list[RepairTarget] = field(default_factory=list)
    manual: list[dict] = field(default_factory=list)
    unmapped: list[dict] = field(default_factory=list)
    pending: list[dict] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not (self.targets or self.manual or self.unmapped or self.pending)


# ---------------------------------------------------------------------------
# 收敛器
# ---------------------------------------------------------------------------

class StartupConverger:
    """启动收敛：分诊 → 修复 → 复检 → 报告（纯逻辑，执行器由调用方注入）"""

    def __init__(
        self,
        max_repairs: int = DEFAULT_MAX_REPAIRS,
        *,
        max_seconds: int = DEFAULT_MAX_SECONDS,
        groups: tuple[str, ...] = ("daily",),
        known_tasks: set[str] | None = None,
        dry_run: bool = False,
    ):
        self.max_repairs = max(0, int(max_repairs))
        self.max_seconds = max(0, int(max_seconds))
        # 启动巡检覆盖的规则组。默认只 daily（199 条，秒级）；weekly（8 条，含全史
        # 缺口扫描，实测 4~7 分钟）由调用方按需加入，并自行节流（见 run.py）。
        self.scan_groups: tuple[str, ...] = tuple(groups) or ("daily",)
        self.dry_run = dry_run
        self._known_tasks = known_tasks          # 可执行任务集合（None = 延迟从 TASKS 取）
        self._writers_cache: dict[str, list[str]] = {}
        self._cron_cache: dict[str, str | None] = {}

    @property
    def scan_params(self) -> str:
        """报告里「第 1 步·启动巡检」的参数文案（随 scan_groups 变化，不写死）"""
        return f"{' + '.join(self.scan_groups)} 组体检（读 dq_rules 逐条执行 → dq_report）"

    # ---------- ② 定位：表 → 写入方 ----------

    def _schedulable_tasks(self) -> set[str]:
        """可被本收敛器调度的任务集合 = TASKS 注册表 ∩ task_config 已启用

        为什么要取交集而不是只用 TASKS：`task_config` 是启停的单一真源，
        被禁用的任务即使有实现也不该被自动触发。
        """
        if self._known_tasks is not None:
            return self._known_tasks
        from .tasks.run import TASKS  # noqa: PLC0415 - 延迟 import 避免循环

        rows = query_all("SELECT task_name FROM task_config WHERE enabled = 1")
        enabled = {r["task_name"] for r in rows}
        self._known_tasks = set(TASKS.keys()) & enabled
        return self._known_tasks

    def writers_of(self, table: str) -> list[str]:
        """table_meta.writers → 该表的写入任务清单（这就是「自动修复」缺的那一环）

        writers 是 JSON 列，pymysql 默认不解析，返回形如 `'["stock_daily_incr"]'` 的字符串。
        容错：解析失败或非列表一律返回空 —— 宁可不修，不可猜修。
        """
        if table in self._writers_cache:
            return self._writers_cache[table]
        rows = query_all("SELECT writers FROM table_meta WHERE table_name = %s", (table,))
        out: list[str] = []
        if rows:
            raw = rows[0].get("writers")
            if isinstance(raw, str) and raw.strip():
                try:
                    parsed = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    logger.warning(f"table_meta.writers 解析失败（{table}）：{raw!r}")
                    parsed = None
                if isinstance(parsed, list):
                    out = [str(x) for x in parsed if x]
            elif isinstance(raw, (list, tuple)):
                out = [str(x) for x in raw if x]
        self._writers_cache[table] = out
        return out

    # ---------- ②b 定位：该任务「今天的数据发出来了没有」 ----------

    def _task_cron(self, task: str) -> str | None:
        """取任务的 cron（仅当已启用且非手动）；不适用返回 None

        ⚠️ cron 是**唯一真源**，此处不另立「数据就绪时刻表」—— 后者会与 cron 漂移
        （改了排期忘了改表），而本判据的全部意义就是「跟着真实排期走」。
        """
        if task in self._cron_cache:
            return self._cron_cache[task]
        rows = query_all("SELECT cron, enabled FROM task_config WHERE task_name = %s", (task,))
        cron: str | None = None
        if rows:
            raw = (rows[0].get("cron") or "").strip()
            enabled = rows[0].get("enabled")
            if enabled and raw and raw.lower() not in (MANUAL_MARK.lower(), "none", "-"):
                cron = raw
        self._cron_cache[task] = cron
        return cron

    def release_floor(self, task: str, now: datetime | None = None) -> tuple[datetime | None, int]:
        """返回 `(当日发布下限, 当日触发次数)`

        **发布下限 = 该任务「今天的最后一次触发时刻」。**
        返 `(None, n)` 表示**不适用门槛**，四种情形：
          · 任务的 cron 是「手动 / none / 非法」⇒ 没有排期可依，不拦
          · 当日无触发点（如交易日任务落在周末）⇒ 今天本就不该有数据
          · 当日触发次数 > HIGH_FREQ_DAILY_CAP ⇒ 高频流（数据一直在更新），随时可修
          · 算不出来 ⇒ 宁可不拦（不能让判据本身成为「永不修复」的原因）

        **为什么用「末次触发」而不是「最近一次已过的触发」**（2026-10-08 实测）：
        `concept_market_sync` cron = `0 21,22 * * 0-4`，21:00 那批**实测只写 2~17 行**、
        且 `status=success`；当日主体（358~375 行）在 22:00 才落库。若按「是否已有过
        成功记录」判，21:00 的 success 会让我们以为「数据已就绪」，于是重跑 —— 白跑
        （实测 60~67s / 写 1126 行，复检仍 warning，id=1002/1006 两次复现）。
        按「末次触发」判则正确归入「待发布」，等它自己的 22:00 班次发完再说。
        """
        cron = self._task_cron(task)
        if not cron:
            return None, 0
        now_local = now or datetime.now(TZ)
        fires = day_fires(cron, now_local)
        if not fires or len(fires) > HIGH_FREQ_DAILY_CAP:
            return None, len(fires)
        return fires[-1], len(fires)

    # ---------- ① 感知：读最近一轮体检结果 ----------

    @staticmethod
    def latest_scan(groups: tuple[str, ...] | None = None) -> list[dict]:
        """每条在册规则**各自最近一次**结果（与 api/quality.py 的 _latest_per_rule 同口径）

        为什么不取「全表 MAX(run_at) 那一轮」：daily / weekly 是两个独立轮次，
        取单一轮次会让另一组规则集体消失（2026-09-12 修正过的坑）。

        `groups`：只返回指定 rule_group 的规则。收敛器传 `("daily",)` —— 启动巡检跑的
        正是 daily 组（`data_quality_check` 的 params = `{"groups":["daily"]}`，199 条），
        若把 weekly 组（8 条，最新结果可能是上周的）也纳入分诊，就会拿**陈旧结果**去
        驱动修复，白跑任务。
        """
        sql = """
            SELECT r.rule_name, r.table_name, r.check_type, r.severity, r.status,
                   r.metric_value, r.message, r.run_at
            FROM dq_report r
            JOIN dq_rules dr ON dr.rule_name = r.rule_name AND dr.enabled = 1
            JOIN (
                SELECT rule_name, MAX(run_at) AS m
                FROM dq_report WHERE check_type <> 'recon' GROUP BY rule_name
            ) t ON t.rule_name = r.rule_name AND t.m = r.run_at
        """
        if groups:
            ph = ",".join(["%s"] * len(groups))
            return query_all(sql + f" WHERE dr.rule_group IN ({ph})", tuple(groups))
        return query_all(sql)

    def scan(self) -> list[dict]:
        """按本收敛器配置的规则组读最近一轮结果（`latest_scan(self.scan_groups)` 的实例封装）

        保留 `latest_scan` 为 staticmethod 是为了向后兼容既有调用与验收脚本。
        """
        return self.latest_scan(self.scan_groups)

    # ---------- ③ 决策：分诊 ----------

    def plan(self, scan: list[dict] | None = None, *, now: datetime | None = None) -> Plan:
        """把体检结果分诊为四类：可自愈（→ 重跑 writers）/ 待发布 / 需人工 / 定位不到写入方

        只处理 status ∈ {fail, warning}：
          · pass 无需动作
          · error 是**规则自身**有问题（表/列不存在、SQL 报错），重跑业务任务无意义
        severity='info' 同样跳过（信息性提示，不是故障）。

        `now` 仅用于测试注入（默认取上海当前时刻）。

        **「待发布」这一类为什么必须存在**（2026-10-08 实测）：
        体检读的是**当前快照**，而当日数据往往还没发完。若不做区分，凡是「今天还没到
        发布时刻」的表都会被判「数据缺失」→ 重跑它的采集任务 → 源里同样没有数据 →
        白跑一轮且复检必然仍不合格（`concept_market_sync` 实测白烧 60~67s，两次复现）。
        这类**不是故障**，是**时点错位**：等它自己的班次到点即可，不该由收敛器抢跑。
        """
        scan = scan if scan is not None else self.latest_scan()
        now_local = now or datetime.now(TZ)
        schedulable = self._schedulable_tasks()
        targets: dict[str, RepairTarget] = {}
        manual: list[dict] = []
        unmapped: list[dict] = []
        pending: list[dict] = []

        for row in scan:
            status = row.get("status")
            if status not in ("fail", "warning"):
                continue
            if (row.get("severity") or "").lower() == "info":
                continue
            ct = row.get("check_type") or ""
            table = row.get("table_name") or ""
            rule = row.get("rule_name") or ""
            base = {"rule_name": rule, "table_name": table, "check_type": ct,
                    "status": status, "message": (row.get("message") or "")[:200]}

            if ct not in SELF_HEALABLE_CHECKERS:
                why = MANUAL_REASON.get(ct) or f"检查器未纳入自愈白名单（{ct}）"
                manual.append({**base, "why": why, "manual_only": ct in MANUAL_ONLY_CHECKERS})
                continue

            writers = self.writers_of(table)
            if not writers:
                # 两类都会落到这里，且都不该被"猜"着修：
                #   ① 历史导入 / 自产 / 已停更的表（如实测的 stock_hold_by_fund、
                #      stock_market_daily_ex、task_runs）—— 根本没有采集任务可重跑
                #   ② 元数据漏配 —— 属于要修的配置问题，不是要跑的任务
                unmapped.append({**base, "why": (
                    "table_meta.writers 为空：该表没有可重跑的采集任务"
                    "（历史导入 / 自产 / 已停更），需人工处置")})
                continue

            for task in writers:
                if task in NEVER_REPAIR_TASKS:
                    manual.append({**base, "why": f"写入方 {task} 属收敛/体检自身，不自动重跑"})
                    continue
                if task not in schedulable:
                    unmapped.append({**base, "why": f"写入方 {task} 未注册或已禁用，无法自动执行"})
                    continue
                floor, n_fires = self.release_floor(task, now_local)
                if floor is not None and now_local < floor:
                    pending.append({**base, "task": task,
                                    "release_at": floor.strftime("%H:%M"),
                                    "fires_today": n_fires,
                                    "why": (f"写入方 {task} 当日末次班次 {floor:%H:%M} 尚未到点"
                                            f"（今日共 {n_fires} 次）：当日数据还没发布，"
                                            "等它自己的班次即可，收敛器不抢跑")})
                    continue
                tg = targets.get(task)
                if tg is None:
                    tg = targets[task] = RepairTarget(task=task)
                tg.rules.append(rule)
                tg.tables.append(table)
                tg.checkers.append(ct)
                tg.reasons.append(f"{table}.{rule}（{status}）")

        out = Plan(
            targets=sorted(targets.values(), key=lambda t: (-len(t.rules), t.task)),
            manual=manual,
            unmapped=unmapped,
            pending=pending,
        )
        logger.info(
            f"🩺 启动收敛分诊：可自愈 {len(out.targets)} 个任务 / 待发布 {len(pending)} 条 / "
            f"需人工 {len(manual)} 条 / 定位不到 {len(unmapped)} 条"
        )
        for t in out.targets:
            logger.info(f"   ↳ 待重跑 {t.task:<26} 因 {', '.join(sorted(set(t.rules))[:4])}"
                        + ("…" if len(t.rules) > 4 else ""))
        for pd in pending[:6]:
            logger.info(f"   ⏳ 待发布 {pd.get('task'):<26} 末次班次 {pd.get('release_at')}"
                        f"（{pd.get('rule_name')}）")
        return out

    # ---------- 队列合并（与 catchup 的「时间遗漏」去重） ----------

    @staticmethod
    def merge_todo(missed: list[dict], plan: Plan) -> list[dict]:
        """把「时间遗漏班次」与「DQ 缺口任务」合并为**一条串行队列**

        去重键 = 任务名。重叠的任务只跑一次，但会标注它同时被两个依据选中
        （source='missed+dq'），便于事后判读「这次补跑到底是哪个依据救回来的」。
        """
        merged: dict[str, dict] = {}
        for m in missed or []:
            name = m.get("task")
            if not name:
                continue
            merged[name] = {
                "task": name,
                "source": "missed",
                "missed_at": m.get("missed_at"),
                "rules": [],
            }
        for t in plan.targets if plan else []:
            if t.task in merged:
                merged[t.task]["source"] = "missed+dq"
                merged[t.task]["rules"] = sorted(set(t.rules))
            else:
                merged[t.task] = {
                    "task": t.task,
                    "source": "dq",
                    "missed_at": None,
                    "rules": sorted(set(t.rules)),
                }
        # 排序：先按遗漏时刻（有则升序），无时刻的（纯 DQ 缺口）排在其后
        items = list(merged.values())
        items.sort(key=lambda x: (x["missed_at"] is None, x["missed_at"] or "", x["task"]))
        return items

    # ---------- ④ 执行（带 ⑤ 止损） ----------

    def repair(
        self,
        todo: list[dict],
        execute: Callable[[str], dict],
        *,
        max_extra: int | None = None,
    ) -> dict:
        """串行执行合并后的启动队列（时间遗漏 ∪ DQ 缺口，已按任务名去重）

        · `execute(task_name) -> {"started": bool, "reason"/"records_written": ...}`
          （注入 `SchedulerManager._execute`，自动获得「同任务运行中保护 ＋
           进程内互斥锁」的双层并发安全，故与 cron 不会双跑）
        · **预算（个数 + 时间）只约束 source='dq' 的项**
          `missed` / `missed+dq` 属原有启动补偿语义（补齐关机期间错过的班次），
          不受额外预算限制 —— 那些是**必需**的，跑得慢也该跑完；
          `dq` 是本次新增的「按水位修复」，是**可选优化**，必须限量 ——
          否则一张长期故障的表会把它变成重跑风暴。
        · 两道预算**任一触发即停**，且都在「执行下一个之前」判定（不会打断进行中的任务）：
            - `max_repairs` 个数维度：防重跑风暴打爆上游
            - `max_seconds` 时间维度：防占用启动窗口（单任务耗时实测 1s ~ 1722s，
              只卡个数根本防不住「10 个任务跑了 5 小时」）
        · 超预算的**不执行**，原样记入 `deferred` 交人工。这是**止损**，不是失败。
        """
        budget = self.max_repairs if max_extra is None else max(0, int(max_extra))
        executed: list[dict] = []
        deferred: list[dict] = []
        ok = 0
        t0 = time.monotonic()

        for it in todo or []:
            name = it.get("task")
            if not name:
                continue
            src = it.get("source") or "missed"
            is_extra = src == "dq"
            rec = {"task": name, "source": src,
                   "rules": it.get("rules") or [], "missed_at": it.get("missed_at")}
            if is_extra:
                elapsed = time.monotonic() - t0
                if budget <= 0:
                    deferred.append({**rec, "why": (
                        f"超出单次修复上限 {self.max_repairs} 个，留待人工")})
                    continue
                if self.max_seconds and elapsed >= self.max_seconds:
                    deferred.append({**rec, "why": (
                        f"超出单次修复时间预算 {self.max_seconds}s"
                        f"（已用 {elapsed:.0f}s），留待人工")})
                    continue
                budget -= 1
            if self.dry_run:
                executed.append({**rec, "started": False, "why": "dry_run：只分诊不执行"})
                continue
            try:
                res = execute(name) or {}
            except Exception as e:  # noqa: BLE001 - 单任务失败不阻断整轮收敛
                logger.exception(f"启动收敛：执行 {name} 异常")
                executed.append({**rec, "started": True, "error": str(e)[:200]})
                continue
            if res.get("started"):
                ok += 1
                executed.append({**rec, "started": True,
                                 "records_written": res.get("records_written")})
                logger.info(f"✅ 启动收敛执行 {name}"
                            f"（写入 {res.get('records_written')} 条，依据 {src}）")
            else:
                executed.append({**rec, "started": False,
                                 "why": res.get("reason") or "未执行"})
                logger.info(f"⏭ 启动收敛跳过 {name}：{res.get('reason')}")

        return {"executed": executed, "deferred": deferred, "repaired_tasks": ok,
                "elapsed_sec": round(time.monotonic() - t0, 1)}

    # ---------- ⑥ 验证：复检 diff ----------

    @staticmethod
    def diff_rules(before: list[dict], after: list[dict],
                   attempted_rules: set[str] | None = None) -> dict:
        """对比修复前后的逐规则状态，分出「已修复 / 仍不合格 / 新出现」

        只用「同一批规则」对比（键 = rule_name），避免 daily/weekly 轮次差异造成假 diff。

        `attempted_rules`：本轮**真的被执行过重跑**的那些规则名（来自 repair.executed）。
        用来给 `still_failing` 每项打 `attempted` 标记 —— 否则「从未尝试修复」的
        manual 类规则会混进「复检仍不合格」里，让自愈显得比实际更没用
        （2026-10-08 首次真实启动实测：仍不合格 3 条里 2 条根本没试过）。
        传 None 表示不区分，此时 `attempted` 一律为 None（保持向后兼容）。
        """
        b = {r["rule_name"]: r for r in (before or [])}
        a = {r["rule_name"]: r for r in (after or [])}
        healed, still, fresh = [], [], []
        for name, br in b.items():
            if br.get("status") == "pass":
                continue          # 修复前就合格，不参与
            ar = a.get(name)
            ar_status = ar.get("status") if ar else None
            item = {
                "rule_name": name,
                "table_name": br.get("table_name"),
                "check_type": br.get("check_type"),
                "before": br.get("status"),
                "after": ar_status,
                "attempted": (name in attempted_rules) if attempted_rules is not None else None,
                "message": ((ar.get("message") if ar else br.get("message")) or "")[:200],
            }
            if ar_status == "pass":
                healed.append(item)
            elif ar_status is None:
                still.append({**item, "after": "未复检"})
            else:
                still.append(item)
        for name in set(a) - set(b):
            ar = a[name]
            if ar.get("status") in ("fail", "warning"):
                fresh.append({"rule_name": name, "table_name": ar.get("table_name"),
                              "status": ar.get("status"),
                              "message": (ar.get("message") or "")[:200]})
        return {"healed": healed, "still_failing": still, "emerged": fresh}

    # ---------- ⑦ 交代：报告 ----------

    def build_report(
        self,
        *,
        before: list[dict],
        plan: Plan,
        repair_result: dict,
        after: list[dict] | None,
        missed: list[dict] | None,
        started_at: datetime,
        finished_at: datetime,
        note: str = "",
    ) -> dict:
        """组装落库报告（写进 task_runs.run_detail，供前端「作业监控」与收敛摘要条展示）"""
        counts = {"pass": 0, "warning": 0, "fail": 0, "error": 0}
        for r in before or []:
            s = r.get("status")
            if s in counts:
                counts[s] += 1
        after_counts = None
        if after is not None:
            after_counts = {"pass": 0, "warning": 0, "fail": 0, "error": 0}
            for r in after:
                s = r.get("status")
                if s in after_counts:
                    after_counts[s] += 1
        # 「真的被执行过重跑」的规则集 —— 只取 repair.executed（已实际跑的任务），
        # 不含 deferred（超限未跑）。用于把「修了但没修好」与「压根没试过的 manual」
        # 在报告里分开计数，避免后者虚增「仍不合格」。
        attempted: set[str] = set()
        for ex in (repair_result.get("executed") or []):
            attempted.update(ex.get("rules") or [])
        diff = self.diff_rules(before, after, attempted_rules=attempted) if after is not None else {
            "healed": [], "still_failing": [], "emerged": []}
        # 尝试过但仍不合格 = 真·修复未果；未尝试 = 归人工侧（不计入「自愈没修好」）
        still_attempted = [x for x in diff["still_failing"] if x.get("attempted")]
        still_unattempted = [x for x in diff["still_failing"] if not x.get("attempted")]

        steps = [
            {"no": 1, "name": "启动巡检", "params": self.scan_params,
             "value": f"不合格 {counts['fail'] + counts['warning']} 条（fail {counts['fail']} / warning {counts['warning']}）"},
            {"no": 2, "name": "缺口定位", "params": "table_meta.writers：表 → 写入任务",
             "value": (f"可自愈 {len(plan.targets)} 个任务 / 待发布 {len(plan.pending)} 条 / "
                       f"需人工 {len(plan.manual)} 条 / 定位不到 {len(plan.unmapped)} 条")},
            {"no": 3, "name": "合并补偿队列", "params": "时间遗漏班次 ∪ DQ 缺口，按任务名去重",
             "value": f"遗漏 {len(missed or [])} 项，合并后待跑 {len(self.merge_todo(missed or [], plan))} 项"},
            {"no": 4, "name": "串行修复",
             "params": f"重跑 writers（个数上限 {self.max_repairs} / 时间预算 {self.max_seconds}s，同任务仅一次）",
             "value": f"实际执行 {repair_result.get('repaired_tasks', 0)} 个 / 顺延待人工 {len(repair_result.get('deferred', []))} 个"
                      + (f" / 用时 {repair_result['elapsed_sec']}s" if repair_result.get("elapsed_sec") is not None else "")},
            {"no": 5, "name": "复检", "params": "修复后再跑一轮体检，逐规则 diff",
             "value": (f"已修复 {len(diff['healed'])} 条 / 尝试后仍未修好 {len(still_attempted)} 条"
                       f" / 未尝试（归人工） {len(still_unattempted)} 条"
                       if after is not None else "未复检")},
            {"no": 6, "name": "出报告", "params": "写 task_runs.run_detail",
             "value": "完成"},
        ]
        duration = max(0.0, (finished_at - started_at).total_seconds())
        return {
            "note": note or f"启动收敛完成（{duration:.0f}s）",
            "converge_started_at": started_at.strftime("%Y-%m-%d %H:%M:%S"),
            "converge_finished_at": finished_at.strftime("%Y-%m-%d %H:%M:%S"),
            "converge_duration_sec": round(duration, 1),
            "before_counts": counts,
            "after_counts": after_counts,
            "issues_total": counts["fail"] + counts["warning"],
            "plan": [t.to_dict() for t in plan.targets],
            "manual_todo": plan.manual,
            "unmapped": plan.unmapped,
            # 「待发布」= 它的写入任务当日末次班次还没到点 ⇒ 当日数据还没发出来。
            # 这类**不是故障也不算需人工**：等它自己的班次即可。单独成列是为了
            # 让「不合格 N 条」不被误读成「N 条都出问题了」。
            "pending_todo": plan.pending,
            "pending_count": len(plan.pending),
            "repair": repair_result,
            "verify": diff,
            "healed_count": len(diff["healed"]),
            "still_failing_count": len(diff["still_failing"]),
            # 尝试过却没修好的条数 —— 这才是「自愈没修好」的真实口径；
            # still_failing_count 含未尝试项，两者之差即「本轮压根没碰的」
            "still_failing_attempted_count": len(still_attempted),
            "still_failing_unattempted_count": len(still_unattempted),
            "needs_human_count": len(plan.manual) + len(plan.unmapped) + len(repair_result.get("deferred", [])),
            "run_steps": steps,
        }
