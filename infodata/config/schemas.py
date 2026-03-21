"""
配置验证模式

使用Pydantic定义配置数据结构并进行验证。
"""

from typing import Dict, List, Optional, Any
from pydantic import BaseModel, Field, validator, field_validator
from datetime import time


class DatabaseConfig(BaseModel):
    """数据库配置"""
    
    mysql: Dict[str, Any] = Field(
        default={
            "host": "localhost",
            "port": 3306,
            "user": "root",
            "password": "",
            "database": "infodata",
            "pool_size": 10,
            "echo": False,
        },
        description="MySQL数据库配置"
    )
    
    sqlite: Optional[Dict[str, Any]] = Field(
        default=None,
        description="SQLite数据库配置（开发环境使用）"
    )
    
    @field_validator('mysql')
    @classmethod
    def validate_mysql_config(cls, v: Dict[str, Any]) -> Dict[str, Any]:
        """验证MySQL配置"""
        if not v.get('host'):
            raise ValueError("MySQL host不能为空")
        if not 1 <= v.get('port', 0) <= 65535:
            raise ValueError("MySQL port必须在1-65535之间")
        if not v.get('database'):
            raise ValueError("MySQL database不能为空")
        if v.get('pool_size', 0) <= 0:
            v['pool_size'] = 10
        return v


class SchedulerConfig(BaseModel):
    """调度器配置"""
    
    timezone: str = Field(
        default="Asia/Shanghai",
        description="调度器时区"
    )
    
    jobstore: str = Field(
        default="sqlalchemy",
        description="任务存储后端"
    )
    
    executor: str = Field(
        default="process",
        description="执行器类型"
    )
    
    max_instances: int = Field(
        default=10,
        ge=1,
        le=100,
        description="最大并发实例数"
    )
    
    coalesce: bool = Field(
        default=True,
        description="是否合并错过执行的任务"
    )
    
    misfire_grace_time: int = Field(
        default=300,
        ge=0,
        description="任务错过执行的宽限时间（秒）"
    )
    
    @field_validator('timezone')
    @classmethod
    def validate_timezone(cls, v: str) -> str:
        """验证时区"""
        # 这里可以添加时区验证逻辑
        # 暂时只检查是否为非空字符串
        if not v:
            raise ValueError("时区不能为空")
        return v


class TaskScheduleConfig(BaseModel):
    """任务调度配置"""
    
    enabled: bool = Field(
        default=True,
        description="是否启用任务"
    )
    
    schedule: str = Field(
        default="0 19 * * *",
        description="cron表达式或间隔时间"
    )
    
    retry_count: int = Field(
        default=3,
        ge=0,
        le=10,
        description="重试次数"
    )
    
    retry_delay: int = Field(
        default=300,
        ge=0,
        description="重试延迟时间（秒）"
    )
    
    timeout: int = Field(
        default=1800,
        ge=60,
        description="任务超时时间（秒）"
    )
    
    max_runtime: Optional[int] = Field(
        default=None,
        ge=60,
        description="最大运行时间（秒），用于确保22:00前完成"
    )
    
    @field_validator('schedule')
    @classmethod
    def validate_schedule(cls, v: str) -> str:
        """验证调度表达式"""
        if not v:
            raise ValueError("调度表达式不能为空")
        return v
    
    @field_validator('max_runtime')
    @classmethod
    def set_max_runtime_for_daily_tasks(cls, v: Optional[int], info) -> Optional[int]:
        """为日度任务设置最大运行时间"""
        if v is None:
            # 如果是日度股票任务，确保22:00前完成（3小时）
            if info.data.get('schedule', '').startswith('0 19'):
                return 10800  # 3小时
        return v


class TaskConfig(BaseModel):
    """任务配置"""
    
    stock_daily_update: TaskScheduleConfig = Field(
        default_factory=lambda: TaskScheduleConfig(
            schedule="0 19 * * *",
            max_runtime=10800,  # 3小时，确保22:00前完成
            timeout=3600,
        ),
        description="股票日度更新任务"
    )
    
    stock_info_weekly_update: TaskScheduleConfig = Field(
        default_factory=lambda: TaskScheduleConfig(
            schedule="0 2 * * 1",  # 每周一2:00
            timeout=7200,
        ),
        description="股票信息周度更新任务"
    )
    
    fund_monthly_update: TaskScheduleConfig = Field(
        default_factory=lambda: TaskScheduleConfig(
            schedule="0 3 1 * *",  # 每月1号3:00
            timeout=3600,
        ),
        description="基金月度更新任务"
    )
    
    bond_monthly_update: TaskScheduleConfig = Field(
        default_factory=lambda: TaskScheduleConfig(
            schedule="0 3 5 * *",  # 每月5号3:00
            timeout=3600,
        ),
        description="债券月度更新任务"
    )
    
    index_monthly_update: TaskScheduleConfig = Field(
        default_factory=lambda: TaskScheduleConfig(
            schedule="0 3 10 * *",  # 每月10号3:00
            timeout=3600,
        ),
        description="指数月度更新任务"
    )
    
    history_sync: TaskScheduleConfig = Field(
        default_factory=lambda: TaskScheduleConfig(
            enabled=False,  # 手动触发
            schedule="0 0 * * *",
            timeout=86400,  # 24小时
        ),
        description="历史数据同步任务"
    )


class DataSourceConfig(BaseModel):
    """数据源配置"""
    
    akshare: Dict[str, Any] = Field(
        default={
            "enabled": True,
            "timeout": 30,
            "retry_count": 3,
        },
        description="AKShare数据源配置"
    )
    
    tushare: Dict[str, Any] = Field(
        default={
            "enabled": True,
            "token": "",
            "timeout": 30,
            "retry_count": 3,
        },
        description="Tushare数据源配置"
    )
    
    @field_validator('tushare')
    @classmethod
    def validate_tushare_token(cls, v: Dict[str, Any]) -> Dict[str, Any]:
        """验证Tushare token"""
        if v.get('enabled', False) and not v.get('token'):
            raise ValueError("Tushare启用时必须提供token")
        return v


class AlertConfig(BaseModel):
    """告警配置"""
    
    email: Dict[str, Any] = Field(
        default={
            "enabled": False,
            "smtp_server": "",
            "smtp_port": 587,
            "username": "",
            "password": "",
            "from_addr": "",
            "to_addrs": [],
        },
        description="邮件告警配置"
    )
    
    webhook: Dict[str, Any] = Field(
        default={
            "enabled": False,
            "url": "",
            "timeout": 10,
            "retry_count": 3,
        },
        description="Webhook告警配置"
    )
    
    thresholds: Dict[str, Any] = Field(
        default={
            "task_failure_rate": 0.1,  # 任务失败率阈值
            "data_accuracy": 0.9999,   # 数据准确性阈值 (99.99%)
            "update_delay": 10800,     # 更新延迟阈值 (3小时)
            "system_cpu": 0.8,         # CPU使用率阈值
            "system_memory": 0.8,      # 内存使用率阈值
        },
        description="告警阈值配置"
    )


class MonitoringConfig(BaseModel):
    """监控配置"""
    
    enabled: bool = Field(
        default=True,
        description="是否启用监控"
    )
    
    metrics_port: int = Field(
        default=9090,
        ge=1024,
        le=65535,
        description="监控指标端口"
    )
    
    alert: AlertConfig = Field(
        default_factory=AlertConfig,
        description="告警配置"
    )
    
    data_quality: Dict[str, Any] = Field(
        default={
            "check_interval": 3600,  # 数据质量检查间隔（秒）
            "accuracy_threshold": 0.9999,  # 数据准确性阈值
            "completeness_threshold": 0.99,  # 数据完整性阈值
            "timeliness_threshold": 10800,  # 数据及时性阈值（秒）
        },
        description="数据质量监控配置"
    )


class AppConfig(BaseModel):
    """应用配置"""
    
    app_name: str = Field(
        default="InfoData",
        description="应用名称"
    )
    
    version: str = Field(
        default="0.1.0",
        description="应用版本"
    )
    
    environment: str = Field(
        default="development",
        description="运行环境"
    )
    
    log_level: str = Field(
        default="INFO",
        description="日志级别"
    )
    
    log_file: str = Field(
        default="logs/infodata.log",
        description="日志文件路径"
    )
    
    database: DatabaseConfig = Field(
        default_factory=DatabaseConfig,
        description="数据库配置"
    )
    
    scheduler: SchedulerConfig = Field(
        default_factory=SchedulerConfig,
        description="调度器配置"
    )
    
    tasks: TaskConfig = Field(
        default_factory=TaskConfig,
        description="任务配置"
    )
    
    data_sources: DataSourceConfig = Field(
        default_factory=DataSourceConfig,
        description="数据源配置"
    )
    
    monitoring: MonitoringConfig = Field(
        default_factory=MonitoringConfig,
        description="监控配置"
    )
    
    @field_validator('environment')
    @classmethod
    def validate_environment(cls, v: str) -> str:
        """验证运行环境"""
        valid_environments = ['development', 'testing', 'production']
        if v not in valid_environments:
            raise ValueError(f"环境必须是以下之一: {valid_environments}")
        return v
    
    @field_validator('log_level')
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        """验证日志级别"""
        valid_levels = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']
        if v.upper() not in valid_levels:
            raise ValueError(f"日志级别必须是以下之一: {valid_levels}")
        return v.upper()