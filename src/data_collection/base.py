"""
数据采集基础模块

提供基础数据采集功能：错误处理、重试机制、速率限制和日志记录。
遵循金融数据处理的最佳实践。
"""

import logging
import time
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional, Union
from dataclasses import dataclass
from datetime import datetime, timedelta
import pandas as pd


@dataclass
class RateLimitConfig:
    """速率限制配置"""
    max_requests_per_minute: int = 60  # 每分钟最大请求数
    max_requests_per_hour: int = 1000  # 每小时最大请求数
    delay_between_requests: float = 0.1  # 请求间延迟（秒）


class DataCollectionError(Exception):
    """数据采集错误基类"""
    pass


class RateLimitExceededError(DataCollectionError):
    """速率限制超出错误"""
    pass


class APINetworkError(DataCollectionError):
    """API网络错误"""
    pass


class DataValidationError(DataCollectionError):
    """数据验证错误"""
    pass


class BaseDataClient(ABC):
    """基础数据客户端抽象类
    
    所有数据采集客户端的基类，提供统一的错误处理、重试机制和速率限制。
    """
    
    def __init__(
        self,
        name: str,
        rate_limit_config: Optional[RateLimitConfig] = None,
        max_retries: int = 3,
        retry_delay: float = 1.0,
        logger: Optional[logging.Logger] = None
    ):
        """初始化基础数据客户端
        
        Args:
            name: 客户端名称
            rate_limit_config: 速率限制配置
            max_retries: 最大重试次数
            retry_delay: 重试延迟（秒）
            logger: 日志记录器
        """
        self.name = name
        self.rate_limit_config = rate_limit_config or RateLimitConfig()
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.logger = logger or logging.getLogger(__name__)
        
        # 速率限制状态
        self.request_timestamps = []
        self.hourly_request_count = 0
        self.last_hour_reset = datetime.now()
        
        # 性能统计
        self.total_requests = 0
        self.failed_requests = 0
        self.total_response_time = 0.0
    
    def _check_rate_limit(self) -> None:
        """检查速率限制"""
        now = datetime.now()
        
        # 重置小时计数器（如果需要）
        if now - self.last_hour_reset > timedelta(hours=1):
            self.hourly_request_count = 0
            self.last_hour_reset = now
        
        # 检查小时限制
        if self.hourly_request_count >= self.rate_limit_config.max_requests_per_hour:
            raise RateLimitExceededError(
                f"每小时请求数超出限制: {self.hourly_request_count}/"
                f"{self.rate_limit_config.max_requests_per_hour}"
            )
        
        # 清理旧的时间戳（超过1分钟）
        one_minute_ago = now - timedelta(minutes=1)
        self.request_timestamps = [
            ts for ts in self.request_timestamps if ts > one_minute_ago
        ]
        
        # 检查分钟限制
        if len(self.request_timestamps) >= self.rate_limit_config.max_requests_per_minute:
            oldest_timestamp = min(self.request_timestamps)
            wait_time = 60 - (now - oldest_timestamp).total_seconds()
            raise RateLimitExceededError(
                f"每分钟请求数超出限制，请等待 {wait_time:.1f} 秒"
            )
        
        # 检查请求间延迟
        if self.request_timestamps:
            last_request = max(self.request_timestamps)
            time_since_last = (now - last_request).total_seconds()
            if time_since_last < self.rate_limit_config.delay_between_requests:
                time.sleep(self.rate_limit_config.delay_between_requests - time_since_last)
    
    def _update_rate_limit(self) -> None:
        """更新速率限制状态"""
        now = datetime.now()
        self.request_timestamps.append(now)
        self.hourly_request_count += 1
        
        # 保持时间戳列表大小合理
        if len(self.request_timestamps) > self.rate_limit_config.max_requests_per_minute * 2:
            self.request_timestamps = self.request_timestamps[-self.rate_limit_config.max_requests_per_minute:]
    
    def _validate_dataframe(self, df: pd.DataFrame, expected_columns: Optional[list] = None) -> None:
        """验证返回的DataFrame
        
        Args:
            df: 要验证的DataFrame
            expected_columns: 期望的列名列表
            
        Raises:
            DataValidationError: 如果数据验证失败
        """
        if df is None:
            raise DataValidationError("返回的数据为空")
        
        if not isinstance(df, pd.DataFrame):
            raise DataValidationError(f"期望DataFrame类型，但得到 {type(df)}")
        
        if df.empty:
            self.logger.warning("返回的DataFrame为空")
            return
        
        if expected_columns:
            missing_columns = [col for col in expected_columns if col not in df.columns]
            if missing_columns:
                raise DataValidationError(f"缺少期望的列: {missing_columns}")
    
    def execute_with_retry(self, func, *args, **kwargs) -> Any:
        """带重试机制的执行函数
        
        Args:
            func: 要执行的函数
            *args: 函数参数
            **kwargs: 函数关键字参数
            
        Returns:
            函数执行结果
            
        Raises:
            DataCollectionError: 如果所有重试都失败
        """
        last_error = None
        
        for attempt in range(self.max_retries + 1):
            try:
                # 检查速率限制
                self._check_rate_limit()
                
                # 执行函数并计时
                start_time = time.time()
                result = func(*args, **kwargs)
                response_time = time.time() - start_time
                
                # 更新统计信息
                self.total_requests += 1
                self.total_response_time += response_time
                self._update_rate_limit()
                
                self.logger.debug(
                    f"{self.name} 请求成功: 尝试 {attempt + 1}, "
                    f"响应时间 {response_time:.3f}秒"
                )
                
                return result
                
            except (RateLimitExceededError, DataValidationError) as e:
                # 这些错误不重试
                self.failed_requests += 1
                raise
            except Exception as e:
                self.failed_requests += 1
                last_error = e
                
                if attempt < self.max_retries:
                    wait_time = self.retry_delay * (2 ** attempt)  # 指数退避
                    self.logger.warning(
                        f"{self.name} 请求失败 (尝试 {attempt + 1}/{self.max_retries + 1}): "
                        f"{str(e)[:100]}... 等待 {wait_time:.1f}秒后重试"
                    )
                    time.sleep(wait_time)
                else:
                    self.logger.error(
                        f"{self.name} 请求失败，已达到最大重试次数: {str(e)}"
                    )
        
        raise DataCollectionError(
            f"所有重试都失败，最后错误: {str(last_error)}"
        ) from last_error
    
    def get_stats(self) -> Dict[str, Any]:
        """获取客户端统计信息
        
        Returns:
            包含统计信息的字典
        """
        avg_response_time = (
            self.total_response_time / self.total_requests
            if self.total_requests > 0 else 0
        )
        
        return {
            "name": self.name,
            "total_requests": self.total_requests,
            "failed_requests": self.failed_requests,
            "success_rate": (
                (self.total_requests - self.failed_requests) / self.total_requests * 100
                if self.total_requests > 0 else 100
            ),
            "avg_response_time_seconds": avg_response_time,
            "hourly_request_count": self.hourly_request_count,
            "active_minute_requests": len(self.request_timestamps),
        }
    
    @abstractmethod
    def test_connection(self) -> bool:
        """测试API连接
        
        Returns:
            连接是否成功
        """
        pass