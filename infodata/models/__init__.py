"""
数据模型模块

定义金融数据的数据模型和数据库表结构。
"""

from .base import BaseModel, BaseMixin
from .stock import (
    StockDaily, StockInfo, StockIndustry, StockConcept,
    StockHolder, StockDividend, StockSplit
)
from .fund import FundDaily, FundInfo, FundNetValue, FundManager
from .bond import BondDaily, BondInfo, BondYield, BondRating
from .index import IndexDaily, IndexInfo, IndexComponent
from .task import TaskExecution, TaskMetric
from .quality import DataQualityMetric, DataValidationRule

__all__ = [
    # 基础模型
    "BaseModel",
    "BaseMixin",
    
    # 股票模型
    "StockDaily",
    "StockInfo",
    "StockIndustry",
    "StockConcept",
    "StockHolder",
    "StockDividend",
    "StockSplit",
    
    # 基金模型
    "FundDaily",
    "FundInfo",
    "FundNetValue",
    "FundManager",
    
    # 债券模型
    "BondDaily",
    "BondInfo",
    "BondYield",
    "BondRating",
    
    # 指数模型
    "IndexDaily",
    "IndexInfo",
    "IndexComponent",
    
    # 任务模型
    "TaskExecution",
    "TaskMetric",
    
    # 数据质量模型
    "DataQualityMetric",
    "DataValidationRule",
]

# 版本信息
__version__ = "0.1.0"