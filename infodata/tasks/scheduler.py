"""
调度管理器

基于APScheduler的任务调度管理器，支持定时任务、依赖管理和监控。
"""

import time
import threading
from typing import Any, Dict, List, Optional, Callable, Union
from datetime import datetime, timedelta
from contextlib import contextmanager

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.executors.pool import ThreadPoolExecutor, ProcessPoolExecutor
from apscheduler.events import (
    EVENT_JOB_ADDED, EVENT_JOB_REMOVED, EVENT_JOB_MODIFIED,
    EVENT_JOB_EXECUTED, EVENT_JOB_ERROR, EVENT_JOB_MISSED,
    EVENT_SCHEDULER_START, EVENT_SCHEDULER_SHUTDOWN,
    EVENT_SCHEDULER_PAUSED, EVENT_SCHEDULER_RESUMED
)

from ..config.schemas import SchedulerConfig, TaskScheduleConfig
from ..utils.logging import get_logger, TaskLogger
from ..utils.context import TaskContext
from ..utils.exceptions import SchedulerError, TaskError, TaskNotFoundError
from .base import BaseTask, TaskFactory, TaskResult, TaskStatus, TaskConfig

logger = get_logger(__name__)


class TaskScheduler:
    """任务调度管理器"""
    
    def __init__(self, config: SchedulerConfig):
        """
        初始化调度管理器
        
        Args:
            config: 调度器配置
        """
        self.config = config
        self.scheduler: Optional[BackgroundScheduler] = None
        self._initialized = False
        self._lock = threading.RLock()
        self._task_results: Dict[str, TaskResult] = {}
        self._task_callbacks: Dict[str, List[Callable]] = {}
        
    def initialize(self) -> None:
        """初始化调度器"""
        with self._lock:
            if self._initialized:
                return
            
            try:
                # 配置作业存储
                jobstores = {
                    'default': MemoryJobStore()
                }
                
                # 配置执行器
                executors = {
                    'default': ThreadPoolExecutor(20),
                    'process': ProcessPoolExecutor(5)
                }
                
                # 配置作业默认值
                job_defaults = {
                    'coalesce': self.config.coalesce,
                    'max_instances': self.config.max_instances,
                    'misfire_grace_time': self.config.misfire_grace_time
                }
                
                # 创建调度器
                self.scheduler = BackgroundScheduler(
                    jobstores=jobstores,
                    executors=executors,
                    job_defaults=job_defaults,
                    timezone=self.config.timezone
                )
                
                # 添加事件监听器
                self._setup_event_listeners()
                
                self._initialized = True
                logger.info("调度器初始化完成")
                
            except Exception as e:
                logger.error(f"调度器初始化失败: {e}")
                raise SchedulerError(f"调度器初始化失败: {e}")
    
    def _setup_event_listeners(self) -> None:
        """设置事件监听器"""
        if not self.scheduler:
            return
        
        # 添加事件监听器
        self.scheduler.add_listener(
            self._on_job_added,
            EVENT_JOB_ADDED
        )
        self.scheduler.add_listener(
            self._on_job_removed,
            EVENT_JOB_REMOVED
        )
        self.scheduler.add_listener(
            self._on_job_executed,
            EVENT_JOB_EXECUTED
        )
        self.scheduler.add_listener(
            self._on_job_error,
            EVENT_JOB_ERROR
        )
        self.scheduler.add_listener(
            self._on_job_missed,
            EVENT_JOB_MISSED
        )
        self.scheduler.add_listener(
            self._on_scheduler_start,
            EVENT_SCHEDULER_START
        )
        self.scheduler.add_listener(
            self._on_scheduler_shutdown,
            EVENT_SCHEDULER_SHUTDOWN
        )
    
    def start(self) -> None:
        """启动调度器"""
        with self._lock:
            if not self._initialized:
                self.initialize()
            
            if not self.scheduler:
                raise SchedulerError("调度器未初始化")
            
            try:
                self.scheduler.start()
                logger.info("调度器已启动")
                
            except Exception as e:
                logger.error(f"调度器启动失败: {e}")
                raise SchedulerError(f"调度器启动失败: {e}")
    
    def shutdown(self, wait: bool = True) -> None:
        """关闭调度器"""
        with self._lock:
            if not self.scheduler or not self._initialized:
                return
            
            try:
                self.scheduler.shutdown(wait=wait)
                logger.info("调度器已关闭")
                self._initialized = False
                
            except Exception as e:
                logger.error(f"调度器关闭失败: {e}")
                raise SchedulerError(f"调度器关闭失败: {e}")
    
    def pause(self) -> None:
        """暂停调度器"""
        with self._lock:
            if not self.scheduler or not self._initialized:
                raise SchedulerError("调度器未初始化")
            
            try:
                self.scheduler.pause()
                logger.info("调度器已暂停")
                
            except Exception as e:
                logger.error(f"调度器暂停失败: {e}")
                raise SchedulerError(f"调度器暂停失败: {e}")
    
    def resume(self) -> None:
        """恢复调度器"""
        with self._lock:
            if not self.scheduler or not self._initialized:
                raise SchedulerError("调度器未初始化")
            
            try:
                self.scheduler.resume()
                logger.info("调度器已恢复")
                
            except Exception as e:
                logger.error(f"调度器恢复失败: {e}")
                raise SchedulerError(f"调度器恢复失败: {e}")
    
    def add_task(
        self,
        task: Union[BaseTask, str],
        schedule_config: TaskScheduleConfig,
        task_id: Optional[str] = None,
        **kwargs
    ) -> str:
        """
        添加定时任务
        
        Args:
            task: 任务实例或任务名称
            schedule_config: 调度配置
            task_id: 任务ID，如果为None则自动生成
            **kwargs: 任务参数
            
        Returns:
            str: 作业ID
            
        Raises:
            SchedulerError: 调度器错误
        """
        with self._lock:
            if not self.scheduler or not self._initialized:
                raise SchedulerError("调度器未初始化")
            
            try:
                # 创建任务实例
                if isinstance(task, str):
                    task_instance = TaskFactory.create(task, task_id=task_id, **kwargs)
                else:
                    task_instance = task
                    if task_id:
                        task_instance.task_id = task_id
                
                # 生成作业ID
                job_id = task_instance.task_id
                
                # 添加作业到调度器
                self.scheduler.add_job(
                    func=self._execute_task_wrapper,
                    trigger='cron',
                    args=[task_instance],
                    id=job_id,
                    name=task_instance.name,
                    **self._parse_schedule_config(schedule_config)
                )
                
                logger.info(f"任务已添加: {task_instance.name} (ID: {job_id})")
                return job_id
                
            except Exception as e:
                logger.error(f"添加任务失败: {e}")
                raise SchedulerError(f"添加任务失败: {e}")
    
    def remove_task(self, task_id: str) -> None:
        """
        移除任务
        
        Args:
            task_id: 任务ID
            
        Raises:
            SchedulerError: 调度器错误
        """
        with self._lock:
            if not self.scheduler or not self._initialized:
                raise SchedulerError("调度器未初始化")
            
            try:
                self.scheduler.remove_job(task_id)
                logger.info(f"任务已移除: {task_id}")
                
            except Exception as e:
                logger.error(f"移除任务失败: {e}")
                raise SchedulerError(f"移除任务失败: {e}")
    
    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        """
        获取任务信息
        
        Args:
            task_id: 任务ID
            
        Returns:
            Optional[Dict[str, Any]]: 任务信息，如果不存在则返回None
        """
        with self._lock:
            if not self.scheduler or not self._initialized:
                return None
            
            try:
                job = self.scheduler.get_job(task_id)
                if not job:
                    return None
                
                return {
                    'id': job.id,
                    'name': job.name,
                    'next_run_time': job.next_run_time,
                    'trigger': str(job.trigger),
                    'args': job.args,
                    'kwargs': job.kwargs,
                }
                
            except Exception:
                return None
    
    def list_tasks(self) -> List[Dict[str, Any]]:
        """
        列出所有任务
        
        Returns:
            List[Dict[str, Any]]: 任务列表
        """
        with self._lock:
            if not self.scheduler or not self._initialized:
                return []
            
            try:
                jobs = self.scheduler.get_jobs()
                return [
                    {
                        'id': job.id,
                        'name': job.name,
                        'next_run_time': job.next_run_time,
                        'trigger': str(job.trigger),
                    }
                    for job in jobs
                ]
                
            except Exception as e:
                logger.error(f"列出任务失败: {e}")
                return []
    
    def run_task_now(self, task_id: str) -> TaskResult:
        """
        立即运行任务
        
        Args:
            task_id: 任务ID
            
        Returns:
            TaskResult: 任务执行结果
            
        Raises:
            SchedulerError: 调度器错误
            TaskNotFoundError: 任务未找到
        """
        with self._lock:
            if not self.scheduler or not self._initialized:
                raise SchedulerError("调度器未初始化")
            
            try:
                job = self.scheduler.get_job(task_id)
                if not job:
                    raise TaskNotFoundError(task_id=task_id)
                
                # 获取任务实例
                task_instance = job.args[0] if job.args else None
                if not isinstance(task_instance, BaseTask):
                    raise SchedulerError(f"任务实例类型错误: {type(task_instance)}")
                
                # 执行任务
                return self._execute_task(task_instance)
                
            except TaskNotFoundError:
                raise
            except Exception as e:
                logger.error(f"立即运行任务失败: {e}")
                raise SchedulerError(f"立即运行任务失败: {e}")
    
    def get_task_result(self, task_id: str) -> Optional[TaskResult]:
        """
        获取任务执行结果
        
        Args:
            task_id: 任务ID
            
        Returns:
            Optional[TaskResult]: 任务结果，如果不存在则返回None
        """
        with self._lock:
            return self._task_results.get(task_id)
    
    def add_task_callback(
        self,
        task_id: str,
        callback: Callable[[TaskResult], None]
    ) -> None:
        """
        添加任务回调函数
        
        Args:
            task_id: 任务ID
            callback: 回调函数
        """
        with self._lock:
            if task_id not in self._task_callbacks:
                self._task_callbacks[task_id] = []
            self._task_callbacks[task_id].append(callback)
    
    def remove_task_callback(
        self,
        task_id: str,
        callback: Callable[[TaskResult], None]
    ) -> None:
        """
        移除任务回调函数
        
        Args:
            task_id: 任务ID
            callback: 回调函数
        """
        with self._lock:
            if task_id in self._task_callbacks:
                try:
                    self._task_callbacks[task_id].remove(callback)
                except ValueError:
                    pass
    
    def _execute_task_wrapper(self, task_instance: BaseTask) -> TaskResult:
        """
        任务执行包装器
        
        Args:
            task_instance: 任务实例
            
        Returns:
            TaskResult: 任务执行结果
        """
        try:
            # 执行任务
            result = self._execute_task(task_instance)
            
            # 触发回调
            self._trigger_callbacks(task_instance.task_id, result)
            
            return result
            
        except Exception as e:
            logger.error(f"任务执行包装器异常: {e}", exc_info=True)
            
            # 创建错误结果
            result = TaskResult(
                task_id=task_instance.task_id,
                task_name=task_instance.name,
                status=TaskStatus.FAILED,
                start_time=datetime.now(),
                end_time=datetime.now(),
                error=str(e),
                error_details={"exception_type": type(e).__name__},
            )
            
            # 触发回调
            self._trigger_callbacks(task_instance.task_id, result)
            
            return result
    
    def _execute_task(self, task_instance: BaseTask) -> TaskResult:
        """
        执行任务
        
        Args:
            task_instance: 任务实例
            
        Returns:
            TaskResult: 任务执行结果
        """
        # 创建任务上下文
        with TaskContext(
            task_instance.task_id,
            task_instance.name,
            start_time=datetime.now()
        ):
            # 执行任务
            result = task_instance.execute()
            
            # 保存结果
            self._task_results[task_instance.task_id] = result
            
            return result
    
    def _trigger_callbacks(self, task_id: str, result: TaskResult) -> None:
        """触发回调函数"""
        if task_id in self._task_callbacks:
            for callback in self._task_callbacks[task_id]:
                try:
                    callback(result)
                except Exception as e:
                    logger.error(f"任务回调执行失败: {e}")
    
    def _parse_schedule_config(self, config: TaskScheduleConfig) -> Dict[str, Any]:
        """
        解析调度配置
        
        Args:
            config: 调度配置
            
        Returns:
            Dict[str, Any]: APScheduler参数
        """
        # 解析cron表达式
        # 格式: "0 19 * * *" -> minute=0, hour=19
        parts = config.schedule.split()
        if len(parts) != 5:
            raise ValueError(f"无效的cron表达式: {config.schedule}")
        
        minute, hour, day, month, day_of_week = parts
        
        return {
            'minute': minute,
            'hour': hour,
            'day': day,
            'month': month,
            'day_of_week': day_of_week,
            'misfire_grace_time': config.retry_delay,
            'coalesce': True,
        }
    
    # 事件处理函数
    def _on_job_added(self, event):
        """作业添加事件"""
        logger.debug(f"作业已添加: {event.job_id}")
    
    def _on_job_removed(self, event):
        """作业移除事件"""
        logger.debug(f"作业已移除: {event.job_id}")
    
    def _on_job_executed(self, event):
        """作业执行成功事件"""
        logger.debug(f"作业执行成功: {event.job_id}")
    
    def _on_job_error(self, event):
        """作业执行错误事件"""
        logger.error(f"作业执行错误: {event.job_id} - {event.exception}")
    
    def _on_job_missed(self, event):
        """作业错过执行事件"""
        logger.warning(f"作业错过执行: {event.job_id}")
    
    def _on_scheduler_start(self, event):
        """调度器启动事件"""
        logger.info("调度器已启动")
    
    def _on_scheduler_shutdown(self, event):
        """调度器关闭事件"""
        logger.info("调度器已关闭")
    
    def get_status(self) -> Dict[str, Any]:
        """
        获取调度器状态
        
        Returns:
            Dict[str, Any]: 状态信息
        """
        with self._lock:
            if not self.scheduler or not self._initialized:
                return {"status": "not_initialized"}
            
            try:
                return {
                    "status": "running" if self.scheduler.running else "stopped",
                    "job_count": len(self.scheduler.get_jobs()),
                    "timezone": self.config.timezone,
                    "initialized": self._initialized,
                    "task_results_count": len(self._task_results),
                }
                
            except Exception as e:
                logger.error(f"获取调度器状态失败: {e}")
                return {"status": "error", "error": str(e)}


@contextmanager
def scheduler_context(config: SchedulerConfig):
    """
    调度器上下文管理器
    
    Args:
        config: 调度器配置
        
    Yields:
        TaskScheduler: 调度器实例
    """
    scheduler = TaskScheduler(config)
    try:
        scheduler.start()
        yield scheduler
    finally:
        scheduler.shutdown(wait=True)


# 全局调度器实例
_scheduler: Optional[TaskScheduler] = None


def get_scheduler(config: Optional[SchedulerConfig] = None) -> TaskScheduler:
    """
    获取调度器实例
    
    Args:
        config: 调度器配置，如果为None则使用默认配置
        
    Returns:
        TaskScheduler: 调度器实例
    """
    global _scheduler
    
    if _scheduler is None:
        if config is None:
            from ..config import ConfigManager
            app_config = ConfigManager.get_config()
            config = app_config.scheduler
        
        _scheduler = TaskScheduler(config)
        _scheduler.initialize()
    
    return _scheduler


def shutdown_scheduler() -> None:
    """关闭调度器"""
    global _scheduler
    
    if _scheduler:
        _scheduler.shutdown()
        _scheduler = None