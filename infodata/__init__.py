"""
InfoData - 金融数据收集、存储和分析系统

一个基于Python的金融数据收集、存储和分析系统，支持股票、基金、债券、指数等
多种金融数据的定时收集、质量检查和智能分析。
"""

__version__ = "0.1.0"
__author__ = "InfoData Team"
__email__ = "team@infodata.example.com"
__description__ = "金融数据收集、存储和分析系统"

# 导出主要组件
from .config import ConfigManager, AppConfig
from .utils.logging import setup_logging, get_logger, TaskLogger
from .utils.database import get_db_manager, DatabaseManager, DatabaseOperations
from .utils.exceptions import InfoDataError, DatabaseError, TaskError

# 导出任务相关
from .tasks.base import BaseTask, TaskResult, TaskStatus

# 导出数据相关
from .data.collector import DataCollector
from .data.processor import DataProcessor
from .data.validator import DataValidator
from .data.storage import DataStorage

# 导出监控相关
from .monitoring.metrics import MetricsCollector
from .monitoring.alerts import AlertManager
from .monitoring.quality import DataQualityMonitor

# 导出模型
from .models.task import TaskExecution, TaskMetric
from .models.quality import DataQualityMetric

__all__ = [
    # 配置
    "ConfigManager",
    "AppConfig",
    
    # 工具
    "setup_logging",
    "get_logger",
    "TaskLogger",
    "get_db_manager",
    "DatabaseManager",
    "DatabaseOperations",
    
    # 异常
    "InfoDataError",
    "DatabaseError",
    "TaskError",
    
    # 任务
    "BaseTask",
    "TaskResult",
    "TaskStatus",
    
    # 数据
    "DataCollector",
    "DataProcessor",
    "DataValidator",
    "DataStorage",
    
    # 监控
    "MetricsCollector",
    "AlertManager",
    "DataQualityMonitor",
    
    # 模型
    "TaskExecution",
    "TaskMetric",
    "DataQualityMetric",
]

# 初始化日志
import logging
logging.getLogger(__name__).addHandler(logging.NullHandler())

# 版本信息
def get_version() -> str:
    """获取版本信息"""
    return __version__

def get_system_info() -> dict:
    """获取系统信息"""
    import sys
    import platform
    
    return {
        "version": __version__,
        "python_version": sys.version,
        "platform": platform.platform(),
        "system": platform.system(),
        "machine": platform.machine(),
    }