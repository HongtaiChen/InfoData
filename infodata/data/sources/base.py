"""
数据源适配器基类

定义统一的数据源接口和基础实现。
"""

import abc
from datetime import datetime, date
from typing import Dict, List, Any, Optional, Union
from dataclasses import dataclass, field
from enum import Enum

from ...utils.logging import get_logger

logger = get_logger(__name__)


class DataType(Enum):
    """数据类型枚举"""
    STOCK_DAILY = "stock_daily"  # 股票日度行情
    STOCK_INFO = "stock_info"    # 股票基本信息
    STOCK_INDUSTRY = "stock_industry"  # 股票行业分类
    STOCK_CONCEPT = "stock_concept"    # 股票概念板块
    STOCK_HOLDER = "stock_holder"      # 股票股东信息
    STOCK_DIVIDEND = "stock_dividend"  # 股票分红信息
    STOCK_SPLIT = "stock_split"        # 股票拆分信息
    
    FUND_DAILY = "fund_daily"    # 基金日度净值
    FUND_INFO = "fund_info"      # 基金基本信息
    FUND_NET_VALUE = "fund_net_value"  # 基金历史净值
    FUND_MANAGER = "fund_manager"      # 基金经理信息
    
    BOND_DAILY = "bond_daily"    # 债券日度行情
    BOND_INFO = "bond_info"      # 债券基本信息
    BOND_YIELD = "bond_yield"    # 债券收益率曲线
    BOND_RATING = "bond_rating"  # 债券评级信息
    
    INDEX_DAILY = "index_daily"  # 指数日度行情
    INDEX_INFO = "index_info"    # 指数基本信息
    INDEX_COMPONENT = "index_component"  # 指数成分股


class DataSourceStatus(Enum):
    """数据源状态枚举"""
    CONNECTED = "connected"      # 已连接
    DISCONNECTED = "disconnected"  # 未连接
    ERROR = "error"              # 错误状态
    RATE_LIMITED = "rate_limited"  # 频率限制
    MAINTENANCE = "maintenance"  # 维护中


@dataclass
class DataSourceConfig:
    """数据源配置"""
    name: str                    # 数据源名称
    base_url: Optional[str] = None  # 基础URL
    api_key: Optional[str] = None   # API密钥
    rate_limit: int = 10         # 每秒请求限制
    timeout: int = 30            # 超时时间（秒）
    retry_count: int = 3         # 重试次数
    retry_delay: int = 5         # 重试延迟（秒）
    enabled: bool = True         # 是否启用
    priority: int = 1            # 优先级（1-10，越高越优先）
    metadata: Dict[str, Any] = field(default_factory=dict)  # 元数据


@dataclass
class DataCollectionResult:
    """数据收集结果"""
    data_type: DataType          # 数据类型
    source_name: str             # 数据源名称
    start_time: datetime         # 开始时间
    end_time: datetime           # 结束时间
    records_collected: int = 0   # 收集记录数
    records_processed: int = 0   # 处理记录数
    records_failed: int = 0      # 失败记录数
    success: bool = False        # 是否成功
    error_message: Optional[str] = None  # 错误信息
    raw_data: Optional[List[Dict]] = None  # 原始数据
    processed_data: Optional[List[Dict]] = None  # 处理后的数据
    metadata: Dict[str, Any] = field(default_factory=dict)  # 元数据


class DataSourceAdapter(abc.ABC):
    """数据源适配器基类"""
    
    def __init__(self, config: DataSourceConfig):
        """
        初始化数据源适配器
        
        Args:
            config: 数据源配置
        """
        self.config = config
        self.status = DataSourceStatus.DISCONNECTED
        self.last_connection_time = None
        self.error_count = 0
        self.total_requests = 0
        self.successful_requests = 0
        
        logger.info(f"初始化数据源适配器: {config.name}")
    
    @abc.abstractmethod
    def connect(self) -> bool:
        """
        连接到数据源
        
        Returns:
            bool: 连接是否成功
        """
        pass
    
    @abc.abstractmethod
    def disconnect(self) -> bool:
        """
        断开数据源连接
        
        Returns:
            bool: 断开是否成功
        """
        pass
    
    @abc.abstractmethod
    def get_status(self) -> Dict[str, Any]:
        """
        获取数据源状态
        
        Returns:
            Dict[str, Any]: 状态信息
        """
        pass
    
    @abc.abstractmethod
    def collect_stock_daily(
        self, 
        symbols: Optional[List[str]] = None,
        start_date: Optional[Union[str, date, datetime]] = None,
        end_date: Optional[Union[str, date, datetime]] = None,
        **kwargs
    ) -> DataCollectionResult:
        """
        收集股票日度行情数据
        
        Args:
            symbols: 股票代码列表，如果为None则收集所有股票
            start_date: 开始日期
            end_date: 结束日期
            **kwargs: 其他参数
            
        Returns:
            DataCollectionResult: 收集结果
        """
        pass
    
    @abc.abstractmethod
    def collect_stock_info(
        self,
        symbols: Optional[List[str]] = None,
        **kwargs
    ) -> DataCollectionResult:
        """
        收集股票基本信息
        
        Args:
            symbols: 股票代码列表，如果为None则收集所有股票
            **kwargs: 其他参数
            
        Returns:
            DataCollectionResult: 收集结果
        """
        pass
    
    @abc.abstractmethod
    def collect_fund_daily(
        self,
        symbols: Optional[List[str]] = None,
        start_date: Optional[Union[str, date, datetime]] = None,
        end_date: Optional[Union[str, date, datetime]] = None,
        **kwargs
    ) -> DataCollectionResult:
        """
        收集基金日度净值数据
        
        Args:
            symbols: 基金代码列表，如果为None则收集所有基金
            start_date: 开始日期
            end_date: 结束日期
            **kwargs: 其他参数
            
        Returns:
            DataCollectionResult: 收集结果
        """
        pass
    
    @abc.abstractmethod
    def collect_fund_info(
        self,
        symbols: Optional[List[str]] = None,
        **kwargs
    ) -> DataCollectionResult:
        """
        收集基金基本信息
        
        Args:
            symbols: 基金代码列表，如果为None则收集所有基金
            **kwargs: 其他参数
            
        Returns:
            DataCollectionResult: 收集结果
        """
        pass
    
    @abc.abstractmethod
    def collect_bond_daily(
        self,
        symbols: Optional[List[str]] = None,
        start_date: Optional[Union[str, date, datetime]] = None,
        end_date: Optional[Union[str, date, datetime]] = None,
        **kwargs
    ) -> DataCollectionResult:
        """
        收集债券日度行情数据
        
        Args:
            symbols: 债券代码列表，如果为None则收集所有债券
            start_date: 开始日期
            end_date: 结束日期
            **kwargs: 其他参数
            
        Returns:
            DataCollectionResult: 收集结果
        """
        pass
    
    @abc.abstractmethod
    def collect_index_daily(
        self,
        symbols: Optional[List[str]] = None,
        start_date: Optional[Union[str, date, datetime]] = None,
        end_date: Optional[Union[str, date, datetime]] = None,
        **kwargs
    ) -> DataCollectionResult:
        """
        收集指数日度行情数据
        
        Args:
            symbols: 指数代码列表，如果为None则收集所有指数
            start_date: 开始日期
            end_date: 结束日期
            **kwargs: 其他参数
            
        Returns:
            DataCollectionResult: 收集结果
        """
        pass
    
    def collect_data(
        self,
        data_type: DataType,
        **kwargs
    ) -> DataCollectionResult:
        """
        通用数据收集方法
        
        Args:
            data_type: 数据类型
            **kwargs: 收集参数
            
        Returns:
            DataCollectionResult: 收集结果
        """
        start_time = datetime.now()
        
        try:
            # 根据数据类型调用相应的方法
            if data_type == DataType.STOCK_DAILY:
                result = self.collect_stock_daily(**kwargs)
            elif data_type == DataType.STOCK_INFO:
                result = self.collect_stock_info(**kwargs)
            elif data_type == DataType.FUND_DAILY:
                result = self.collect_fund_daily(**kwargs)
            elif data_type == DataType.FUND_INFO:
                result = self.collect_fund_info(**kwargs)
            elif data_type == DataType.BOND_DAILY:
                result = self.collect_bond_daily(**kwargs)
            elif data_type == DataType.INDEX_DAILY:
                result = self.collect_index_daily(**kwargs)
            else:
                raise NotImplementedError(f"数据类型 {data_type} 暂未实现")
            
            result.start_time = start_time
            result.end_time = datetime.now()
            
            # 更新统计信息
            self.total_requests += 1
            if result.success:
                self.successful_requests += 1
            
            logger.info(
                f"数据收集完成: {data_type.value}, "
                f"收集 {result.records_collected} 条记录, "
                f"成功 {result.records_processed} 条, "
                f"耗时 {(result.end_time - result.start_time).total_seconds():.2f} 秒"
            )
            
            return result
            
        except Exception as e:
            end_time = datetime.now()
            logger.error(f"数据收集失败: {data_type.value}, 错误: {e}", exc_info=True)
            
            return DataCollectionResult(
                data_type=data_type,
                source_name=self.config.name,
                start_time=start_time,
                end_time=end_time,
                success=False,
                error_message=str(e),
                metadata={"error_type": type(e).__name__}
            )
    
    def get_statistics(self) -> Dict[str, Any]:
        """
        获取数据源统计信息
        
        Returns:
            Dict[str, Any]: 统计信息
        """
        return {
            "source_name": self.config.name,
            "status": self.status.value,
            "total_requests": self.total_requests,
            "successful_requests": self.successful_requests,
            "error_count": self.error_count,
            "success_rate": (
                self.successful_requests / self.total_requests 
                if self.total_requests > 0 else 0
            ),
            "last_connection_time": self.last_connection_time,
            "config": {
                "enabled": self.config.enabled,
                "priority": self.config.priority,
                "rate_limit": self.config.rate_limit,
            }
        }
    
    def _validate_connection(self) -> bool:
        """
        验证数据源连接
        
        Returns:
            bool: 连接是否有效
        """
        if self.status != DataSourceStatus.CONNECTED:
            logger.warning(f"数据源 {self.config.name} 未连接，尝试重新连接")
            return self.connect()
        return True
    
    def _handle_error(self, error: Exception) -> None:
        """
        处理错误
        
        Args:
            error: 错误异常
        """
        self.error_count += 1
        
        # 根据错误类型更新状态
        error_message = str(error).lower()
        if "rate limit" in error_message or "too many requests" in error_message:
            self.status = DataSourceStatus.RATE_LIMITED
        elif "connection" in error_message or "timeout" in error_message:
            self.status = DataSourceStatus.DISCONNECTED
        else:
            self.status = DataSourceStatus.ERROR
        
        logger.error(f"数据源 {self.config.name} 错误: {error}", exc_info=True)
    
    def _format_date(self, date_value: Optional[Union[str, date, datetime]]) -> Optional[str]:
        """
        格式化日期
        
        Args:
            date_value: 日期值
            
        Returns:
            Optional[str]: 格式化后的日期字符串
        """
        if date_value is None:
            return None
        
        if isinstance(date_value, str):
            return date_value
        elif isinstance(date_value, datetime):
            return date_value.strftime("%Y-%m-%d")
        elif isinstance(date_value, date):
            return date_value.strftime("%Y-%m-%d")
        else:
            raise ValueError(f"不支持的日期类型: {type(date_value)}")