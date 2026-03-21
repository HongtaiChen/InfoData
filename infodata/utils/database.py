"""
数据库工具类

提供数据库连接池、事务管理和操作工具。
"""

import time
from typing import Any, Dict, List, Optional, Union
from contextlib import contextmanager
from sqlalchemy import create_engine, Engine, event
from sqlalchemy.orm import sessionmaker, Session, scoped_session
from sqlalchemy.pool import QueuePool
from sqlalchemy.exc import SQLAlchemyError, OperationalError
from ..config.schemas import DatabaseConfig
from .logging import get_logger
from .exceptions import DatabaseError, ConnectionError, TransactionError

logger = get_logger(__name__)


class DatabaseManager:
    """数据库管理器"""
    
    def __init__(self, config: DatabaseConfig):
        """
        初始化数据库管理器
        
        Args:
            config: 数据库配置
        """
        self.config = config
        self.engine: Optional[Engine] = None
        self.session_factory: Optional[sessionmaker] = None
        self.scoped_session_factory: Optional[scoped_session] = None
        self._initialized = False
        
    def initialize(self) -> None:
        """初始化数据库连接"""
        if self._initialized:
            return
        
        try:
            # 选择数据库类型
            if self.config.sqlite:
                self._initialize_sqlite()
            else:
                self._initialize_mysql()
            
            self._initialized = True
            logger.info("数据库连接初始化完成")
            
        except Exception as e:
            logger.error(f"数据库连接初始化失败: {e}")
            raise ConnectionError(f"数据库连接初始化失败: {e}")
    
    def _initialize_mysql(self) -> None:
        """初始化MySQL连接"""
        mysql_config = self.config.mysql
        
        # 构建连接URL
        connection_url = (
            f"mysql+pymysql://{mysql_config['user']}:{mysql_config['password']}"
            f"@{mysql_config['host']}:{mysql_config['port']}"
            f"/{mysql_config['database']}"
            f"?charset=utf8mb4"
        )
        
        # 创建引擎
        self.engine = create_engine(
            connection_url,
            poolclass=QueuePool,
            pool_size=mysql_config.get('pool_size', 10),
            max_overflow=mysql_config.get('max_overflow', 20),
            pool_timeout=mysql_config.get('pool_timeout', 30),
            pool_recycle=mysql_config.get('pool_recycle', 3600),
            echo=mysql_config.get('echo', False),
            echo_pool=mysql_config.get('echo_pool', False),
            future=True,
        )
        
        # 添加连接事件监听
        self._setup_engine_events()
        
        # 创建会话工厂
        self.session_factory = sessionmaker(
            bind=self.engine,
            autocommit=False,
            autoflush=False,
            expire_on_commit=False,
        )
        
        # 创建线程安全的会话工厂
        self.scoped_session_factory = scoped_session(self.session_factory)
    
    def _initialize_sqlite(self) -> None:
        """初始化SQLite连接"""
        sqlite_config = self.config.sqlite
        
        # 构建连接URL
        connection_url = f"sqlite:///{sqlite_config['database']}"
        
        # 创建引擎
        self.engine = create_engine(
            connection_url,
            echo=sqlite_config.get('echo', False),
            future=True,
        )
        
        # 创建会话工厂
        self.session_factory = sessionmaker(
            bind=self.engine,
            autocommit=False,
            autoflush=False,
            expire_on_commit=False,
        )
        
        # 创建线程安全的会话工厂
        self.scoped_session_factory = scoped_session(self.session_factory)
    
    def _setup_engine_events(self) -> None:
        """设置引擎事件监听"""
        if not self.engine:
            return
        
        @event.listens_for(self.engine, "connect")
        def set_sql_mode(dbapi_connection, connection_record):
            """设置SQL模式"""
            cursor = dbapi_connection.cursor()
            cursor.execute("SET SESSION sql_mode='STRICT_TRANS_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION'")
            cursor.close()
        
        @event.listens_for(self.engine, "checkout")
        def ping_connection(dbapi_connection, connection_record, connection_proxy):
            """检查连接是否有效"""
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute("SELECT 1")
            except:
                # 连接失效，抛出异常让连接池重新创建连接
                raise OperationalError("数据库连接失效", None, None)
            finally:
                cursor.close()
    
    def get_session(self) -> Session:
        """
        获取数据库会话
        
        Returns:
            Session: 数据库会话
            
        Raises:
            ConnectionError: 数据库未初始化或连接失败
        """
        if not self._initialized or not self.session_factory:
            raise ConnectionError("数据库未初始化")
        
        return self.session_factory()
    
    def get_scoped_session(self) -> Session:
        """
        获取线程安全的数据库会话
        
        Returns:
            Session: 线程安全的数据库会话
            
        Raises:
            ConnectionError: 数据库未初始化或连接失败
        """
        if not self._initialized or not self.scoped_session_factory:
            raise ConnectionError("数据库未初始化")
        
        return self.scoped_session_factory()
    
    def close(self) -> None:
        """关闭数据库连接"""
        if self.engine:
            self.engine.dispose()
            self.engine = None
        
        if self.scoped_session_factory:
            self.scoped_session_factory.remove()
            self.scoped_session_factory = None
        
        self.session_factory = None
        self._initialized = False
        logger.info("数据库连接已关闭")
    
    def test_connection(self) -> bool:
        """
        测试数据库连接
        
        Returns:
            bool: 连接是否成功
        """
        try:
            if not self._initialized:
                self.initialize()
            
            with self.get_session() as session:
                session.execute("SELECT 1")
            return True
            
        except Exception as e:
            logger.error(f"数据库连接测试失败: {e}")
            return False
    
    def get_connection_info(self) -> Dict[str, Any]:
        """
        获取数据库连接信息
        
        Returns:
            Dict[str, Any]: 连接信息
        """
        if not self.engine:
            return {"status": "not_initialized"}
        
        pool = self.engine.pool
        return {
            "status": "connected",
            "dialect": str(self.engine.dialect),
            "pool_size": pool.size() if hasattr(pool, 'size') else None,
            "checked_out": pool.checkedout() if hasattr(pool, 'checkedout') else None,
            "overflow": pool.overflow() if hasattr(pool, 'overflow') else None,
        }


@contextmanager
def session_scope(db_manager: DatabaseManager, auto_commit: bool = True):
    """
    数据库会话上下文管理器
    
    Args:
        db_manager: 数据库管理器
        auto_commit: 是否自动提交
        
    Yields:
        Session: 数据库会话
        
    Raises:
        TransactionError: 事务错误
    """
    session = db_manager.get_session()
    try:
        yield session
        if auto_commit:
            session.commit()
    except Exception as e:
        session.rollback()
        logger.error(f"数据库操作失败: {e}", exc_info=True)
        raise TransactionError(f"数据库操作失败: {e}")
    finally:
        session.close()


@contextmanager
def scoped_session_scope(db_manager: DatabaseManager, auto_commit: bool = True):
    """
    线程安全的数据库会话上下文管理器
    
    Args:
        db_manager: 数据库管理器
        auto_commit: 是否自动提交
        
    Yields:
        Session: 数据库会话
        
    Raises:
        TransactionError: 事务错误
    """
    session = db_manager.get_scoped_session()
    try:
        yield session
        if auto_commit:
            session.commit()
    except Exception as e:
        session.rollback()
        logger.error(f"数据库操作失败: {e}", exc_info=True)
        raise TransactionError(f"数据库操作失败: {e}")
    finally:
        session.remove()


class DatabaseOperations:
    """数据库操作工具类"""
    
    def __init__(self, db_manager: DatabaseManager):
        """
        初始化数据库操作工具
        
        Args:
            db_manager: 数据库管理器
        """
        self.db_manager = db_manager
    
    def execute_query(self, query: str, params: Optional[Dict] = None) -> List[Dict]:
        """
        执行查询语句
        
        Args:
            query: SQL查询语句
            params: 查询参数
            
        Returns:
            List[Dict]: 查询结果
            
        Raises:
            DatabaseError: 数据库操作错误
        """
        try:
            with session_scope(self.db_manager) as session:
                result = session.execute(query, params or {})
                return [dict(row._mapping) for row in result]
        except Exception as e:
            logger.error(f"查询执行失败: {e}")
            raise DatabaseError(f"查询执行失败: {e}")
    
    def execute_update(self, query: str, params: Optional[Dict] = None) -> int:
        """
        执行更新语句
        
        Args:
            query: SQL更新语句
            params: 更新参数
            
        Returns:
            int: 影响的行数
            
        Raises:
            DatabaseError: 数据库操作错误
        """
        try:
            with session_scope(self.db_manager) as session:
                result = session.execute(query, params or {})
                return result.rowcount
        except Exception as e:
            logger.error(f"更新执行失败: {e}")
            raise DatabaseError(f"更新执行失败: {e}")
    
    def batch_insert(
        self,
        table: str,
        data: List[Dict],
        batch_size: int = 1000,
        ignore_duplicates: bool = False
    ) -> int:
        """
        批量插入数据
        
        Args:
            table: 表名
            data: 数据列表
            batch_size: 批量大小
            ignore_duplicates: 是否忽略重复数据
            
        Returns:
            int: 插入的行数
            
        Raises:
            DatabaseError: 数据库操作错误
        """
        if not data:
            return 0
        
        total_inserted = 0
        start_time = time.time()
        
        try:
            for i in range(0, len(data), batch_size):
                batch = data[i:i + batch_size]
                inserted = self._insert_batch(table, batch, ignore_duplicates)
                total_inserted += inserted
                
                # 记录进度
                if i % (batch_size * 10) == 0:
                    elapsed = time.time() - start_time
                    logger.info(
                        f"批量插入进度: {i + len(batch)}/{len(data)} "
                        f"(已插入: {total_inserted}, 耗时: {elapsed:.2f}秒)"
                    )
            
            elapsed = time.time() - start_time
            logger.info(
                f"批量插入完成: 总数={len(data)}, 插入={total_inserted}, "
                f"耗时={elapsed:.2f}秒, 速率={len(data)/elapsed:.2f}条/秒"
            )
            
            return total_inserted
            
        except Exception as e:
            logger.error(f"批量插入失败: {e}")
            raise DatabaseError(f"批量插入失败: {e}")
    
    def _insert_batch(self, table: str, batch: List[Dict], ignore_duplicates: bool) -> int:
        """插入单个批次"""
        if not batch:
            return 0
        
        # 构建INSERT语句
        columns = list(batch[0].keys())
        columns_str = ", ".join(columns)
        placeholders = ", ".join([f":{col}" for col in columns])
        
        if ignore_duplicates:
            sql = f"INSERT IGNORE INTO {table} ({columns_str}) VALUES ({placeholders})"
        else:
            sql = f"INSERT INTO {table} ({columns_str}) VALUES ({placeholders})"
        
        with session_scope(self.db_manager) as session:
            result = session.execute(sql, batch)
            return result.rowcount
    
    def create_table(self, table_name: str, columns: Dict[str, str], if_not_exists: bool = True) -> None:
        """
        创建表
        
        Args:
            table_name: 表名
            columns: 列定义字典 {列名: 类型}
            if_not_exists: 如果表不存在则创建
            
        Raises:
            DatabaseError: 数据库操作错误
        """
        try:
            columns_def = ", ".join([f"{name} {type_def}" for name, type_def in columns.items()])
            if_exists = "IF NOT EXISTS " if if_not_exists else ""
            
            sql = f"CREATE TABLE {if_exists}{table_name} ({columns_def})"
            
            with session_scope(self.db_manager) as session:
                session.execute(sql)
            
            logger.info(f"表创建成功: {table_name}")
            
        except Exception as e:
            logger.error(f"表创建失败: {e}")
            raise DatabaseError(f"表创建失败: {e}")
    
    def table_exists(self, table_name: str) -> bool:
        """
        检查表是否存在
        
        Args:
            table_name: 表名
            
        Returns:
            bool: 表是否存在
        """
        try:
            sql = """
                SELECT COUNT(*) as count 
                FROM information_schema.tables 
                WHERE table_schema = DATABASE() AND table_name = :table_name
            """
            
            result = self.execute_query(sql, {"table_name": table_name})
            return result[0]["count"] > 0
            
        except Exception as e:
            logger.error(f"检查表存在失败: {e}")
            return False
    
    def get_table_info(self, table_name: str) -> List[Dict]:
        """
        获取表结构信息
        
        Args:
            table_name: 表名
            
        Returns:
            List[Dict]: 表结构信息
        """
        try:
            sql = """
                SELECT 
                    column_name, 
                    data_type, 
                    is_nullable,
                    column_default,
                    column_key,
                    extra
                FROM information_schema.columns 
                WHERE table_schema = DATABASE() AND table_name = :table_name
                ORDER BY ordinal_position
            """
            
            return self.execute_query(sql, {"table_name": table_name})
            
        except Exception as e:
            logger.error(f"获取表结构失败: {e}")
            raise DatabaseError(f"获取表结构失败: {e}")
    
    def optimize_table(self, table_name: str) -> None:
        """
        优化表
        
        Args:
            table_name: 表名
            
        Raises:
            DatabaseError: 数据库操作错误
        """
        try:
            sql = f"OPTIMIZE TABLE {table_name}"
            
            with session_scope(self.db_manager) as session:
                session.execute(sql)
            
            logger.info(f"表优化完成: {table_name}")
            
        except Exception as e:
            logger.error(f"表优化失败: {e}")
            raise DatabaseError(f"表优化失败: {e}")


# 全局数据库管理器实例
_db_manager: Optional[DatabaseManager] = None


def get_db_manager(config: Optional[DatabaseConfig] = None) -> DatabaseManager:
    """
    获取数据库管理器实例
    
    Args:
        config: 数据库配置，如果为None则使用默认配置
        
    Returns:
        DatabaseManager: 数据库管理器实例
    """
    global _db_manager
    
    if _db_manager is None:
        if config is None:
            from ..config import ConfigManager
            app_config = ConfigManager.get_config()
            config = app_config.database
        
        _db_manager = DatabaseManager(config)
        _db_manager.initialize()
    
    return _db_manager


def close_db_manager() -> None:
    """关闭数据库管理器"""
    global _db_manager
    
    if _db_manager:
        _db_manager.close()
        _db_manager = None