"""
日志系统

提供结构化的日志记录功能，支持JSON格式、分级日志和日志轮转。
"""

import os
import sys
import json
import logging
import logging.handlers
from pathlib import Path
from typing import Any, Dict, Optional
from datetime import datetime
from .context import get_context


class JSONFormatter(logging.Formatter):
    """JSON格式日志格式化器"""
    
    def format(self, record: logging.LogRecord) -> str:
        """格式化日志记录为JSON字符串"""
        log_data: Dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }
        
        # 添加上下文信息
        context = get_context()
        if context:
            log_data.update(context)
        
        # 添加异常信息
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)
        
        # 添加额外字段
        if hasattr(record, "extra"):
            log_data.update(record.extra)
        
        return json.dumps(log_data, ensure_ascii=False)


class TaskLogFilter(logging.Filter):
    """任务日志过滤器"""
    
    def filter(self, record: logging.LogRecord) -> bool:
        """过滤日志记录"""
        # 添加任务上下文
        if not hasattr(record, "task_id"):
            record.task_id = get_context().get("task_id", "")
        if not hasattr(record, "task_name"):
            record.task_name = get_context().get("task_name", "")
        
        return True


def setup_logging(
    log_level: str = "INFO",
    log_file: Optional[str] = None,
    enable_console: bool = True,
    enable_json: bool = False,
    max_bytes: int = 10 * 1024 * 1024,  # 10MB
    backup_count: int = 5,
) -> None:
    """
    设置日志系统
    
    Args:
        log_level: 日志级别
        log_file: 日志文件路径，如果为None则只输出到控制台
        enable_console: 是否启用控制台输出
        enable_json: 是否使用JSON格式
        max_bytes: 日志文件最大大小
        backup_count: 备份文件数量
    """
    # 清除现有的日志处理器
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    
    # 设置日志级别
    level = getattr(logging, log_level.upper())
    root_logger.setLevel(level)
    
    # 创建格式化器
    if enable_json:
        formatter = JSONFormatter()
    else:
        formatter = logging.Formatter(
            fmt="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
    
    # 添加控制台处理器
    if enable_console:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(level)
        console_handler.setFormatter(formatter)
        console_handler.addFilter(TaskLogFilter())
        root_logger.addHandler(console_handler)
    
    # 添加文件处理器
    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        
        file_handler = logging.handlers.RotatingFileHandler(
            filename=log_path,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding='utf-8'
        )
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)
        file_handler.addFilter(TaskLogFilter())
        root_logger.addHandler(file_handler)
    
    # 设置第三方库的日志级别
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """
    获取日志记录器
    
    Args:
        name: 日志记录器名称
        
    Returns:
        logging.Logger: 日志记录器
    """
    return logging.getLogger(name)


class TaskLogger:
    """任务专用日志记录器"""
    
    def __init__(self, task_id: str, task_name: str):
        """
        初始化任务日志记录器
        
        Args:
            task_id: 任务ID
            task_name: 任务名称
        """
        self.task_id = task_id
        self.task_name = task_name
        self.logger = logging.getLogger(f"task.{task_name}")
        
        # 添加上下文信息
        self.extra = {
            "task_id": task_id,
            "task_name": task_name,
        }
    
    def debug(self, message: str, **kwargs) -> None:
        """记录DEBUG级别日志"""
        self.logger.debug(message, extra={**self.extra, **kwargs})
    
    def info(self, message: str, **kwargs) -> None:
        """记录INFO级别日志"""
        self.logger.info(message, extra={**self.extra, **kwargs})
    
    def warning(self, message: str, **kwargs) -> None:
        """记录WARNING级别日志"""
        self.logger.warning(message, extra={**self.extra, **kwargs})
    
    def error(self, message: str, **kwargs) -> None:
        """记录ERROR级别日志"""
        self.logger.error(message, extra={**self.extra, **kwargs})
    
    def critical(self, message: str, **kwargs) -> None:
        """记录CRITICAL级别日志"""
        self.logger.critical(message, extra={**self.extra, **kwargs})
    
    def exception(self, message: str, exc_info: Optional[Exception] = None, **kwargs) -> None:
        """记录异常日志"""
        self.logger.exception(message, exc_info=exc_info, extra={**self.extra, **kwargs})
    
    def progress(self, current: int, total: int, message: str = "", **kwargs) -> None:
        """记录进度日志"""
        if total > 0:
            percentage = (current / total) * 100
            progress_msg = f"{message} [{current}/{total}] ({percentage:.1f}%)"
        else:
            progress_msg = f"{message} [{current}]"
        
        self.info(progress_msg, **kwargs)
    
    def start(self, **kwargs) -> None:
        """记录任务开始日志"""
        self.info(f"任务开始: {self.task_name}", **kwargs)
    
    def complete(self, duration: float, **kwargs) -> None:
        """记录任务完成日志"""
        self.info(f"任务完成: {self.task_name} (耗时: {duration:.2f}秒)", **kwargs)
    
    def fail(self, error: str, **kwargs) -> None:
        """记录任务失败日志"""
        self.error(f"任务失败: {self.task_name} - {error}", **kwargs)


def log_execution_time(logger: logging.Logger):
    """
    记录函数执行时间的装饰器
    
    Args:
        logger: 日志记录器
    """
    def decorator(func):
        def wrapper(*args, **kwargs):
            import time
            start_time = time.time()
            try:
                result = func(*args, **kwargs)
                end_time = time.time()
                duration = end_time - start_time
                logger.debug(
                    f"函数 {func.__name__} 执行完成",
                    extra={
                        "function": func.__name__,
                        "duration": duration,
                        "module": func.__module__,
                    }
                )
                return result
            except Exception as e:
                end_time = time.time()
                duration = end_time - start_time
                logger.error(
                    f"函数 {func.__name__} 执行失败",
                    extra={
                        "function": func.__name__,
                        "duration": duration,
                        "module": func.__module__,
                        "error": str(e),
                    },
                    exc_info=True
                )
                raise
        return wrapper
    return decorator


class LogContext:
    """日志上下文管理器"""
    
    def __init__(self, **context):
        """
        初始化日志上下文
        
        Args:
            **context: 上下文键值对
        """
        self.context = context
        self.old_context = {}
    
    def __enter__(self):
        """进入上下文"""
        from .context import set_context, get_context
        self.old_context = get_context()
        set_context({**self.old_context, **self.context})
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """退出上下文"""
        from .context import set_context
        set_context(self.old_context)


# 默认日志配置
DEFAULT_LOG_CONFIG = {
    "level": "INFO",
    "file": "logs/infodata.log",
    "console": True,
    "json": False,
    "max_bytes": 10 * 1024 * 1024,  # 10MB
    "backup_count": 5,
}