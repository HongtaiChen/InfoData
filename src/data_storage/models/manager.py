"""
表管理器

管理数据库表结构的创建、更新和验证。
"""

import logging
from typing import Type, List, Dict, Any, Optional
from .base import BaseModel
from ..database import MySQLDatabaseManager


class TableManagerError(Exception):
    """表管理器错误"""
    pass


class TableManager:
    """表管理器
    
    管理数据库表结构的创建、更新和验证。
    """
    
    def __init__(
        self,
        db_manager: MySQLDatabaseManager,
        logger: Optional[logging.Logger] = None
    ):
        """初始化表管理器
        
        Args:
            db_manager: 数据库管理器
            logger: 日志记录器
        """
        self.db = db_manager
        self.logger = logger or logging.getLogger(__name__)
        
        # 已注册的模型
        self._models: Dict[str, Type[BaseModel]] = {}
    
    def register_model(self, model_class: Type[BaseModel]) -> None:
        """注册数据模型
        
        Args:
            model_class: 数据模型类
        """
        table_name = model_class.get_table_name()
        self._models[table_name] = model_class
        self.logger.debug(f"注册数据模型: {model_class.__name__} -> {table_name}")
    
    def register_models(self, model_classes: List[Type[BaseModel]]) -> None:
        """批量注册数据模型
        
        Args:
            model_classes: 数据模型类列表
        """
        for model_class in model_classes:
            self.register_model(model_class)
    
    def get_model(self, table_name: str) -> Optional[Type[BaseModel]]:
        """获取表对应的模型类
        
        Args:
            table_name: 表名
            
        Returns:
            模型类或None
        """
        return self._models.get(table_name)
    
    def create_table_for_model(
        self,
        model_class: Type[BaseModel],
        if_not_exists: bool = True
    ) -> bool:
        """为模型创建表
        
        Args:
            model_class: 数据模型类
            if_not_exists: 如果表不存在则创建
            
        Returns:
            是否成功创建
        """
        table_name = model_class.get_table_name()
        
        try:
            # 获取表定义
            columns_def = model_class.get_columns_def()
            primary_key = model_class.get_primary_key()
            indexes = model_class.get_indexes()
            
            self.logger.info(f"为模型 {model_class.__name__} 创建表: {table_name}")
            
            # 创建表
            created = self.db.create_table(
                table_name=table_name,
                columns_def=columns_def,
                primary_key=primary_key,
                indexes=indexes,
                if_not_exists=if_not_exists
            )
            
            if created:
                self.logger.info(f"表创建成功: {table_name}")
            else:
                self.logger.info(f"表已存在: {table_name}")
            
            return created
            
        except Exception as e:
            error_msg = f"为模型 {model_class.__name__} 创建表失败: {e}"
            self.logger.error(error_msg)
            raise TableManagerError(error_msg) from e
    
    def create_all_tables(self, if_not_exists: bool = True) -> Dict[str, bool]:
        """创建所有已注册模型的表
        
        Args:
            if_not_exists: 如果表不存在则创建
            
        Returns:
            创建结果字典 {表名: 是否创建成功}
        """
        results = {}
        
        for table_name, model_class in self._models.items():
            try:
                created = self.create_table_for_model(model_class, if_not_exists)
                results[table_name] = created
            except Exception as e:
                results[table_name] = False
                self.logger.error(f"创建表 {table_name} 失败: {e}")
        
        return results
    
    def table_exists_for_model(self, model_class: Type[BaseModel]) -> bool:
        """检查模型对应的表是否存在
        
        Args:
            model_class: 数据模型类
            
        Returns:
            表是否存在
        """
        table_name = model_class.get_table_name()
        return self.db.table_exists(table_name)
    
    def validate_table_structure(
        self,
        model_class: Type[BaseModel]
    ) -> Dict[str, Any]:
        """验证表结构是否符合模型定义
        
        Args:
            model_class: 数据模型类
            
        Returns:
            验证结果字典
        """
        table_name = model_class.get_table_name()
        
        result = {
            "table_name": table_name,
            "model_class": model_class.__name__,
            "exists": False,
            "columns_match": False,
            "primary_key_match": False,
            "missing_columns": [],
            "extra_columns": [],
            "type_mismatches": [],
            "valid": False
        }
        
        # 检查表是否存在
        if not self.db.table_exists(table_name):
            result["exists"] = False
            result["valid"] = False
            return result
        
        result["exists"] = True
        
        try:
            # 获取实际表信息
            table_info = self.db.get_table_info(table_name)
            actual_columns = {col["name"]: col["type"] for col in table_info["columns"]}
            
            # 获取模型定义的列
            expected_columns = model_class.get_columns_def()
            expected_column_names = set(expected_columns.keys())
            actual_column_names = set(actual_columns.keys())
            
            # 检查列匹配
            missing_columns = expected_column_names - actual_column_names
            extra_columns = actual_column_names - expected_column_names
            
            result["missing_columns"] = list(missing_columns)
            result["extra_columns"] = list(extra_columns)
            
            # 检查列类型匹配（简化版本）
            type_mismatches = []
            for column in expected_column_names & actual_column_names:
                expected_type = expected_columns[column].upper()
                actual_type = actual_columns[column].upper()
                
                # 简化类型比较
                if not self._compare_column_types(expected_type, actual_type):
                    type_mismatches.append({
                        "column": column,
                        "expected": expected_type,
                        "actual": actual_type
                    })
            
            result["type_mismatches"] = type_mismatches
            
            # 检查主键匹配（简化版本）
            # 实际数据库中获取主键信息比较复杂，这里暂不实现
            
            # 判断表结构是否有效
            result["columns_match"] = (len(missing_columns) == 0 and 
                                      len(extra_columns) == 0 and 
                                      len(type_mismatches) == 0)
            result["valid"] = result["columns_match"]
            
        except Exception as e:
            result["error"] = str(e)
            result["valid"] = False
        
        return result
    
    def _compare_column_types(self, expected_type: str, actual_type: str) -> bool:
        """比较列类型（简化版本）
        
        Args:
            expected_type: 期望类型
            actual_type: 实际类型
            
        Returns:
            类型是否匹配
        """
        # 简化类型比较逻辑
        expected_lower = expected_type.lower()
        actual_lower = actual_type.lower()
        
        # 基本类型匹配
        type_groups = {
            "int": ["int", "bigint", "smallint", "tinyint", "mediumint"],
            "decimal": ["decimal", "numeric", "float", "double"],
            "varchar": ["varchar", "char", "text"],
            "date": ["date", "datetime", "timestamp"]
        }
        
        for group_name, type_list in type_groups.items():
            if any(t in expected_lower for t in type_list) and any(t in actual_lower for t in type_list):
                return True
        
        # 精确匹配
        return expected_lower == actual_lower
    
    def migrate_table_structure(
        self,
        model_class: Type[BaseModel],
        strategy: str = "safe"
    ) -> bool:
        """迁移表结构以匹配模型定义
        
        Args:
            model_class: 数据模型类
            strategy: 迁移策略，可选值：safe（安全，不删除数据）, full（完整迁移）
            
        Returns:
            迁移是否成功
        """
        table_name = model_class.get_table_name()
        
        try:
            # 验证当前表结构
            validation = self.validate_table_structure(model_class)
            
            if validation["valid"]:
                self.logger.info(f"表结构已是最新: {table_name}")
                return True
            
            if not validation["exists"]:
                # 表不存在，直接创建
                return self.create_table_for_model(model_class, if_not_exists=True)
            
            self.logger.warning(f"表结构需要迁移: {table_name}")
            self.logger.warning(f"缺失列: {validation['missing_columns']}")
            self.logger.warning(f"多余列: {validation['extra_columns']}")
            self.logger.warning(f"类型不匹配: {validation['type_mismatches']}")
            
            # 根据策略执行迁移
            if strategy == "safe":
                # 安全迁移：只添加缺失列，不删除多余列
                return self._safe_migrate_table(model_class, validation)
            elif strategy == "full":
                # 完整迁移：重新创建表（会丢失数据）
                return self._full_migrate_table(model_class)
            else:
                raise TableManagerError(f"不支持的迁移策略: {strategy}")
            
        except Exception as e:
            error_msg = f"迁移表结构失败: {table_name} - {e}"
            self.logger.error(error_msg)
            raise TableManagerError(error_msg) from e
    
    def _safe_migrate_table(
        self,
        model_class: Type[BaseModel],
        validation: Dict[str, Any]
    ) -> bool:
        """安全迁移表结构
        
        Args:
            model_class: 数据模型类
            validation: 验证结果
            
        Returns:
            迁移是否成功
        """
        table_name = model_class.get_table_name()
        columns_def = model_class.get_columns_def()
        
        try:
            # 添加缺失列
            for column in validation["missing_columns"]:
                column_type = columns_def[column]
                alter_query = f"ALTER TABLE `{table_name}` ADD COLUMN `{column}` {column_type}"
                
                self.logger.info(f"添加列: {table_name}.{column} ({column_type})")
                self.db.execute_query(alter_query)
            
            # 处理类型不匹配（这里只记录警告，不实际修改）
            for mismatch in validation["type_mismatches"]:
                self.logger.warning(
                    f"类型不匹配（需要手动处理）: {table_name}.{mismatch['column']} "
                    f"期望: {mismatch['expected']}, 实际: {mismatch['actual']}"
                )
            
            self.logger.info(f"安全迁移完成: {table_name}")
            return True
            
        except Exception as e:
            error_msg = f"安全迁移失败: {table_name} - {e}"
            self.logger.error(error_msg)
            raise TableManagerError(error_msg) from e
    
    def _full_migrate_table(self, model_class: Type[BaseModel]) -> bool:
        """完整迁移表结构（重新创建表）
        
        Args:
            model_class: 数据模型类
            
        Returns:
            迁移是否成功
        """
        table_name = model_class.get_table_name()
        
        try:
            self.logger.warning(f"完整迁移（重新创建表，会丢失数据）: {table_name}")
            
            # 备份数据（这里只记录警告，实际生产环境需要更完善的备份机制）
            self.logger.warning(f"注意：表 {table_name} 的数据将会丢失！")
            
            # 删除旧表
            drop_query = f"DROP TABLE IF EXISTS `{table_name}`"
            self.db.execute_query(drop_query)
            self.logger.info(f"删除旧表: {table_name}")
            
            # 创建新表
            return self.create_table_for_model(model_class, if_not_exists=False)
            
        except Exception as e:
            error_msg = f"完整迁移失败: {table_name} - {e}"
            self.logger.error(error_msg)
            raise TableManagerError(error_msg) from e
    
    def get_all_table_status(self) -> Dict[str, Dict[str, Any]]:
        """获取所有已注册表的状态
        
        Returns:
            表状态字典
        """
        status = {}
        
        for table_name, model_class in self._models.items():
            try:
                table_info = {
                    "model": model_class.__name__,
                    "exists": self.db.table_exists(table_name),
                    "validation": self.validate_table_structure(model_class)
                }
                status[table_name] = table_info
            except Exception as e:
                status[table_name] = {
                    "model": model_class.__name__,
                    "error": str(e)
                }
        
        return status
    
    def insert_model_data(
        self,
        model_instance: BaseModel,
        on_duplicate_key_update: bool = True
    ) -> bool:
        """插入模型数据到数据库
        
        Args:
            model_instance: 模型实例
            on_duplicate_key_update: 如果主键冲突是否更新
            
        Returns:
            插入是否成功
        """
        try:
            # 验证数据
            model_instance.validate()
            
            # 获取表名和数据
            table_name = model_instance.get_table_name()
            data = model_instance.to_dict()
            
            # 构建INSERT语句
            columns = list(data.keys())
            placeholders = ", ".join(["%s"] * len(columns))
            column_names = ", ".join([f"`{col}`" for col in columns])
            
            insert_query = f"INSERT INTO `{table_name}` ({column_names}) VALUES ({placeholders})"
            
            # 如果启用重复键更新
            if on_duplicate_key_update:
                update_clause = ", ".join([f"`{col}` = VALUES(`{col}`)" for col in columns])
                insert_query += f" ON DUPLICATE KEY UPDATE {update_clause}"
            
            # 准备参数
            params = tuple(data[col] for col in columns)
            
            # 执行插入
            self.db.execute_query(insert_query, params)
            
            self.logger.debug(f"插入数据成功: {table_name}")
            return True
            
        except Exception as e:
            error_msg = f"插入模型数据失败: {e}"
            self.logger.error(error_msg)
            raise TableManagerError(error_msg) from e
    
    def bulk_insert_model_data(
        self,
        model_instances: List[BaseModel],
        on_duplicate_key_update: bool = True,
        chunk_size: int = 1000
    ) -> int:
        """批量插入模型数据
        
        Args:
            model_instances: 模型实例列表
            on_duplicate_key_update: 如果主键冲突是否更新
            chunk_size: 分块大小
            
        Returns:
            插入的行数
        """
        if not model_instances:
            return 0
        
        # 验证所有实例
        for instance in model_instances:
            instance.validate()
        
        # 使用DataFrame批量插入
        table_name = model_instances[0].get_table_name()
        
        # 转换为DataFrame
        data_list = [instance.to_dict() for instance in model_instances]
        
        import pandas as pd
        df = pd.DataFrame(data_list)
        
        # 插入数据
        inserted_rows = self.db.insert_dataframe(
            table_name=table_name,
            df=df,
            if_exists="append",
            chunk_size=chunk_size
        )
        
        self.logger.info(f"批量插入完成: {table_name}, 行数: {inserted_rows}")
        return inserted_rows