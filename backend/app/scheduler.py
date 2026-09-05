#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
InvestBuddy 定时调度器（APScheduler）

- 消费 task_config 表中 enabled=1 且 cron 合法（5 字段 crontab）的任务
- 只调度 TASKS 注册表中有实现的任务（未实现任务跳过，避免空跑）
- 配置修改（PUT /api/jobs/tasks/{name}）后调用 sync_from_db() 热生效
- 立即执行（POST /api/jobs/tasks/{name}/trigger）走独立线程 + TaskRecorder 记录，
  带运行中保护：同任务已有 running 记录（2 小时内）则拒绝，避免并发双写
"""
import logging
import threading
from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from .db import query_all

logger = logging.getLogger("infodata.scheduler")

# 手动 cron 标识（task_config 中 cron='手动' 表示不自动调度）
MANUAL_MARK = "手动"
# running 记录视为「仍在运行」的时间窗口（超过视为遗留脏记录，允许重跑）
RUNNING_STALE_HOURS = 2


def parse_cron(cron: str):
    """解析 5 字段 crontab，非法返回 None"""
    if not cron or cron.strip().lower() in (MANUAL_MARK, "none", "-"):
        return None
    try:
        return CronTrigger.from_crontab(cron.strip())
    except ValueError as e:
        logger.warning(f"cron 表达式非法: {cron!r} -> {e}")
        return None


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
        self.sync_from_db()
        logger.info("🕒 调度器已启动")

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
                    misfire_grace_time=3600,  # 错过 1 小时内仍补跑（如休眠期）
                    replace_existing=True,
                )
            scheduled.append(name)
        logger.info(f"调度同步完成: 计划 {len(scheduled)} 个任务 {scheduled}, 解析失败 {errors}")
        return {"scheduled": scheduled, "errors": errors}

    # ---------- 任务执行 ----------

    def _run_scheduled(self, task_name: str):
        """APScheduler 触发的任务入口（异常必须吞掉，避免 scheduler 内部报错）"""
        try:
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

    def _execute(self, task_name: str) -> dict:
        """单次执行（含运行中保护），返回 {started, reason}"""
        if self._running_count(task_name) > 0:
            return {"started": False, "reason": "同任务运行中，已跳过本次触发"}
        from .tasks.run import run_task  # noqa: PLC0415

        written = run_task(task_name)
        return {"started": True, "records_written": written}

    def trigger_now(self, task_name: str) -> dict:
        """立即执行（异步线程），供 API 调用"""
        if task_name not in self._known_tasks():
            return {"started": False, "reason": f"任务 {task_name} 无执行实现"}
        if self._running_count(task_name) > 0:
            return {"started": False, "reason": "任务正在运行中，请稍后再试"}
        t = threading.Thread(target=self._run_scheduled, args=(task_name,), daemon=True)
        t.start()
        return {"started": True, "reason": "已提交执行，请到运行记录查看进度"}

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
