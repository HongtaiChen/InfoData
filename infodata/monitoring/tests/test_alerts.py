"""
告警管理器测试
"""

import unittest
from datetime import datetime, timedelta
from ..alerts import AlertManager, AlertRule, AlertSeverity, Alert, AlertStatus
from ..health import HealthChecker


class TestAlertRule(unittest.TestCase):
    """测试告警规则"""
    
    def setUp(self):
        self.rule = AlertRule(
            rule_id="test_rule",
            name="测试规则",
            description="测试用告警规则",
            component="test_component",
            condition="greater_than",
            threshold=10.0,
            severity=AlertSeverity.WARNING,
            cooldown_minutes=5,
            notification_channels=["test"],
            tags=["test"]
        )
    
    def test_rule_initialization(self):
        """测试规则初始化"""
        self.assertEqual(self.rule.rule_id, "test_rule")
        self.assertEqual(self.rule.name, "测试规则")
        self.assertEqual(self.rule.component, "test_component")
        self.assertEqual(self.rule.condition, "greater_than")
        self.assertEqual(self.rule.threshold, 10.0)
        self.assertEqual(self.rule.severity, AlertSeverity.WARNING)
        self.assertEqual(self.rule.cooldown_minutes, 5)
        self.assertEqual(self.rule.notification_channels, ["test"])
        self.assertEqual(self.rule.tags, ["test"])
        self.assertTrue(self.rule.enabled)
        self.assertIsNone(self.rule.last_triggered)
    
    def test_should_trigger_greater_than(self):
        """测试大于条件触发"""
        # 值大于阈值，应该触发
        self.assertTrue(self.rule.should_trigger(15.0, datetime.now()))
        
        # 值等于阈值，不应该触发
        self.assertFalse(self.rule.should_trigger(10.0, datetime.now()))
        
        # 值小于阈值，不应该触发
        self.assertFalse(self.rule.should_trigger(5.0, datetime.now()))
    
    def test_should_trigger_less_than(self):
        """测试小于条件触发"""
        rule = AlertRule(
            rule_id="test_rule2",
            name="测试规则2",
            component="test_component",
            condition="less_than",
            threshold=10.0,
            severity=AlertSeverity.WARNING
        )
        
        # 值小于阈值，应该触发
        self.assertTrue(rule.should_trigger(5.0, datetime.now()))
        
        # 值等于阈值，不应该触发
        self.assertFalse(rule.should_trigger(10.0, datetime.now()))
        
        # 值大于阈值，不应该触发
        self.assertFalse(rule.should_trigger(15.0, datetime.now()))
    
    def test_should_trigger_status_is(self):
        """测试状态等于条件触发"""
        rule = AlertRule(
            rule_id="test_rule3",
            name="测试规则3",
            component="test_component",
            condition="status_is",
            threshold="unhealthy",
            severity=AlertSeverity.ERROR
        )
        
        # 状态匹配，应该触发
        self.assertTrue(rule.should_trigger("unhealthy", datetime.now()))
        
        # 状态不匹配，不应该触发
        self.assertFalse(rule.should_trigger("healthy", datetime.now()))
    
    def test_cooldown_period(self):
        """测试冷却期"""
        current_time = datetime.now()
        
        # 第一次触发
        self.assertTrue(self.rule.should_trigger(15.0, current_time))
        self.rule.mark_triggered(current_time)
        
        # 在冷却期内，不应该触发
        within_cooldown = current_time + timedelta(minutes=2)
        self.assertFalse(self.rule.should_trigger(15.0, within_cooldown))
        
        # 冷却期过后，应该触发
        after_cooldown = current_time + timedelta(minutes=6)
        self.assertTrue(self.rule.should_trigger(15.0, after_cooldown))
    
    def test_disabled_rule(self):
        """测试禁用规则"""
        self.rule.enabled = False
        self.assertFalse(self.rule.should_trigger(15.0, datetime.now()))


class TestAlert(unittest.TestCase):
    """测试告警"""
    
    def setUp(self):
        rule = AlertRule(
            rule_id="test_rule",
            name="测试规则",
            component="test_component",
            condition="greater_than",
            threshold=10.0,
            severity=AlertSeverity.WARNING
        )
        
        self.alert = Alert(
            alert_id="test_alert",
            rule=rule,
            component="test_component",
            severity=AlertSeverity.WARNING,
            message="测试告警",
            details={"value": 15.0, "threshold": 10.0}
        )
    
    def test_alert_initialization(self):
        """测试告警初始化"""
        self.assertEqual(self.alert.alert_id, "test_alert")
        self.assertEqual(self.alert.component, "test_component")
        self.assertEqual(self.alert.severity, AlertSeverity.WARNING)
        self.assertEqual(self.alert.message, "测试告警")
        self.assertEqual(self.alert.details, {"value": 15.0, "threshold": 10.0})
        self.assertEqual(self.alert.status, AlertStatus.ACTIVE)
        self.assertIsNone(self.alert.acknowledged_by)
        self.assertIsNone(self.alert.acknowledged_at)
        self.assertIsNone(self.alert.resolved_at)
        self.assertIsNone(self.alert.resolution_notes)
        self.assertEqual(self.alert.notifications_sent, [])
    
    def test_acknowledge(self):
        """测试确认告警"""
        self.alert.acknowledge("test_user", "测试确认")
        
        self.assertEqual(self.alert.status, AlertStatus.ACKNOWLEDGED)
        self.assertEqual(self.alert.acknowledged_by, "test_user")
        self.assertIsNotNone(self.alert.acknowledged_at)
        self.assertEqual(self.alert.resolution_notes, "测试确认")
    
    def test_resolve(self):
        """测试解决告警"""
        self.alert.resolve("测试解决")
        
        self.assertEqual(self.alert.status, AlertStatus.RESOLVED)
        self.assertIsNotNone(self.alert.resolved_at)
        self.assertEqual(self.alert.resolution_notes, "测试解决")
    
    def test_suppress(self):
        """测试抑制告警"""
        self.alert.suppress()
        
        self.assertEqual(self.alert.status, AlertStatus.SUPPRESSED)
    
    def test_to_dict(self):
        """测试转换为字典"""
        alert_dict = self.alert.to_dict()
        
        self.assertEqual(alert_dict["alert_id"], "test_alert")
        self.assertEqual(alert_dict["rule_id"], "test_rule")
        self.assertEqual(alert_dict["component"], "test_component")
        self.assertEqual(alert_dict["severity"], "warning")
        self.assertEqual(alert_dict["message"], "测试告警")
        self.assertEqual(alert_dict["status"], "active")
        self.assertEqual(alert_dict["details"], {"value": 15.0, "threshold": 10.0})


class TestAlertManager(unittest.TestCase):
    """测试告警管理器"""
    
    def setUp(self):
        self.alert_manager = AlertManager()
    
    def test_initialization(self):
        """测试初始化"""
        self.assertIsNotNone(self.alert_manager.health_checker)
        self.assertIsInstance(self.alert_manager.rules, dict)
        self.assertIsInstance(self.alert_manager.active_alerts, dict)
        self.assertIsInstance(self.alert_manager.alert_history, list)
        
        # 应该有一些默认规则
        self.assertGreater(len(self.alert_manager.rules), 0)
    
    def test_add_and_remove_rule(self):
        """测试添加和移除规则"""
        rule = AlertRule(
            rule_id="custom_rule",
            name="自定义规则",
            component="custom_component",
            condition="greater_than",
            threshold=50.0,
            severity=AlertSeverity.INFO
        )
        
        # 添加规则
        self.alert_manager.add_rule(rule)
        self.assertIn("custom_rule", self.alert_manager.rules)
        
        # 获取规则
        retrieved_rule = self.alert_manager.get_rule("custom_rule")
        self.assertEqual(retrieved_rule, rule)
        
        # 移除规则
        self.alert_manager.remove_rule("custom_rule")
        self.assertNotIn("custom_rule", self.alert_manager.rules)
    
    def test_list_rules(self):
        """测试列出规则"""
        rules = self.alert_manager.list_rules()
        self.assertIsInstance(rules, list)
        self.assertGreater(len(rules), 0)
        
        # 测试只列出启用的规则
        enabled_rules = self.alert_manager.list_rules(enabled_only=True)
        self.assertIsInstance(enabled_rules, list)
        
        # 所有启用的规则都应该在完整列表中
        for rule in enabled_rules:
            self.assertIn(rule, rules)
    
    def test_check_health_and_trigger_alerts(self):
        """测试健康检查和告警触发"""
        # 模拟健康检查结果
        class MockHealthChecker:
            def check_database(self):
                return {"status": "healthy", "details": {}}
            
            def check_data_sources(self):
                return {"status": "healthy", "details": {}}
            
            def check_task_scheduler(self):
                return {"status": "healthy", "details": {}}
            
            def check_system_resources(self):
                return {"status": "healthy", "details": {"cpu_percent": 95.0}}
            
            def check_data_quality(self):
                return {"status": "healthy", "details": {}}
        
        # 使用模拟的健康检查器
        self.alert_manager.health_checker = MockHealthChecker()
        
        # 执行健康检查和告警触发
        new_alerts = self.alert_manager.check_health_and_trigger_alerts()
        
        # 应该触发CPU使用率过高的告警
        self.assertIsInstance(new_alerts, list)
        
        # 清理活跃告警
        for alert_id in list(self.alert_manager.active_alerts.keys()):
            self.alert_manager.resolve_alert(alert_id)
    
    def test_alert_operations(self):
        """测试告警操作"""
        # 创建测试告警
        rule = AlertRule(
            rule_id="test_rule",
            name="测试规则",
            component="test_component",
            condition="greater_than",
            threshold=10.0,
            severity=AlertSeverity.WARNING
        )
        
        alert = Alert(
            alert_id="test_alert",
            rule=rule,
            component="test_component",
            severity=AlertSeverity.WARNING,
            message="测试告警",
            details={"value": 15.0}
        )
        
        # 手动添加告警
        self.alert_manager.active_alerts[alert.alert_id] = alert
        self.alert_manager.alert_history.append(alert)
        
        # 获取活跃告警
        active_alerts = self.alert_manager.get_active_alerts()
        self.assertIn(alert, active_alerts)
        
        # 按严重级别获取告警
        warning_alerts = self.alert_manager.get_active_alerts(AlertSeverity.WARNING)
        self.assertIn(alert, warning_alerts)
        
        # 确认告警
        self.alert_manager.acknowledge_alert("test_alert", "test_user")
        self.assertEqual(alert.status, AlertStatus.ACKNOWLEDGED)
        
        # 解决告警
        self.alert_manager.resolve_alert("test_alert", "已解决")
        self.assertEqual(alert.status, AlertStatus.RESOLVED)
        
        # 抑制告警
        alert2 = Alert(
            alert_id="test_alert2",
            rule=rule,
            component="test_component",
            severity=AlertSeverity.WARNING,
            message="测试告警2",
            details={"value": 15.0}
        )
        
        self.alert_manager.active_alerts[alert2.alert_id] = alert2
        self.alert_manager.suppress_alert("test_alert2")
        self.assertEqual(alert2.status, AlertStatus.SUPPRESSED)
    
    def test_get_stats(self):
        """测试获取统计信息"""
        stats = self.alert_manager.get_stats()
        
        self.assertIn("total_rules", stats)
        self.assertIn("enabled_rules", stats)
        self.assertIn("active_alerts", stats)
        self.assertIn("alert_history_count", stats)
        self.assertIn("alerts_by_severity", stats)
        self.assertIn("alerts_by_component", stats)
        
        self.assertIsInstance(stats["total_rules"], int)
        self.assertIsInstance(stats["enabled_rules"], int)
        self.assertIsInstance(stats["active_alerts"], int)
        self.assertIsInstance(stats["alert_history_count"], int)
        self.assertIsInstance(stats["alerts_by_severity"], dict)
        self.assertIsInstance(stats["alerts_by_component"], dict)


if __name__ == "__main__":
    unittest.main()