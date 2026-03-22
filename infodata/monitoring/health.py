"""
系统健康检查模块

监控系统各组件的健康状态。
"""

from datetime import datetime
from typing import Dict, List, Any, Optional
import psutil
import time

from ..utils.logging import get_logger
from ..utils.database import get_db_manager, session_scope
from ..data.sources.manager import get_data_source_manager
from ..tasks.scheduler import get_task_scheduler
from ..models.task import TaskExecution

logger = get_logger(__name__)


class HealthChecker:
    """健康检查器"""
    
    def __init__(self):
        """初始化健康检查器"""
        self.db_manager = get_db_manager()
        self.data_source_manager = get_data_source_manager()
        self.task_scheduler = get_task_scheduler()
        
        logger.info("健康检查器初始化完成")
    
    def check_database(self) -> Dict[str, Any]:
        """
        检查数据库健康状态
        
        Returns:
            Dict[str, Any]: 数据库健康状态
        """
        check_start = datetime.now()
        
        try:
            # 检查数据库连接
            with session_scope(self.db_manager) as session:
                # 执行简单查询测试连接
                result = session.execute("SELECT 1").fetchone()
                
                if result and result[0] == 1:
                    status = "healthy"
                    message = "数据库连接正常"
                else:
                    status = "unhealthy"
                    message = "数据库查询测试失败"
            
            # 检查数据库性能
            performance_start = time.time()
            
            with session_scope(self.db_manager) as session:
                # 执行稍复杂的查询测试性能
                session.execute("SELECT COUNT(*) FROM task_execution").fetchone()
            
            query_time = time.time() - performance_start
            
            check_end = datetime.now()
            duration = (check_end - check_start).total_seconds()
            
            return {
                "component": "database",
                "status": status,
                "message": message,
                "duration_seconds": duration,
                "query_time_seconds": query_time,
                "timestamp": check_end.isoformat(),
                "details": {
                    "connection_test": "passed" if status == "healthy" else "failed",
                    "performance_test": f"{query_time:.4f}秒",
                }
            }
            
        except Exception as e:
            check_end = datetime.now()
            duration = (check_end - check_start).total_seconds()
            
            logger.error(f"数据库健康检查失败: {e}")
            
            return {
                "component": "database",
                "status": "unhealthy",
                "message": f"数据库检查失败: {str(e)}",
                "duration_seconds": duration,
                "timestamp": check_end.isoformat(),
                "error": str(e),
            }
    
    def check_data_sources(self) -> Dict[str, Any]:
        """
        检查数据源健康状态
        
        Returns:
            Dict[str, Any]: 数据源健康状态
        """
        check_start = datetime.now()
        
        try:
            # 获取数据源状态
            status = self.data_source_manager.get_status()
            health = self.data_source_manager.health_check()
            
            # 分析数据源状态
            total_adapters = status.get('total_adapters', 0)
            connected_adapters = status.get('connected_adapters', 0)
            enabled_adapters = status.get('enabled_adapters', 0)
            
            # 计算连接率
            if enabled_adapters > 0:
                connection_rate = connected_adapters / enabled_adapters
            else:
                connection_rate = 0
            
            # 确定总体状态
            if connection_rate >= 0.8:  # 80%以上连接成功
                overall_status = "healthy"
            elif connection_rate >= 0.5:  # 50%-80%连接成功
                overall_status = "degraded"
            else:  # 低于50%连接成功
                overall_status = "unhealthy"
            
            # 分析各个数据源状态
            adapters_status = []
            for name, adapter_status in status.get('adapters', {}).items():
                adapters_status.append({
                    "name": name,
                    "status": adapter_status.get('status', 'unknown'),
                    "last_connection": adapter_status.get('last_connection'),
                    "success_rate": adapter_status.get('success_rate', 0),
                })
            
            check_end = datetime.now()
            duration = (check_end - check_start).total_seconds()
            
            return {
                "component": "data_sources",
                "status": overall_status,
                "message": f"数据源状态: {connected_adapters}/{enabled_adapters} 连接成功",
                "duration_seconds": duration,
                "timestamp": check_end.isoformat(),
                "details": {
                    "total_adapters": total_adapters,
                    "enabled_adapters": enabled_adapters,
                    "connected_adapters": connected_adapters,
                    "connection_rate": connection_rate,
                    "adapters": adapters_status,
                }
            }
            
        except Exception as e:
            check_end = datetime.now()
            duration = (check_end - check_start).total_seconds()
            
            logger.error(f"数据源健康检查失败: {e}")
            
            return {
                "component": "data_sources",
                "status": "unhealthy",
                "message": f"数据源检查失败: {str(e)}",
                "duration_seconds": duration,
                "timestamp": check_end.isoformat(),
                "error": str(e),
            }
    
    def check_task_scheduler(self) -> Dict[str, Any]:
        """
        检查任务调度器健康状态
        
        Returns:
            Dict[str, Any]: 任务调度器健康状态
        """
        check_start = datetime.now()
        
        try:
            # 获取调度器状态
            status = self.task_scheduler.get_scheduler_status()
            
            # 分析调度器状态
            running = status.get('running', False)
            total_jobs = status.get('total_jobs', 0)
            active_jobs = status.get('active_jobs', 0)
            paused_jobs = status.get('paused_jobs', 0)
            
            if running:
                if total_jobs > 0:
                    if active_jobs > 0:
                        scheduler_status = "healthy"
                        message = f"调度器运行正常: {active_jobs}/{total_jobs} 个活跃任务"
                    else:
                        scheduler_status = "degraded"
                        message = f"调度器运行但无活跃任务: {paused_jobs}/{total_jobs} 个暂停任务"
                else:
                    scheduler_status = "degraded"
                    message = "调度器运行但无任务"
            else:
                scheduler_status = "unhealthy"
                message = "调度器未运行"
            
            # 检查最近任务执行
            recent_failures = 0
            try:
                with session_scope() as session:
                    # 检查最近1小时内的失败任务
                    one_hour_ago = datetime.now() - timedelta(hours=1)
                    failures = session.query(TaskExecution).filter(
                        TaskExecution.execution_status == 'failed',
                        TaskExecution.execution_start >= one_hour_ago
                    ).count()
                    
                    recent_failures = failures
            except Exception as e:
                logger.warning(f"检查最近任务失败记录时出错: {e}")
            
            check_end = datetime.now()
            duration = (check_end - check_start).total_seconds()
            
            return {
                "component": "task_scheduler",
                "status": scheduler_status,
                "message": message,
                "duration_seconds": duration,
                "timestamp": check_end.isoformat(),
                "details": {
                    "running": running,
                    "total_jobs": total_jobs,
                    "active_jobs": active_jobs,
                    "paused_jobs": paused_jobs,
                    "recent_failures_1h": recent_failures,
                    "next_job": status.get('next_job'),
                }
            }
            
        except Exception as e:
            check_end = datetime.now()
            duration = (check_end - check_start).total_seconds()
            
            logger.error(f"任务调度器健康检查失败: {e}")
            
            return {
                "component": "task_scheduler",
                "status": "unhealthy",
                "message": f"任务调度器检查失败: {str(e)}",
                "duration_seconds": duration,
                "timestamp": check_end.isoformat(),
                "error": str(e),
            }
    
    def check_system_resources(self) -> Dict[str, Any]:
        """
        检查系统资源
        
        Returns:
            Dict[str, Any]: 系统资源状态
        """
        check_start = datetime.now()
        
        try:
            # 获取CPU使用率
            cpu_percent = psutil.cpu_percent(interval=0.1)
            
            # 获取内存使用率
            memory = psutil.virtual_memory()
            memory_percent = memory.percent
            memory_used_gb = memory.used / (1024**3)
            memory_total_gb = memory.total / (1024**3)
            
            # 获取磁盘使用率
            disk = psutil.disk_usage('/')
            disk_percent = disk.percent
            disk_used_gb = disk.used / (1024**3)
            disk_total_gb = disk.total / (1024**3)
            
            # 确定资源状态
            resource_status = "healthy"
            resource_messages = []
            
            if cpu_percent > 90:
                resource_status = "unhealthy"
                resource_messages.append(f"CPU使用率过高: {cpu_percent:.1f}%")
            elif cpu_percent > 80:
                resource_status = "degraded"
                resource_messages.append(f"CPU使用率较高: {cpu_percent:.1f}%")
            
            if memory_percent > 90:
                resource_status = "unhealthy"
                resource_messages.append(f"内存使用率过高: {memory_percent:.1f}%")
            elif memory_percent > 80:
                resource_status = "degraded"
                resource_messages.append(f"内存使用率较高: {memory_percent:.1f}%")
            
            if disk_percent > 95:
                resource_status = "unhealthy"
                resource_messages.append(f"磁盘使用率过高: {disk_percent:.1f}%")
            elif disk_percent > 90:
                resource_status = "degraded"
                resource_messages.append(f"磁盘使用率较高: {disk_percent:.1f}%")
            
            if not resource_messages:
                resource_message = "系统资源正常"
            else:
                resource_message = "; ".join(resource_messages)
            
            check_end = datetime.now()
            duration = (check_end - check_start).total_seconds()
            
            return {
                "component": "system_resources",
                "status": resource_status,
                "message": resource_message,
                "duration_seconds": duration,
                "timestamp": check_end.isoformat(),
                "details": {
                    "cpu_percent": cpu_percent,
                    "memory_percent": memory_percent,
                    "memory_used_gb": round(memory_used_gb, 2),
                    "memory_total_gb": round(memory_total_gb, 2),
                    "disk_percent": disk_percent,
                    "disk_used_gb": round(disk_used_gb, 2),
                    "disk_total_gb": round(disk_total_gb, 2),
                }
            }
            
        except Exception as e:
            check_end = datetime.now()
            duration = (check_end - check_start).total_seconds()
            
            logger.error(f"系统资源健康检查失败: {e}")
            
            return {
                "component": "system_resources",
                "status": "unhealthy",
                "message": f"系统资源检查失败: {str(e)}",
                "duration_seconds": duration,
                "timestamp": check_end.isoformat(),
                "error": str(e),
            }
    
    def check_data_quality(self) -> Dict[str, Any]:
        """
        检查数据质量
        
        Returns:
            Dict[str, Any]: 数据质量状态
        """
        check_start = datetime.now()
        
        try:
            from ..models.quality import DataQualityMetric
            
            with session_scope() as session:
                # 获取最近24小时的数据质量指标
                twenty_four_hours_ago = datetime.now() - timedelta(hours=24)
                
                quality_metrics = session.query(DataQualityMetric).filter(
                    DataQualityMetric.measurement_date >= twenty_four_hours_ago
                ).all()
                
                if not quality_metrics:
                    return {
                        "component": "data_quality",
                        "status": "unknown",
                        "message": "最近24小时内无数据质量指标",
                        "duration_seconds": (datetime.now() - check_start).total_seconds(),
                        "timestamp": datetime.now().isoformat(),
                        "details": {
                            "metrics_count": 0,
                        }
                    }
                
                # 计算平均质量评分
                total_score = 0
                low_quality_count = 0
                data_types = set()
                
                for metric in quality_metrics:
                    if metric.overall_score:
                        total_score += metric.overall_score
                    
                    if metric.overall_score and metric.overall_score < 0.9:  # 低于90%为低质量
                        low_quality_count += 1
                    
                    if metric.data_type:
                        data_types.add(metric.data_type)
                
                avg_score = total_score / len(quality_metrics) if quality_metrics else 0
                
                # 确定数据质量状态
                if avg_score >= 0.95:  # 95%以上为优秀
                    quality_status = "healthy"
                    quality_message = f"数据质量优秀: {avg_score:.2%}"
                elif avg_score >= 0.90:  # 90%-95%为良好
                    quality_status = "healthy"
                    quality_message = f"数据质量良好: {avg_score:.2%}"
                elif avg_score >= 0.80:  # 80%-90%为一般
                    quality_status = "degraded"
                    quality_message = f"数据质量一般: {avg_score:.2%}"
                else:  # 低于80%为需改进
                    quality_status = "unhealthy"
                    quality_message = f"数据质量需改进: {avg_score:.2%}"
                
                if low_quality_count > 0:
                    quality_message += f", {low_quality_count} 个低质量指标"
            
            check_end = datetime.now()
            duration = (check_end - check_start).total_seconds()
            
            return {
                "component": "data_quality",
                "status": quality_status,
                "message": quality_message,
                "duration_seconds": duration,
                "timestamp": check_end.isoformat(),
                "details": {
                    "metrics_count": len(quality_metrics),
                    "average_score": avg_score,
                    "low_quality_count": low_quality_count,
                    "data_types": list(data_types),
                }
            }
            
        except Exception as e:
            check_end = datetime.now()
            duration = (check_end - check_start).total_seconds()
            
            logger.error(f"数据质量健康检查失败: {e}")
            
            return {
                "component": "data_quality",
                "status": "unhealthy",
                "message": f"数据质量检查失败: {str(e)}",
                "duration_seconds": duration,
                "timestamp": check_end.isoformat(),
                "error": str(e),
            }
    
    def check_all(self) -> Dict[str, Any]:
        """
        检查所有组件健康状态
        
        Returns:
            Dict[str, Any]: 所有组件健康状态
        """
        check_start = datetime.now()
        
        try:
            logger.info("开始执行全面健康检查")
            
            # 并行检查各个组件
            checks = [
                ("database", self.check_database),
                ("data_sources", self.check_data_sources),
                ("task_scheduler", self.check_task_scheduler),
                ("system_resources", self.check_system_resources),
                ("data_quality", self.check_data_quality),
            ]
            
            results = {}
            for component_name, check_func in checks:
                try:
                    logger.debug(f"检查组件: {component_name}")
                    result = check_func()
                    results[component_name] = result
                except Exception as e:
                    logger.error(f"检查组件 {component_name} 失败: {e}")
                    results[component_name] = {
                        "component": component_name,
                        "status": "unhealthy",
                        "message": f"检查失败: {str(e)}",
                        "error": str(e),
                    }
            
            # 计算总体状态
            component_statuses = [r.get('status', 'unknown') for r in results.values()]
            
            unhealthy_count = component_statuses.count('unhealthy')
            degraded_count = component_statuses.count('degraded')
            healthy_count = component_statuses.count('healthy')
            unknown_count = component_statuses.count('unknown')
            
            total_components = len(component_statuses)
            
            if unhealthy_count > 0:
                overall_status = "unhealthy"
                overall_message = f"系统不健康: {unhealthy_count}/{total_components} 个组件异常"
            elif degraded_count > 0:
                overall_status = "degraded"
                overall_message = f"系统降级: {degraded_count}/{total_components} 个组件降级"
            elif healthy_count == total_components:
                overall_status = "healthy"
                overall_message = f"系统健康: 所有 {total_components} 个组件正常"
            else:
                overall_status = "unknown"
                overall_message = f"系统状态未知: {unknown_count}/{total_components} 个组件状态未知"
            
            check_end = datetime.now()
            duration = (check_end - check_start).total_seconds()
            
            result = {
                "overall": {
                    "status": overall_status,
                    "message": overall_message,
                    "duration_seconds": duration,
                    "timestamp": check_end.isoformat(),
                    "summary": {
                        "total_components": total_components,
                        "healthy": healthy_count,
                        "degraded": degraded_count,
                        "unhealthy": unhealthy_count,
                        "unknown": unknown_count,
                    }
                },
                "components": results,
            }
            
            logger.info(f"全面健康检查完成: {overall_status} - {overall_message}")
            
            return result
            
        except Exception as e:
            check_end = datetime.now()
            duration = (check_end - check_start).total_seconds()
            
            logger.error(f"全面健康检查失败: {e}", exc_info=True)
            
            return {
                "overall": {
                    "status": "unhealthy",
                    "message": f"全面健康检查失败: {str(e)}",
                    "duration_seconds": duration,
                    "timestamp": check_end.isoformat(),
                    "error": str(e),
                },
                "components": {},
            }


# 全局健康检查器实例
_global_health_checker: Optional[HealthChecker] = None


def get_health_checker() -> HealthChecker:
    """
    获取全局健康检查器
    
    Returns:
        HealthChecker: 健康检查器实例
    """
    global _global_health_checker
    
    if _global_health_checker is None:
        _global_health_checker = HealthChecker()
    
    return _global_health_checker


def check_health() -> Dict[str, Any]:
    """
    检查系统健康状态（便捷函数）
    
    Returns:
        Dict[str, Any]: 系统健康状态
    """
    checker = get_health_checker()
    return checker.check_all()


def check_database_health() -> Dict[str, Any]:
    """
    检查数据库健康状态（便捷函数）
    
    Returns:
        Dict[str, Any]: 数据库健康状态
    """
    checker = get_health_checker()
    return checker.check_database()


def check_system_resources_health() -> Dict[str, Any]:
    """
    检查系统资源健康状态（便捷函数）
    
    Returns:
        Dict[str, Any]: 系统资源健康状态

