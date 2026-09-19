#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
InvestBuddy 定时调度器（APScheduler）

- 消费 task_config 表中 enabled=1 且 cron 合法（5 字段 crontab）的任务
- 只调度 TASKS 注册表中有实现的任务（未实现任务跳过，避免空跑）
- 配置修改（PUT /api/jobs/tasks/{name}）后调用 sync_from_db() 热生效
- 立即执行（POST /api/jobs/tasks/{name}/trigger）走独立线程 + TaskRecorder 记录，
  带运行中保护：同任务已有 running 记录（2 小时内）则拒绝，避免并发双写

🔒 双重防重（2026-09-19 修复）：
  ① 进程内互斥锁 `_task_lock()` —— 见 `_execute()` 文档，解决链式线程 / 启动补跑线程 /
     调度 worker **同进程并发**调用 `_execute` 时的「先查后跑」竞态（实测产出同秒重复行）；
  ② DB 层 `_running_count()` —— 覆盖跨进程 / 重启残留的 running 记录。
  两者互补，缺一不可。
"""
import logging
import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from .db import query_all

logger = logging.getLogger("infodata.scheduler")

# 业务时区：A 股与 cron 均按上海时间，DB 时间戳亦为本地(=上海)写入，
# 补偿判定显式锁定该时区，避免依赖系统默认时区
TZ = ZoneInfo("Asia/Shanghai")

# 手动 cron 标识（task_config 中 cron='手动' 表示不自动调度）
MANUAL_MARK = "手动"
# running 记录视为「仍在运行」的时间窗口（超过视为遗留脏记录，允许重跑）
RUNNING_STALE_HOURS = 2

# 开机后追加资讯回看的判定阈值（小时）。与 collectors/news_fetch 的
# BACKFILL_TRIGGER_HOURS 同口径：资讯水位线比现在旧这么多，说明关机/睡眠时长
# 已超出采集器的常规滚动窗口，需要开机即补一次。
# 为什么单独做这件事：news_fetch 是 */30 的高频任务，被 CATCHUP_MIN_INTERVAL(2h)
# 挡在启动补偿之外（设计如此，高频任务补跑会堆积）；而资讯是唯一「离线即永久
# 丢失」的数据通道（日线可逐股补 500 天、指数回看 7 天、物化表可全史重算），
# 因此不能像别的任务那样「等下一个班次」——开机后立即补一次，把缺口窗口压到最小。
BOOT_NEWS_REVIEW_TASK = "news_fetch"
BOOT_NEWS_REVIEW_STALE_HOURS = 2

# 错过多少秒内仍补跑。原值 3600（1h）会让「睡眠超过 1 小时」的班次被 APScheduler 静默丢弃
# （连日志都不留）。放宽到 6h 覆盖「头天没开机、次日才开机」的场景；coalesce=True 保证
# 错过多次也只补一次，不会堆积。仍不设 None——唤醒瞬间全部 job 同时到期会造成
# ThreadPoolExecutor(10) 并发风暴。
MISFIRE_GRACE_SECONDS = 21600

# ============================================================================
# 链式触发（2026-09-17）：把「下游等固定时刻」改成「上游完成即接力」
#
# 动机：stock_daily_incr 常态耗时 15~52 分钟，任何固定时刻都会赌输。两次实测：
#   09-16  快照在日线只跑了一半时写入（2820/5119 行），连锁致 stock_info_sync 的
#          名单护栏连续 6 个班次失败，A 股名单停更；
#   09-17  物化表 20:05 报 success 写入 5254 行，实为**上一交易日**的数据
#          （护栏把上界退回），白跑一次却显示成功，前端看到的仍是昨天。
# 链式触发后，数据就绪时间 = 「日线完成 + 数分钟」，固定时刻降级为兜底。
# ============================================================================
CHAIN_NEXT: dict[str, list[str]] = {
    "stock_daily_incr": ["market_style_sync"],
    "market_style_sync": ["market_current_sync"],
    "market_current_sync": ["data_quality_check"],
}

# 兜底班次清单：这些任务的固定时刻只在「当日链式未跑成」时才真正执行。
# 判定看当日是否已有 success —— blocked（数据未就绪）不算跑成，故仍允许兜底再试一次。
CHAIN_FALLBACK_TASKS: set[str] = {"market_style_sync", "market_current_sync", "data_quality_check"}


def parse_cron(cron: str):
    """解析 5 字段 crontab，非法返回 None"""
    if not cron or cron.strip().lower() in (MANUAL_MARK, "none", "-"):
        return None
    try:
        return CronTrigger.from_crontab(cron.strip())
    except ValueError as e:
        logger.warning(f"cron 表达式非法: {cron!r} -> {e}")
        return None


# ============================================================================
# ⚠️ 星期语义陷阱（2026-09-14 事故根因，务必记住）
#
# APScheduler 的 `CronTrigger.from_crontab()` **不做任何语义翻译**，其
# day_of_week 字段直接沿用 APScheduler 自身的编号：**0=周一、6=周日**。
# 这与 Unix crontab「0=周日、1=周一…6=周六」**完全相反**。
#
# 后果实测：`20 22 * * 1-5` 在 Unix 语义下是「工作日」，在 APScheduler 下却是
# **周二~周六**——futures_sync 因此周一漏跑、周六空跑（2026-09-14 发现）。
# 反过来 `0-4` 才等于周一~周五。
#
# 故本模块统一提供 `cron_human()` 把 cron 渲染成中文，并在调度同步时逐条打日志、
# 在 /api/jobs 与数据流卡里回传，让「写 cron 的人」和「读 cron 的人」都能立刻
# 看到真实语义，避免继续凭直觉写数字。
# ============================================================================
_WEEK_CN = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]  # APScheduler: 0=周一


def _week_span(token: str) -> str | None:
    """把 day_of_week 字段渲染为中文（None 表示无法识别）"""
    token = token.strip()
    if not token or token == "*":
        return None

    def _one(t: str) -> str | None:
        t = t.strip()
        if not t.isdigit():
            return None
        i = int(t)
        return _WEEK_CN[i] if 0 <= i <= 6 else None

    if "-" in token:
        a, _, b = token.partition("-")
        wa, wb = _one(a), _one(b)
        return f"{wa}至{wb}" if wa and wb else None
    parts = [_one(p) for p in token.split(",")]
    return "、".join(p for p in parts if p) if all(parts) else None


def _dom_span(token: str) -> str | None:
    """把 day_of_month 字段渲染为中文（None 表示每天）"""
    token = token.strip()
    if not token or token == "*":
        return None
    if token.isdigit():
        return f"每月{int(token)}日"
    if "," in token and all(p.strip().isdigit() for p in token.split(",")):
        return "每月" + "、".join(str(int(p)) for p in token.split(",")) + "日"
    return None


def cron_human(cron: str | None) -> str:
    """cron → 中文可读调度描述（用于日志/接口展示，消除 day_of_week 语义误读）。

    例：
      '0 19 * * 0-4'  -> '每周一至周五 19:00'
      '30 21 * * 0'   -> '每周一 21:30'
      '0 10 * * 6'    -> '每周日 10:00'
      '20 22 * * 1-5' -> '每周二至周六 22:20'   ← 就是这个语义坑
      '*/30 * * * *'  -> '每 30 分钟'
      '30 20 * * *'   -> '每天 20:30'
      '0 3 1,15 * *'  -> '每月1、15日 03:00'
      '手动'           -> '手动触发'
    无法识别时原样返回 cron。
    """
    if not cron:
        return "手动触发"
    raw = cron.strip()
    if raw.lower() in (MANUAL_MARK, "none", "-"):
        return "手动触发"
    parts = raw.split()
    if len(parts) != 5:
        return raw
    mi, ho, dom, mon, dow = parts

    # 高频形态：分钟 */N 且其余为 *
    if mi.startswith("*/") and mi[2:].isdigit() and ho == dom == mon == dow == "*":
        return f"每 {int(mi[2:])} 分钟"

    if not mi.isdigit():
        return raw
    # 小时支持逗号列表（如 '0 20,22 * * 0-4' = 每天 20:00、22:00 两班）
    hours = ho.split(",")
    if not hours or not all(h.strip().isdigit() for h in hours):
        return raw
    hhmm = "、".join(f"{int(h):02d}:{int(mi):02d}" for h in hours)

    dom_txt = _dom_span(dom)
    dow_txt = _week_span(dow)
    month_txt = f"{int(mon)} 月" if mon.isdigit() else None

    if dom_txt:
        prefix = dom_txt
        if month_txt:
            prefix = f"每年{month_txt}的{dom_txt[2:]}".replace("每月", "")
    elif dow_txt:
        prefix = f"每{dow_txt}"
    elif dow == "*":
        prefix = "每天"
    else:
        return raw
    return f"{prefix} {hhmm}"


class SchedulerManager:
    """全局单例：管理 APScheduler 与 task_config 的同步"""

    def __init__(self):
        self._scheduler: BackgroundScheduler | None = None
        self._locks: dict[str, threading.Lock] = {}
        self._lock_guard = threading.Lock()

    # ---------- 生命周期 ----------

    @property
    def running(self) -> bool:
        return self._scheduler is not None and self._scheduler.running

    def start(self):
        if self.running:
            return
        self._scheduler = BackgroundScheduler(timezone="Asia/Shanghai")
        self._scheduler.start()
        # 启动自愈：上一进程被中断留下的 running 记录必须先回收，否则
        # 「同任务 2h 内 running 视为仍在运行」的保护会让这些任务在 2h 内拒绝重跑，
        # 且 catchup_missed 也会被同一保护挡住（见 reap_orphan_runs 文档）
        try:
            self.reap_orphan_runs()
        except Exception:
            logger.exception("启动自愈（僵尸 running 回收）异常")
        self.sync_from_db()
        # 启动补偿：电脑关机/睡眠期间错过的 cron 班次，开机后自动补跑
        try:
            self.catchup_missed()
        except Exception:
            logger.exception("启动补偿执行异常")
        logger.info("🕒 调度器已启动")

    def reap_orphan_runs(self) -> int:
        """把上一进程遗留的 running 记录标记为 failed（启动自愈），返回回收条数。

        为什么必须做（2026-09-14 复盘）：uvicorn 未开 --reload，重启/被杀会中断正在
        执行的任务，被中断的 run 会永久停在 running 状态。而调度器的运行中保护是
        「同任务 2h 内有 running 记录则跳过本次触发」——于是被中断的任务在重启后
        **2 小时内无法重跑**，连 catchup_missed 补跑也会被挡掉。
        实测代价：`daily_recon_window` 在 09-11 20:00 起的 run 被中断后留下僵尸记录，
        导致 09-11、09-12 连续两个 20:45 计划班次被静默跳过（历史 4 次运行全是
        手工/中断，从未按 cron 自然跑成）。

        判定依据：本方法只在 SchedulerManager.start() 中调用，此刻进程刚起，
        **不可能存在由本进程发起的 running 记录**，因此全部视作僵尸，可安全回收。
        （原先靠人工执行 scripts/fix_stale_running.py，属「需要人记得做」的脆弱流程。）
        """
        from .db import _connect  # noqa: PLC0415

        conn = _connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT id, task_name, started_at FROM task_runs WHERE status='running'")
                zombies = cur.fetchall()
                if zombies:
                    cur.execute(
                        "UPDATE task_runs SET status='failed', finished_at=NOW(), "
                        "error_message=CONCAT(COALESCE(error_message,''), %s) WHERE status='running'",
                        ("[启动自愈] 进程重启中断，已自动标记为 failed（非任务本身失败）",),
                    )
            conn.commit()
            if zombies:
                names = ", ".join(f"{z[1]}#{z[0]}" for z in zombies[:8])
                logger.warning(f"🧹 启动自愈：回收僵尸 running 记录 {len(zombies)} 条 → {names}")
            return len(zombies)
        finally:
            conn.close()

    def shutdown(self):
        if self._scheduler and self._scheduler.running:
            self._scheduler.shutdown(wait=False)
        self._scheduler = None
        logger.info("🕒 调度器已停止")

    # ---------- 配置同步 ----------

    def _known_tasks(self) -> set[str]:
        """TASKS 注册表中有实现的任务名（延迟 import 避免启动加载 akshare）"""
        from .tasks.run import TASKS  # noqa: PLC0415

        return set(TASKS.keys())

    def sync_from_db(self) -> dict:
        """按 task_config 全量重建调度。返回 {scheduled: [...], skipped: [...], errors: [...]}"""
        if self._scheduler is None:
            self._scheduler = BackgroundScheduler(timezone="Asia/Shanghai")
            self._scheduler.start()
        known = self._known_tasks()
        rows = query_all(
            "SELECT task_name, enabled, cron, params FROM task_config ORDER BY task_name"
        )
        want: dict[str, dict] = {}
        for r in rows:
            name = r["task_name"]
            cron = (r["cron"] or "").strip()
            if not r["enabled"] or cron.lower() == MANUAL_MARK or cron.lower() == "none":
                continue  # 不参与调度
            if name not in known:
                logger.info(f"任务 {name} 无实现，调度跳过")
                continue
            want[name] = {"cron": cron, "params": r["params"]}

        # 移除已不需要的任务
        for job_id in [j.id for j in self._scheduler.get_jobs()]:
            if job_id not in want:
                self._scheduler.remove_job(job_id)

        # 新增 / 更新调度
        scheduled, errors = [], []
        for name, cfg in want.items():
            trigger = parse_cron(cfg["cron"])
            if trigger is None:
                errors.append(name)
                continue
            job_id = name
            if self._scheduler.get_job(job_id):
                self._scheduler.reschedule_job(job_id, trigger=trigger)
            else:
                self._scheduler.add_job(
                    self._run_scheduled,
                    trigger=trigger,
                    id=job_id,
                    args=[name],
                    coalesce=True,          # 错过多次只补跑一次
                    max_instances=1,        # 同任务不并发
                    misfire_grace_time=MISFIRE_GRACE_SECONDS,  # 错过 6h 内仍补跑（如休眠期）
                    replace_existing=True,
                )
            scheduled.append(name)
        # 逐条打印中文调度语义：APScheduler 的 day_of_week 是 0=周一（与 Unix crontab
        # 的 0=周日相反），写错 `1-5` 会静默变成「周二~周六」。把 cron 渲染成中文可肉眼复核。
        for name in sorted(scheduled):
            logger.info(f"  ↳ {name:<26} {want[name]['cron']:<16} {cron_human(want[name]['cron'])}")
        logger.info(f"调度同步完成: 计划 {len(scheduled)} 个任务, 解析失败 {errors}")
        return {"scheduled": scheduled, "errors": errors}

    # ---------- 任务执行 ----------

    @staticmethod
    def _succeeded_today(task_name: str) -> bool:
        """该任务今日是否已有 success 记录（兜底班次据此跳过重复执行）"""
        from .db import _connect  # noqa: PLC0415

        conn = _connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM task_runs "
                    "WHERE task_name=%s AND status='success' AND finished_at >= CURDATE()",
                    (task_name,),
                )
                return (cur.fetchone()[0] or 0) > 0
        finally:
            conn.close()

    def _run_scheduled(self, task_name: str, force: bool = False):
        """APScheduler 触发的任务入口（异常必须吞掉，避免 scheduler 内部报错）

        force=True（API 手动触发）跳过兜底去重检查，保证人工触发一定执行。
        """
        try:
            if not force and task_name in CHAIN_FALLBACK_TASKS and self._succeeded_today(task_name):
                logger.info(f"⏭ 兜底班次跳过：{task_name} 当日已有成功记录（链式已跑成）")
                return
            self._execute(task_name)
        except Exception as e:
            logger.exception(f"定时任务 {task_name} 执行异常: {e}")

    def _running_count(self, task_name: str) -> int:
        """查询该任务 2 小时内的 running 记录数"""
        from .db import _connect  # noqa: PLC0415

        conn = _connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM task_runs WHERE task_name=%s AND status='running' AND started_at >= %s",
                    (task_name, datetime.now() - timedelta(hours=RUNNING_STALE_HOURS)),
                )
                return cur.fetchone()[0]
        finally:
            conn.close()

    def _task_lock(self, task_name: str) -> threading.Lock:
        """取该任务的进程内互斥锁（惰性创建，进程生命周期内复用）"""
        with self._lock_guard:
            lk = self._locks.get(task_name)
            if lk is None:
                lk = threading.Lock()
                self._locks[task_name] = lk
            return lk

    def _execute(self, task_name: str) -> dict:
        """单次执行（含运行中保护 + 进程内互斥），返回 {started, reason}

        🔒 为什么必须加进程内锁（2026-09-19 修复真实重复派发）：
        `_running_count()` 是「先查后跑」（TOCTOU）——查的时候还没有 running 行，
        真正 INSERT running 发生在 `run_task()` 内部的 `TaskRecorder.start()`，
        这中间没有任何互斥。于是**同一进程内的多个触发源会同时通过检查**：
          - 链式线程：`_run_chain` → `_execute(下游)`
          - 启动补跑线程：`_run_catchup` → `_execute(todo[i])`
          - APScheduler worker：`_run_scheduled` → `_execute`
        实测证据（task_runs 里 started_at/finished_at **同秒**的重复行，全库共 4 组 5 行）：
          09-19 07:36:45 catchup 串行补到 `market_style_sync` → 完成后起链式线程接力
          `market_current_sync`；而 catchup 自己的 todo 队列下一步**也是**
          `market_current_sync` → 两个调用者同时通过检查 → 双份执行（id 482/483）；
          两份 `market_current_sync` 又各自接力 `data_quality_check`（2 线程）
          ＋ catchup 队列里的 `data_quality_check` 自身（1）→ **三份并发**（id 484/485/486）。
        锁按 task_name 取（非全局），所以不同任务仍可并行，不牺牲调度吞吐。
        取锁用 blocking=False：已在跑就直接跳过本次触发（与 running 保护语义一致）。

        注：本锁只覆盖**本进程内**的竞态；跨进程/重启残留仍由 `_running_count`
        与 `task_stale_running` 规则兜底，故两者保留、不互相替代。
        """
        lock = self._task_lock(task_name)
        if not lock.acquire(blocking=False):
            return {"started": False, "reason": "同任务正在执行中（进程内互斥锁），已跳过本次触发"}
        try:
            if self._running_count(task_name) > 0:
                return {"started": False, "reason": "同任务运行中，已跳过本次触发"}
            from .tasks.run import run_task  # noqa: PLC0415

            written = run_task(task_name)
            # 链式触发：本任务成功后接力下游。上游失败或 blocked 会抛异常，
            # 根本走不到这里 —— 这正是期望行为：数据没就绪就不接力。
            downstream = CHAIN_NEXT.get(task_name)
            if downstream:
                threading.Thread(
                    target=self._run_chain, args=(task_name, list(downstream)), daemon=True
                ).start()
            return {"started": True, "records_written": written}
        finally:
            lock.release()

    def _run_chain(self, upstream: str, downstream: list[str]):
        """上游成功后按依赖顺序接力下游（独立线程，不占用调度器 worker）

        每级都经 _execute 调用，成功后自动继续接力下一级（自然递归）。
        任一级失败 / blocked 即中止整条链，等兜底班次或下次开机补偿再试。
        """
        logger.info(f"🔗 链式触发：{upstream} 已完成 → 接力 {downstream}")
        for name in downstream:
            try:
                res = self._execute(name)
                if not res.get("started"):
                    logger.warning(f"🔗 链式接力 {name} 未启动，链条中止：{res.get('reason')}")
                    break
                logger.info(f"🔗 链式接力 {name} 完成")
            except Exception as e:
                logger.warning(f"🔗 链式接力 {name} 未完成，链条中止：{e}")
                break

    def trigger_now(self, task_name: str) -> dict:
        """立即执行（异步线程），供 API 调用"""
        if task_name not in self._known_tasks():
            return {"started": False, "reason": f"任务 {task_name} 无执行实现"}
        if self._running_count(task_name) > 0:
            return {"started": False, "reason": "任务正在运行中，请稍后再试"}
        t = threading.Thread(target=self._run_scheduled, args=(task_name, True), daemon=True)
        t.start()
        return {"started": True, "reason": "已提交执行，请到运行记录查看进度"}

    # ---------- 启动补偿（关机/睡眠错过班次） ----------

    # 触发间隔低于该值的高频任务（如 news_fetch */30）不参与补偿，等下次 cron 即可
    CATCHUP_MIN_INTERVAL = timedelta(hours=2)
    # cron 触发点回看窗口（覆盖每日/每周/每月任务）
    CATCHUP_LOOKBACK_DAYS = 45

    def _recent_fire_times(self, trigger, now: datetime, lookback_days: int | None = None) -> list:
        """正向迭代 cron 触发点，返回 (now - lookback, now] 窗口内的触发序列（升序）"""
        lookback_days = lookback_days or self.CATCHUP_LOOKBACK_DAYS
        t0 = now - timedelta(days=lookback_days)
        fires: list = []
        prev, cur = None, t0
        while True:
            nxt = trigger.get_next_fire_time(prev, cur)
            if nxt is None or nxt > now:
                break
            fires.append(nxt)
            prev, cur = nxt, nxt + timedelta(seconds=1)
        return fires

    @staticmethod
    def _last_success_at(task_name: str) -> datetime | None:
        """该任务最近一次 success 的 finished_at（无则 None）"""
        from .db import _connect  # noqa: PLC0415

        conn = _connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT MAX(finished_at) FROM task_runs WHERE task_name=%s AND status='success'",
                    (task_name,),
                )
                row = cur.fetchone()
                return row[0] if row and row[0] else None
        finally:
            conn.close()

    def catchup_missed(self) -> dict:
        """启动补偿：cron 最近应触发点已过去、且晚于上次成功时间 → 该班次遗漏，补跑一次。

        判定天然防重复/防堆积：
        - 只补「最近一个未覆盖的触发点」，多天关机也只补一次（不会逐天堆积）
        - 周末/长假：非当日触发点的任务（如交易日任务）的最近触发点若已被覆盖则跳过
        - 高频任务（触发间隔 < CATCHUP_MIN_INTERVAL）不参与
        - 执行走 _execute（带同任务 running 保护），与 cron 并发也安全
        """
        if self._scheduler is None or not self._scheduler.running:
            return {"checked": 0, "todo": []}
        now = datetime.now(TZ)  # 上海时区，与 CronTrigger(Asia/Shanghai) 及 DB 时间一致
        now_naive = now.replace(tzinfo=None)
        jobs = self._scheduler.get_jobs()
        todo: list[dict] = []
        for job in jobs:
            name = job.id
            try:
                fires = self._recent_fire_times(job.trigger, now)
            except Exception:
                continue
            if not fires:
                continue
            last = fires[-1]  # 最近一次应触发点
            interval = (last - fires[-2]) if len(fires) >= 2 else timedelta(days=999)
            if interval < self.CATCHUP_MIN_INTERVAL:
                continue  # 高频任务跳过
            last_naive = last.astimezone(TZ).replace(tzinfo=None)
            if last_naive > now_naive:
                continue  # 应触发点在未来，还没到点
            last_ok = self._last_success_at(name)
            if last_ok is not None and last_ok >= last_naive:
                continue  # 该班次已有 success 覆盖
            todo.append({"task": name, "missed_at": last_naive.strftime("%Y-%m-%d %H:%M")})
        if not todo:
            logger.info(f"启动补偿：检查 {len(jobs)} 个任务，无遗漏班次")
            self._spawn_boot_news_review()
            return {"checked": len(jobs), "todo": []}
        todo.sort(key=lambda x: x["missed_at"])
        logger.info(
            f"启动补偿：发现 {len(todo)} 个遗漏班次 → {[t['task'] for t in todo]}，已排队补跑"
        )
        threading.Thread(target=self._run_catchup, args=(todo,), daemon=True).start()
        return {"checked": len(jobs), "todo": todo}

    def _spawn_boot_news_review(self):
        """后台线程执行开机资讯回看（不阻塞 start()）"""
        threading.Thread(target=self.review_news_after_boot, daemon=True).start()

    def review_news_after_boot(self) -> dict:
        """开机后追加一次资讯回看（见 BOOT_NEWS_REVIEW_STALE_HOURS 处的说明）。

        只回答「该不该跑」，不做去重：幂等性由采集器自身保证
        （(title, published_at) 判重 + 以库内 MAX(published_at) 为水位线往前回看）。
        水位线新鲜时不跑，避免「正常重启」也白跑一轮。
        """
        row = query_all("SELECT MAX(published_at) AS mx FROM news")
        watermark = row[0]["mx"] if row else None
        if watermark is None:
            logger.info("开机资讯回看：news 表为空，直接补采一次")
            age_h = None
        else:
            age_h = (datetime.now(TZ).replace(tzinfo=None) - watermark).total_seconds() / 3600
            if age_h < BOOT_NEWS_REVIEW_STALE_HOURS:
                logger.info(
                    f"开机资讯回看：水位线 {watermark} 距今 {age_h:.1f}h（< "
                    f"{BOOT_NEWS_REVIEW_STALE_HOURS}h），无需补采"
                )
                return {"ran": False, "watermark": str(watermark), "age_hours": round(age_h, 1)}
            logger.info(
                f"开机资讯回看：水位线 {watermark} 距今 {age_h:.1f}h，追加一次资讯补采"
            )
        try:
            res = self._execute(BOOT_NEWS_REVIEW_TASK)
            logger.info(f"开机资讯回看完成: {res}")
            return {"ran": True, "watermark": str(watermark) if watermark else None,
                    "age_hours": round(age_h, 1) if age_h is not None else None, "result": res}
        except Exception as e:
            logger.exception(f"开机资讯回看失败: {e}")
            return {"ran": False, "error": str(e)}

    def _run_catchup(self, todo: list[dict]):
        """后台串行补跑（按遗漏时刻升序），单任务 running 保护自动防重"""
        for it in todo:
            try:
                self._execute(it["task"])
                logger.info(f"启动补偿完成: {it['task']}（遗漏 {it['missed_at']}）")
            except Exception as e:
                logger.exception(f"启动补偿失败: {it['task']}: {e}")
        # 补跑全部收尾后再补资讯：避免与补跑抢连接，且刻意排到最后——
        # 资讯是唯一不可回补的通道，优先级的表达方式是「保证它一定会跑」。
        self._spawn_boot_news_review()

    # ---------- 状态查询 ----------

    def list_status(self) -> list[dict]:
        """返回各任务调度状态（供 jobs API / 前端展示）"""
        known = self._known_tasks()
        jobs = {}
        if self._scheduler:
            for j in self._scheduler.get_jobs():
                jobs[j.id] = j

        # 该任务是否有（2h 内）running 记录
        conn = None
        running_set: set[str] = set()
        try:
            from .db import _connect  # noqa: PLC0415

            conn = _connect()
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT task_name FROM task_runs WHERE status='running' AND started_at >= %s GROUP BY task_name",
                    (datetime.now() - timedelta(hours=RUNNING_STALE_HOURS),),
                )
                running_set = {r[0] for r in cur.fetchall()}
        finally:
            if conn:
                conn.close()

        rows = query_all(
            "SELECT task_name, enabled, cron, params, update_time FROM task_config ORDER BY task_name"
        )
        out = []
        for r in rows:
            name = r["task_name"]
            job = jobs.get(name)
            out.append(
                {
                    "task_name": name,
                    "enabled": bool(r["enabled"]),
                    "cron": r["cron"] or MANUAL_MARK,
                    "cron_human": cron_human(r["cron"]),   # 中文语义（防 day_of_week 误读）
                    "params": r["params"],
                    "update_time": r["update_time"],
                    "implemented": name in known,            # TASKS 中是否有实现
                    "scheduled": job is not None,             # 调度器是否已挂载
                    "next_run": job.next_run_time.strftime("%Y-%m-%d %H:%M:%S") if (job and job.next_run_time) else None,
                    "running": name in running_set,           # 是否有运行中记录
                }
            )
        return out


# 全局单例
manager = SchedulerManager()
