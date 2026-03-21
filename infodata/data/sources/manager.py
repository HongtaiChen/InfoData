"""
数据源管理器

管理多个数据源适配器，提供统一的数据收集接口。
"""

from typing import Dict, List, Any, Optional, Union
from datetime import datetime, date
import threading
import time

from .base import (
    DataSourceAdapter, DataSourceConfig, DataCollectionResult,
    DataType, DataSourceStatus
)
from .akshare import AKShareAdapter
from .tushare import TushareAdapter
from ...utils.logging import get_logger

logger = get_logger(__name__)


class DataSourceManager:
    """数据源管理器"""
    
    def __init__(self, configs: Optional[Dict[str, DataSourceConfig]] = None):
        """
        初始化数据源管理器
        
        Args:
            configs: 数据源配置字典
        """
        self.adapters: Dict[str, DataSourceAdapter] = {}
        self.lock = threading.RLock()
        self._init_adapters(configs or {})
        
        logger.info(f"数据源管理器初始化完成，共 {len(self.adapters)} 个适配器")
    
    def _init_adapters(self, configs: Dict[str, DataSourceConfig]) -> None:
        """
        初始化数据源适配器
        
        Args:
            configs: 数据源配置字典
        """
        # 默认配置
        default_configs = {
            "akshare": DataSourceConfig(
                name="akshare",
                base_url=None,
                api_key=None,
                rate_limit=5,  # AKShare频率限制较宽松
                timeout=30,
                retry_count=3,
                retry_delay=5,
                enabled=True,
                priority=1,  # 默认优先级
                metadata={"type": "free"}
            ),
            "tushare": DataSourceConfig(
                name="tushare",
                base_url=None,
                api_key="d74c40bf7bb33a39e27a8e8f47d1d628b09560c652f9caf713dc9db0",
                rate_limit=2,  # Tushare有频率限制
                timeout=30,
                retry_count=3,
                retry_delay=5,
                enabled=True,
                priority=2,  # 备用数据源
                metadata={"type": "free"}
            )
        }
        
        # 合并配置
        all_configs = {**default_configs, **configs}
        
        # 创建适配器
        for name, config in all_configs.items():
            if not config.enabled:
                logger.info(f"数据源 {name} 已禁用，跳过初始化")
                continue
            
            try:
                if name == "akshare":
                    adapter = AKShareAdapter(config)
                elif name == "tushare":
                    adapter = TushareAdapter(config)
                else:
                    logger.warning(f"未知的数据源类型: {name}")
                    continue
                
                self.adapters[name] = adapter
                logger.info(f"数据源适配器 {name} 初始化成功")
                
            except Exception as e:
                logger.error(f"初始化数据源适配器 {name} 失败: {e}")
    
    def connect_all(self) -> Dict[str, bool]:
        """
        连接所有数据源
        
        Returns:
            Dict[str, bool]: 各数据源连接结果
        """
        results = {}
        
        with self.lock:
            for name, adapter in self.adapters.items():
                try:
                    logger.info(f"连接数据源: {name}")
                    success = adapter.connect()
                    results[name] = success
                    
                    if success:
                        logger.info(f"数据源 {name} 连接成功")
                    else:
                        logger.error(f"数据源 {name} 连接失败")
                        
                except Exception as e:
                    logger.error(f"连接数据源 {name} 时发生错误: {e}")
                    results[name] = False
        
        return results
    
    def disconnect_all(self) -> Dict[str, bool]:
        """
        断开所有数据源连接
        
        Returns:
            Dict[str, bool]: 各数据源断开结果
        """
        results = {}
        
        with self.lock:
            for name, adapter in self.adapters.items():
                try:
                    logger.info(f"断开数据源: {name}")
                    success = adapter.disconnect()
                    results[name] = success
                    
                    if success:
                        logger.info(f"数据源 {name} 断开成功")
                    else:
                        logger.error(f"数据源 {name} 断开失败")
                        
                except Exception as e:
                    logger.error(f"断开数据源 {name} 时发生错误: {e}")
                    results[name] = False
        
        return results
    
    def get_adapter(self, source_name: str) -> Optional[DataSourceAdapter]:
        """
        获取数据源适配器
        
        Args:
            source_name: 数据源名称
            
        Returns:
            Optional[DataSourceAdapter]: 数据源适配器，如果不存在则返回None
        """
        with self.lock:
            return self.adapters.get(source_name)
    
    def get_available_adapters(self, data_type: DataType) -> List[DataSourceAdapter]:
        """
        获取可用的数据源适配器（按优先级排序）
        
        Args:
            data_type: 数据类型
            
        Returns:
            List[DataSourceAdapter]: 可用的适配器列表
        """
        with self.lock:
            # 过滤启用的适配器
            enabled_adapters = [
                (name, adapter) 
                for name, adapter in self.adapters.items() 
                if adapter.config.enabled
            ]
            
            # 按优先级排序
            enabled_adapters.sort(key=lambda x: x[1].config.priority, reverse=True)
            
            return [adapter for _, adapter in enabled_adapters]
    
    def collect_data(
        self,
        data_type: DataType,
        source_name: Optional[str] = None,
        **kwargs
    ) -> DataCollectionResult:
        """
        收集数据（自动选择数据源）
        
        Args:
            data_type: 数据类型
            source_name: 指定数据源名称，如果为None则自动选择
            **kwargs: 收集参数
            
        Returns:
            DataCollectionResult: 收集结果
        """
        start_time = datetime.now()
        
        try:
            if source_name:
                # 使用指定数据源
                adapter = self.get_adapter(source_name)
                if not adapter:
                    raise ValueError(f"数据源 {source_name} 不存在或未启用")
                
                adapters_to_try = [adapter]
            else:
                # 自动选择数据源
                adapters_to_try = self.get_available_adapters(data_type)
                if not adapters_to_try:
                    raise ValueError("没有可用的数据源适配器")
            
            # 尝试各个数据源
            last_error = None
            for adapter in adapters_to_try:
                try:
                    logger.info(f"使用数据源 {adapter.config.name} 收集 {data_type.value} 数据")
                    
                    result = adapter.collect_data(data_type, **kwargs)
                    
                    if result.success:
                        logger.info(
                            f"数据收集成功: {data_type.value}, "
                            f"数据源: {adapter.config.name}, "
                            f"记录数: {result.records_processed}"
                        )
                        return result
                    else:
                        logger.warning(
                            f"数据源 {adapter.config.name} 收集失败: {result.error_message}"
                        )
                        last_error = result.error_message
                        
                except Exception as e:
                    logger.error(f"数据源 {adapter.config.name} 收集异常: {e}")
                    last_error = str(e)
                    continue
            
            # 所有数据源都失败
            error_msg = f"所有数据源收集失败: {last_error}"
            logger.error(error_msg)
            
            return DataCollectionResult(
                data_type=data_type,
                source_name=source_name or "auto",
                start_time=start_time,
                end_time=datetime.now(),
                success=False,
                error_message=error_msg,
                metadata={"tried_sources": [a.config.name for a in adapters_to_try]}
            )
            
        except Exception as e:
            logger.error(f"数据收集过程异常: {e}", exc_info=True)
            
            return DataCollectionResult(
                data_type=data_type,
                source_name=source_name or "auto",
                start_time=start_time,
                end_time=datetime.now(),
                success=False,
                error_message=str(e),
                metadata={"error_type": type(e).__name__}
            )
    
    def collect_data_with_fallback(
        self,
        data_type: DataType,
        primary_source: str,
        fallback_sources: List[str],
        **kwargs
    ) -> DataCollectionResult:
        """
        收集数据（带备用数据源）
        
        Args:
            data_type: 数据类型
            primary_source: 主数据源
            fallback_sources: 备用数据源列表
            **kwargs: 收集参数
            
        Returns:
            DataCollectionResult: 收集结果
        """
        # 尝试主数据源
        result = self.collect_data(data_type, source_name=primary_source, **kwargs)
        
        if result.success:
            return result
        
        # 主数据源失败，尝试备用数据源
        logger.warning(f"主数据源 {primary_source} 失败，尝试备用数据源")
        
        for fallback_source in fallback_sources:
            result = self.collect_data(data_type, source_name=fallback_source, **kwargs)
            
            if result.success:
                logger.info(f"备用数据源 {fallback_source} 收集成功")
                return result
            
            logger.warning(f"备用数据源 {fallback_source} 也失败: {result.error_message}")
        
        # 所有数据源都失败
        return result
    
    def get_status(self) -> Dict[str, Any]:
        """
        获取所有数据源状态
        
        Returns:
            Dict[str, Any]: 状态信息
        """
        status = {
            "total_adapters": len(self.adapters),
            "enabled_adapters": 0,
            "connected_adapters": 0,
            "adapters": {},
            "timestamp": datetime.now().isoformat(),
        }
        
        with self.lock:
            for name, adapter in self.adapters.items():
                adapter_status = adapter.get_status()
                status["adapters"][name] = adapter_status
                
                if adapter.config.enabled:
                    status["enabled_adapters"] += 1
                
                if adapter.status == DataSourceStatus.CONNECTED:
                    status["connected_adapters"] += 1
        
        return status
    
    def get_statistics(self) -> Dict[str, Any]:
        """
        获取数据源统计信息
        
        Returns:
            Dict[str, Any]: 统计信息
        """
        statistics = {
            "total_requests": 0,
            "successful_requests": 0,
            "error_count": 0,
            "adapters": {},
            "timestamp": datetime.now().isoformat(),
        }
        
        with self.lock:
            for name, adapter in self.adapters.items():
                adapter_stats = adapter.get_statistics()
                statistics["adapters"][name] = adapter_stats
                
                statistics["total_requests"] += adapter_stats.get("total_requests", 0)
                statistics["successful_requests"] += adapter_stats.get("successful_requests", 0)
                statistics["error_count"] += adapter_stats.get("error_count", 0)
        
        # 计算总体成功率
        if statistics["total_requests"] > 0:
            statistics["overall_success_rate"] = (
                statistics["successful_requests"] / statistics["total_requests"]
            )
        else:
            statistics["overall_success_rate"] = 0
        
        return statistics
    
    def health_check(self) -> Dict[str, Any]:
        """
        健康检查
        
        Returns:
            Dict[str, Any]: 健康检查结果
        """
        health = {
            "status": "healthy",
            "checks": {},
            "timestamp": datetime.now().isoformat(),
        }
        
        with self.lock:
            for name, adapter in self.adapters.items():
                if not adapter.config.enabled:
                    health["checks"][name] = {"status": "disabled", "message": "适配器已禁用"}
                    continue
                
                try:
                    # 检查连接状态
                    if adapter.status != DataSourceStatus.CONNECTED:
                        health["checks"][name] = {
                            "status": "unhealthy", 
                            "message": f"连接状态: {adapter.status.value}"
                        }
                        health["status"] = "degraded"
                    else:
                        health["checks"][name] = {"status": "healthy", "message": "连接正常"}
                        
                except Exception as e:
                    health["checks"][name] = {
                        "status": "error", 
                        "message": f"健康检查失败: {str(e)}"
                    }
                    health["status"] = "unhealthy"
        
        return health
    
    def add_adapter(self, name: str, config: DataSourceConfig) -> bool:
        """
        添加数据源适配器
        
        Args:
            name: 适配器名称
            config: 适配器配置
            
        Returns:
            bool: 添加是否成功
        """
        with self.lock:
            if name in self.adapters:
                logger.warning(f"数据源适配器 {name} 已存在")
                return False
            
            try:
                if name == "akshare":
                    adapter = AKShareAdapter(config)
                elif name == "tushare":
                    adapter = TushareAdapter(config)
                else:
                    logger.error(f"不支持的数据源类型: {name}")
                    return False
                
                self.adapters[name] = adapter
                logger.info(f"数据源适配器 {name} 添加成功")
                return True
                
            except Exception as e:
                logger.error(f"添加数据源适配器 {name} 失败: {e}")
                return False
    
    def remove_adapter(self, name: str) -> bool:
        """
        移除数据源适配器
        
        Args:
            name: 适配器名称
            
        Returns:
            bool: 移除是否成功
        """
        with self.lock:
            if name not in self.adapters:
                logger.warning(f"数据源适配器 {name} 不存在")
                return False
            
            try:
                # 先断开连接
                self.adapters[name].disconnect()
                
                # 移除适配器
                del self.adapters[name]
                logger.info(f"数据源适配器 {name} 移除成功")
                return True
                
            except Exception as e:
                logger.error(f"移除数据源适配器 {name} 失败: {e}")
                return False
    
    def update_adapter_config(self, name: str, config: DataSourceConfig) -> bool:
        """
        更新数据源适配器配置
        
        Args:
            name: 适配器名称
            config: 新的配置
            
        Returns:
            bool: 更新是否成功
        """
        with self.lock:
            if name not in self.adapters:
                logger.warning(f"数据源适配器 {name} 不存在")
                return False
            
            try:
                # 移除旧适配器
                self.adapters[name].disconnect()
                
                # 创建新适配器
                if name == "akshare":
                    adapter = AKShareAdapter(config)
                elif name == "tushare":
                    adapter = TushareAdapter(config)
                else:
                    logger.error(f"不支持的数据源类型: {name}")
                    return False
                
                self.adapters[name] = adapter
                logger.info(f"数据源适配器 {name} 配置更新成功")
                return True
                
            except Exception as e:
                logger.error(f"更新数据源适配器 {name} 配置失败: {e}")
                return False


# 全局数据源管理器实例
_global_manager: Optional[DataSourceManager] = None


def get_data_source_manager(
    configs: Optional[Dict[str, DataSourceConfig]] = None
) -> DataSourceManager:
    """
    获取全局数据源管理器
    
    Args:
        configs: 数据源配置字典
        
    Returns:
        DataSourceManager: 数据源管理器实例
    """
    global _global_manager
    
    if _global_manager is None:
        _global_manager = DataSourceManager(configs)
    
    return _global_manager


def collect_stock_daily(
    symbols: Optional[List[str]] = None,
    start_date: Optional[Union[str, date, datetime]] = None,
    end_date: Optional[Union[str, date, datetime]] = None,
    source_name: Optional[str] = None,
    **kwargs
) -> DataCollectionResult:
    """
    收集股票日度行情数据（便捷函数）
    
    Args:
        symbols: 股票代码列表
        start_date: 开始日期
        end_date: 结束日期
        source_name: 数据源名称
        **kwargs: 其他参数
        
    Returns:
        DataCollectionResult: 收集结果
    """
    manager = get_data_source_manager()
    return manager.collect_data(
        data_type=DataType.STOCK_DAILY,
        source_name=source_name,
        symbols=symbols,
        start_date=start_date,
        end_date=end_date,
        **kwargs
    )


def collect_stock_info(
    symbols: Optional[List[str]] = None,
    source_name: Optional[str] = None,
    **kwargs
) -> DataCollectionResult:
    """
    收集股票基本信息（便捷函数）
    
    Args:
        symbols: 股票代码列表
        source_name: 数据源名称
        **kwargs: 其他参数
        
    Returns:
        DataCollectionResult: 收集结果
    """
    manager = get_data_source_manager()
    return manager.collect_data(
        data_type=DataType.STOCK_INFO,
        source_name=source_name,
        symbols=symbols,
        **kwargs
    )


def collect_fund_daily(
    symbols: Optional[List[str]] = None,
    start_date: Optional[Union[str, date, datetime]] = None,
    end_date: Optional[Union[str, date, datetime]] = None,
    source_name: Optional[str] = None,
    **kwargs
) -> DataCollectionResult:
    """
    收集基金日度净值数据（便捷函数）
    
    Args:
        symbols: 基金代码列表
        start_date: 开始日期
        end_date: 结束日期
        source_name: 数据源名称
        **kwargs: 其他参数
        
    Returns:
        DataCollectionResult: 收集结果
    """
    manager = get_data_source_manager()
    return manager.collect_data(
        data_type=DataType.FUND_DAILY,
        source_name=source_name,
        symbols=symbols,
        start_date=start_date,
        end_date=end_date,
        **kwargs
    )


def collect_fund_info(
    symbols: Optional[List[str]] = None,
    source_name: Optional[str] = None,
    **kwargs
) -> DataCollectionResult:
    """
    收集基金基本信息（便捷函数）
    
    Args:
        symbols: 基金代码列表
        source_name: 数据源名称
        **kwargs: 其他参数
        
    Returns:
        DataCollectionResult: 收集结果
    """
    manager = get_data_source_manager()
    return manager.collect_data(
        data_type=DataType.FUND_INFO,
        source_name=source_name,
        symbols=symbols,
        **kwargs
    )


def collect_bond_daily(
    symbols: Optional[List[str]] = None,
    start_date: Optional[Union[str, date, datetime]] = None,
    end_date: Optional[Union[str, date, datetime]] = None,
    source_name: Optional[str] = None,
    **kwargs
) -> DataCollectionResult:
    """
    收集债券日度行情数据（便捷函数）
    
    Args:
        symbols: 债券代码列表
        start_date: 开始日期
        end_date: 结束日期
        source_name: 数据源名称
        **kwargs: 其他参数
        
    Returns:
        DataCollectionResult: 收集结果
    """
    manager = get_data_source_manager()
    return manager.collect_data(
        data_type=DataType.BOND_DAILY,
        source_name=source_name,
        symbols=symbols,
        start_date=start_date,
        end_date=end_date,
        **kwargs
    )


def collect_index_daily(
    symbols: Optional[List[str]] = None,
    start_date: Optional[Union[str, date, datetime]] = None,
    end_date: Optional[Union[str, date, datetime]] = None,
    source_name: Optional[str] = None,
    **kwargs
) -> DataCollectionResult:
    """
    收集指数日度行情数据（便捷函数）
    
    Args:
        symbols: 指数代码列表
        start_date: 开始日期
        end_date: 结束日期
        source_name: 数据源名称
        **kwargs: 其他参数
        
    Returns:
        DataCollectionResult: 收集结果
    """
    manager = get_data_source_manager()
    return manager.collect_data(
        data_type=DataType.INDEX_DAILY,
        source_name=source_name,
        symbols=symbols,
        start_date=start_date,
        end_date=end_date,
        **kwargs
    )