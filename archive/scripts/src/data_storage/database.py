"""
数据库管理器

提供统一的数据库操作接口，支持连接池、事务管理和安全配置。
"""

import logging
import pymysql
from typing import Any, Dict, List, Optional, Union, Tuple
from contextlib import contextmanager
from dataclasses import dataclass
from .base import DatabaseError


class MySQLDatabaseManager:
    """MySQL数据库管理器
    
    提供MySQL数据库的统一操作接口，支持连接池和事务管理。
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
        pool_recycle: int = 3600,
        logger: Optional[logging.Logger] = None
    ):
        """初始化MySQL数据库管理器
        
        Args:
            host: 数据库主机
            port: 数据库端口
            user: 用户名
            password: 密码
            database: 数据库名
            charset: 字符集
            pool_size: 连接池大小
            pool_recycle: 连接回收时间（秒）
            logger: 日志记录器
        """
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.database = database
        self.charset = charset
        self.pool_size = pool_size
        self.pool_recycle = pool_recycle
        self.logger = logger or logging.getLogger(__name__)
        
        # 连接池
        self._connection_pool = []
        self._active_connections = 0
        
        self.logger.info(f"初始化MySQL数据库管理器: {host}:{port}/{database}")
    
    def _create_connection(self) -> pymysql.connections.Connection:
        """创建数据库连接
        
        Returns:
            数据库连接
            
        Raises:
            DatabaseError: 连接创建失败
        """
        try:
            connection = pymysql.connect(
                host=self.host,
                port=self.port,
                user=self.user,
                password=self.password,
                database=self.database,
                charset=self.charset,
                cursorclass=pymysql.cursors.DictCursor
            )
            
            self.logger.debug(f"创建数据库连接: {self.host}:{self.port}/{self.database}")
            return connection
            
        except Exception as e:
            error_msg = f"数据库连接失败: {self.host}:{self.port}/{self.database} - {e}"
            self.logger.error(error_msg)
            raise DatabaseError(error_msg) from e
    
    @contextmanager
    def get_connection(self):
        """获取数据库连接（上下文管理器）
        
        Yields:
            数据库连接
            
        Raises:
            DatabaseError: 获取连接失败
        """
        connection = None
        try:
            # 从连接池获取或创建新连接
            if self._connection_pool:
                connection = self._connection_pool.pop()
                self.logger.debug("从连接池获取数据库连接")
            else:
                connection = self._create_connection()
            
            self._active_connections += 1
            self.logger.debug(f"活跃连接数: {self._active_connections}")
            
            yield connection
            
        except Exception as e:
            if connection:
                connection.close()
                self._active_connections -= 1
            raise DatabaseError(f"获取数据库连接失败: {e}") from e
        
        finally:
            if connection:
                # 检查连接是否仍然有效
                try:
                    connection.ping(reconnect=True)
                    
                    # 如果连接池未满，放回连接池
                    if len(self._connection_pool) < self.pool_size:
                        self._connection_pool.append(connection)
                        self.logger.debug("归还数据库连接到连接池")
                    else:
                        connection.close()
                        self.logger.debug("关闭数据库连接（连接池已满）")
                    
                except Exception:
                    # 连接无效，直接关闭
                    connection.close()
                    self.logger.warning("数据库连接无效，已关闭")
                
                self._active_connections -= 1
                self.logger.debug(f"活跃连接数: {self._active_connections}")
    
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
            
        Raises:
            DatabaseError: 查询执行失败
        """
        with self.get_connection() as connection:
            try:
                with connection.cursor() as cursor:
                    self.logger.debug(f"执行查询: {query[:100]}...")
                    
                    cursor.execute(query, params)
                    
                    if query.strip().upper().startswith("SELECT"):
                        if fetch_all:
                            result = cursor.fetchall()
                            self.logger.debug(f"查询结果行数: {len(result)}")
                            return result
                        else:
                            result = cursor.fetchone()
                            return [result] if result else []
                    else:
                        # INSERT, UPDATE, DELETE等操作
                        connection.commit()
                        affected_rows = cursor.rowcount
                        self.logger.debug(f"受影响的行数: {affected_rows}")
                        return affected_rows
                        
            except Exception as e:
                connection.rollback()
                error_msg = f"查询执行失败: {query[:100]}... - {e}"
                self.logger.error(error_msg)
                raise DatabaseError(error_msg) from e
    
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
            
        Raises:
            DatabaseError: 批量执行失败
        """
        with self.get_connection() as connection:
            try:
                with connection.cursor() as cursor:
                    self.logger.debug(f"批量执行: {query[:100]}... (共{len(params_list)}组参数)")
                    
                    cursor.executemany(query, params_list)
                    connection.commit()
                    
                    affected_rows = cursor.rowcount
                    self.logger.debug(f"受影响的总行数: {affected_rows}")
                    return affected_rows
                    
            except Exception as e:
                connection.rollback()
                error_msg = f"批量执行失败: {query[:100]}... - {e}"
                self.logger.error(error_msg)
                raise DatabaseError(error_msg) from e
    
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
            if_exists: 如果表存在时的操作，可选值：fail, replace, append
            chunk_size: 分块大小
            
        Returns:
            插入的行数
            
        Raises:
            DatabaseError: 插入失败
        """
        if df.empty:
            self.logger.warning("DataFrame为空，跳过插入")
            return 0
        
        # 获取列名
        columns = df.columns.tolist()
        
        # 构建INSERT语句
        placeholders = ", ".join(["%s"] * len(columns))
        column_names = ", ".join([f"`{col}`" for col in columns])
        insert_query = f"INSERT INTO `{table_name}` ({column_names}) VALUES ({placeholders})"
        
        total_rows = 0
        
        # 分块插入
        for i in range(0, len(df), chunk_size):
            chunk = df.iloc[i:i + chunk_size]
            
            # 准备参数
            params_list = []
            for _, row in chunk.iterrows():
                params = tuple(row[col] for col in columns)
                params_list.append(params)
            
            # 批量插入
            try:
                affected_rows = self.execute_many(insert_query, params_list)
                total_rows += affected_rows
                self.logger.debug(f"插入块 {i//chunk_size + 1}: {len(chunk)}行")
                
            except Exception as e:
                # 检查是否是重复键错误
                if "Duplicate entry" in str(e) and if_exists == "append":
                    # 尝试逐行插入，跳过重复项
                    self.logger.warning(f"块 {i//chunk_size + 1} 存在重复项，尝试逐行插入...")
                    
                    for params in params_list:
                        try:
                            self.execute_query(insert_query, params)
                            total_rows += 1
                        except Exception as row_error:
                            if "Duplicate entry" not in str(row_error):
                                raise row_error
                            # 跳过重复项
                
                else:
                    raise DatabaseError(f"插入DataFrame失败: {e}") from e
        
        self.logger.info(f"成功插入 {total_rows} 行到表 {table_name}")
        return total_rows
    
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
            columns_def: 列定义字典 {列名: 数据类型}
            primary_key: 主键列列表
            indexes: 索引定义列表
            if_not_exists: 如果表不存在则创建
            
        Returns:
            是否成功创建
            
        Raises:
            DatabaseError: 创建表失败
        """
        # 构建列定义
        column_defs = []
        for col_name, col_type in columns_def.items():
            column_defs.append(f"`{col_name}` {col_type}")
        
        # 添加主键
        if primary_key:
            pk_columns = ", ".join([f"`{col}`" for col in primary_key])
            column_defs.append(f"PRIMARY KEY ({pk_columns})")
        
        # 构建CREATE TABLE语句
        if_not_exists_clause = "IF NOT EXISTS" if if_not_exists else ""
        create_query = f"""
            CREATE TABLE {if_not_exists_clause} `{table_name}` (
                {', '.join(column_defs)}
            ) ENGINE=InnoDB DEFAULT CHARSET={self.charset}
        """
        
        try:
            self.execute_query(create_query)
            self.logger.info(f"创建表成功: {table_name}")
            
            # 创建索引
            if indexes:
                for index_def in indexes:
                    self._create_index(table_name, index_def)
            
            return True
            
        except Exception as e:
            if "already exists" in str(e).lower():
                self.logger.info(f"表已存在: {table_name}")
                return False
            else:
                error_msg = f"创建表失败: {table_name} - {e}"
                self.logger.error(error_msg)
                raise DatabaseError(error_msg) from e
    
    def _create_index(self, table_name: str, index_def: Dict):
        """创建索引
        
        Args:
            table_name: 表名
            index_def: 索引定义
            
        Raises:
            DatabaseError: 创建索引失败
        """
        index_name = index_def.get("name", "")
        columns = index_def.get("columns", [])
        index_type = index_def.get("type", "INDEX")
        
        if not columns:
            return
        
        column_list = ", ".join([f"`{col}`" for col in columns])
        
        if index_type.upper() == "UNIQUE":
            create_index_query = f"""
                CREATE UNIQUE INDEX `{index_name}` 
                ON `{table_name}` ({column_list})
            """
        else:
            create_index_query = f"""
                CREATE INDEX `{index_name}` 
                ON `{table_name}` ({column_list})
            """
        
        try:
            self.execute_query(create_index_query)
            self.logger.debug(f"创建索引成功: {table_name}.{index_name}")
        except Exception as e:
            if "already exists" in str(e).lower():
                self.logger.debug(f"索引已存在: {table_name}.{index_name}")
            else:
                error_msg = f"创建索引失败: {table_name}.{index_name} - {e}"
                self.logger.error(error_msg)
                raise DatabaseError(error_msg) from e
    
    def table_exists(self, table_name: str) -> bool:
        """检查表是否存在
        
        Args:
            table_name: 表名
            
        Returns:
            表是否存在
        """
        check_query = """
            SELECT COUNT(*) as count
            FROM information_schema.tables 
            WHERE table_schema = %s AND table_name = %s
        """
        
        try:
            result = self.execute_query(
                check_query,
                params=(self.database, table_name),
                fetch_all=True
            )
            
            return bool(result and result[0]["count"] > 0)
            
        except Exception as e:
            self.logger.error(f"检查表存在失败: {table_name} - {e}")
            return False
    
    def get_table_info(self, table_name: str) -> Dict:
        """获取表信息
        
        Args:
            table_name: 表名
            
        Returns:
            表信息字典
        """
        columns_query = f"""
            SHOW COLUMNS FROM `{table_name}`
        """
        
        try:
            columns = self.execute_query(columns_query, fetch_all=True)
            
            table_info = {
                "name": table_name,
                "columns": [],
                "row_count": 0
            }
            
            for col in columns:
                column_info = {
                    "name": col["Field"],
                    "type": col["Type"],
                    "nullable": col["Null"] == "YES",
                    "default": col["Default"],
                    "key": col["Key"],
                    "extra": col["Extra"]
                }
                table_info["columns"].append(column_info)
            
            # 获取行数
            count_query = f"SELECT COUNT(*) as count FROM `{table_name}`"
            count_result = self.execute_query(count_query, fetch_all=True)
            if count_result:
                table_info["row_count"] = count_result[0]["count"]
            
            return table_info
            
        except Exception as e:
            error_msg = f"获取表信息失败: {table_name} - {e}"
            self.logger.error(error_msg)
            raise DatabaseError(error_msg) from e
    
    def close_all_connections(self):
        """关闭所有连接"""
        for connection in self._connection_pool:
            try:
                connection.close()
            except Exception:
                pass
        
        self._connection_pool.clear()
        self._active_connections = 0
        self.logger.info("已关闭所有数据库连接")