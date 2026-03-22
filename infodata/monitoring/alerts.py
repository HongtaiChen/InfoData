            elif "query" in rule.rule_id:
                return details.get("query_time_seconds")
            elif "failures" in rule.rule_id:
                return details.get("failed_tasks_count")
            elif "quality" in rule.rule_id:
                return details.get("overall_score")
            else:
                # 尝试从details中查找与规则相关的值
                for key, value in details.items():
                    if isinstance(value, (int, float)):
                        return float(value)
        except (ValueError, TypeError) as e:
            logger.error(f"提取数值失败: {e}")
            return None
        
        return None
    
    def _create_alert_from_rule(
        self, 
        rule: AlertRule, 
        component: str, 
        value: Any, 
        result: Dict[str, Any], 
        timestamp: datetime
    ) -> Alert:
        """根据规则创建告警"""
        import uuid
        
        alert_id = f"alert_{uuid.uuid4().hex[:8]}"
        
        # 构建告警消息
        if rule.condition in ["greater_than", "greater_than_or_equal"]:
            message = f"{rule.name}: {value} > {rule.threshold}"
        elif rule.condition in ["less_than", "less_than_or_equal"]:
            message = f"{rule.name}: {value} < {rule.threshold}"
        elif rule.condition in ["status_is", "status_is_not"]:
            message = f"{rule.name}: 状态为 {value}"
        else:
            message = rule.name
        
        # 构建详情
        details = {
            "component": component,
            "value": value,
            "threshold": rule.threshold,
            "condition": rule.condition,
            "health_result": result,
            "rule_id": rule.rule_id,
            "rule_name": rule.name,
            "timestamp": timestamp.isoformat(),
        }
        
        return Alert(
            alert_id=alert_id,
            rule=rule,
            component=component,
            severity=rule.severity,
            message=message,
            details=details,
            timestamp=timestamp
        )
    
    def _create_system_alert(self, alert_type: str, message: str):
        """创建系统级告警"""
        import uuid
        
        alert_id = f"sys_alert_{uuid.uuid4().hex[:8]}"
        
        # 创建系统规则
        system_rule = AlertRule(
            rule_id="system_error",
            name="系统错误",
            description="系统内部错误",
            component="system",
            condition="status_is",
            threshold="error",
            severity=AlertSeverity.CRITICAL,
            notification_channels=["email", "feishu"],
            tags=["system", "critical"]
        )
        
        alert = Alert(
            alert_id=alert_id,
            rule=system_rule,
            component="system",
            severity=AlertSeverity.CRITICAL,
            message=message,
            details={
                "alert_type": alert_type,
                "timestamp": datetime.now().isoformat(),
            }
        )
        
        self.active_alerts[alert_id] = alert
        self.alert_history.append(alert)
        
        logger.error(f"系统告警创建: {message}")
    
    def _cleanup_resolved_alerts(self):
        """清理已解决的告警"""
        # 保留最近1000个告警历史
        if len(self.alert_history) > 1000:
            self.alert_history = self.alert_history[-1000:]
        
        # 从活跃告警中移除已解决的告警
        resolved_ids = []
        for alert_id, alert in self.active_alerts.items():
            if alert.status in [AlertStatus.RESOLVED, AlertStatus.SUPPRESSED]:
                resolved_ids.append(alert_id)
        
        for alert_id in resolved_ids:
            self.active_alerts.pop(alert_id, None)
    
    def get_active_alerts(self, severity: AlertSeverity = None) -> List[Alert]:
        """获取活跃告警"""
        if severity:
            return [alert for alert in self.active_alerts.values() 
                   if alert.severity == severity]
        return list(self.active_alerts.values())
    
    def get_alert_history(
        self, 
        limit: int = 100, 
        severity: AlertSeverity = None,
        component: str = None,
        start_time: datetime = None,
        end_time: datetime = None
    ) -> List[Alert]:
        """获取告警历史"""
        filtered = self.alert_history
        
        if severity:
            filtered = [alert for alert in filtered if alert.severity == severity]
        
        if component:
            filtered = [alert for alert in filtered if alert.component == component]
        
        if start_time:
            filtered = [alert for alert in filtered if alert.timestamp >= start_time]
        
        if end_time:
            filtered = [alert for alert in filtered if alert.timestamp <= end_time]
        
        # 按时间倒序排序
        filtered.sort(key=lambda x: x.timestamp, reverse=True)
        
        return filtered[:limit]
    
    def acknowledge_alert(self, alert_id: str, user: str, notes: str = None):
        """确认告警"""
        if alert_id in self.active_alerts:
            alert = self.active_alerts[alert_id]
            alert.acknowledge(user, notes)
        else:
            logger.warning(f"告警不存在: {alert_id}")
    
    def resolve_alert(self, alert_id: str, notes: str = None):
        """解决告警"""
        if alert_id in self.active_alerts:
            alert = self.active_alerts[alert_id]
            alert.resolve(notes)
        else:
            logger.warning(f"告警不存在: {alert_id}")
    
    def suppress_alert(self, alert_id: str):
        """抑制告警"""
        if alert_id in self.active_alerts:
            alert = self.active_alerts[alert_id]
            alert.suppress()
        else:
            logger.warning(f"告警不存在: {alert_id}")
    
    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        stats = {
            "total_rules": len(self.rules),
            "enabled_rules": len([r for r in self.rules.values() if r.enabled]),
            "active_alerts": len(self.active_alerts),
            "alert_history_count": len(self.alert_history),
            "alerts_by_severity": {},
            "alerts_by_component": {},
        }
        
        # 按严重级别统计
        for severity in AlertSeverity:
            count = len([a for a in self.active_alerts.values() if a.severity == severity])
            stats["alerts_by_severity"][severity.value] = count
        
        # 按组件统计
        components = set(alert.component for alert in self.active_alerts.values())
        for component in components:
            count = len([a for a in self.active_alerts.values() if a.component == component])
            stats["alerts_by_component"][component] = count
        
        return stats
    
    def save_to_database(self):
        """保存到数据库"""
        try:
            # TODO: 实现数据库保存逻辑
            logger.debug("告警数据保存到数据库")
        except Exception as e:
            logger.error(f"保存告警数据失败: {e}")
    
    def load_from_database(self):
        """从数据库加载"""
        try:
            # TODO: 实现数据库加载逻辑
            logger.debug("从数据库加载告警数据")
        except Exception as e:
            logger.error(f"加载告警数据失败: {e}")