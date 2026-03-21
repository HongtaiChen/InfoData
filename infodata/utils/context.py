"""
上下文管理工具

提供线程安全的上下文管理，用于传递任务ID、用户信息等上下文数据。
"""

import threading
from typing import Any, Dict, Optional
from contextvars import ContextVar

# 线程本地存储
_thread_local = threading.local()

# 上下文变量
_context_var: ContextVar[Dict[str, Any]] = ContextVar("infodata_context", default={})


def get_context() -> Dict[str, Any]:
    """
    获取当前上下文
    
    Returns:
        Dict[str, Any]: 上下文字典
    """
    return _context_var.get().copy()


def set_context(context: Dict[str, Any]) -> None:
    """
    设置当前上下文
    
    Args:
        context: 上下文字典
    """
    _context_var.set(context.copy())


def update_context(**kwargs) -> None:
    """
    更新当前上下文
    
    Args:
        **kwargs: 要更新的键值对
    """
    current = get_context()
    current.update(kwargs)
    set_context(current)


def clear_context() -> None:
    """清除当前上下文"""
    _context_var.set({})


def get_context_value(key: str, default: Any = None) -> Any:
    """
    获取上下文中的特定值
    
    Args:
        key: 键名
        default: 默认值
        
    Returns:
        Any: 值
    """
    return get_context().get(key, default)


def set_context_value(key: str, value: Any) -> None:
    """
    设置上下文中的特定值
    
    Args:
        key: 键名
        value: 值
    """
    update_context(**{key: value})


class TaskContext:
    """任务上下文管理器"""
    
    def __init__(self, task_id: str, task_name: str, **extra):
        """
        初始化任务上下文
        
        Args:
            task_id: 任务ID
            task_name: 任务名称
            **extra: 额外上下文信息
        """
        self.task_id = task_id
        self.task_name = task_name
        self.extra = extra
        self.old_context = {}
    
    def __enter__(self):
        """进入任务上下文"""
        self.old_context = get_context()
        
        task_context = {
            "task_id": self.task_id,
            "task_name": self.task_name,
            "task_start_time": self.extra.get("start_time"),
            **self.extra
        }
        
        set_context(task_context)
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """退出任务上下文"""
        set_context(self.old_context)
    
    def update(self, **kwargs) -> None:
        """更新任务上下文"""
        update_context(**kwargs)


class DatabaseContext:
    """数据库上下文管理器"""
    
    def __init__(self, connection_id: Optional[str] = None, **extra):
        """
        初始化数据库上下文
        
        Args:
            connection_id: 连接ID
            **extra: 额外上下文信息
        """
        self.connection_id = connection_id
        self.extra = extra
        self.old_context = {}
    
    def __enter__(self):
        """进入数据库上下文"""
        self.old_context = get_context()
        
        db_context = {
            "db_connection_id": self.connection_id,
            "db_operation": self.extra.get("operation"),
            "db_table": self.extra.get("table"),
            **self.extra
        }
        
        set_context(db_context)
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """退出数据库上下文"""
        set_context(self.old_context)


class RequestContext:
    """请求上下文管理器"""
    
    def __init__(self, request_id: str, endpoint: str, **extra):
        """
        初始化请求上下文
        
        Args:
            request_id: 请求ID
            endpoint: 端点路径
            **extra: 额外上下文信息
        """
        self.request_id = request_id
        self.endpoint = endpoint
        self.extra = extra
        self.old_context = {}
    
    def __enter__(self):
        """进入请求上下文"""
        self.old_context = get_context()
        
        request_context = {
            "request_id": self.request_id,
            "endpoint": self.endpoint,
            "request_start_time": self.extra.get("start_time"),
            **self.extra
        }
        
        set_context(request_context)
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """退出请求上下文"""
        set_context(self.old_context)


def with_context(**context_kwargs):
    """
    为函数添加上下文的装饰器
    
    Args:
        **context_kwargs: 上下文参数
        
    Returns:
        装饰器函数
    """
    def decorator(func):
        def wrapper(*args, **kwargs):
            old_context = get_context()
            try:
                update_context(**context_kwargs)
                return func(*args, **kwargs)
            finally:
                set_context(old_context)
        return wrapper
    return decorator


# 预定义的上下文键
CONTEXT_KEYS = {
    # 任务相关
    "TASK_ID": "task_id",
    "TASK_NAME": "task_name",
    "TASK_START_TIME": "task_start_time",
    "TASK_END_TIME": "task_end_time",
    
    # 数据库相关
    "DB_CONNECTION_ID": "db_connection_id",
    "DB_OPERATION": "db_operation",
    "DB_TABLE": "db_table",
    "DB_QUERY": "db_query",
    
    # 请求相关
    "REQUEST_ID": "request_id",
    "ENDPOINT": "endpoint",
    "USER_ID": "user_id",
    "CLIENT_IP": "client_ip",
    
    # 系统相关
    "CORRELATION_ID": "correlation_id",
    "TRACE_ID": "trace_id",
    "SPAN_ID": "span_id",
    
    # 性能相关
    "EXECUTION_TIME": "execution_time",
    "MEMORY_USAGE": "memory_usage",
}


def create_correlation_id() -> str:
    """
    创建关联ID
    
    Returns:
        str: 关联ID
    """
    import uuid
    import time
    
    timestamp = int(time.time() * 1000)
    unique_id = str(uuid.uuid4())[:8]
    return f"{timestamp}-{unique_id}"


def get_correlation_id() -> str:
    """
    获取当前关联ID，如果不存在则创建
    
    Returns:
        str: 关联ID
    """
    correlation_id = get_context_value(CONTEXT_KEYS["CORRELATION_ID"])
    if not correlation_id:
        correlation_id = create_correlation_id()
        set_context_value(CONTEXT_KEYS["CORRELATION_ID"], correlation_id)
    
    return correlation_id


# 线程本地存储工具（向后兼容）
def get_thread_local(key: str, default: Any = None) -> Any:
    """
    获取线程本地存储的值
    
    Args:
        key: 键名
        default: 默认值
        
    Returns:
        Any: 值
    """
    return getattr(_thread_local, key, default)


def set_thread_local(key: str, value: Any) -> None:
    """
    设置线程本地存储的值
    
    Args:
        key: 键名
        value: 值
    """
    setattr(_thread_local, key, value)


def clear_thread_local(key: str) -> None:
    """
    清除线程本地存储的值
    
    Args:
        key: 键名
    """
    if hasattr(_thread_local, key):
        delattr(_thread_local, key)