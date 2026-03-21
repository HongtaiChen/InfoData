"""
任务基类和接口

定义任务的基础抽象类、状态枚举和结果对象。
"""

import abc
import time
import uuid
from typing import Any, Dict, List, Optional, Type, Union
from enum import Enum
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from pydantic import BaseModel, Field

from ..utils.logging import TaskLogger
from ..utils.context import TaskContext
from ..utils.exceptions import TaskError, TaskExecutionError, TaskTimeoutError


class TaskStatus(str, Enum):
    """任务状态枚举"""
    
    PENDING = "pending"          # 等待执行
    RUNNING = "running"          # 执行中
    SUCCESS = "success"          # 执行成功
    FAILED = "failed"            # 执行失败
    CANCELLED = "cancelled"      # 已取消
    SKIPPED = "skipped"          # 已跳过（如节假日）
    RETRYING = "retrying"        # 重试中


class TaskPriority(int, Enum):
    """任务优先级枚举"""
    
    LOW = 10      # 低优先级
    NORMAL = 20   # 普通优先级
    HIGH = 30     # 高优先级
    CRITICAL = 40 # 关键优先级


@dataclass
class TaskResult:
    """任务执行结果"""
    
    task_id: str
    task_name: str
    status: TaskStatus
    start_time: datetime
    end_time: Optional[datetime] = None
    duration: Optional[float] = None
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    error_details: Optional[Dict[str, Any]] = None
    retry_count: int = 0
    metrics: Dict[str, Any] = field(default_factory=dict)
    
    def __post_init__(self):
        """计算执行时长"""
        if self.end_time and self.start_time:
            self.duration = (self.end_time - self.start_time).total_seconds()
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        result = {
            "task_id": self.task_id,
            "task_name": self.task_name,
            "status": self.status.value,
            "start_time": self.start_time.isoformat(),
            "retry_count": self.retry_count,
            "metrics": self.metrics,
        }
        
        if self.end_time:
            result["end_time"] = self.end_time.isoformat()
        if self.duration:
            result["duration"] = self.duration
        if self.data:
            result["data"] = self.data
        if self.error:
            result["error"] = self.error
        if self.error_details:
            result["error_details"] = self.error_details
        
        return result
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TaskResult":
        """从字典创建"""
        # 转换字符串状态为枚举
        status_str = data.get("status", "pending")
        status = TaskStatus(status_str) if isinstance(status_str, str) else status_str
        
        # 转换时间字符串为datetime
        start_time = data["start_time"]
        if isinstance(start_time, str):
            start_time = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
        
        end_time = data.get("end_time")
        if end_time and isinstance(end_time, str):
            end_time = datetime.fromisoformat(end_time.replace('Z', '+00:00'))
        
        return cls(
            task_id=data["task_id"],
            task_name=data["task_name"],
            status=status,
            start_time=start_time,
            end_time=end_time,
            duration=data.get("duration"),
            data=data.get("data"),
            error=data.get("error"),
            error_details=data.get("error_details"),
            retry_count=data.get("retry_count", 0),
            metrics=data.get("metrics", {}),
        )


class TaskConfig(BaseModel):
    """任务配置"""
    
    enabled: bool = Field(default=True, description="是否启用任务")
    priority: TaskPriority = Field(default=TaskPriority.NORMAL, description="任务优先级")
    timeout: int = Field(default=1800, ge=60, description="任务超时时间（秒）")
    max_retries: int = Field(default=3, ge=0, le=10, description="最大重试次数")
    retry_delay: int = Field(default=300, ge=0, description="重试延迟时间（秒）")
    max_runtime: Optional[int] = Field(default=None, ge=60, description="最大运行时间（秒）")
    depends_on: List[str] = Field(default_factory=list, description="依赖的任务名称")
    parameters: Dict[str, Any] = Field(default_factory=dict, description="任务参数")


class BaseTask(abc.ABC):
    """任务基类（抽象类）"""
    
    def __init__(
        self,
        task_id: Optional[str] = None,
        config: Optional[TaskConfig] = None,
        logger: Optional[TaskLogger] = None,
        **kwargs
    ):
        """
        初始化任务
        
        Args:
            task_id: 任务ID，如果为None则自动生成
            config: 任务配置
            logger: 任务日志记录器
            **kwargs: 额外参数
        """
        self.task_id = task_id or str(uuid.uuid4())
        self.config = config or TaskConfig()
        self.logger = logger
        self._result: Optional[TaskResult] = None
        self._start_time: Optional[datetime] = None
        self._cancelled = False
        
        # 更新配置参数
        if kwargs:
            self.config.parameters.update(kwargs)
    
    @property
    def name(self) -> str:
        """任务名称"""
        return self.__class__.__name__
    
    @property
    def description(self) -> str:
        """任务描述"""
        return self.__doc__ or "未提供任务描述"
    
    @property
    def result(self) -> Optional[TaskResult]:
        """任务执行结果"""
        return self._result
    
    @property
    def is_running(self) -> bool:
        """任务是否正在运行"""
        return self._start_time is not None and self._result is None
    
    @property
    def is_completed(self) -> bool:
        """任务是否已完成"""
        return self._result is not None
    
    def execute(self) -> TaskResult:
        """
        执行任务
        
        Returns:
            TaskResult: 任务执行结果
            
        Raises:
            TaskError: 任务执行错误
        """
        if self._cancelled:
            return self._create_result(TaskStatus.CANCELLED, error="任务已取消")
        
        # 检查任务是否启用
        if not self.config.enabled:
            return self._create_result(TaskStatus.SKIPPED, error="任务未启用")
        
        # 创建日志记录器（如果未提供）
        if self.logger is None:
            from ..utils.logging import TaskLogger
            self.logger = TaskLogger(self.task_id, self.name)
        
        # 记录任务开始
        self._start_time = datetime.now()
        self.logger.start()
        
        try:
            # 执行任务
            result = self._execute_with_timeout()
            
            # 记录任务完成
            duration = (datetime.now() - self._start_time).total_seconds()
            self.logger.complete(duration)
            
            return result
            
        except TaskTimeoutError as e:
            # 超时错误
            self.logger.fail(f"任务执行超时: {self.config.timeout}秒")
            return self._create_result(
                TaskStatus.FAILED,
                error=f"任务执行超时: {self.config.timeout}秒",
                error_details={"timeout": self.config.timeout},
            )
            
        except Exception as e:
            # 其他错误
            error_msg = str(e)
            self.logger.exception(f"任务执行失败: {error_msg}", exc_info=e)
            return self._create_result(
                TaskStatus.FAILED,
                error=error_msg,
                error_details={"exception_type": type(e).__name__},
            )
    
    def _execute_with_timeout(self) -> TaskResult:
        """带超时控制的执行"""
        import signal
        import threading
        
        # 设置超时
        timeout = self.config.timeout
        
        def timeout_handler(signum, frame):
            raise TaskTimeoutError(timeout, self.task_id, self.name)
        
        # 设置信号处理（仅在主线程中有效）
        if threading.current_thread() is threading.main_thread():
            signal.signal(signal.SIGALRM, timeout_handler)
            signal.alarm(timeout)
        
        try:
            # 执行实际任务
            result = self._execute()
            
            # 取消超时
            if threading.current_thread() is threading.main_thread():
                signal.alarm(0)
            
            return result
            
        except TaskTimeoutError:
            raise
        except Exception as e:
            # 取消超时
            if threading.current_thread() is threading.main_thread():
                signal.alarm(0)
            raise
    
    @abc.abstractmethod
    def _execute(self) -> TaskResult:
        """
        执行任务的具体实现（抽象方法）
        
        Returns:
            TaskResult: 任务执行结果
        """
        pass
    
    def cancel(self) -> None:
        """取消任务"""
        self._cancelled = True
        if self.logger:
            self.logger.info("任务已取消")
    
    def retry(self) -> TaskResult:
        """
        重试任务
        
        Returns:
            TaskResult: 重试结果
        """
        if self.config.max_retries <= 0:
            return self._create_result(
                TaskStatus.FAILED,
                error="任务已达到最大重试次数",
                error_details={"max_retries": self.config.max_retries},
            )
        
        # 应用重试延迟
        if self.config.retry_delay > 0:
            time.sleep(self.config.retry_delay)
        
        self.logger.info(f"任务重试中 (第{self.config.parameters.get('retry_count', 0) + 1}次)")
        return self.execute()
    
    def _create_result(
        self,
        status: TaskStatus,
        data: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
        error_details: Optional[Dict[str, Any]] = None,
        metrics: Optional[Dict[str, Any]] = None,
    ) -> TaskResult:
        """创建任务结果"""
        end_time = datetime.now()
        duration = None
        if self._start_time:
            duration = (end_time - self._start_time).total_seconds()
        
        self._result = TaskResult(
            task_id=self.task_id,
            task_name=self.name,
            status=status,
            start_time=self._start_time or end_time,
            end_time=end_time,
            duration=duration,
            data=data,
            error=error,
            error_details=error_details,
            retry_count=self.config.parameters.get("retry_count", 0),
            metrics=metrics or {},
        )
        
        return self._result
    
    def validate(self) -> bool:
        """
        验证任务配置
        
        Returns:
            bool: 验证是否通过
        """
        try:
            self._validate()
            return True
        except Exception as e:
            if self.logger:
                self.logger.error(f"任务验证失败: {e}")
            return False
    
    def _validate(self) -> None:
        """
        验证任务配置的具体实现
        
        Raises:
            ValueError: 验证失败
        """
        # 检查超时设置
        if self.config.timeout <= 0:
            raise ValueError("任务超时时间必须大于0")
        
        # 检查最大运行时间
        if self.config.max_runtime is not None and self.config.max_runtime <= 0:
            raise ValueError("最大运行时间必须大于0")
        
        # 检查重试次数
        if self.config.max_retries < 0:
            raise ValueError("最大重试次数不能为负数")
        
        # 检查重试延迟
        if self.config.retry_delay < 0:
            raise ValueError("重试延迟时间不能为负数")
    
    def get_progress(self) -> float:
        """
        获取任务进度
        
        Returns:
            float: 进度百分比（0-100）
        """
        return 0.0  # 默认实现，子类可以覆盖
    
    def get_metrics(self) -> Dict[str, Any]:
        """
        获取任务指标
        
        Returns:
            Dict[str, Any]: 任务指标
        """
        return self._result.metrics if self._result else {}


class SimpleTask(BaseTask):
    """简单任务（用于测试和示例）"""
    
    def __init__(self, action: str = "echo", **kwargs):
        """
        初始化简单任务
        
        Args:
            action: 执行动作（echo, sleep, error）
            **kwargs: 额外参数
        """
        super().__init__(**kwargs)
        self.action = action
        self._progress = 0.0
    
    def _execute(self) -> TaskResult:
        """执行简单任务"""
        if self.action == "echo":
            self.logger.info("执行echo动作")
            return self._create_result(
                TaskStatus.SUCCESS,
                data={"message": "Hello from SimpleTask!"},
                metrics={"action": "echo", "executed": True},
            )
        
        elif self.action == "sleep":
            self.logger.info("执行sleep动作")
            import time
            
            # 模拟进度更新
            for i in range(10):
                time.sleep(0.1)
                self._progress = (i + 1) * 10
                self.logger.progress(i + 1, 10, "sleep进度")
            
            return self._create_result(
                TaskStatus.SUCCESS,
                data={"slept": "1秒"},
                metrics={"action": "sleep", "duration": 1.0},
            )
        
        elif self.action == "error":
            self.logger.info("执行error动作")
            raise ValueError("这是模拟的错误")
        
        else:
            raise ValueError(f"未知的动作: {self.action}")
    
    def get_progress(self) -> float:
        """获取任务进度"""
        return self._progress


class TaskFactory:
    """任务工厂"""
    
    _task_registry: Dict[str, Type[BaseTask]] = {}
    
    @classmethod
    def register(cls, task_class: Type[BaseTask]) -> None:
        """
        注册任务类
        
        Args:
            task_class: 任务类
        """
        task_name = task_class.__name__
        cls._task_registry[task_name] = task_class
    
    @classmethod
    def create(
        cls,
        task_name: str,
        task_id: Optional[str] = None,
        config: Optional[TaskConfig] = None,
        **kwargs
    ) -> BaseTask:
        """
        创建任务实例
        
        Args:
            task_name: 任务名称
            task_id: 任务ID
            config: 任务配置
            **kwargs: 额外参数
            
        Returns:
            BaseTask: 任务实例
            
        Raises:
            TaskNotFoundError: 任务未找到
        """
        from ..utils.exceptions import TaskNotFoundError
        
        if task_name not in cls._task_registry:
            raise TaskNotFoundError(task_name=task_name)
        
        task_class = cls._task_registry[task_name]
        return task_class(task_id=task_id, config=config, **kwargs)
    
    @classmethod
    def list_tasks(cls) -> List[str]:
        """
        列出所有注册的任务
        
        Returns:
            List[str]: 任务名称列表
        """
        return list(cls._task_registry.keys())
    
    @classmethod
    def get_task_class(cls, task_name: str) -> Type[BaseTask]:
        """
        获取任务类
        
        Args:
            task_name: 任务名称
            
        Returns:
            Type[BaseTask]: 任务类
            
        Raises:
            TaskNotFoundError: 任务未找到
        """
        from ..utils.exceptions import TaskNotFoundError
        
        if task_name not in cls._task_registry:
            raise TaskNotFoundError(task_name=task_name)
        
        return cls._task_registry[task_name]


# 注册内置任务
TaskFactory.register(SimpleTask)