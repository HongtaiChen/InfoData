"""
数据模型基类

提供数据验证、序列化和数据库映射的基础功能。
"""

import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Type, TypeVar, Generic
from datetime import datetime, date
import pandas as pd

T = TypeVar('T', bound='BaseModel')


class ModelError(Exception):
    """模型错误基类"""
    pass


class ValidationError(ModelError):
    """数据验证错误"""
    pass


class BaseModel(ABC):
    """数据模型基类
    
    提供数据验证、序列化和数据库映射的基础功能。
    """
    
    # 表名（子类必须覆盖）
    TABLE_NAME: str = ""
    
    # 列定义：{列名: 数据类型}
    # 数据类型格式：MySQL数据类型字符串，如 "VARCHAR(255)", "INT", "DECIMAL(10,2)"
    COLUMNS: Dict[str, str] = {}
    
    # 主键列
    PRIMARY_KEY: List[str] = []
    
    # 索引定义
    INDEXES: List[Dict] = []
    
    # 必需列
    REQUIRED_COLUMNS: List[str] = []
    
    def __init__(self, **kwargs):
        """初始化模型实例
        
        Args:
            **kwargs: 模型属性值
        """
        self._data = {}
        self._errors = {}
        
        # 设置属性值
        for key, value in kwargs.items():
            if key in self.COLUMNS:
                setattr(self, key, value)
    
    def __setattr__(self, name: str, value: Any):
        """设置属性值
        
        Args:
            name: 属性名
            value: 属性值
        """
        # 如果是数据列，存储到 _data 字典
        if name in self.COLUMNS:
            self._data[name] = value
        else:
            super().__setattr__(name, value)
    
    def __getattr__(self, name: str) -> Any:
        """获取属性值
        
        Args:
            name: 属性名
            
        Returns:
            属性值
        """
        if name in self.COLUMNS:
            return self._data.get(name)
        raise AttributeError(f"'{self.__class__.__name__}' 对象没有属性 '{name}'")
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典
        
        Returns:
            包含所有数据列的字典
        """
        return self._data.copy()
    
    def to_series(self) -> pd.Series:
        """转换为pandas Series
        
        Returns:
            pandas Series
        """
        return pd.Series(self._data)
    
    @classmethod
    def from_dict(cls: Type[T], data: Dict[str, Any]) -> T:
        """从字典创建模型实例
        
        Args:
            data: 数据字典
            
        Returns:
            模型实例
        """
        return cls(**data)
    
    @classmethod
    def from_series(cls: Type[T], series: pd.Series) -> T:
        """从pandas Series创建模型实例
        
        Args:
            series: pandas Series
            
        Returns:
            模型实例
        """
        return cls(**series.to_dict())
    
    @classmethod
    def from_dataframe(cls: Type[T], df: pd.DataFrame) -> List[T]:
        """从DataFrame创建模型实例列表
        
        Args:
            df: pandas DataFrame
            
        Returns:
            模型实例列表
        """
        instances = []
        for _, row in df.iterrows():
            instance = cls.from_series(row)
            instances.append(instance)
        return instances
    
    def validate(self) -> bool:
        """验证模型数据
        
        Returns:
            数据是否有效
            
        Raises:
            ValidationError: 数据验证失败
        """
        self._errors.clear()
        
        # 验证必需列
        for column in self.REQUIRED_COLUMNS:
            if column not in self._data or self._data[column] is None:
                self._errors[column] = "必需列不能为空"
        
        # 验证数据类型
        for column, value in self._data.items():
            if value is not None:
                try:
                    self._validate_column(column, value)
                except ValueError as e:
                    self._errors[column] = str(e)
        
        # 自定义验证（子类可实现）
        self._custom_validate()
        
        if self._errors:
            error_msg = ", ".join([f"{k}: {v}" for k, v in self._errors.items()])
            raise ValidationError(f"数据验证失败: {error_msg}")
        
        return True
    
    def _validate_column(self, column: str, value: Any):
        """验证列数据
        
        Args:
            column: 列名
            value: 列值
            
        Raises:
            ValueError: 数据验证失败
        """
        column_type = self.COLUMNS.get(column, "")
        
        if not column_type:
            return
        
        # 基础类型验证
        if "INT" in column_type.upper():
            try:
                int(value)
            except (ValueError, TypeError):
                raise ValueError(f"列 '{column}' 应为整数类型")
        
        elif "DECIMAL" in column_type.upper() or "FLOAT" in column_type.upper() or "DOUBLE" in column_type.upper():
            try:
                float(value)
            except (ValueError, TypeError):
                raise ValueError(f"列 '{column}' 应为数值类型")
        
        elif "DATE" in column_type.upper() or "DATETIME" in column_type.upper():
            if not isinstance(value, (str, datetime, date)):
                raise ValueError(f"列 '{column}' 应为日期类型")
        
        elif "VARCHAR" in column_type.upper() or "TEXT" in column_type.upper():
            if not isinstance(value, str):
                raise ValueError(f"列 '{column}' 应为字符串类型")
        
        # 长度验证（简单版本）
        if "VARCHAR" in column_type.upper():
            # 提取长度，如 VARCHAR(255) -> 255
            import re
            match = re.search(r'VARCHAR\((\d+)\)', column_type.upper())
            if match:
                max_length = int(match.group(1))
                if isinstance(value, str) and len(value) > max_length:
                    raise ValueError(f"列 '{column}' 长度不能超过 {max_length} 字符")
    
    def _custom_validate(self):
        """自定义验证（子类可覆盖）"""
        pass
    
    def get_errors(self) -> Dict[str, str]:
        """获取验证错误
        
        Returns:
            错误字典
        """
        return self._errors.copy()
    
    def is_valid(self) -> bool:
        """检查数据是否有效
        
        Returns:
            数据是否有效
        """
        try:
            return self.validate()
        except ValidationError:
            return False
    
    @classmethod
    def get_table_name(cls) -> str:
        """获取表名
        
        Returns:
            表名
        """
        if not cls.TABLE_NAME:
            raise ModelError(f"模型 {cls.__name__} 未定义 TABLE_NAME")
        return cls.TABLE_NAME
    
    @classmethod
    def get_columns_def(cls) -> Dict[str, str]:
        """获取列定义
        
        Returns:
            列定义字典
        """
        if not cls.COLUMNS:
            raise ModelError(f"模型 {cls.__name__} 未定义 COLUMNS")
        return cls.COLUMNS.copy()
    
    @classmethod
    def get_primary_key(cls) -> List[str]:
        """获取主键
        
        Returns:
            主键列列表
        """
        return cls.PRIMARY_KEY.copy()
    
    @classmethod
    def get_indexes(cls) -> List[Dict]:
        """获取索引定义
        
        Returns:
            索引定义列表
        """
        return cls.INDEXES.copy()
    
    @classmethod
    def get_column_names(cls) -> List[str]:
        """获取列名列表
        
        Returns:
            列名列表
        """
        return list(cls.COLUMNS.keys())
    
    def __repr__(self) -> str:
        """模型表示
        
        Returns:
            模型表示字符串
        """
        class_name = self.__class__.__name__
        data_str = ", ".join([f"{k}={repr(v)}" for k, v in self._data.items()])
        return f"{class_name}({data_str})"
    
    def __str__(self) -> str:
        """模型字符串表示
        
        Returns:
            模型字符串
        """
        return self.__repr__()