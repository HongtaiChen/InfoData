"""
监控服务模块

主监控服务，负责协调健康检查、告警触发和通知发送。
"""

import time
import threading
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
import signal
import sys

from ..utils.logging import get_logger
from .alerts import AlertManager, Alert
from .health import HealthChecker
from .notifiers import NotificationManager, NotificationConfig
from .dashboard import MonitoringDashboard
from .config import ConfigManager, MonitoringConfig
from ..task_scheduler import TaskScheduler

logger = get_logger(__name__)


class MonitoringService:
    """监控服务"""
    
    def __init__(self, config_manager: ConfigManager = None):
        """
        初始化监控服务
        
        Args:
            config_manager: 配置管理器实例
        """
        self.config_manager = config_manager or ConfigManager()
        self.config = self.config_manager.load_config()
        
        # 初始化组件
        self.health_checker = HealthChecker()
        self.alert_manager = AlertManager(self.health_checker)
        self.notification_manager = NotificationManager(self.config.notification_config)
        self.dashboard = MonitoringDashboard(self.alert_manager, self.health_checker)
        
        # 任务调度器
        self.task_scheduler = TaskScheduler()
        
        # 服务状态
        self.running = False
        self.health_check_thread: Optional[threading.Thread] = None
        self.alert_check_thread: Optional[threading.Thread] = None
        self.cleanup_thread: Optional[threading.Thread] = None
        
        # 统计信息
        self.stats = {
            "start_time": datetime.now(),
            "health_checks": 0,
            "alerts_triggered": 0,
            "notifications_sent": 0,
            "last_health_check": None,
            "last_alert_check": None,
        }
        
        # 加载告警规则
        self._load_alert_rules()
        
        logger.info("监控服务初始化完成")
    
    def _load_alert_rules(self):
        """加载告警规则"""
        try:
            rules = self.config_manager.list_alert_rules()
            for rule_data in rules:
                try:
                    # 创建告警规则对象
                    from .alerts import AlertRule, AlertSeverity
                    
                    severity = AlertSeverity(rule_data["severity"])
                    
                    rule = AlertRule(
                        rule_id=rule_data["rule_id"],
                        name=rule_data["name"],
                        description=rule_data.get("description", ""),
                        component=rule_data["component"],
                        condition=rule_data["condition"],
                        threshold=rule_data["threshold"],
                        severity=severity,
                        enabled=rule_data.get("enabled", True),
                        cooldown_minutes=rule_data.get("cooldown_minutes", 5),
                        notification_channels=rule_data.get("notification_channels", []),
                        tags=rule_data.get("tags", [])
                    )
                    
                    self.alert_manager.add_rule(rule)
                    
                except Exception as e:
                    logger.error(f"加载告警规则失败 {rule_data.get('rule_id', 'unknown')}: {e}")
            
            logger.info(f"已加载 {len(rules)} 个告警规则")
            
        except Exception as e:
            logger.error(f"加载告警规则失败: {e}")
    
    def start(self):
        """启动监控服务"""
        if self.running:
            logger.warning("监控服务已经在运行")
            return
        
        logger.info("启动监控服务...")
        self.running = True
        
        # 注册信号处理
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
        
        # 启动健康检查线程
        self.health_check_thread = threading.Thread(
            target=self._health_check_loop,
            name="HealthCheckThread",
            daemon=True
        )
        self.health_check_thread.start()
        
        # 启动告警检查线程
        self.alert_check_thread = threading.Thread(
            target=self._alert_check_loop,
            name="AlertCheckThread",
            daemon=True
        )
        self.alert_check_thread.start()
        
        # 启动清理线程
        self.cleanup_thread = threading.Thread(
            target=self._cleanup_loop,
            name="CleanupThread",
            daemon=True
        )
        self.cleanup_thread.start()
        
        # 注册定时任务
        self._register_scheduled_tasks()
        
        logger.info("监控服务已启动")
    
    def stop(self):
        """停止监控服务"""
        if not self.running:
            return
        
        logger.info("停止监控服务...")
        self.running = False
        
        # 等待线程结束
        if self.health_check_thread:
            self.health_check_thread.join(timeout=5)
        if self.alert_check_thread:
            self.alert_check_thread.join(timeout=5)
        if self.cleanup_thread:
            self.cleanup_thread.join(timeout=5)
        
        # 停止任务调度器
        self.task_scheduler.stop()
        
        logger.info("监控服务已停止")
    
    def _signal_handler(self, signum, frame):
        """信号处理"""
        logger.info(f"收到信号 {signum}，准备停止服务")
        self.stop()
        sys.exit(0)
    
    def _health_check_loop(self):
        """健康检查循环"""
        logger.info("健康检查线程启动")
        
        while self.running:
            try:
                # 执行健康检查
                self._perform_health_check()
                
                # 更新统计
                self.stats["health_checks"] += 1
                self.stats["last_health_check"] = datetime.now()
                
            except Exception as e:
                logger.error(f"健康检查执行失败: {e}")
            
            # 等待下一次检查
            time.sleep(self.config.health_check_interval)
        
        logger.info("健康检查线程停止")
    
    def _perform_health_check(self):
        """执行健康检查"""
        logger.debug("执行健康检查")
        
        # 执行健康检查
        health_results = self.health_checker.check_all()
        
        # 检查并触发告警
        new_alerts = self.alert_manager.check_health_and_trigger_alerts()
        
        # 发送通知
        if new_alerts:
            self._send_alerts_notifications(new_alerts)
            self.stats["alerts_triggered"] += len(new_alerts)
        
        # 更新仪表板
        self.dashboard.refresh()
        
        logger.debug(f"健康检查完成，发现 {len(new_alerts)} 个新告警")
    
    def _alert_check_loop(self):
        """告警检查循环"""
        logger.info("告警检查线程启动")
        
        while self.running:
            try:
                # 检查是否需要自动解决告警
                if self.config.enable_auto_resolution:
                    self._auto_resolve_alerts()
                
                # 检查是否需要抑制告警
                if self.config.enable_alert_suppression:
                    self._suppress_old_alerts()
                
                # 更新统计
                self.stats["last_alert_check"] = datetime.now()
                
            except Exception as e:
                logger.error(f"告警检查执行失败: {e}")
            
            # 等待下一次检查
            time.sleep(self.config.alert_check_interval)
        
        logger.info("告警检查线程停止")
    
    def _auto_resolve_alerts(self):
        """自动解决告警"""
        current_time = datetime.now()
        auto_resolution_time = current_time - timedelta(hours=self.config.auto_resolution_hours)
        
        active_alerts = self.alert_manager.get_active_alerts()
        for alert in active_alerts:
            # 只自动解决已确认的告警
            from .alerts import AlertStatus
            if alert.status == AlertStatus.ACKNOWLEDGED:
                # 检查告警时间
                if alert.timestamp < auto_resolution_time:
                    alert.resolve("自动解决：超过自动解决时间阈值")
                    logger.info(f"告警自动解决: {alert.alert_id}")
    
    def _suppress_old_alerts(self):
        """抑制旧告警"""
        current_time = datetime.now()
        suppression_time = current_time - timedelta(hours=self.config.suppression_hours)
        
        active_alerts = self.alert_manager.get_active_alerts()
        for alert in active_alerts:
            # 只抑制未确认的告警
            from .alerts import AlertStatus
            if alert.status == AlertStatus.ACTIVE:
                # 检查告警时间
                if alert.timestamp < suppression_time:
                    alert.suppress()
                    logger.info(f"告警自动抑制: {alert.alert_id}")
    
    def _cleanup_loop(self):
        """清理循环"""
        logger.info("清理线程启动")
        
        while self.running:
            try:
                # 清理旧数据
                self._cleanup_old_data()
                
            except Exception as e:
                logger.error(f"清理执行失败: {e}")
            
            # 每天清理一次
            time.sleep(86400)  # 24小时
        
        logger.info("清理线程停止")
    
    def _cleanup_old_data(self):
        """清理旧数据"""
        logger.debug("执行数据清理")
        
        # 计算清理时间点
        cleanup_time = datetime.now() - timedelta(days=self.config.retention_days)
        
        # 清理告警历史
        alert_history = self.alert_manager.alert_history
        original_count = len(alert_history)
        
        # 保留最近的数据
        self.alert_manager.alert_history = [
            alert for alert in alert_history
            if alert.timestamp > cleanup_time
        ]
        
        removed_count = original_count - len(self.alert_manager.alert_history)
        if removed_count > 0:
            logger.info(f"清理了 {removed_count} 个旧告警记录")
    
    def _send_alerts_notifications(self, alerts: List[Alert]):
        """发送告警通知"""
        for alert in alerts:
            # 获取规则指定的通知渠道
            channels = alert.rule.notification_channels
            
            # 发送通知
            results = self.notification_manager.send_alert(alert, channels)
            
            # 更新告警的通知状态
            for channel, success in results.items():
                if success:
                    alert.notifications_sent.append(channel)
            
            # 更新统计
            self.stats["notifications_sent"] += sum(1 for success in results.values() if success)
    
    def _register_scheduled_tasks(self):
        """注册定时任务"""
        # 每小时执行一次完整健康检查
        self.task_scheduler.add_task(
            task_id="hourly_health_check",
            func=self._perform_health_check,
            schedule="0 * * * *",  # 每小时的第0分钟
            description="每小时健康检查"
        )
        
        # 每天凌晨执行数据清理
        self.task_scheduler.add_task(
            task_id="daily_cleanup",
            func=self._cleanup_old_data,
            schedule="0 2 * * *",  # 每天凌晨2点
            description="每日数据清理"
        )
        
        # 启动任务调度器
        self.task_scheduler.start()
        
        logger.info("定时任务已注册")
    
    def get_service_status(self) -> Dict[str, Any]:
        """获取服务状态"""
        return {
            "running": self.running,
            "start_time": self.stats["start_time"].isoformat(),
            "health_checks": self.stats["health_checks"],
            "alerts_triggered": self.stats["alerts_triggered"],
            "notifications_sent": self.stats["notifications_sent"],
            "last_health_check": self.stats["last_health_check"].isoformat() if self.stats["last_health_check"] else None,
            "last_alert_check": self.stats["last_alert_check"].isoformat() if self.stats["last_alert_check"] else None,
            "threads": {
                "health_check": self.health_check_thread.is_alive() if self.health_check_thread else False,
                "alert_check": self.alert_check_thread.is_alive() if self.alert_check_thread else False,
                "cleanup": self.cleanup_thread.is_alive() if self.cleanup_thread else False,
            },
            "config": {
                "health_check_interval": self.config.health_check_interval,
                "alert_check_interval": self.config.alert_check_interval,
                "retention_days": self.config.retention_days,
            }
        }
    
    def update_config(self, new_config: MonitoringConfig):
        """更新配置"""
        self.config = new_config
        self.config_manager.update_config(new_config)
        
        # 更新通知管理器配置
        self.notification_manager.update_config(new_config.notification_config)
        
        # 重新加载告警规则
        self._load_alert_rules()
        
        logger.info("服务配置已更新")
    
    def run_manual_health_check(self) -> Dict[str, Any]:
        """手动运行健康检查"""
        try:
            # 执行健康检查
            health_results = self.health_checker.check_all()
            
            # 检查并触发告警
            new_alerts = self.alert_manager.check_health_and_trigger_alerts()
            
            # 发送通知
            if new_alerts:
                self._send_alerts_notifications(new_alerts)
            
            # 更新统计
            self.stats["health_checks"] += 1
            self.stats["last_health_check"] = datetime.now()
            self.stats["alerts_triggered"] += len(new_alerts)
            
            return {
                "success": True,
                "health_results": health_results,
                "new_alerts": len(new_alerts),
                "timestamp": datetime.now().isoformat()
            }
            
        except Exception as e:
            logger.error(f"手动健康检查失败: {e}")
            return {
                "success": False,
                "error": str(e),
                "timestamp": datetime.now().isoformat()
            }
    
    def get_dashboard_data(self) -> Dict[str, Any]:
        """获取仪表板数据"""
        return self.dashboard.get_dashboard_data()
    
    def acknowledge_alert(self, alert_id: str, user: str, notes: str = None):
        """确认告警"""
        self.dashboard.acknowledge_alert(alert_id, user, notes)
    
    def resolve_alert(self, alert_id: str, notes: str = None):
        """解决告警"""
        self.dashboard.resolve_alert(alert_id, notes)
    
    def suppress_alert(self, alert_id: str):
        """抑制告警"""
        self.dashboard.suppress_alert(alert_id)
    
    def get_notification_stats(self) -> Dict[str, Any]:
        """获取通知统计"""
        return self.notification_manager.get_stats()


def create_monitoring_service(config_dir: str = None) -> MonitoringService:
    """创建监控服务实例"""
    config_manager = ConfigManager(config_dir)
    return MonitoringService(config_manager)