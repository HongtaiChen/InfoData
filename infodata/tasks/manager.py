"""
任务管理器

提供任务注册、依赖管理、执行控制和结果跟踪功能。
"""

import threading
import time
from typing import Any, Dict, List, Optional, Set, Tuple, Callable
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from enum import Enum

from ..utils.logging import get_logger, TaskLogger
from ..utils.context import TaskContext
from ..utils.exceptions import TaskError, TaskNotFoundError, TaskExecutionError
from .base import BaseTask, TaskFactory, TaskResult, TaskStatus, TaskConfig, TaskPriority
from .scheduler import TaskScheduler

logger = get_logger(__name__)


class TaskDependencyStatus(str, Enum):
    """任务依赖状态"""
    
    PENDING = "pending"      # 依赖未满足
    SATISFIED = "satisfied"  # 依赖已满足
    FAILED = "failed"       # 依赖失败
    SKIPPED = "skipped"     # 依赖被跳过


@dataclass
class TaskDependency:
    """任务依赖"""
    
    task_name: str
    required_status: TaskStatus = TaskStatus.SUCCESS
    timeout: Optional[int] = None  # 等待超时时间（秒）
    
    def check(self, task_result: Optional[TaskResult]) -> Tuple[bool, str]:
        """
        检查依赖是否满足
        
        Args:
            task_result: 依赖任务的结果
            
        Returns:
            Tuple[bool, str]: (是否满足, 原因)
        """
        if task_result is None:
            return False, f"依赖任务 '{self.task_name}' 未执行"
        
        if task_result.status == self.required_status:
            return True, f"依赖任务 '{self.task_name}' 状态为 {self.required_status.value}"
        
        return False, f"依赖任务 '{self.task_name}' 状态为 {task_result.status.value}，需要 {self.required_status.value}"


class TaskManager:
    """任务管理器"""
    
    def __init__(self, scheduler: Optional[TaskScheduler] = None):
        """
        初始化任务管理器
        
        Args:
            scheduler: 调度器实例，如果为None则创建默认实例
        """
        self.scheduler = scheduler
        self._lock = threading.RLock()
        self._task_registry: Dict[str, Type[BaseTask]] = {}
        self._task_dependencies: Dict[str, List[TaskDependency]] = {}
        self._task_execution_history: Dict[str, List[TaskResult]] = {}
        self._task_metrics: Dict[str, Dict[str, Any]] = {}
        
        # 任务执行队列
        self._execution_queue: List[Tuple[str, TaskConfig, Dict[str, Any]]] = []
        self._execution_lock = threading.Lock()
        self._execution_thread: Optional[threading.Thread] = None
        self._stop_execution = threading.Event()
        
    def register_task(self, task_class: Type[BaseTask]) -> None:
        """
        注册任务类
        
        Args:
            task_class: 任务类
        """
        with self._lock:
            task_name = task_class.__name__
            self._task_registry[task_name] = task_class
            TaskFactory.register(task_class)
            logger.info(f"任务已注册: {task_name}")
    
    def add_dependency(
        self,
        task_name: str,
        depends_on: str,
        required_status: TaskStatus = TaskStatus.SUCCESS,
        timeout: Optional[int] = None
    ) -> None:
        """
        添加任务依赖
        
        Args:
            task_name: 任务名称
            depends_on: 依赖的任务名称
            required_status: 需要的依赖任务状态
            timeout: 等待超时时间（秒）
        """
        with self._lock:
            if task_name not in self._task_dependencies:
                self._task_dependencies[task_name] = []
            
            dependency = TaskDependency(depends_on, required_status, timeout)
            self._task_dependencies[task_name].append(dependency)
            logger.debug(f"任务依赖已添加: {task_name} -> {depends_on} ({required_status.value})")
    
    def get_dependencies(self, task_name: str) -> List[TaskDependency]:
        """
        获取任务依赖
        
        Args:
            task_name: 任务名称
            
        Returns:
            List[TaskDependency]: 依赖列表
        """
        with self._lock:
            return self._task_dependencies.get(task_name, [])
    
    def check_dependencies(
        self,
        task_name: str,
        task_results: Optional[Dict[str, TaskResult]] = None
    ) -> Tuple[bool, List[str]]:
        """
        检查任务依赖是否满足
        
        Args:
            task_name: 任务名称
            task_results: 任务结果字典，如果为None则使用历史记录
            
        Returns:
            Tuple[bool, List[str]]: (是否满足, 原因列表)
        """
        with self._lock:
            dependencies = self._task_dependencies.get(task_name, [])
            if not dependencies:
                return True, ["无依赖"]
            
            reasons = []
            all_satisfied = True
            
            if task_results is None:
                task_results = self._get_latest_task_results()
            
            for dependency in dependencies:
                dep_result = task_results.get(dependency.task_name)
                satisfied, reason = dependency.check(dep_result)
                
                reasons.append(reason)
                if not satisfied:
                    all_satisfied = False
            
            return all_satisfied, reasons
    
    def execute_task(
        self,
        task_name: str,
        task_id: Optional[str] = None,
        config: Optional[TaskConfig] = None,
        wait_for_dependencies: bool = True,
        **kwargs
    ) -> TaskResult:
        """
        执行任务
        
        Args:
            task_name: 任务名称
            task_id: 任务ID
            config: 任务配置
            wait_for_dependencies: 是否等待依赖满足
            **kwargs: 任务参数
            
        Returns:
            TaskResult: 任务执行结果
            
        Raises:
            TaskNotFoundError: 任务未找到
            TaskExecutionError: 任务执行错误
        """
        # 检查任务是否存在
        if task_name not in self._task_registry:
            raise TaskNotFoundError(task_name=task_name)
        
        # 检查依赖
        if wait_for_dependencies:
            satisfied, reasons = self.check_dependencies(task_name)
            if not satisfied:
                error_msg = f"任务依赖未满足: {', '.join(reasons)}"
                logger.warning(error_msg)
                raise TaskExecutionError(error_msg, task_name=task_name)
        
        try:
            # 创建任务实例
            task_instance = TaskFactory.create(
                task_name,
                task_id=task_id,
                config=config,
                **kwargs
            )
            
            # 执行任务
            result = task_instance.execute()
            
            # 记录执行历史
            self._record_execution(task_name, result)
            
            # 更新指标
            self._update_metrics(task_name, result)
            
            return result
            
        except TaskError:
            raise
        except Exception as e:
            error_msg = f"任务执行失败: {str(e)}"
            logger.error(error_msg, exc_info=True)
            raise TaskExecutionError(error_msg, task_name=task_name)
    
    def execute_tasks_sequential(
        self,
        task_names: List[str],
        configs: Optional[Dict[str, TaskConfig]] = None,
        stop_on_error: bool = True,
        **kwargs
    ) -> Dict[str, TaskResult]:
        """
        顺序执行多个任务
        
        Args:
            task_names: 任务名称列表
            configs: 任务配置字典
            stop_on_error: 出错时是否停止
            **kwargs: 通用任务参数
            
        Returns:
            Dict[str, TaskResult]: 任务执行结果字典
        """
        results = {}
        configs = configs or {}
        
        for task_name in task_names:
            try:
                # 检查依赖（使用已执行任务的结果）
                satisfied, reasons = self.check_dependencies(task_name, results)
                if not satisfied:
                    error_msg = f"任务 '{task_name}' 依赖未满足: {', '.join(reasons)}"
                    logger.error(error_msg)
                    
                    if stop_on_error:
                        raise TaskExecutionError(error_msg, task_name=task_name)
                    else:
                        # 创建跳过结果
                        result = TaskResult(
                            task_id=f"{task_name}_skipped",
                            task_name=task_name,
                            status=TaskStatus.SKIPPED,
                            start_time=datetime.now(),
                            end_time=datetime.now(),
                            error=error_msg,
                        )
                        results[task_name] = result
                        continue
                
                # 执行任务
                task_config = configs.get(task_name)
                result = self.execute_task(
                    task_name,
                    config=task_config,
                    wait_for_dependencies=False,  # 已手动检查
                    **kwargs.get(task_name, {})
                )
                
                results[task_name] = result
                
                # 检查是否继续执行
                if stop_on_error and result.status == TaskStatus.FAILED:
                    logger.warning(f"任务 '{task_name}' 失败，停止执行后续任务")
                    break
                    
            except Exception as e:
                logger.error(f"执行任务 '{task_name}' 时发生错误: {e}", exc_info=True)
                
                if stop_on_error:
                    raise
                else:
                    # 创建错误结果
                    result = TaskResult(
                        task_id=f"{task_name}_error",
                        task_name=task_name,
                        status=TaskStatus.FAILED,
                        start_time=datetime.now(),
                        end_time=datetime.now(),
                        error=str(e),
                    )
                    results[task_name] = result
        
        return results
    
    def schedule_task(
        self,
        task_name: str,
        schedule_config: TaskScheduleConfig,
        task_id: Optional[str] = None,
        config: Optional[TaskConfig] = None,
        **kwargs
    ) -> str:
        """
        调度任务
        
        Args:
            task_name: 任务名称
            schedule_config: 调度配置
            task_id: 任务ID
            config: 任务配置
            **kwargs: 任务参数
            
        Returns:
            str: 作业ID
            
        Raises:
            TaskNotFoundError: 任务未找到
            SchedulerError: 调度器错误
        """
        if not self.scheduler:
            from .scheduler import get_scheduler
            self.scheduler = get_scheduler()
        
        # 创建任务实例
        task_instance = TaskFactory.create(
            task_name,
            task_id=task_id,
            config=config,
            **kwargs
        )
        
        # 添加到调度器
        return self.scheduler.add_task(task_instance, schedule_config)
    
    def get_task_history(self, task_name: str, limit: int = 10) -> List[TaskResult]:
        """
        获取任务执行历史
        
        Args:
            task_name: 任务名称
            limit: 返回结果数量限制
            
        Returns:
            List[TaskResult]: 任务执行历史
        """
        with self._lock:
            history = self._task_execution_history.get(task_name, [])
            return history[-limit:] if limit > 0 else history
    
    def get_task_metrics(self, task_name: str) -> Dict[str, Any]:
        """
        获取任务指标
        
        Args:
            task_name: 任务名称
            
        Returns:
            Dict[str, Any]: 任务指标
        """
        with self._lock:
            return self._task_metrics.get(task_name, {}).copy()
    
    def get_all_metrics(self) -> Dict[str, Dict[str, Any]]:
        """
        获取所有任务指标
        
        Returns:
            Dict[str, Dict[str, Any]]: 所有任务指标
        """
        with self._lock:
            return {k: v.copy() for k, v in self._task_metrics.items()}
    
    def clear_history(self, task_name: Optional[str] = None) -> None:
        """
        清除任务历史
        
        Args:
            task_name: 任务名称，如果为None则清除所有历史
        """
        with self._lock:
            if task_name:
                if task_name in self._task_execution_history:
                    self._task_execution_history[task_name].clear()
                if task_name in self._task_metrics:
                    self._task_metrics[task_name].clear()
            else:
                self._task_execution_history.clear()
                self._task_metrics.clear()
    
    def list_tasks(self) -> List[str]:
        """
        列出所有注册的任务
        
        Returns:
            List[str]: 任务名称列表
        """
        with self._lock:
            return list(self._task_registry.keys())
    
    def get_task_info(self, task_name: str) -> Dict[str, Any]:
        """
        获取任务信息
        
        Args:
            task_name: 任务名称
            
        Returns:
            Dict[str, Any]: 任务信息
            
        Raises:
            TaskNotFoundError: 任务未找到
        """
        with self._lock:
            if task_name not in self._task_registry:
                raise TaskNotFoundError(task_name=task_name)
            
            task_class = self._task_registry[task_name]
            dependencies = self._task_dependencies.get(task_name, [])
            metrics = self._task_metrics.get(task_name, {})
            history_count = len(self._task_execution_history.get(task_name, []))
            
            return {
                "name": task_name,
                "class": task_class.__name__,
                "description": task_class.__doc__ or "未提供描述",
                "dependencies": [dep.task_name for dep in dependencies],
                "dependency_count": len(dependencies),
                "execution_count": history_count,
                "metrics": metrics,
            }
    
    def _record_execution(self, task_name: str, result: TaskResult) -> None:
        """记录任务执行"""
        with self._lock:
            if task_name not in self._task_execution_history:
                self._task_execution_history[task_name] = []
            self._task_execution_history[task_name].append(result)
    
    def _update_metrics(self, task_name: str, result: TaskResult) -> None:
        """更新任务指标"""
        with self._lock:
            if task_name not in self._task_metrics:
                self._task_metrics[task_name] = {
                    "total_executions": 0,
                    "success_count": 0,
                    "failure_count": 0,
                    "total_duration": 0.0,
                    "avg_duration": 0.0,
                    "last_execution": None,
                    "last_status": None,
                }
            
            metrics = self._task_metrics[task_name]
            metrics["total_executions"] += 1
            metrics["last_execution"] = result.end_time.isoformat() if result.end_time else None
            metrics["last_status"] = result.status.value
            
            if result.status == TaskStatus.SUCCESS:
                metrics["success_count"] += 1
            elif result.status == TaskStatus.FAILED:
                metrics["failure_count"] += 1
            
            if result.duration:
                metrics["total_duration"] += result.duration
                metrics["avg_duration"] = metrics["total_duration"] / metrics["total_executions"]
    
    def _get_latest_task_results(self) -> Dict[str, TaskResult]:
        """获取最新任务结果"""
        with self._lock:
            results = {}
            for task_name, history in self._task_execution_history.items():
                if history:
                    results[task_name] = history[-1]
            return results
    
    def start_execution_queue(self) -> None:
        """启动任务执行队列"""
        with self._execution_lock:
            if self._execution_thread and self._execution_thread.is_alive():
                return
            
            self._stop_execution.clear()
            self._execution_thread = threading.Thread(
                target=self._execution_worker,
                daemon=True,
                name="TaskExecutionWorker"
            )
            self._execution_thread.start()
            logger.info("任务执行队列已启动")
    
    def stop_execution_queue(self) -> None:
        """停止任务执行队列"""
        with self._execution_lock:
            if self._execution_thread:
                self._stop_execution.set()
                self._execution_thread.join(timeout=5.0)
                self._execution_thread = None
                logger.info("任务执行队列已停止")
    
    def add_to_queue(
        self,
        task_name: str,
        config: Optional[TaskConfig] = None,
        **kwargs
    ) -> str:
        """
        添加任务到执行队列
        
        Args:
            task_name: 任务名称
            config: 任务配置
            **kwargs: 任务参数
            
        Returns:
            str: 队列任务ID
        """
        task_id = f"{task_name}_{int(time.time())}"
        
        with self._execution_lock:
            self._execution_queue.append((task_id, task_name, config or TaskConfig(), kwargs))
            logger.debug(f"任务已添加到队列: {task_name} (ID: {task_id})")
        
        # 确保执行队列在运行
        self.start_execution_queue()
        
        return task_id
    
    def _execution_worker(self) -> None:
        """任务执行工作线程"""
        logger.info("任务执行工作线程已启动")
        
        while not self._stop_execution.is_set():
            try:
                # 获取下一个任务
                task_data = None
                with self._execution_lock:
                    if self._execution_queue:
                        task_data = self._execution_queue.pop(0)
                
                if task_data:
                    task_id, task_name, config, kwargs = task_data
                    self._execute_queued_task(task_id, task_name, config, kwargs)
                else:
                    # 队列为空，等待新任务
                    time.sleep(1.0)
                    
            except Exception as e:
                logger.error(f"任务执行工作线程异常: {e}", exc_info=True)
                time.sleep(5.0)  # 异常后等待一段时间
        
        logger.info("任务执行工作线程已停止")
    
    def _execute_queued_task(
        self,
        task_id: str,
        task_name: str,
        config: TaskConfig,
        kwargs: Dict[str, Any]
    ) -> None:
        """执行队列中的任务"""
        try:
            logger.info(f"开始执行队列任务: {task_name} (ID: {task_id})")
            
            # 执行任务
            result = self.execute_task(
                task_name,
                task_id=task_id,
                config=config,
                wait_for_dependencies=True,
                **kwargs
            )
            
            logger.info(f"队列任务执行完成: {task_name} (状态: {result.status.value})")
            
        except TaskNotFoundError as e:
            logger.error(f"队列任务未找到: {task_name} - {e}")
        except TaskExecutionError as e:
            logger.error(f"队列任务执行错误: {task_name} - {e}")
        except Exception as e:
            logger.error(f"队列任务执行异常: {task_name} - {e}", exc_info=True)
    
    def get_queue_status(self) -> Dict[str, Any]:
        """
        获取队列状态
        
        Returns:
            Dict[str, Any]: 队列状态
        """
        with self._execution_lock:
            return {
                "queue_size": len(self._execution_queue),
                "worker_running": self._execution_thread is not None and self._execution_thread.is_alive(),
                "worker_name": self._execution_thread.name if self._execution_thread else None,
                "tasks_in_queue": [task[1] for task in self._execution_queue],
            }


# 全局任务管理器实例
_task_manager: Optional[TaskManager] = None


def get_task_manager(scheduler: Optional[TaskScheduler] = None) -> TaskManager:
    """
    获取任务管理器实例
    
    Args:
        scheduler: 调度器实例，如果为None则使用默认实例
        
    Returns:
        TaskManager: 任务管理器实例
    """
    global _task_manager
    
    if _task_manager is None:
        if scheduler is None:
            from .scheduler import get_scheduler
            scheduler = get_scheduler()
        
        _task_manager = TaskManager(scheduler)
    
    return _task_manager


def register_task(task_class: Type[BaseTask]) -> None:
    """
    注册任务类（便捷函数）
    
    Args:
        task_class: 任务类
    """
    manager = get_task_manager()
    manager.register_task(task_class)


def execute_task(
    task_name: str,
    task_id: Optional[str] = None,
    config: Optional[TaskConfig] = None,
    **kwargs
) -> TaskResult:
    """
    执行任务（便捷函数）
    
    Args:
        task_name: 任务名称
        task_id: 任务ID
        config: 任务配置
        **kwargs: 任务参数
        
    Returns:
        TaskResult: 任务执行结果
    """
    manager = get_task_manager()
    return manager.execute_task(task_name, task_id, config, **kwargs)


def schedule_task(
    task_name: str,
    schedule_config: TaskScheduleConfig,
    task_id: Optional[str] = None,
    config: Optional[TaskConfig] = None,
    **kwargs
) -> str:
    """
    调度任务（便捷函数）
    
    Args:
        task_name: 任务名称
        schedule_config: 调度配置
        task_id: 任务ID
        config: 任务配置
        **kwargs: 任务参数
        
    Returns:
        str: 作业ID
    """
    manager = get_task_manager()
    return manager.schedule_task(task_name, schedule_config, task_id, config, **kwargs)
