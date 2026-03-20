"""
数据存储基础模块

定义数据存储抽象类和异常。
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Union, Tuple


class DatabaseError(Exception):
    """数据库错误基类"""
    pass


class ConnectionError(DatabaseError):
    """连接错误"""
    pass


class QueryError(DatabaseError):
    """查询错误"""
    pass


class TransactionError(DatabaseError):
    """事务错误"""
    pass


class BaseDatabaseManager(ABC):
    """基础数据库管理器抽象类"""
    
    @abstractmethod
    def execute_query(
        self,
        query: str,
        params: Optional[Union[Tuple, Dict, List]] = None,
        fetch_all: bool = True
    ) -> Union[List[Dict], int]:
        """执行查询
        
        Args:
            query: SQL查询语句
            params: 查询参数
            fetch_all: 是否获取所有结果
            
        Returns:
            查询结果列表或受影响的行数
        """
        pass
    
    @abstractmethod
    def execute_many(
        self,
        query: str,
        params_list: List[Union[Tuple, Dict]]
    ) -> int:
        """批量执行操作
        
        Args:
            query: SQL语句
            params_list: 参数列表
            
        Returns:
            受影响的总行数
        """
        pass
    
    @abstractmethod
    def insert_dataframe(
        self,
        table_name: str,
        df,
        if_exists: str = "append",
        chunk_size: int = 1000
    ) -> int:
        """插入DataFrame到数据库
        
        Args:
            table_name: 表名
            df: pandas DataFrame
            if_exists: 如果表存在时的操作
            chunk_size: 分块大小
            
        Returns:
            插入的行数
        """
        pass
    
    @abstractmethod
    def create_table(
        self,
        table_name: str,
        columns_def: Dict[str, str],
        primary_key: Optional[List[str]] = None,
        indexes: Optional[List[Dict]] = None,
        if_not_exists: bool = True
    ) -> bool:
        """创建表
        
        Args:
            table_name: 表名
            columns_def: 列定义字典
            primary_key: 主键列列表
            indexes: 索引定义列表
            if_not_exists: 如果表不存在则创建
            
        Returns:
            是否成功创建
        """
        pass
    
    @abstractmethod
    def table_exists(self, table_name: str) -> bool:
        """检查表是否存在
        
        Args:
            table_name: 表名
            
        Returns:
            表是否存在
        """
        pass
    
    @abstractmethod
    def get_table_info(self, table_name: str) -> Dict:
        """获取表信息
        
        Args:
            table_name: 表名
            
        Returns:
            表信息字典
        """
        pass