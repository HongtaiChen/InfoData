"""
基础数据模型

定义所有数据模型的基类和通用混合类。
"""

from datetime import datetime
from typing import Any, Dict, Optional
from sqlalchemy import Column, Integer, String, DateTime, Text, Boolean, Float, JSON
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import declared_attr

# 声明性基类
Base = declarative_base()


class BaseMixin:
    """基础混合类，提供通用字段和方法"""
    
    @declared_attr
    def __tablename__(cls) -> str:
        """自动生成表名：类名转换为小写蛇形"""
        import re
        # 将驼峰命名转换为蛇形命名
        name = re.sub(r'(?<!^)(?=[A-Z])', '_', cls.__name__).lower()
        return name
    
    id = Column(Integer, primary_key=True, autoincrement=True, comment="主键ID")
    created_at = Column(DateTime, default=datetime.now, nullable=False, comment="创建时间")
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False, comment="更新时间")
    is_deleted = Column(Boolean, default=False, nullable=False, comment="是否已删除")
    
    def to_dict(self, exclude: Optional[list] = None) -> Dict[str, Any]:
        """
        将模型转换为字典
        
        Args:
            exclude: 要排除的字段列表
            
        Returns:
            Dict[str, Any]: 字典表示
        """
        exclude = exclude or []
        result = {}
        
        for column in self.__table__.columns:
            column_name = column.name
            if column_name in exclude:
                continue
            
            value = getattr(self, column_name)
            
            # 处理特殊类型
            if isinstance(value, datetime):
                value = value.isoformat()
            elif hasattr(value, 'to_dict'):
                value = value.to_dict()
            
            result[column_name] = value
        
        return result
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'BaseMixin':
        """
        从字典创建模型实例
        
        Args:
            data: 字典数据
            
        Returns:
            BaseMixin: 模型实例
        """
        # 过滤掉非列字段
        columns = {column.name for column in cls.__table__.columns}
        filtered_data = {k: v for k, v in data.items() if k in columns}
        
        return cls(**filtered_data)
    
    def update_from_dict(self, data: Dict[str, Any]) -> None:
        """
        从字典更新模型实例
        
        Args:
            data: 字典数据
        """
        columns = {column.name for column in self.__table__.columns}
        
        for key, value in data.items():
            if key in columns and hasattr(self, key):
                setattr(self, key, value)


class BaseModel(Base, BaseMixin):
    """基础模型类"""
    
    __abstract__ = True
    
    # 通用字段
    source = Column(String(50), nullable=False, comment="数据来源")
    source_id = Column(String(100), nullable=False, comment="数据源ID")
    version = Column(Integer, default=1, nullable=False, comment="数据版本")
    metadata = Column(JSON, default=dict, comment="元数据")
    
    # 数据质量字段
    data_quality_score = Column(Float, default=1.0, comment="数据质量评分")
    validation_status = Column(String(20), default="pending", comment="验证状态")
    validation_errors = Column(JSON, default=list, comment="验证错误")
    last_validated_at = Column(DateTime, nullable=True, comment="最后验证时间")
    
    def __repr__(self) -> str:
        """字符串表示"""
        return f"<{self.__class__.__name__}(id={self.id}, source={self.source}, source_id={self.source_id})>"
    
    @classmethod
    def get_by_source_id(cls, session, source: str, source_id: str) -> Optional['BaseModel']:
        """
        根据数据源ID获取记录
        
        Args:
            session: 数据库会话
            source: 数据来源
            source_id: 数据源ID
            
        Returns:
            Optional[BaseModel]: 找到的记录或None
        """
        return session.query(cls).filter(
            cls.source == source,
            cls.source_id == source_id,
            cls.is_deleted == False
        ).first()
    
    @classmethod
    def bulk_upsert(cls, session, records: list, conflict_columns: list = None) -> Dict[str, int]:
        """
        批量插入或更新记录
        
        Args:
            session: 数据库会话
            records: 记录列表
            conflict_columns: 冲突检测列，默认为['source', 'source_id']
            
        Returns:
            Dict[str, int]: 操作统计
        """
        from sqlalchemy.dialects.mysql import insert
        
        if not records:
            return {"inserted": 0, "updated": 0, "skipped": 0}
        
        conflict_columns = conflict_columns or ['source', 'source_id']
        
        # 构建插入语句
        stmt = insert(cls).values(records)
        
        # 构建更新语句
        update_dict = {}
        for column in cls.__table__.columns:
            if column.name not in conflict_columns and column.name not in ['id', 'created_at']:
                update_dict[column.name] = stmt.inserted[column.name]
        
        # 执行插入或更新
        stmt = stmt.on_duplicate_key_update(**update_dict)
        
        result = session.execute(stmt)
        
        return {
            "inserted": result.rowcount,
            "updated": 0,  # MySQL的on_duplicate_key_update不区分插入和更新
            "skipped": 0
        }


class TimeSeriesModel(BaseModel):
    """时间序列数据模型基类"""
    
    __abstract__ = True
    
    # 时间序列字段
    trade_date = Column(DateTime, nullable=False, index=True, comment="交易日期")
    timestamp = Column(DateTime, nullable=False, index=True, comment="时间戳")
    
    # 数据状态字段
    data_status = Column(String(20), default="active", comment="数据状态")
    is_adjusted = Column(Boolean, default=False, comment="是否已调整")
    adjustment_factor = Column(Float, default=1.0, comment="调整因子")
    
    @classmethod
    def get_latest_by_source_id(cls, session, source: str, source_id: str) -> Optional['TimeSeriesModel']:
        """
        获取指定数据源的最新记录
        
        Args:
            session: 数据库会话
            source: 数据来源
            source_id: 数据源ID
            
        Returns:
            Optional[TimeSeriesModel]: 最新记录或None
        """
        return session.query(cls).filter(
            cls.source == source,
            cls.source_id == source_id,
            cls.is_deleted == False
        ).order_by(cls.timestamp.desc()).first()
    
    @classmethod
    def get_by_date_range(cls, session, source: str, source_id: str, 
                         start_date: datetime, end_date: datetime) -> list:
        """
        获取指定日期范围内的记录
        
        Args:
            session: 数据库会话
            source: 数据来源
            source_id: 数据源ID
            start_date: 开始日期
            end_date: 结束日期
            
        Returns:
            list: 记录列表
        """
        return session.query(cls).filter(
            cls.source == source,
            cls.source_id == source_id,
            cls.trade_date >= start_date,
            cls.trade_date <= end_date,
            cls.is_deleted == False
        ).order_by(cls.trade_date).all()


class FinancialInstrumentModel(BaseModel):
    """金融工具模型基类"""
    
    __abstract__ = True
    
    # 基础信息字段
    symbol = Column(String(50), nullable=False, index=True, comment="代码")
    name = Column(String(200), nullable=False, comment="名称")
    full_name = Column(String(500), nullable=True, comment="全称")
    
    # 分类字段
    market = Column(String(50), nullable=True, comment="市场")
    exchange = Column(String(50), nullable=True, comment="交易所")
    sector = Column(String(100), nullable=True, comment="行业板块")
    industry = Column(String(200), nullable=True, comment="行业")
    
    # 状态字段
    listing_date = Column(DateTime, nullable=True, comment="上市日期")
    delisting_date = Column(DateTime, nullable=True, comment="退市日期")
    is_active = Column(Boolean, default=True, comment="是否活跃")
    
    @classmethod
    def get_by_symbol(cls, session, symbol: str, market: str = None) -> Optional['FinancialInstrumentModel']:
        """
        根据代码获取记录
        
        Args:
            session: 数据库会话
            symbol: 代码
            market: 市场（可选）
            
        Returns:
            Optional[FinancialInstrumentModel]: 找到的记录或None
        """
        query = session.query(cls).filter(
            cls.symbol == symbol,
            cls.is_deleted == False
        )
        
        if market:
            query = query.filter(cls.market == market)
        
        return query.first()
    
    @classmethod
    def search_by_name(cls, session, name: str, limit: int = 10) -> list:
        """
        根据名称搜索记录
        
        Args:
            session: 数据库会话
            name: 名称关键词
            limit: 返回数量限制
            
        Returns:
            list: 记录列表
        """
        return session.query(cls).filter(
            cls.name.like(f"%{name}%"),
            cls.is_deleted == False
        ).limit(limit).all()