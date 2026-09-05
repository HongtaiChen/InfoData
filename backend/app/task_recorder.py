#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
InvestBuddy 作业记录模块
所有采集任务统一通过 TaskRecorder 记录运行状态到 task_runs 表：
  running -> (success | failed) + 写入条数 + 错误信息
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

    def finish(self, records_written: int = 0, error_message: str | None = None):
        """记录任务结束（成功或失败）"""
        if self.connection is None:
            return
        status = "failed" if error_message else "success"
        finished_at = datetime.now()
        try:
            with self.connection.cursor() as cur:
                cur.execute(
                    "UPDATE task_runs SET status=%s, finished_at=%s, records_written=%s, error_message=%s WHERE id=%s",
                    (status, finished_at, records_written, error_message, self.run_id),
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
