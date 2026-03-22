"""
配置管理模块

负责监控告警系统的配置管理。
"""

import json
import yaml
from typing import Dict, List, Any, Optional
from pathlib import Path
import os

from ..utils.logging import get_logger
from .alerts import AlertRule, AlertSeverity
from .notifiers import NotificationConfig

logger = get_logger(__name__)


class MonitoringConfig:
    """监控配置"""
    
    def __init__(
        self,
        health_check_interval: int = 300,  # 健康检查间隔（秒）
        alert_check_interval: int = 60,    # 告警检查间隔（秒）
        retention_days: int = 30,          # 数据保留天数
        enable_auto_resolution: bool = False,  # 是否启用自动解决
        auto_resolution_hours: int = 24,   # 自动解决小时数
        enable_alert_suppression: bool = True,  # 是否启用告警抑制
        suppression_hours: int = 1,        # 告警抑制小时数
        
        # 通知配置
        notification_config: NotificationConfig = None,
        
        # 告警规则
        alert_rules: List[Dict[str, Any]] = None,
        
        # 组件配置
        components: Dict[str, Dict[str, Any]] = None
    ):
        """
        初始化监控配置
        
        Args:
            health_check_interval: 健康检查间隔（秒）
            alert_check_interval: 告警检查间隔（秒）
            retention_days: 数据保留天数
            enable_auto_resolution: 是否启用自动解决
            auto_resolution_hours: 自动解决小时数
            enable_alert_suppression: 是否启用告警抑制
            suppression_hours: 告警抑制小时数
            notification_config: 通知配置
            alert_rules: 告警规则列表
            components: 组件配置
        """
        self.health_check_interval = health_check_interval
        self.alert_check_interval = alert_check_interval
        self.retention_days = retention_days
        self.enable_auto_resolution = enable_auto_resolution
        self.auto_resolution_hours = auto_resolution_hours
        self.enable_alert_suppression = enable_alert_suppression
        self.suppression_hours = suppression_hours
        
        self.notification_config = notification_config or NotificationConfig()
        self.alert_rules = alert_rules or []
        self.components = components or {}
        
        logger.debug("监控配置初始化完成")
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "health_check_interval": self.health_check_interval,
            "alert_check_interval": self.alert_check_interval,
            "retention_days": self.retention_days,
            "enable_auto_resolution": self.enable_auto_resolution,
            "auto_resolution_hours": self.auto_resolution_hours,
            "enable_alert_suppression": self.enable_alert_suppression,
            "suppression_hours": self.suppression_hours,
            "notification_config": self.notification_config.to_dict(),
            "alert_rules": self.alert_rules,
            "components": self.components,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'MonitoringConfig':
        """从字典创建配置"""
        notification_config_data = data.get("notification_config", {})
        notification_config = NotificationConfig(**notification_config_data)
        
        return cls(
            health_check_interval=data.get("health_check_interval", 300),
            alert_check_interval=data.get("alert_check_interval", 60),
            retention_days=data.get("retention_days", 30),
            enable_auto_resolution=data.get("enable_auto_resolution", False),
            auto_resolution_hours=data.get("auto_resolution_hours", 24),
            enable_alert_suppression=data.get("enable_alert_suppression", True),
            suppression_hours=data.get("suppression_hours", 1),
            notification_config=notification_config,
            alert_rules=data.get("alert_rules", []),
            components=data.get("components", {})
        )


class ConfigManager:
    """配置管理器"""
    
    def __init__(self, config_dir: str = None):
        """
        初始化配置管理器
        
        Args:
            config_dir: 配置目录路径
        """
        if config_dir is None:
            # 默认配置目录
            base_dir = Path(__file__).parent.parent.parent
            self.config_dir = base_dir / "config" / "monitoring"
        else:
            self.config_dir = Path(config_dir)
        
        # 确保目录存在
        self.config_dir.mkdir(parents=True, exist_ok=True)
        
        # 配置文件路径
        self.config_file = self.config_dir / "monitoring_config.json"
        self.rules_file = self.config_dir / "alert_rules.json"
        self.notification_file = self.config_dir / "notification_config.json"
        
        # 当前配置
        self.config: Optional[MonitoringConfig] = None
        
        logger.info(f"配置管理器初始化完成，配置目录: {self.config_dir}")
    
    def load_config(self) -> MonitoringConfig:
        """加载配置"""
        try:
            if self.config_file.exists():
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                self.config = MonitoringConfig.from_dict(data)
                logger.info(f"配置已从文件加载: {self.config_file}")
            else:
                # 使用默认配置
                self.config = self.get_default_config()
                self.save_config()
                logger.info("使用默认配置并保存")
            
            # 加载告警规则
            self._load_alert_rules()
            
            # 加载通知配置
            self._load_notification_config()
            
            return self.config
            
        except Exception as e:
            logger.error(f"加载配置失败: {e}")
            # 使用默认配置
            self.config = self.get_default_config()
            return self.config
    
    def save_config(self):
        """保存配置"""
        if self.config is None:
            logger.warning("没有配置可保存")
            return
        
        try:
            # 保存主配置
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(self.config.to_dict(), f, indent=2, ensure_ascii=False)
            
            # 保存告警规则
            self._save_alert_rules()
            
            # 保存通知配置
            self._save_notification_config()
            
            logger.info(f"配置已保存到文件: {self.config_file}")
            
        except Exception as e:
            logger.error(f"保存配置失败: {e}")
    
    def _load_alert_rules(self):
        """加载告警规则"""
        if self.rules_file.exists():
            try:
                with open(self.rules_file, 'r', encoding='utf-8') as f:
                    rules_data = json.load(f)
                self.config.alert_rules = rules_data
                logger.info(f"告警规则已从文件加载: {self.rules_file}")
            except Exception as e:
                logger.error(f"加载告警规则失败: {e}")
    
    def _save_alert_rules(self):
        """保存告警规则"""
        try:
            with open(self.rules_file, 'w', encoding='utf-8') as f:
                json.dump(self.config.alert_rules, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"保存告警规则失败: {e}")
    
    def _load_notification_config(self):
        """加载通知配置"""
        if self.notification_file.exists():
            try:
                with open(self.notification_file, 'r', encoding='utf-8') as f:
                    notification_data = json.load(f)
                self.config.notification_config = NotificationConfig(**notification_data)
                logger.info(f"通知配置已从文件加载: {self.notification_file}")
            except Exception as e:
                logger.error(f"加载通知配置失败: {e}")
    
    def _save_notification_config(self):
        """保存通知配置"""
        try:
            with open(self.notification_file, 'w', encoding='utf-8') as f:
                json.dump(self.config.notification_config.to_dict(), f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"保存通知配置失败: {e}")
    
    def get_default_config(self) -> MonitoringConfig:
        """获取默认配置"""
        # 默认通知配置
        notification_config = NotificationConfig(
            email_enabled=False,
            feishu_enabled=True,
            webhook_enabled=False,
            sms_enabled=False,
            severity_filter=["error", "critical"]
        )
        
        # 默认告警规则
        default_rules = [
            {
                "rule_id": "db_connection_failed",
                "name": "数据库连接失败",
                "description": "数据库连接检查失败",
                "component": "database",
                "condition": "status_is",
                "threshold": "unhealthy",
                "severity": "critical",
                "enabled": True,
                "cooldown_minutes": 10,
                "notification_channels": ["email", "feishu"],
                "tags": ["database", "critical"]
            },
            {
                "rule_id": "cpu_high",
                "name": "CPU使用率过高",
                "description": "CPU使用率超过90%",
                "component": "system_resources",
                "condition": "greater_than",
                "threshold": 90.0,
                "severity": "error",
                "enabled": True,
                "cooldown_minutes": 10,
                "notification_channels": ["email", "feishu"],
                "tags": ["system", "cpu"]
            },
            {
                "rule_id": "memory_high",
                "name": "内存使用率过高",
                "description": "内存使用率超过90%",
                "component": "system_resources",
                "condition": "greater_than",
                "threshold": 90.0,
                "severity": "error",
                "enabled": True,
                "cooldown_minutes": 10,
                "notification_channels": ["email", "feishu"],
                "tags": ["system", "memory"]
            },
            {
                "rule_id": "disk_high",
                "name": "磁盘使用率过高",
                "description": "磁盘使用率超过95%",
                "component": "system_resources",
                "condition": "greater_than",
                "threshold": 95.0,
                "severity": "critical",
                "enabled": True,
                "cooldown_minutes": 60,
                "notification_channels": ["email", "feishu"],
                "tags": ["system", "disk", "critical"]
            },
            {
                "rule_id": "data_source_unhealthy",
                "name": "数据源连接失败",
                "description": "数据源连接状态为不健康",
                "component": "data_sources",
                "condition": "status_is",
                "threshold": "unhealthy",
                "severity": "error",
                "enabled": True,
                "cooldown_minutes": 15,
                "notification_channels": ["email", "feishu"],
                "tags": ["data_source", "connection"]
            },
        ]
        
        # 默认组件配置
        default_components = {
            "database": {
                "check_interval": 60,
                "connection_timeout": 5,
                "query_timeout": 10,
                "max_connections": 10
            },
            "data_sources": {
                "check_interval": 300,
                "timeout": 10,
                "retry_count": 3
            },
            "task_scheduler": {
                "check_interval": 60,
                "timeout": 5
            },
            "system_resources": {
                "check_interval": 60,
                "cpu_threshold": 90,
                "memory_threshold": 90,
                "disk_threshold": 95
            },
            "data_quality": {
                "check_interval": 3600,
                "score_threshold": 0.8
            }
        }
        
        return MonitoringConfig(
            health_check_interval=300,
            alert_check_interval=60,
            retention_days=30,
            enable_auto_resolution=False,
            auto_resolution_hours=24,
            enable_alert_suppression=True,
            suppression_hours=1,
            notification_config=notification_config,
            alert_rules=default_rules,
            components=default_components
        )
    
    def update_config(self, new_config: MonitoringConfig):
        """更新配置"""
        self.config = new_config
        self.save_config()
        logger.info("配置已更新")
    
    def update_notification_config(self, notification_config: NotificationConfig):
        """更新通知配置"""
        self.config.notification_config = notification_config
        self._save_notification_config()
        logger.info("通知配置已更新")
    
    def add_alert_rule(self, rule_data: Dict[str, Any]):
        """添加告警规则"""
        # 验证规则数据
        required_fields = ["rule_id", "name", "component", "condition", "threshold", "severity"]
        for field in required_fields:
            if field not in rule_data:
                raise ValueError(f"缺少必要字段: {field}")
        
        # 检查规则ID是否已存在
        for existing_rule in self.config.alert_rules:
            if existing_rule["rule_id"] == rule_data["rule_id"]:
                raise ValueError(f"规则ID已存在: {rule_data['rule_id']}")
        
        # 添加默认值
        rule_data.setdefault("enabled", True)
        rule_data.setdefault("cooldown_minutes", 5)
        rule_data.setdefault("notification_channels", [])
        rule_data.setdefault("tags", [])
        
        self.config.alert_rules.append(rule_data)
        self._save_alert_rules()
        logger.info(f"告警规则已添加: {rule_data['name']} ({rule_data['rule_id']})")
    
    def update_alert_rule(self, rule_id: str, rule_data: Dict[str, Any]):
        """更新告警规则"""
        for i, rule in enumerate(self.config.alert_rules):
            if rule["rule_id"] == rule_id:
                # 保留原始ID
                rule_data["rule_id"] = rule_id
                self.config.alert_rules[i] = rule_data
                self._save_alert_rules()
                logger.info(f"告警规则已更新: {rule_id}")
                return
        
        raise ValueError(f"规则不存在: {rule_id}")
    
    def delete_alert_rule(self, rule_id: str):
        """删除告警规则"""
        for i, rule in enumerate(self.config.alert_rules):
            if rule["rule_id"] == rule_id:
                deleted_rule = self.config.alert_rules.pop(i)
                self._save_alert_rules()
                logger.info(f"告警规则已删除: {deleted_rule['name']} ({rule_id})")
                return
        
        raise ValueError(f"规则不存在: {rule_id}")
    
    def get_alert_rule(self, rule_id: str) -> Optional[Dict[str, Any]]:
        """获取告警规则"""
        for rule in self.config.alert_rules:
            if rule["rule_id"] == rule_id:
                return rule
        return None
    
    def list_alert_rules(self, enabled_only: bool = False) -> List[Dict[str, Any]]:
        """列出告警规则"""
        if enabled_only:
            return [rule for rule in self.config.alert_rules if rule.get("enabled", True)]
        return self.config.alert_rules.copy()
    
    def export_config(self, format: str = "json") -> str:
        """导出配置"""
        if format == "json":
            return json.dumps(self.config.to_dict(), indent=2, ensure_ascii=False)
        elif format == "yaml":
            return yaml.dump(self.config.to_dict(), default_flow_style=False, allow_unicode=True)
        else:
            raise ValueError(f"不支持的格式: {format}")
    
    def import_config(self, config_data: str, format: str = "json"):
        """导入配置"""
        if format == "json":
            data = json.loads(config_data)
        elif format == "yaml":
            data = yaml.safe_load(config_data)
        else:
            raise ValueError(f"不支持的格式: {format}")
        
        self.config = MonitoringConfig.from_dict(data)
        self.save_config()
        logger.info("配置已导入")
    
    def validate_config(self) -> List[str]:
        """验证配置，返回错误列表"""
        errors = []
        
        # 验证健康检查间隔
        if self.config.health_check_interval < 10:
            errors.append("健康检查间隔不能小于10秒")
        
        # 验证告警检查间隔
        if self.config.alert_check_interval < 10:
            errors.append("告警检查间隔不能小于10秒")
        
        # 验证数据保留天数
        if self.config.retention_days < 1:
            errors.append("数据保留天数不能小于1天")
        
        # 验证告警规则
        for i, rule in enumerate(self.config.alert_rules):
            rule_id = rule.get("rule_id", f"规则#{i}")
            
            # 检查必要字段
            required_fields = ["rule_id", "name", "component", "condition", "threshold", "severity"]
            for field in required_fields:
                if field not in rule:
                    errors.append(f"规则 {rule_id} 缺少必要字段: {field}")
            
            # 检查严重级别
            if "severity" in rule:
                try:
                    AlertSeverity(rule["severity"])
                except ValueError:
                    errors.append(f"规则 {rule_id} 的严重级别无效: {rule['severity']}")
        
        return errors