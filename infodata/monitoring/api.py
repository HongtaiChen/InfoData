"""
监控系统Web API接口

提供RESTful API来管理监控告警系统。
"""

from flask import Flask, jsonify, request, Response
from typing import Dict, Any, List, Optional
import json
from datetime import datetime, timedelta

from .service import create_monitoring_service
from .config import ConfigManager
from .alerts import AlertSeverity, AlertStatus
from ..utils.logging import get_logger

logger = get_logger(__name__)


class MonitoringAPI:
    """监控系统API"""
    
    def __init__(self, service=None):
        """
        初始化API
        
        Args:
            service: 监控服务实例
        """
        self.service = service or create_monitoring_service()
        self.app = Flask(__name__)
        self._setup_routes()
        
        logger.info("监控API初始化完成")
    
    def _setup_routes(self):
        """设置路由"""
        
        # 服务管理
        @self.app.route('/api/v1/service/status', methods=['GET'])
        def get_service_status():
            """获取服务状态"""
            status = self.service.get_service_status()
            return jsonify(status)
        
        @self.app.route('/api/v1/service/start', methods=['POST'])
        def start_service():
            """启动服务"""
            self.service.start()
            return jsonify({"message": "服务已启动"})
        
        @self.app.route('/api/v1/service/stop', methods=['POST'])
        def stop_service():
            """停止服务"""
            self.service.stop()
            return jsonify({"message": "服务已停止"})
        
        @self.app.route('/api/v1/service/restart', methods=['POST'])
        def restart_service():
            """重启服务"""
            self.service.stop()
            self.service.start()
            return jsonify({"message": "服务已重启"})
        
        # 健康检查
        @self.app.route('/api/v1/health/check', methods=['POST'])
        def run_health_check():
            """运行健康检查"""
            result = self.service.run_manual_health_check()
            return jsonify(result)
        
        @self.app.route('/api/v1/health/metrics', methods=['GET'])
        def get_health_metrics():
            """获取健康指标"""
            metrics = self.service.dashboard.get_health_metrics()
            metrics_data = [metric.to_dict() for metric in metrics]
            return jsonify(metrics_data)
        
        # 告警管理
        @self.app.route('/api/v1/alerts', methods=['GET'])
        def get_alerts():
            """获取活跃告警"""
            # 获取查询参数
            severity = request.args.get('severity')
            component = request.args.get('component')
            limit = request.args.get('limit', default=20, type=int)
            
            alerts = self.service.dashboard.get_active_alerts(
                limit=limit,
                severity=severity,
                component=component
            )
            return jsonify(alerts)
        
        @self.app.route('/api/v1/alerts/history', methods=['GET'])
        def get_alert_history():
            """获取告警历史"""
            # 获取查询参数
            severity = request.args.get('severity')
            component = request.args.get('component')
            limit = request.args.get('limit', default=50, type=int)
            hours = request.args.get('hours', default=24, type=int)
            
            end_time = datetime.now()
            start_time = end_time - timedelta(hours=hours)
            
            alerts = self.service.dashboard.get_alert_history(
                limit=limit,
                severity=severity,
                component=component,
                start_time=start_time,
                end_time=end_time
            )
            return jsonify(alerts)
        
        @self.app.route('/api/v1/alerts/stats', methods=['GET'])
        def get_alert_stats():
            """获取告警统计"""
            stats = self.service.alert_manager.get_stats()
            return jsonify(stats)
        
        # 告警操作
        @self.app.route('/api/v1/alerts/<alert_id>/acknowledge', methods=['POST'])
        def acknowledge_alert(alert_id):
            """确认告警"""
            data = request.get_json() or {}
            user = data.get('user', 'api')
            notes = data.get('notes')
            
            self.service.acknowledge_alert(alert_id, user, notes)
            return jsonify({"message": f"告警 {alert_id} 已确认"})
        
        @self.app.route('/api/v1/alerts/<alert_id>/resolve', methods=['POST'])
        def resolve_alert(alert_id):
            """解决告警"""
            data = request.get_json() or {}
            notes = data.get('notes')
            
            self.service.resolve_alert(alert_id, notes)
            return jsonify({"message": f"告警 {alert_id} 已解决"})
        
        @self.app.route('/api/v1/alerts/<alert_id>/suppress', methods=['POST'])
        def suppress_alert(alert_id):
            """抑制告警"""
            self.service.suppress_alert(alert_id)
            return jsonify({"message": f"告警 {alert_id} 已抑制"})
        
        # 配置管理
        @self.app.route('/api/v1/config', methods=['GET'])
        def get_config():
            """获取配置"""
            config_dict = self.service.config.to_dict()
            return jsonify(config_dict)
        
        @self.app.route('/api/v1/config', methods=['PUT'])
        def update_config():
            """更新配置"""
            data = request.get_json()
            if not data:
                return jsonify({"error": "请求体不能为空"}), 400
            
            try:
                from .config import MonitoringConfig
                new_config = MonitoringConfig.from_dict(data)
                self.service.update_config(new_config)
                return jsonify({"message": "配置已更新"})
            except Exception as e:
                return jsonify({"error": str(e)}), 400
        
        @self.app.route('/api/v1/config/validate', methods=['GET'])
        def validate_config():
            """验证配置"""
            config_manager = ConfigManager()
            errors = config_manager.validate_config()
            return jsonify({"valid": len(errors) == 0, "errors": errors})
        
        # 规则管理
        @self.app.route('/api/v1/rules', methods=['GET'])
        def get_rules():
            """获取告警规则"""
            config_manager = ConfigManager()
            rules = config_manager.list_alert_rules()
            return jsonify(rules)
        
        @self.app.route('/api/v1/rules/<rule_id>', methods=['GET'])
        def get_rule(rule_id):
            """获取单个规则"""
            config_manager = ConfigManager()
            rule = config_manager.get_alert_rule(rule_id)
            if rule:
                return jsonify(rule)
            else:
                return jsonify({"error": "规则不存在"}), 404
        
        @self.app.route('/api/v1/rules', methods=['POST'])
        def create_rule():
            """创建规则"""
            data = request.get_json()
            if not data:
                return jsonify({"error": "请求体不能为空"}), 400
            
            try:
                config_manager = ConfigManager()
                config_manager.add_alert_rule(data)
                return jsonify({"message": "规则已创建"}), 201
            except Exception as e:
                return jsonify({"error": str(e)}), 400
        
        @self.app.route('/api/v1/rules/<rule_id>', methods=['PUT'])
        def update_rule(rule_id):
            """更新规则"""
            data = request.get_json()
            if not data:
                return jsonify({"error": "请求体不能为空"}), 400
            
            try:
                config_manager = ConfigManager()
                config_manager.update_alert_rule(rule_id, data)
                return jsonify({"message": "规则已更新"})
            except ValueError as e:
                return jsonify({"error": str(e)}), 404
            except Exception as e:
                return jsonify({"error": str(e)}), 400
        
        @self.app.route('/api/v1/rules/<rule_id>', methods=['DELETE'])
        def delete_rule(rule_id):
            """删除规则"""
            try:
                config_manager = ConfigManager()
                config_manager.delete_alert_rule(rule_id)
                return jsonify({"message": "规则已删除"})
            except ValueError as e:
                return jsonify({"error": str(e)}), 404
        
        # 仪表板
        @self.app.route('/api/v1/dashboard/overview', methods=['GET'])
        def get_dashboard_overview():
            """获取仪表板概览"""
            overview = self.service.dashboard.get_overview()
            return jsonify(overview)
        
        @self.app.route('/api/v1/dashboard/data', methods=['GET'])
        def get_dashboard_data():
            """获取完整仪表板数据"""
            data = self.service.get_dashboard_data()
            return jsonify(data)
        
        # 通知管理
        @self.app.route('/api/v1/notifications/stats', methods=['GET'])
        def get_notification_stats():
            """获取通知统计"""
            stats = self.service.get_notification_stats()
            return jsonify(stats)
        
        # WebSocket端点（用于实时更新）
        @self.app.route('/api/v1/ws')
        def websocket_endpoint():
            """WebSocket端点"""
            # TODO: 实现WebSocket支持
            return jsonify({"message": "WebSocket端点（尚未实现）"})
        
        # 错误处理
        @self.app.errorhandler(404)
        def not_found(error):
            return jsonify({"error": "资源不存在"}), 404
        
        @self.app.errorhandler(500)
        def internal_error(error):
            logger.error(f"内部服务器错误: {error}")
            return jsonify({"error": "内部服务器错误"}), 500
    
    def run(self, host='0.0.0.0', port=5000, debug=False):
        """
        运行API服务器
        
        Args:
            host: 主机地址
            port: 端口号
            debug: 调试模式
        """
        logger.info(f"启动监控API服务器: {host}:{port}")
        self.app.run(host=host, port=port, debug=debug)
    
    def get_app(self):
        """获取Flask应用实例"""
        return self.app


def create_api(service=None) -> MonitoringAPI:
    """创建API实例"""
    return MonitoringAPI(service)


# 快速启动函数
def run_api_server(host='0.0.0.0', port=5000, debug=False):
    """运行API服务器"""
    api = create_api()
    api.run(host=host, port=port, debug=debug)


if __name__ == '__main__':
    run_api_server()