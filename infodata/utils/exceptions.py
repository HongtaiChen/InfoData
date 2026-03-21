"""
异常定义

定义InfoData系统的自定义异常类。
"""

from typing import Optional, Dict, Any


class InfoDataError(Exception):
    """InfoData基础异常类"""
    
    def __init__(self, message: str, code: str = "INTERNAL_ERROR", details: Optional[Dict[str, Any]] = None):
        """
        初始化异常
        
        Args:
            message: 错误消息
            code: 错误代码
            details: 错误详情
        """
        self.message = message
        self.code = code
        self.details = details or {}
        super().__init__(self.message)
    
    def __str__(self) -> str:
        """字符串表示"""
        details_str = f", details: {self.details}" if self.details else ""
        return f"{self.code}: {self.message}{details_str}"


# 配置相关异常
class ConfigError(InfoDataError):
    """配置错误"""
    
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message, "CONFIG_ERROR", details)


class ConfigValidationError(ConfigError):
    """配置验证错误"""
    
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message, "CONFIG_VALIDATION_ERROR", details)


class ConfigNotFoundError(ConfigError):
    """配置未找到错误"""
    
    def __init__(self, config_path: str, details: Optional[Dict[str, Any]] = None):
        message = f"配置文件未找到: {config_path}"
        super().__init__(message, "CONFIG_NOT_FOUND", details)


# 数据库相关异常
class DatabaseError(InfoDataError):
    """数据库错误"""
    
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message, "DATABASE_ERROR", details)


class ConnectionError(DatabaseError):
    """数据库连接错误"""
    
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message, "CONNECTION_ERROR", details)


class TransactionError(DatabaseError):
    """事务错误"""
    
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message, "TRANSACTION_ERROR", details)


class QueryError(DatabaseError):
    """查询错误"""
    
    def __init__(self, message: str, query: Optional[str] = None, details: Optional[Dict[str, Any]] = None):
        if query:
            details = details or {}
            details["query"] = query
        super().__init__(message, "QUERY_ERROR", details)


# 调度相关异常
class SchedulerError(InfoDataError):
    """调度器错误"""
    
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message, "SCHEDULER_ERROR", details)


class TaskError(SchedulerError):
    """任务错误"""
    
    def __init__(self, message: str, task_id: Optional[str] = None, task_name: Optional[str] = None, 
                 details: Optional[Dict[str, Any]] = None):
        if task_id or task_name:
            details = details or {}
            if task_id:
                details["task_id"] = task_id
            if task_name:
                details["task_name"] = task_name
        super().__init__(message, "TASK_ERROR", details)


class TaskNotFoundError(TaskError):
    """任务未找到错误"""
    
    def __init__(self, task_id: Optional[str] = None, task_name: Optional[str] = None, 
                 details: Optional[Dict[str, Any]] = None):
        if task_id:
            message = f"任务未找到: ID={task_id}"
        elif task_name:
            message = f"任务未找到: 名称={task_name}"
        else:
            message = "任务未找到"
        super().__init__(message, task_id, task_name, details)


class TaskExecutionError(TaskError):
    """任务执行错误"""
    
    def __init__(self, message: str, task_id: Optional[str] = None, task_name: Optional[str] = None,
                 retry_count: Optional[int] = None, details: Optional[Dict[str, Any]] = None):
        if retry_count is not None:
            details = details or {}
            details["retry_count"] = retry_count
        super().__init__(message, task_id, task_name, details)


class TaskTimeoutError(TaskExecutionError):
    """任务超时错误"""
    
    def __init__(self, timeout: int, task_id: Optional[str] = None, task_name: Optional[str] = None,
                 details: Optional[Dict[str, Any]] = None):
        message = f"任务执行超时: {timeout}秒"
        details = details or {}
        details["timeout"] = timeout
        super().__init__(message, task_id, task_name, None, details)


# 数据相关异常
class DataError(InfoDataError):
    """数据错误"""
    
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message, "DATA_ERROR", details)


class DataSourceError(DataError):
    """数据源错误"""
    
    def __init__(self, message: str, source: Optional[str] = None, details: Optional[Dict[str, Any]] = None):
        if source:
            details = details or {}
            details["source"] = source
        super().__init__(message, "DATA_SOURCE_ERROR", details)


class DataValidationError(DataError):
    """数据验证错误"""
    
    def __init__(self, message: str, field: Optional[str] = None, value: Optional[Any] = None,
                 details: Optional[Dict[str, Any]] = None):
        if field or value is not None:
            details = details or {}
            if field:
                details["field"] = field
            if value is not None:
                details["value"] = value
        super().__init__(message, "DATA_VALIDATION_ERROR", details)


class DataQualityError(DataError):
    """数据质量错误"""
    
    def __init__(self, message: str, metric: Optional[str] = None, value: Optional[float] = None,
                 threshold: Optional[float] = None, details: Optional[Dict[str, Any]] = None):
        if metric or value is not None or threshold is not None:
            details = details or {}
            if metric:
                details["metric"] = metric
            if value is not None:
                details["value"] = value
            if threshold is not None:
                details["threshold"] = threshold
        super().__init__(message, "DATA_QUALITY_ERROR", details)


# 网络相关异常
class NetworkError(InfoDataError):
    """网络错误"""
    
    def __init__(self, message: str, url: Optional[str] = None, status_code: Optional[int] = None,
                 details: Optional[Dict[str, Any]] = None):
        if url or status_code is not None:
            details = details or {}
            if url:
                details["url"] = url
            if status_code is not None:
                details["status_code"] = status_code
        super().__init__(message, "NETWORK_ERROR", details)


class APIRateLimitError(NetworkError):
    """API频率限制错误"""
    
    def __init__(self, message: str, url: Optional[str] = None, retry_after: Optional[int] = None,
                 details: Optional[Dict[str, Any]] = None):
        if retry_after is not None:
            details = details or {}
            details["retry_after"] = retry_after
        super().__init__(message, url, None, details)


# 监控相关异常
class MonitoringError(InfoDataError):
    """监控错误"""
    
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message, "MONITORING_ERROR", details)


class AlertError(MonitoringError):
    """告警错误"""
    
    def __init__(self, message: str, alert_type: Optional[str] = None, details: Optional[Dict[str, Any]] = None):
        if alert_type:
            details = details or {}
            details["alert_type"] = alert_type
        super().__init__(message, "ALERT_ERROR", details)


# 系统相关异常
class SystemError(InfoDataError):
    """系统错误"""
    
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message, "SYSTEM_ERROR", details)


class ResourceError(SystemError):
    """资源错误"""
    
    def __init__(self, message: str, resource_type: Optional[str] = None, details: Optional[Dict[str, Any]] = None):
        if resource_type:
            details = details or {}
            details["resource_type"] = resource_type
        super().__init__(message, "RESOURCE_ERROR", details)


class MemoryError(ResourceError):
    """内存错误"""
    
    def __init__(self, message: str, current_usage: Optional[float] = None, limit: Optional[float] = None,
                 details: Optional[Dict[str, Any]] = None):
        if current_usage is not None or limit is not None:
            details = details or {}
            if current_usage is not None:
                details["current_usage"] = current_usage
            if limit is not None:
                details["limit"] = limit
        super().__init__(message, "memory", details)


# 业务逻辑异常
class BusinessError(InfoDataError):
    """业务逻辑错误"""
    
    def __init__(self, message: str, business_code: Optional[str] = None, details: Optional[Dict[str, Any]] = None):
        code = business_code or "BUSINESS_ERROR"
        super().__init__(message, code, details)


class ValidationError(BusinessError):
    """验证错误"""
    
    def __init__(self, message: str, field: Optional[str] = None, details: Optional[Dict[str, Any]] = None):
        if field:
            details = details or {}
            details["field"] = field
        super().__init__(message, "VALIDATION_ERROR", details)


class AuthorizationError(BusinessError):
    """授权错误"""
    
    def __init__(self, message: str, permission: Optional[str] = None, details: Optional[Dict[str, Any]] = None):
        if permission:
            details = details or {}
            details["permission"] = permission
        super().__init__(message, "AUTHORIZATION_ERROR", details)


# 工具函数
def wrap_exception(exception: Exception, wrapper_class: type, **kwargs) -> InfoDataError:
    """
    包装异常
    
    Args:
        exception: 原始异常
        wrapper_class: 包装异常类
        **kwargs: 额外参数
        
    Returns:
        InfoDataError: 包装后的异常
    """
    if isinstance(exception, InfoDataError):
        return exception
    
    message = str(exception)
    details = kwargs.get("details", {})
    details["original_exception"] = type(exception).__name__
    details["original_message"] = message
    
    return wrapper_class(message, **kwargs)


def retry_on_exception(
    exceptions: tuple,
    max_retries: int = 3,
    delay: float = 1.0,
    backoff: float = 2.0,
    logger=None
):
    """
    异常重试装饰器
    
    Args:
        exceptions: 需要重试的异常类型
        max_retries: 最大重试次数
        delay: 初始延迟时间（秒）
        backoff: 退避系数
        logger: 日志记录器
        
    Returns:
        装饰器函数
    """
    def decorator(func):
        def wrapper(*args, **kwargs):
            retry_count = 0
            current_delay = delay
            
            while True:
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    retry_count += 1
                    
                    if retry_count > max_retries:
                        if logger:
                            logger.error(f"达到最大重试次数 {max_retries}，放弃重试: {e}")
                        raise
                    
                    if logger:
                        logger.warning(
                            f"操作失败，第 {retry_count} 次重试 (延迟 {current_delay:.1f}秒): {e}"
                        )
                    
                    import time
                    time.sleep(current_delay)
                    current_delay *= backoff
        return wrapper
    return decorator