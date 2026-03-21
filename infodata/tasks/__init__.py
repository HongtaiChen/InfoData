"""
任务模块

提供任务管理、调度和执行功能。
"""

from .base import (
    BaseTask,
    TaskConfig,
    TaskResult,
    TaskStatus,
    TaskPriority,
    TaskFactory,
    SimpleTask,
)
from .scheduler import TaskScheduler, scheduler_context, get_scheduler, shutdown_scheduler
from .manager import (
    TaskManager,
    TaskDependency,
    TaskDependencyStatus,
    get_task_manager,
    register_task,
    execute_task,
    schedule_task,
)

__all__ = [
    # 基础类
    "BaseTask",
    "TaskConfig",
    "TaskResult",
    "TaskStatus",
    "TaskPriority",
    "TaskFactory",
    "SimpleTask",
    
    # 调度器
    "TaskScheduler",
    "scheduler_context",
    "get_scheduler",
    "shutdown_scheduler",
    
    # 任务管理器
    "TaskManager",
    "TaskDependency",
    "TaskDependencyStatus",
    "get_task_manager",
    "register_task",
    "execute_task",
    "schedule_task",
]

# 版本信息
__version__ = "0.1.0"

# 初始化任务工厂（注册内置任务）
TaskFactory.register(SimpleTask)