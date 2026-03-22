        # 通知统计
        self.stats = {
            "total_sent": 0,
            "email_sent": 0,
            "email_failed": 0,
            "feishu_sent": 0,
            "feishu_failed": 0,
            "webhook_sent": 0,
            "webhook_failed": 0,
            "sms_sent": 0,
            "sms_failed": 0,
        }
        
        logger.info("通知管理器初始化完成")
    
    def send_alert(self, alert: Alert, channels: List[str] = None) -> Dict[str, bool]:
        """
        发送告警通知
        
        Args:
            alert: 告警对象
            channels: 指定通知渠道列表，如果为None则使用所有启用的渠道
            
        Returns:
            Dict[str, bool]: 各渠道发送结果
        """
        results = {}
        
        # 确定要使用的渠道
        if channels is None:
            channels = []
            if self.config.email_enabled:
                channels.append("email")
            if self.config.feishu_enabled:
                channels.append("feishu")
            if self.config.webhook_enabled:
                channels.append("webhook")
            if self.config.sms_enabled:
                channels.append("sms")
        
        # 发送到各个渠道
        for channel in channels:
            success = False
            
            try:
                if channel == "email":
                    success = self.email_notifier.send(alert)
                    if success:
                        self.stats["email_sent"] += 1
                    else:
                        self.stats["email_failed"] += 1
                
                elif channel == "feishu":
                    success = self.feishu_notifier.send(alert)
                    if success:
                        self.stats["feishu_sent"] += 1
                    else:
                        self.stats["feishu_failed"] += 1
                
                elif channel == "webhook":
                    success = self.webhook_notifier.send(alert)
                    if success:
                        self.stats["webhook_sent"] += 1
                    else:
                        self.stats["webhook_failed"] += 1
                
                elif channel == "sms":
                    success = self.sms_notifier.send(alert)
                    if success:
                        self.stats["sms_sent"] += 1
                    else:
                        self.stats["sms_failed"] += 1
                
                else:
                    logger.warning(f"未知的通知渠道: {channel}")
                    success = False
                
            except Exception as e:
                logger.error(f"发送通知到渠道 {channel} 失败: {e}")
                success = False
            
            results[channel] = success
            
            if success:
                self.stats["total_sent"] += 1
                logger.info(f"告警通知发送成功: {alert.alert_id} -> {channel}")
            else:
                logger.warning(f"告警通知发送失败: {alert.alert_id} -> {channel}")
        
        return results
    
    def send_batch_alerts(self, alerts: List[Alert]) -> Dict[str, Dict[str, bool]]:
        """
        批量发送告警通知
        
        Args:
            alerts: 告警对象列表
            
        Returns:
            Dict[str, Dict[str, bool]]: 每个告警的发送结果
        """
        results = {}
        
        for alert in alerts:
            alert_results = self.send_alert(alert)
            results[alert.alert_id] = alert_results
        
        return results
    
    def update_config(self, config: NotificationConfig):
        """更新配置"""
        self.config = config
        
        # 重新初始化通知器
        self.email_notifier = EmailNotifier(self.config)
        self.feishu_notifier = FeishuNotifier(self.config)
        self.webhook_notifier = WebhookNotifier(self.config)
        self.sms_notifier = SMSNotifier(self.config)
        
        logger.info("通知配置已更新")
    
    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        return self.stats.copy()
    
    def reset_stats(self):
        """重置统计信息"""
        self.stats = {
            "total_sent": 0,
            "email_sent": 0,
            "email_failed": 0,
            "feishu_sent": 0,
            "feishu_failed": 0,
            "webhook_sent": 0,
            "webhook_failed": 0,
            "sms_sent": 0,
            "sms_failed": 0,
        }
        logger.debug("通知统计已重置")


# 默认配置
def get_default_config() -> NotificationConfig:
    """获取默认配置"""
    return NotificationConfig(
        email_enabled=False,
        feishu_enabled=True,
        webhook_enabled=False,
        sms_enabled=False,
        severity_filter=["error", "critical"]
    )