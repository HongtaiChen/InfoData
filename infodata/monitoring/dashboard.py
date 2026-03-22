"""
监控仪表板模块

提供监控数据的可视化展示和告警管理界面。
"""

from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional, Tuple
import json
from dataclasses import dataclass, asdict

from ..utils.logging import get_logger
from .alerts import AlertManager, Alert, AlertSeverity, AlertStatus
from .health import HealthChecker

logger = get_logger(__name__)


@dataclass
class DashboardStats:
    """仪表板统计信息"""
    total_alerts: int = 0
    active_alerts: int = 0
    critical_alerts: int = 0
    error_alerts: int = 0
    warning_alerts: int = 0
    info_alerts: int = 0
    
    alerts_by_component: Dict[str, int] = None
    alerts_by_severity: Dict[str, int] = None
    
    health_status: str = "unknown"
    last_check_time: Optional[datetime] = None
    uptime_days: float = 0.0
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        data = asdict(self)
        if self.last_check_time:
            data["last_check_time"] = self.last_check_time.isoformat()
        return data


@dataclass
class HealthMetric:
    """健康指标"""
    name: str
    value: Any
    status: str  # healthy, degraded, unhealthy
    threshold: Optional[Any] = None
    unit: Optional[str] = None
    timestamp: Optional[datetime] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        data = asdict(self)
        if self.timestamp:
            data["timestamp"] = self.timestamp.isoformat()
        return data


class MonitoringDashboard:
    """监控仪表板"""
    
    def __init__(self, alert_manager: AlertManager = None, health_checker: HealthChecker = None):
        """
        初始化监控仪表板
        
        Args:
            alert_manager: 告警管理器实例
            health_checker: 健康检查器实例
        """
        self.alert_manager = alert_manager or AlertManager()
        self.health_checker = health_checker or HealthChecker()
        
        # 仪表板状态
        self.last_refresh = datetime.now()
        self.start_time = datetime.now()
        
        logger.info("监控仪表板初始化完成")
    
    def refresh(self):
        """刷新仪表板数据"""
        self.last_refresh = datetime.now()
        logger.debug("仪表板数据已刷新")
    
    def get_overview(self) -> Dict[str, Any]:
        """获取概览信息"""
        # 获取告警统计
        alert_stats = self.alert_manager.get_stats()
        
        # 获取活跃告警
        active_alerts = self.alert_manager.get_active_alerts()
        
        # 计算严重级别统计
        severity_stats = {}
        for severity in AlertSeverity:
            count = len([a for a in active_alerts if a.severity == severity])
            severity_stats[severity.value] = count
        
        # 计算组件统计
        component_stats = {}
        for alert in active_alerts:
            component = alert.component
            component_stats[component] = component_stats.get(component, 0) + 1
        
        # 计算运行时间
        uptime = datetime.now() - self.start_time
        uptime_days = uptime.total_seconds() / 86400
        
        # 获取健康状态
        try:
            health_result = self.health_checker.check_all()
            health_status = "healthy"
            for component, result in health_result.items():
                if result.get("status") == "unhealthy":
                    health_status = "unhealthy"
                    break
                elif result.get("status") == "degraded":
                    health_status = "degraded"
        except Exception as e:
            logger.error(f"获取健康状态失败: {e}")
            health_status = "unknown"
        
        overview = {
            "system_status": {
                "health": health_status,
                "uptime_days": round(uptime_days, 2),
                "last_refresh": self.last_refresh.isoformat(),
                "start_time": self.start_time.isoformat(),
            },
            "alert_summary": {
                "total_alerts": len(active_alerts),
                "by_severity": severity_stats,
                "by_component": component_stats,
            },
            "rule_summary": {
                "total_rules": alert_stats.get("total_rules", 0),
                "enabled_rules": alert_stats.get("enabled_rules", 0),
            },
            "notification_summary": {
                "active_alerts": len(active_alerts),
                "history_count": alert_stats.get("alert_history_count", 0),
            }
        }
        
        return overview
    
    def get_active_alerts(
        self, 
        limit: int = 50,
        severity: str = None,
        component: str = None,
        sort_by: str = "timestamp",
        sort_order: str = "desc"
    ) -> List[Dict[str, Any]]:
        """
        获取活跃告警列表
        
        Args:
            limit: 返回数量限制
            severity: 严重级别过滤
            component: 组件过滤
            sort_by: 排序字段 (timestamp, severity, component)
            sort_order: 排序顺序 (asc, desc)
            
        Returns:
            List[Dict[str, Any]]: 告警列表
        """
        # 获取活跃告警
        active_alerts = self.alert_manager.get_active_alerts()
        
        # 过滤
        filtered = []
        for alert in active_alerts:
            if severity and alert.severity.value != severity:
                continue
            if component and alert.component != component:
                continue
            filtered.append(alert)
        
        # 排序
        reverse = (sort_order == "desc")
        
        if sort_by == "timestamp":
            filtered.sort(key=lambda x: x.timestamp, reverse=reverse)
        elif sort_by == "severity":
            # 严重级别排序：critical > error > warning > info
            severity_order = {
                AlertSeverity.CRITICAL: 0,
                AlertSeverity.ERROR: 1,
                AlertSeverity.WARNING: 2,
                AlertSeverity.INFO: 3
            }
            filtered.sort(key=lambda x: severity_order.get(x.severity, 4), reverse=reverse)
        elif sort_by == "component":
            filtered.sort(key=lambda x: x.component, reverse=reverse)
        
        # 转换为字典
        result = []
        for alert in filtered[:limit]:
            alert_dict = alert.to_dict()
            result.append(alert_dict)
        
        return result
    
    def get_alert_history(
        self,
        limit: int = 100,
        severity: str = None,
        component: str = None,
        start_time: datetime = None,
        end_time: datetime = None,
        status: str = None
    ) -> List[Dict[str, Any]]:
        """
        获取告警历史
        
        Args:
            limit: 返回数量限制
            severity: 严重级别过滤
            component: 组件过滤
            start_time: 开始时间
            end_time: 结束时间
            status: 状态过滤
            
        Returns:
            List[Dict[str, Any]]: 告警历史列表
        """
        # 转换严重级别
        severity_enum = None
        if severity:
            try:
                severity_enum = AlertSeverity(severity)
            except ValueError:
                logger.warning(f"无效的严重级别: {severity}")
        
        # 获取告警历史
        alerts = self.alert_manager.get_alert_history(
            limit=limit * 2,  # 多取一些用于过滤
            severity=severity_enum,
            component=component,
            start_time=start_time,
            end_time=end_time
        )
        
        # 状态过滤
        if status:
            try:
                status_enum = AlertStatus(status)
                alerts = [a for a in alerts if a.status == status_enum]
            except ValueError:
                logger.warning(f"无效的状态: {status}")
        
        # 转换为字典
        result = []
        for alert in alerts[:limit]:
            alert_dict = alert.to_dict()
            result.append(alert_dict)
        
        return result
    
    def get_health_metrics(self) -> List[HealthMetric]:
        """获取健康指标"""
        metrics = []
        current_time = datetime.now()
        
        try:
            # 检查数据库
            db_result = self.health_checker.check_database()
            metrics.append(HealthMetric(
                name="数据库连接",
                value=db_result.get("status"),
                status=db_result.get("status"),
                timestamp=current_time
            ))
            
            # 检查数据源
            ds_result = self.health_checker.check_data_sources()
            healthy_count = sum(1 for ds in ds_result.get("details", {}).get("sources", []) 
                              if ds.get("status") == "healthy")
            total_count = len(ds_result.get("details", {}).get("sources", []))
            
            metrics.append(HealthMetric(
                name="数据源连接",
                value=f"{healthy_count}/{total_count}",
                status=ds_result.get("status"),
                timestamp=current_time
            ))
            
            # 检查任务调度器
            ts_result = self.health_checker.check_task_scheduler()
            metrics.append(HealthMetric(
                name="任务调度器",
                value=ts_result.get("status"),
                status=ts_result.get("status"),
                timestamp=current_time
            ))
            
            # 检查系统资源
            sys_result = self.health_checker.check_system_resources()
            details = sys_result.get("details", {})
            
            metrics.append(HealthMetric(
                name="CPU使用率",
                value=details.get("cpu_percent", 0),
                status="healthy" if details.get("cpu_percent", 0) < 80 else "degraded",
                threshold=80,
                unit="%",
                timestamp=current_time
            ))
            
            metrics.append(HealthMetric(
                name="内存使用率",
                value=details.get("memory_percent", 0),
                status="healthy" if details.get("memory_percent", 0) < 80 else "degraded",
                threshold=80,
                unit="%",
                timestamp=current_time
            ))
            
            metrics.append(HealthMetric(
                name="磁盘使用率",
                value=details.get("disk_percent", 0),
                status="healthy" if details.get("disk_percent", 0) < 90 else "degraded",
                threshold=90,
                unit="%",
                timestamp=current_time
            ))
            
            # 检查数据质量
            dq_result = self.health_checker.check_data_quality()
            overall_score = dq_result.get("details", {}).get("overall_score", 0)
            
            metrics.append(HealthMetric(
                name="数据质量评分",
                value=overall_score,
                status="healthy" if overall_score >= 0.8 else "degraded",
                threshold=0.8,
                unit="score",
                timestamp=current_time
            ))
            
        except Exception as e:
            logger.error(f"获取健康指标失败: {e}")
            metrics.append(HealthMetric(
                name="系统状态",
                value="error",
                status="unhealthy",
                timestamp=current_time
            ))
        
        return metrics
    
    def get_trend_data(self, hours: int = 24) -> Dict[str, List[Tuple[datetime, int]]]:
        """
        获取趋势数据
        
        Args:
            hours: 小时数
            
        Returns:
            Dict[str, List[Tuple[datetime, int]]]: 趋势数据
        """
        end_time = datetime.now()
        start_time = end_time - timedelta(hours=hours)
        
        # 获取告警历史
        alerts = self.alert_manager.get_alert_history(
            limit=1000,
            start_time=start_time,
            end_time=end_time
        )
        
        # 按小时分组
        trend_data = {
            "total": [],
            "critical": [],
            "error": [],
            "warning": [],
            "info": []
        }
        
        # 初始化时间点
        current = start_time.replace(minute=0, second=0, microsecond=0)
        while current <= end_time:
            for key in trend_data.keys():
                trend_data[key].append((current, 0))
            current += timedelta(hours=1)
        
        # 统计告警
        for alert in alerts:
            alert_hour = alert.timestamp.replace(minute=0, second=0, microsecond=0)
            
            # 找到对应的时间点
            for i, (time_point, count) in enumerate(trend_data["total"]):
                if time_point == alert_hour:
                    trend_data["total"][i] = (time_point, count + 1)
                    
                    # 按严重级别统计
                    if alert.severity == AlertSeverity.CRITICAL:
                        trend_data["critical"][i] = (time_point, trend_data["critical"][i][1] + 1)
                    elif alert.severity == AlertSeverity.ERROR:
                        trend_data["error"][i] = (time_point, trend_data["error"][i][1] + 1)
                    elif alert.severity == AlertSeverity.WARNING:
                        trend_data["warning"][i] = (time_point, trend_data["warning"][i][1] + 1)
                    elif alert.severity == AlertSeverity.INFO:
                        trend_data["info"][i] = (time_point, trend_data["info"][i][1] + 1)
                    
                    break
        
        # 转换为可序列化格式
        result = {}
        for key, data in trend_data.items():
            result[key] = [(dt.isoformat(), count) for dt, count in data]
        
        return result
    
    def acknowledge_alert(self, alert_id: str, user: str, notes: str = None):
        """确认告警"""
        self.alert_manager.acknowledge_alert(alert_id, user, notes)
        logger.info(f"告警已确认: {alert_id}, 用户: {user}")
    
    def resolve_alert(self, alert_id: str, notes: str = None):
        """解决告警"""
        self.alert_manager.resolve_alert(alert_id, notes)
        logger.info(f"告警已解决: {alert_id}")
    
    def suppress_alert(self, alert_id: str):
        """抑制告警"""
        self.alert_manager.suppress_alert(alert_id)
        logger.info(f"告警已抑制: {alert_id}")
    
    def run_health_check(self) -> Dict[str, Any]:
        """运行健康检查"""
        try:
            results = self.health_checker.check_all()
            
            # 触发告警检查
            new_alerts = self.alert_manager.check_health_and_trigger_alerts()
            
            return {
                "success": True,
                "health_results": results,
                "new_alerts": len(new_alerts),
                "timestamp": datetime.now().isoformat()
            }
            
        except Exception as e:
            logger.error(f"运行健康检查失败: {e}")
            return {
                "success": False,
                "error": str(e),
                "timestamp": datetime.now().isoformat()
            }
    
    def get_dashboard_data(self) -> Dict[str, Any]:
        """获取完整的仪表板数据"""
        return {
            "overview": self.get_overview(),
            "active_alerts": self.get_active_alerts(limit=20),
            "health_metrics": [metric.to_dict() for metric in self.get_health_metrics()],
            "trend_data": self.get_trend_data(hours=24),
            "last_refresh": self.last_refresh.isoformat(),
            "system_info": {
                "version": "1.0.0",
                "start_time": self.start_time.isoformat(),
                "uptime_days": round((datetime.now() - self.start_time).total_seconds() / 86400, 2)
            }
        }