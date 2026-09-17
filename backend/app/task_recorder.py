#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
InvestBuddy 作业记录模块
所有采集任务统一通过 TaskRecorder 记录运行状态到 task_runs 表：
  running -> (success | failed | blocked) + 写入条数 + 错误信息

⚠️ 三态语义（2026-09-17 立）：
  success —— 任务正常跑完并写入数据
  failed  —— 任务本身出错（异常/源不可用/写入失败）
  blocked —— **数据未就绪，本次按设计不执行**（如快照护栏发现日线只跑了一半）
            它不是故障，因此不计入失败率；但也不算成功，
            故 catchup_missed 仍会在下次开机时重新补跑它（这是期望行为）。
            加此状态的动机：护栏拒绝若记 failed，每个交易日都会产出一次假失败。
"""
import pymysql
from datetime import datetime
from contextlib import contextmanager

from .db import get_db_config


class TaskRecorder:
    """采集作业记录器"""

    def __init__(self, task_name: str):
        self.task_name = task_name
        self.run_id: int | None = None
        self.started_at = datetime.now()
        self.connection = None

    def _connect(self):
        self.connection = pymysql.connect(**get_db_config().to_dict())

    def start(self) -> int:
        """记录任务开始，返回 run_id"""
        self._connect()
        with self.connection.cursor() as cur:
            cur.execute(
                "INSERT INTO task_runs (task_name, status, started_at) VALUES (%s, 'running', %s)",
                (self.task_name, self.started_at),
            )
            self.connection.commit()
            self.run_id = cur.lastrowid
        return self.run_id

    def finish(self, records_written: int = 0, error_message: str | None = None,
               run_detail=None, status: str | None = None):
        """记录任务结束（成功 / 失败 / 未执行）

        run_detail: 可选 dict/str —— 结构化运行快照（步骤链+当轮实录），存入 task_runs.run_detail JSON 列
        status: 可选显式状态。默认按 error_message 推导（有错误信息 → failed，否则 success）；
                传 'blocked' 表示「数据未就绪，本次按设计不执行」（非任务故障，不计失败率）。
        """
        if self.connection is None:
            return
        status = status or ("failed" if error_message else "success")
        finished_at = datetime.now()
        try:
            with self.connection.cursor() as cur:
                cur.execute(
                    "UPDATE task_runs SET status=%s, finished_at=%s, records_written=%s, error_message=%s, run_detail=%s WHERE id=%s",
                    (status, finished_at, records_written, error_message, run_detail, self.run_id),
                )
                self.connection.commit()
        finally:
            self.connection.close()
            self.connection = None

    @contextmanager
    def run(self):
        """上下文管理器：start -> yield -> finish(成功/失败)"""
        self.start()
        try:
            yield self
        except Exception as e:
            self.finish(records_written=0, error_message=str(e))
            raise
        else:
            self.finish(records_written=self._written if hasattr(self, "_written") else 0)

    def set_written(self, n: int):
        """记录写入条数（供上下文管理器结束时更新）"""
        self._written = n
