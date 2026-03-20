"""
数据存储管理器

提供统一的数据存储接口，整合数据库管理器、表管理器和数据模型。
"""

import logging
from typing import Dict, Any, Optional, List, Type
from .database import MySQLDatabaseManager
from .models.manager import TableManager
from .models.base import BaseModel


class DataStorageError(Exception):
    """数据存储错误"""
    pass


class DataStorageManager:
    """数据存储管理器
    
    提供统一的数据存储接口。
    """
    
    def __init__(
        self,
        host: str = "localhost",
        port: int = 3306,
        user: str = "",
        password: str = "",
        database: str = "",
        charset: str = "utf8mb4",
        pool_size: int = 10,
        logger: Optional[logging.Logger] = None
    ):
        """初始化数据存储管理器
        
        Args:
            host: 数据库主机
            port: 数据库端口
            user: 用户名
            password: 密码
            database: 数据库名
            charset: 字符集
            pool_size: 连接池大小
            logger: 日志记录器
        """
        self.logger = logger or logging.getLogger(__name__)
        
        # 初始化数据库管理器
        self.db_manager = MySQLDatabaseManager(
            host=host,
            port=port,
            user=user,
            password=password,
            database=database,
            charset=charset,
            pool_size=pool_size,
            logger=self.logger.getChild("db")
        )
        
        # 初始化表管理器
        self.table_manager = TableManager(
            db_manager=self.db_manager,
            logger=self.logger.getChild("table")
        )
        
        self.logger.info(f"初始化数据存储管理器: {host}:{port}/{database}")
    
    def register_all_models(self):
        """注册所有预定义的数据模型"""
        try:
            from .models.stock import (
                StockInfo, StockDailyInfo, StockDividendInfo, InstitutionalTradingInfo
            )
            from .models.financial import (
                IndexInfo, FundInfo, BondInfo, IndexDailyInfo
            )
            
            models = [
                StockInfo,
                StockDailyInfo,
                StockDividendInfo,
                InstitutionalTradingInfo,
                IndexInfo,
                FundInfo,
                BondInfo,
                IndexDailyInfo
            ]
            
            self.table_manager.register_models(models)
            self.logger.info(f"注册了 {len(models)} 个数据模型")
            
        except ImportError as e:
            error_msg = f"导入数据模型失败: {e}"
            self.logger.error(error_msg)
            raise DataStorageError(error_msg) from e
    
    def setup_database(self, create_tables: bool = True) -> Dict[str, bool]:
        """设置数据库
        
        Args:
            create_tables: 是否创建表
            
        Returns:
            创建结果字典
        """
        results = {}
        
        try:
            # 注册所有模型
            self.register_all_models()
            
            # 创建所有表
            if create_tables:
                results = self.table_manager.create_all_tables(if_not_exists=True)
                self.logger.info(f"表创建完成: {sum(results.values())}/{len(results)} 成功")
            
            return results
            
        except Exception as e:
            error_msg = f"设置数据库失败: {e}"
            self.logger.error(error_msg)
            raise DataStorageError(error_msg) from e
    
    def get_table_status(self) -> Dict[str, Dict[str, Any]]:
        """获取所有表状态
        
        Returns:
            表状态字典
        """
        return self.table_manager.get_all_table_status()
    
    def insert_data(
        self,
        model_instance: BaseModel,
        on_duplicate_key_update: bool = True
    ) -> bool:
        """插入数据
        
        Args:
            model_instance: 模型实例
            on_duplicate_key_update: 如果主键冲突是否更新
            
        Returns:
            插入是否成功
        """
        return self.table_manager.insert_model_data(
            model_instance=model_instance,
            on_duplicate_key_update=on_duplicate_key_update
        )
    
    def bulk_insert_data(
        self,
        model_instances: List[BaseModel],
        on_duplicate_key_update: bool = True,
        chunk_size: int = 1000
    ) -> int:
        """批量插入数据
        
        Args:
            model_instances: 模型实例列表
            on_duplicate_key_update: 如果主键冲突是否更新
            chunk_size: 分块大小
            
        Returns:
            插入的行数
        """
        return self.table_manager.bulk_insert_model_data(
            model_instances=model_instances,
            on_duplicate_key_update=on_duplicate_key_update,
            chunk_size=chunk_size
        )
    
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
        return self.db_manager.insert_dataframe(
            table_name=table_name,
            df=df,
            if_exists=if_exists,
            chunk_size=chunk_size
        )
    
    def execute_query(
        self,
        query: str,
        params: Optional[Any] = None,
        fetch_all: bool = True
    ) -> Any:
        """执行查询
        
        Args:
            query: SQL查询语句
            params: 查询参数
            fetch_all: 是否获取所有结果
            
        Returns:
            查询结果
        """
        return self.db_manager.execute_query(
            query=query,
            params=params,
            fetch_all=fetch_all
        )
    
    def table_exists(self, table_name: str) -> bool:
        """检查表是否存在
        
        Args:
            table_name: 表名
            
        Returns:
            表是否存在
        """
        return self.db_manager.table_exists(table_name)
    
    def get_table_info(self, table_name: str) -> Dict[str, Any]:
        """获取表信息
        
        Args:
            table_name: 表名
            
        Returns:
            表信息字典
        """
        return self.db_manager.get_table_info(table_name)
    
    def close(self):
        """关闭所有连接"""
        self.db_manager.close_all_connections()
        self.logger.info("数据存储管理器已关闭")


# 全局数据存储管理器实例
_storage_manager: Optional[DataStorageManager] = None


def get_storage_manager(
    host: str = "localhost",
    port: int = 3306,
    user: str = "",
    password: str = "",
    database: str = "",
    charset: str = "utf8mb4",
    pool_size: int = 10
) -> DataStorageManager:
    """获取全局数据存储管理器
    
    Args:
        host: 数据库主机
        port: 数据库端口
        user: 用户名
        password: 密码
        database: 数据库名
        charset: 字符集
        pool_size: 连接池大小
        
    Returns:
        数据存储管理器实例
    """
    global _storage_manager
    
    if _storage_manager is None:
        _storage_manager = DataStorageManager(
            host=host,
            port=port,
            user=user,
            password=password,
            database=database,
            charset=charset,
            pool_size=pool_size
        )
    
    return _storage_manager


def setup_data_storage(
    host: str = "localhost",
    port: int = 3306,
    user: str = "",
    password: str = "",
    database: str = "",
    create_tables: bool = True
) -> DataStorageManager:
    """设置数据存储系统（便捷函数）
    
    Args:
        host: 数据库主机
        port: 数据库端口
        user: 用户名
        password: 密码
        database: 数据库名
        create_tables: 是否创建表
        
    Returns:
        数据存储管理器实例
    """
    storage = get_storage_manager(
        host=host,
        port=port,
        user=user,
        password=password,
        database=database
    )
    
    storage.setup_database(create_tables=create_tables)
    return storage