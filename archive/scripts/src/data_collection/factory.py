"""
数据采集客户端工厂

提供统一的客户端创建和管理接口。
"""

import logging
from typing import Optional, Dict, Any
from .base import BaseDataClient, RateLimitConfig
from .akshare_client import AKShareClient
from .tushare_client import TushareClient


class DataClientFactory:
    """数据客户端工厂
    
    管理数据采集客户端的创建和配置。
    """
    
    # 默认配置
    DEFAULT_RATE_LIMIT = RateLimitConfig(
        max_requests_per_minute=60,
        max_requests_per_hour=1000,
        delay_between_requests=0.1
    )
    
    def __init__(self, logger: Optional[logging.Logger] = None):
        """初始化工厂
        
        Args:
            logger: 日志记录器
        """
        self.logger = logger or logging.getLogger(__name__)
        self._clients: Dict[str, BaseDataClient] = {}
    
    def create_akshare_client(
        self,
        rate_limit_config: Optional[RateLimitConfig] = None,
        max_retries: int = 3,
        retry_delay: float = 1.0,
        client_id: str = "akshare_default"
    ) -> AKShareClient:
        """创建AKShare客户端
        
        Args:
            rate_limit_config: 速率限制配置
            max_retries: 最大重试次数
            retry_delay: 重试延迟（秒）
            client_id: 客户端ID
            
        Returns:
            AKShare客户端实例
        """
        client = AKShareClient(
            rate_limit_config=rate_limit_config or self.DEFAULT_RATE_LIMIT,
            max_retries=max_retries,
            retry_delay=retry_delay,
            logger=self.logger.getChild("akshare") if self.logger else None
        )
        
        self._clients[client_id] = client
        self.logger.info(f"创建AKShare客户端: {client_id}")
        return client
    
    def create_tushare_client(
        self,
        token: Optional[str] = None,
        rate_limit_config: Optional[RateLimitConfig] = None,
        max_retries: int = 3,
        retry_delay: float = 1.0,
        client_id: str = "tushare_default"
    ) -> TushareClient:
        """创建Tushare客户端
        
        Args:
            token: Tushare Pro token
            rate_limit_config: 速率限制配置
            max_retries: 最大重试次数
            retry_delay: 重试延迟（秒）
            client_id: 客户端ID
            
        Returns:
            Tushare客户端实例
        """
        client = TushareClient(
            token=token,
            rate_limit_config=rate_limit_config or self.DEFAULT_RATE_LIMIT,
            max_retries=max_retries,
            retry_delay=retry_delay,
            logger=self.logger.getChild("tushare") if self.logger else None
        )
        
        self._clients[client_id] = client
        self.logger.info(f"创建Tushare客户端: {client_id}")
        return client
    
    def get_client(self, client_id: str) -> Optional[BaseDataClient]:
        """获取已创建的客户端
        
        Args:
            client_id: 客户端ID
            
        Returns:
            客户端实例或None
        """
        return self._clients.get(client_id)
    
    def get_all_clients(self) -> Dict[str, BaseDataClient]:
        """获取所有客户端
        
        Returns:
            客户端字典
        """
        return self._clients.copy()
    
    def get_stats(self) -> Dict[str, Dict[str, Any]]:
        """获取所有客户端统计信息
        
        Returns:
            包含所有客户端统计信息的字典
        """
        stats = {}
        for client_id, client in self._clients.items():
            stats[client_id] = client.get_stats()
        return stats
    
    def test_all_connections(self) -> Dict[str, bool]:
        """测试所有客户端连接
        
        Returns:
            连接测试结果字典
        """
        results = {}
        for client_id, client in self._clients.items():
            try:
                success = client.test_connection()
                results[client_id] = success
                status = "成功" if success else "失败"
                self.logger.info(f"客户端 {client_id} 连接测试: {status}")
            except Exception as e:
                results[client_id] = False
                self.logger.error(f"客户端 {client_id} 连接测试异常: {e}")
        
        return results


# 全局工厂实例
_factory: Optional[DataClientFactory] = None


def get_factory() -> DataClientFactory:
    """获取全局工厂实例
    
    Returns:
        全局工厂实例
    """
    global _factory
    if _factory is None:
        _factory = DataClientFactory()
    return _factory


def get_akshare_client(
    client_id: str = "akshare_default",
    **kwargs
) -> AKShareClient:
    """获取AKShare客户端（便捷函数）
    
    Args:
        client_id: 客户端ID
        **kwargs: 传递给create_akshare_client的参数
        
    Returns:
        AKShare客户端实例
    """
    factory = get_factory()
    
    # 如果客户端已存在，直接返回
    existing_client = factory.get_client(client_id)
    if isinstance(existing_client, AKShareClient):
        return existing_client
    
    # 否则创建新的客户端
    return factory.create_akshare_client(client_id=client_id, **kwargs)


def get_tushare_client(
    client_id: str = "tushare_default",
    **kwargs
) -> TushareClient:
    """获取Tushare客户端（便捷函数）
    
    Args:
        client_id: 客户端ID
        **kwargs: 传递给create_tushare_client的参数
        
    Returns:
        Tushare客户端实例
    """
    factory = get_factory()
    
    # 如果客户端已存在，直接返回
    existing_client = factory.get_client(client_id)
    if isinstance(existing_client, TushareClient):
        return existing_client
    
    # 否则创建新的客户端
    return factory.create_tushare_client(client_id=client_id, **kwargs)