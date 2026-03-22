"""
监控系统CLI接口

提供命令行界面来管理监控告警系统。
"""

import argparse
import sys
import json
from typing import Dict, Any, List
from datetime import datetime, timedelta

from .service import create_monitoring_service
from .config import ConfigManager
from .dashboard import MonitoringDashboard
from .alerts import AlertSeverity, AlertStatus
from ..utils.logging import setup_logging, get_logger

logger = get_logger(__name__)


class MonitoringCLI:
    """监控系统CLI"""
    
    def __init__(self):
        self.service = None
        self.config_manager = None
    
    def setup(self):
        """设置CLI"""
        self.config_manager = ConfigManager()
        self.service = create_monitoring_service()
    
    def run(self, args):
        """运行CLI命令"""
        parser = argparse.ArgumentParser(description="监控告警系统CLI")
        subparsers = parser.add_subparsers(dest="command", help="可用命令")
        
        # 服务管理命令
        service_parser = subparsers.add_parser("service", help="服务管理")
        service_subparsers = service_parser.add_subparsers(dest="service_command")
        
        service_subparsers.add_parser("start", help="启动监控服务")
        service_subparsers.add_parser("stop", help="停止监控服务")
        service_subparsers.add_parser("status", help="查看服务状态")
        service_subparsers.add_parser("restart", help="重启监控服务")
        
        # 健康检查命令
        health_parser = subparsers.add_parser("health", help="健康检查")
        health_subparsers = health_parser.add_subparsers(dest="health_command")
        
        health_subparsers.add_parser("check", help="执行健康检查")
        health_subparsers.add_parser("metrics", help="查看健康指标")
        
        # 告警管理命令
        alert_parser = subparsers.add_parser("alerts", help="告警管理")
        alert_subparsers = alert_parser.add_subparsers(dest="alert_command")
        
        alert_subparsers.add_parser("list", help="列出活跃告警")
        alert_subparsers.add_parser("history", help="查看告警历史")
        alert_subparsers.add_parser("stats", help="告警统计")
        
        list_parser = alert_subparsers.choices["list"]
        list_parser.add_argument("--severity", help="按严重级别过滤")
        list_parser.add_argument("--component", help="按组件过滤")
        list_parser.add_argument("--limit", type=int, default=20, help="显示数量限制")
        
        history_parser = alert_subparsers.choices["history"]
        history_parser.add_argument("--severity", help="按严重级别过滤")
        history_parser.add_argument("--component", help="按组件过滤")
        history_parser.add_argument("--limit", type=int, default=50, help="显示数量限制")
        history_parser.add_argument("--hours", type=int, default=24, help="时间范围（小时）")
        
        # 告警操作命令
        alert_action_parser = subparsers.add_parser("alert", help="告警操作")
        alert_action_subparsers = alert_action_parser.add_subparsers(dest="alert_action_command")
        
        acknowledge_parser = alert_action_subparsers.add_parser("acknowledge", help="确认告警")
        acknowledge_parser.add_argument("alert_id", help="告警ID")
        acknowledge_parser.add_argument("--user", default="cli", help="操作用户")
        acknowledge_parser.add_argument("--notes", help="备注")
        
        resolve_parser = alert_action_subparsers.add_parser("resolve", help="解决告警")
        resolve_parser.add_argument("alert_id", help="告警ID")
        resolve_parser.add_argument("--notes", help="备注")
        
        suppress_parser = alert_action_subparsers.add_parser("suppress", help="抑制告警")
        suppress_parser.add_argument("alert_id", help="告警ID")
        
        # 配置管理命令
        config_parser = subparsers.add_parser("config", help="配置管理")
        config_subparsers = config_parser.add_subparsers(dest="config_command")
        
        config_subparsers.add_parser("show", help="显示当前配置")
        config_subparsers.add_parser("validate", help="验证配置")
        
        export_parser = config_subparsers.add_parser("export", help="导出配置")
        export_parser.add_argument("--format", choices=["json", "yaml"], default="json", help="导出格式")
        export_parser.add_argument("--output", help="输出文件路径")
        
        import_parser = config_subparsers.add_parser("import", help="导入配置")
        import_parser.add_argument("file", help="配置文件路径")
        import_parser.add_argument("--format", choices=["json", "yaml"], default="json", help="文件格式")
        
        # 规则管理命令
        rules_parser = subparsers.add_parser("rules", help="规则管理")
        rules_subparsers = rules_parser.add_subparsers(dest="rules_command")
        
        rules_subparsers.add_parser("list", help="列出告警规则")
        rules_subparsers.add_parser("show", help="显示规则详情")
        
        show_parser = rules_subparsers.choices["show"]
        show_parser.add_argument("rule_id", help="规则ID")
        
        # 仪表板命令
        dashboard_parser = subparsers.add_parser("dashboard", help="仪表板")
        dashboard_subparsers = dashboard_parser.add_subparsers(dest="dashboard_command")
        
        dashboard_subparsers.add_parser("overview", help="查看概览")
        dashboard_subparsers.add_parser("data", help="获取完整数据")
        
        # 通知命令
        notification_parser = subparsers.add_parser("notifications", help="通知管理")
        notification_subparsers = notification_parser.add_subparsers(dest="notification_command")
        
        notification_subparsers.add_parser("stats", help="通知统计")
        notification_subparsers.add_parser("test", help="测试通知")
        
        test_parser = notification_subparsers.choices["test"]
        test_parser.add_argument("--channel", choices=["email", "feishu", "webhook"], help="测试渠道")
        
        # 解析参数
        parsed_args = parser.parse_args(args)
        
        if not parsed_args.command:
            parser.print_help()
            return
        
        # 执行命令
        self._execute_command(parsed_args)
    
    def _execute_command(self, args):
        """执行命令"""
        try:
            if args.command == "service":
                self._handle_service_command(args)
            elif args.command == "health":
                self._handle_health_command(args)
            elif args.command == "alerts":
                self._handle_alerts_command(args)
            elif args.command == "alert":
                self._handle_alert_command(args)
            elif args.command == "config":
                self._handle_config_command(args)
            elif args.command == "rules":
                self._handle_rules_command(args)
            elif args.command == "dashboard":
                self._handle_dashboard_command(args)
            elif args.command == "notifications":
                self._handle_notifications_command(args)
            else:
                print(f"未知命令: {args.command}")
                
        except Exception as e:
            logger.error(f"执行命令失败: {e}")
            print(f"错误: {e}")
    
    def _handle_service_command(self, args):
        """处理服务命令"""
        if args.service_command == "start":
            self.service.start()
            print("监控服务已启动")
            
        elif args.service_command == "stop":
            self.service.stop()
            print("监控服务已停止")
            
        elif args.service_command == "status":
            status = self.service.get_service_status()
            self._print_json(status)
            
        elif args.service_command == "restart":
            self.service.stop()
            self.service.start()
            print("监控服务已重启")
    
    def _handle_health_command(self, args):
        """处理健康检查命令"""
        if args.health_command == "check":
            result = self.service.run_manual_health_check()
            self._print_json(result)
            
        elif args.health_command == "metrics":
            metrics = self.service.dashboard.get_health_metrics()
            metrics_data = [metric.to_dict() for metric in metrics]
            self._print_json(metrics_data)
    
    def _handle_alerts_command(self, args):
        """处理告警命令"""
        if args.alert_command == "list":
            alerts = self.service.dashboard.get_active_alerts(
                limit=args.limit,
                severity=args.severity,
                component=args.component
            )
            self._print_alerts_table(alerts, "活跃告警")
            
        elif args.alert_command == "history":
            end_time = datetime.now()
            start_time = end_time - timedelta(hours=args.hours)
            
            alerts = self.service.dashboard.get_alert_history(
                limit=args.limit,
                severity=args.severity,
                component=args.component,
                start_time=start_time,
                end_time=end_time
            )
            self._print_alerts_table(alerts, f"告警历史（最近{args.hours}小时）")
            
        elif args.alert_command == "stats":
            stats = self.service.alert_manager.get_stats()
            self._print_json(stats)
    
    def _handle_alert_command(self, args):
        """处理告警操作命令"""
        if args.alert_action_command == "acknowledge":
            self.service.acknowledge_alert(args.alert_id, args.user, args.notes)
            print(f"告警 {args.alert_id} 已确认")
            
        elif args.alert_action_command == "resolve":
            self.service.resolve_alert(args.alert_id, args.notes)
            print(f"告警 {args.alert_id} 已解决")
            
        elif args.alert_action_command == "suppress":
            self.service.suppress_alert(args.alert_id)
            print(f"告警 {args.alert_id} 已抑制")
    
    def _handle_config_command(self, args):
        """处理配置命令"""
        if args.config_command == "show":
            config_dict = self.service.config.to_dict()
            self._print_json(config_dict)
            
        elif args.config_command == "validate":
            errors = self.config_manager.validate_config()
            if errors:
                print("配置验证失败:")
                for error in errors:
                    print(f"  - {error}")
            else:
                print("配置验证通过")
                
        elif args.config_command == "export":
            config_str = self.config_manager.export_config(args.format)
            
            if args.output:
                with open(args.output, 'w', encoding='utf-8') as f:
                    f.write(config_str)
                print(f"配置已导出到: {args.output}")
            else:
                print(config_str)
                
        elif args.config_command == "import":
            with open(args.file, 'r', encoding='utf-8') as f:
                config_data = f.read()
            
            self.config_manager.import_config(config_data, args.format)
            print("配置已导入")
    
    def _handle_rules_command(self, args):
        """处理规则命令"""
        if args.rules_command == "list":
            rules = self.config_manager.list_alert_rules()
            self._print_rules_table(rules)
            
        elif args.rules_command == "show":
            rule = self.config_manager.get_alert_rule(args.rule_id)
            if rule:
                self._print_json(rule)
            else:
                print(f"规则不存在: {args.rule_id}")
    
    def _handle_dashboard_command(self, args):
        """处理仪表板命令"""
        if args.dashboard_command == "overview":
            overview = self.service.dashboard.get_overview()
            self._print_json(overview)
            
        elif args.dashboard_command == "data":
            data = self.service.get_dashboard_data()
            self._print_json(data)
    
    def _handle_notifications_command(self, args):
        """处理通知命令"""
        if args.notification_command == "stats":
            stats = self.service.get_notification_stats()
            self._print_json(stats)
            
        elif args.notification_command == "test":
            # TODO: 实现测试通知
            print("测试通知功能尚未实现")
    
    def _print_json(self, data: Dict[str, Any]):
        """打印JSON数据"""
        print(json.dumps(data, indent=2, ensure_ascii=False))
    
    def _print_alerts_table(self, alerts: List[Dict[str, Any]], title: str):
        """打印告警表格"""
        if not alerts:
            print(f"{title}: 无")
            return
        
        print(f"\n{title}:")
        print("-" * 100)
        print(f"{'ID':<12} {'时间':<20} {'严重级别':<10} {'组件':<15} {'消息':<40}")
        print("-" * 100)
        
        for alert in alerts:
            alert_id = alert.get("alert_id", "")[:10]
            timestamp = alert.get("timestamp", "")
            if timestamp:
                # 简化时间显示
                dt = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                timestamp = dt.strftime("%m-%d %H:%M")
            
            severity = alert.get("severity", "")
            component = alert.get("component", "")
            message = alert.get("message", "")
            
            # 截断过长的消息
            if len(message) > 37:
                message = message[:34] + "..."
            
            print(f"{alert_id:<12} {timestamp:<20} {severity:<10} {component:<15} {message:<40}")
        
        print("-" * 100)
        print(f"总计: {len(alerts)} 个告警")
    
    def _print_rules_table(self, rules: List[Dict[str, Any]]):
        """打印规则表格"""
        if not rules:
            print("告警规则: 无")
            return
        
        print("\n告警规则:")
        print("-" * 80)
        print(f"{'ID':<20} {'名称':<25} {'组件':<15} {'严重级别':<10} {'启用':<6}")
        print("-" * 80)
        
        for rule in rules:
            rule_id = rule.get("rule_id", "")
            name = rule.get("name", "")
            component = rule.get("component", "")
            severity = rule.get("severity", "")
            enabled = "是" if rule.get("enabled", True) else "否"
            
            # 截断过长的名称
            if len(name) > 22:
                name = name[:19] + "..."
            
            print(f"{rule_id:<20} {name:<25} {component:<15} {severity:<10} {enabled:<6}")
        
        print("-" * 80)
        print(f"总计: {len(rules)} 个规则")


def main():
    """主函数"""
    # 设置日志
    setup_logging()
    
    # 创建并运行CLI
    cli = MonitoringCLI()
    cli.setup()
    cli.run(sys.argv[1:])


if __name__ == "__main__":
    main()