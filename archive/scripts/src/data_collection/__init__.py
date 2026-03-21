"""
数据采集层 - 统一AKShare和Tushare接口

提供统一的金融数据采集接口，包含错误处理、重试机制和速率限制。
遵循项目宪法：代码质量优先、测试驱动开发、数据完整性。
"""

__version__ = "0.1.0"
__all__ = ["AKShareClient", "TushareClient", "DataCollectionError", "get_client"]