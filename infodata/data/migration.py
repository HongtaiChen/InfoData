"""
数据库迁移工具

提供数据库表创建、升级和数据迁移功能。
"""

import os
import sys
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime

from sqlalchemy import create_engine, MetaData, Table, inspect
from sqlalchemy.orm import sessionmaker
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from alembic.runtime.environment import EnvironmentContext

from ..utils.logging import get_logger
from ..utils.database import get_db_manager, DatabaseManager
from ..models.base import Base
from ..models.stock import (
    StockDaily, StockInfo, StockIndustry, StockConcept,
    StockHolder, StockDividend, StockSplit
)
from ..models.fund import FundDaily, FundInfo, FundNetValue, FundManager
from ..models.bond import BondDaily, BondInfo, BondYield, BondRating
from ..models.index import IndexDaily, IndexInfo, IndexComponent
from ..models.task import TaskExecution, TaskMetric
from ..models.quality import DataQualityMetric, DataValidationRule

logger = get_logger(__name__)


class DatabaseMigrator:
    """数据库迁移器"""
    
    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        """
        初始化数据库迁移器
        
        Args:
            db_manager: 数据库管理器，如果为None则使用默认实例
        """
        self.db_manager = db_manager or get_db_manager()
        self.alembic_cfg = None
        self._setup_alembic()
    
    def _setup_alembic(self) -> None:
        """设置Alembic配置"""
        # 获取项目根目录
        project_root = Path(__file__).parent.parent.parent
        
        # 创建alembic.ini文件路径
        alembic_ini_path = project_root / "alembic.ini"
        
        # 如果alembic.ini不存在，创建它
        if not alembic_ini_path.exists():
            self._create_alembic_config(alembic_ini_path)
        
        # 创建alembic目录
        alembic_dir = project_root / "alembic"
        if not alembic_dir.exists():
            alembic_dir.mkdir(exist_ok=True)
        
        # 创建versions目录
        versions_dir = alembic_dir / "versions"
        if not versions_dir.exists():
            versions_dir.mkdir(exist_ok=True)
        
        # 创建env.py文件
        env_py_path = alembic_dir / "env.py"
        if not env_py_path.exists():
            self._create_alembic_env(env_py_path)
        
        # 创建script.py.mako模板
        script_mako_path = alembic_dir / "script.py.mako"
        if not script_mako_path.exists():
            self._create_script_mako(script_mako_path)
        
        # 加载Alembic配置
        self.alembic_cfg = Config(str(alembic_ini_path))
        self.alembic_cfg.set_main_option("script_location", str(alembic_dir))
        
        # 设置数据库URL
        if self.db_manager.engine:
            self.alembic_cfg.set_main_option(
                "sqlalchemy.url",
                str(self.db_manager.engine.url)
            )
    
    def _create_alembic_config(self, config_path: Path) -> None:
        """创建Alembic配置文件"""
        config_content = """# A generic, single database configuration.

[alembic]
# path to migration scripts
script_location = alembic

# template used to generate migration file names; The default value is %%(rev)s_%%(slug)s
# Uncomment the line below if you want the files to be prepended with date and time
# file_template = %%(year)d_%%(month).2d_%%(day).2d_%%(hour).2d%%(minute).2d-%%(rev)s_%%(slug)s

# sys.path path, will be prepended to sys.path if present.
# defaults to the current working directory.
prepend_sys_path = .

# timezone to use when rendering the date within the migration file
# as well as the filename.
# If specified, requires the python-dateutil library that can be
# installed by adding `alembic[tz]` to the pip requirements
# string value is passed to dateutil.tz.gettz()
# leave blank for localtime
# timezone =

# max length of characters to apply to the
# "slug" field
# truncate_slug_length = 40

# set to 'true' to run the environment during
# the 'revision' command, regardless of autogenerate
# revision_environment = false

# set to 'true' to allow .pyc and .pyo files without
# a source .py file to be detected as revisions in the
# versions/ directory
# sourceless = false

# version path separator; As mentioned above, this is the character used to split
# version_locations. The default within new alembic.ini files is "os", which uses
# os.pathsep. If this key is omitted entirely, it falls back to the legacy
# behavior of splitting on spaces and/or commas.
# Valid values for version_path_separator are:
#
# version_path_separator = :
# version_path_separator = ;
# version_path_separator = space
version_path_separator = os

# set to 'true' to search source files recursively
# in each "version_locations" directory
# new in Alembic version 1.10
# recursive_version_locations = false

# the output encoding used when revision files
# are written from script.py.mako
# output_encoding = utf-8

sqlalchemy.url = driver://user:pass@localhost/dbname


[post_write_hooks]
# post_write_hooks defines scripts or Python functions that are run
# on newly generated revision scripts.  See the documentation for further
# detail and examples

# format using "black" - use the console_scripts runner, against the "black" entrypoint
# hooks = black
# black.type = console_scripts
# black.entrypoint = black
# black.options = -l 79 REVISION_SCRIPT_FILENAME

# lint with attempts to fix using "ruff" - use the console_scripts runner, against the "ruff" entrypoint
# hooks = ruff
# ruff.type = console_scripts
# ruff.entrypoint = ruff
# ruff.options = --fix REVISION_SCRIPT_FILENAME

# Logging configuration
[loggers]
keys = root,sqlalchemy,alembic

[handlers]
keys = console

[formatters]
keys = generic

[logger_root]
level = WARN
handlers = console
qualname =

[logger_sqlalchemy]
level = WARN
handlers =
qualname = sqlalchemy.engine

[logger_alembic]
level = INFO
handlers =
qualname = alembic

[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic

[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
datefmt = %H:%M:%S
"""
        
        with open(config_path, 'w', encoding='utf-8') as f:
            f.write(config_content)
        
        logger.info(f"创建Alembic配置文件: {config_path}")
    
    def _create_alembic_env(self, env_path: Path) -> None:
        """创建Alembic环境文件"""
        env_content = """from logging.config import fileConfig

from sqlalchemy import engine_from_config
from sqlalchemy import pool

from alembic import context

# 导入所有模型
from infodata.models.base import Base
from infodata.models.stock import *
from infodata.models.fund import *
from infodata.models.bond import *
from infodata.models.index import *
from infodata.models.task import *
from infodata.models.quality import *

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# add your model's MetaData object here
# for 'autogenerate' support
target_metadata = Base.metadata

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline() -> None:
    '''Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    '''
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    '''Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    '''
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
"""
        
        with open(env_path, 'w', encoding='utf-8') as f:
            f.write(env_content)
        
        logger.info(f"创建Alembic环境文件: {env_path}")
    
    def _create_script_mako(self, mako_path: Path) -> None:
        """创建脚本模板文件"""
        mako_content = """\"\"\"${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}

\"\"\"
from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

# revision identifiers, used by Alembic.
revision = ${repr(up_revision)}
down_revision = ${repr(down_revision)}
branch_labels = ${repr(branch_labels)}
depends_on = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
"""
        
        with open(mako_path, 'w', encoding='utf-8') as f:
            f.write(mako_content)
        
        logger.info(f"创建Alembic脚本模板: {mako_path}")
    
    def create_tables(self) -> Dict[str, Any]:
        """
        创建所有数据库表
        
        Returns:
            Dict[str, Any]: 创建结果
        """
        try:
            # 确保数据库连接
            if not self.db_manager._initialized:
                self.db_manager.initialize()
            
            # 创建所有表
            Base.metadata.create_all(bind=self.db_manager.engine)
            
            # 获取创建的表列表
            inspector = inspect(self.db_manager.engine)
            tables = inspector.get_table_names()
            
            result = {
                'success': True,
                'tables_created': len(tables),
                'table_list': tables,
                'timestamp': datetime.now().isoformat(),
            }
            
            logger.info(f"数据库表创建完成，共创建 {len(tables)} 个表")
            
            return result
            
        except Exception as e:
            logger.error(f"创建数据库表失败: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e),
                'timestamp': datetime.now().isoformat(),
            }
    
    def drop_tables(self) -> Dict[str, Any]:
        """
        删除所有数据库表
        
        Returns:
            Dict[str, Any]: 删除结果
        """
        try:
            # 确保数据库连接
            if not self.db_manager._initialized:
                self.db_manager.initialize()
            
            # 删除所有表
            Base.metadata.drop_all(bind=self.db_manager.engine)
            
            result = {
                'success': True,
                'message': '所有数据库表已删除',
                'timestamp': datetime.now().isoformat(),
            }
            
            logger.warning("数据库表已删除")
            
            return result
            
        except Exception as e:
            logger.error(f"删除数据库表失败: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e),
                'timestamp': datetime.now().isoformat(),
            }
    
    def check_tables(self) -> Dict[str, Any]:
        """
        检查数据库表状态
        
        Returns:
            Dict[str, Any]: 检查结果
        """
        try:
            # 确保数据库连接
            if not self.db_manager._initialized:
                self.db_manager.initialize()
            
            inspector = inspect(self.db_manager.engine)
            existing_tables = inspector.get_table_names()
            
            # 获取所有定义的模型表
            defined_tables = Base.metadata.tables.keys()
            
            # 检查缺失的表
            missing_tables = []
            for table_name in defined_tables:
                if table_name not in existing_tables:
                    missing_tables.append(table_name)
            
            # 检查额外的表
            extra_tables = []
            for table_name in existing_tables:
                if table_name not in defined_tables:
                    extra_tables.append(table_name)
            
            result = {
                'success': True,
                'database_connected': True,
                'defined_tables_count': len(defined_tables),
                'existing_tables_count': len(existing_tables),
                'missing_tables': missing_tables,
                'extra_tables': extra_tables,
                'all_tables_match': len(missing_tables) == 0 and len(extra_tables) == 0,
                'timestamp': datetime.now().isoformat(),
            }
            
            if missing_tables:
                logger.warning(f"发现缺失的表: {missing_tables}")
            if extra_tables:
                logger.warning(f"发现额外的表: {extra_tables}")
            
            return result
            
        except Exception as e:
            logger.error(f"检查数据库表失败: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e),
                'database_connected': False,
                'timestamp': datetime.now().isoformat(),
            }
    
    def create_migration(self, message: str) -> Dict[str, Any]:
        """
        创建新的迁移脚本
        
        Args:
            message: 迁移描述
            
        Returns:
            Dict[str, Any]: 创建结果
        """
        try:
            if not self.alembic_cfg:
                raise RuntimeError("Alembic配置未初始化")
            
            # 生成迁移脚本
            command.revision(
                self.alembic_cfg,
                message=message,
                autogenerate=True,
            )
            
            # 获取最新迁移
            script = ScriptDirectory.from_config(self.alembic_cfg)
            revisions = list(script.walk_revisions())
            latest_revision = revisions[0] if revisions else None
            
            result = {
                'success': True,
                'message': f"迁移脚本已创建: {message}",
                'revision': latest_revision.revision if latest_revision else None,
                'timestamp': datetime.now().isoformat(),
            }
            
            logger.info(f"创建迁移脚本: {message}")
            
            return result
            
        except Exception as e:
            logger.error(f"创建迁移脚本失败: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e),
                'timestamp': datetime.now().isoformat(),
            }
    
    def run_migrations(self, revision: str = "head") -> Dict[str, Any]:
        """
        运行数据库迁移
        
        Args:
            revision: 目标版本，默认为最新版本
            
        Returns:
            Dict[str, Any]: 迁移结果
        """
        try:
            if not self.alembic_cfg:
                raise RuntimeError("Alembic配置未初始化")
            
            # 运行迁移
            command.upgrade(self.alembic_cfg, revision)
            
            # 检查当前版本
            script = ScriptDirectory.from_config(self.alembic_cfg)
            
            with EnvironmentContext(
                self.alembic_cfg,
                script,
                fn=lambda rev, context: rev
            ) as env:
                current_revision = env.get_head_revision()
            
            result = {
                'success': True,
                'message': f"数据库迁移完成到版本: {revision}",
                'current_revision': current_revision,
                'target_revision': revision,
                'timestamp': datetime.now().isoformat(),
            }
            
            logger.info(f"数据库迁移完成: {revision}")
            
            return result
            
        except Exception as e:
            logger.error(f"运行数据库迁移失败: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e),
                'timestamp': datetime.now().isoformat(),
            }
    
    def get_migration_history(self) -> Dict[str, Any]:
        """
        获取迁移历史
        
        Returns:
            Dict[str, Any]: 迁移历史
        """
        try:
            if not self.alembic_cfg:
                raise RuntimeError("Alembic配置未初始化")
            
            script = ScriptDirectory.from_config(self.alembic_cfg)
            
            # 获取所有版本
            revisions = []
            for rev in script.walk_revisions():
                revisions.append({
                    'revision': rev.revision,
                    'down_revision': rev.down_revision,
                    'branch_labels': rev.branch_labels,
                    'message': rev.doc,
                    'date': rev.date,
                })
            
            # 获取当前版本
            with EnvironmentContext(
                self.alembic_cfg,
                script,
                fn=lambda rev, context: rev
            ) as env:
                current_revision = env.get_head_revision()
            
            result = {
                'success': True,
                'current_revision': current_revision,
                'total_revisions': len(revisions),
                'revisions': revisions,
                'timestamp': datetime.now().isoformat(),
            }
            
            return result
            
        except Exception as e:
            logger.error(f"获取迁移历史失败: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e),
                'timestamp': datetime.now().isoformat(),
            }
    
    def reset_database(self) -> Dict[str, Any]:
        """
        重置数据库（删除所有表并重新创建）
        
        Returns:
            Dict[str, Any]: 重置结果
        """
        try:
            logger.warning("开始重置数据库...")
            
            # 删除所有表
            drop_result = self.drop_tables()
            if not drop_result['success']:
                return drop_result
            
            # 创建所有表
            create_result = self.create_tables()
            
            result = {
                'success': create_result['success'],
                'message': '数据库重置完成',
                'drop_result': drop_result,
                'create_result': create_result,
                'timestamp': datetime.now().isoformat(),
            }
            
            if create_result['success']:
                logger.info("数据库重置完成")
            else:
                logger.error("数据库重置失败")
            
            return result
            
        except Exception as e:
            logger.error(f"重置数据库失败: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e),
                'timestamp': datetime.now().isoformat(),
            }
    
    def export_schema(self, output_file: Optional[str] = None) -> Dict[str, Any]:
        """
        导出数据库模式
        
        Args:
            output_file: 输出文件路径，如果为None则返回字符串
            
        Returns:
            Dict[str, Any]: 导出结果
        """
        try:
            from sqlalchemy.schema import CreateTable
            
            # 生成DDL语句
            ddl_statements = []
            for table in Base.metadata.sorted_tables:
                ddl = str(CreateTable(table).compile(self.db_manager.engine))
                ddl_statements.append(ddl)
            
            ddl_content = "\n\n".join(ddl_statements)
            
            # 写入文件或返回内容
            if output_file:
                output_path = Path(output_file)
                output_path.parent.mkdir(parents=True, exist_ok=True)
                
                with open(output_path, 'w', encoding='utf-8') as f:
                    f.write(ddl_content)
                
                result = {
                    'success': True,
                    'message': f"数据库模式已导出到: {output_file}",
                    'file_path': str(output_path),
                    'file_size': len(ddl_content),
                    'table_count': len(Base.metadata.tables),
                    'timestamp': datetime.now().isoformat(),
                }
            else:
                result = {
                    'success': True,
                    'message': "数据库模式导出完成",
                    'ddl_content': ddl_content,
                    'table_count': len(Base.metadata.tables),
                    'timestamp': datetime.now().isoformat(),
                }
            
            logger.info(f"数据库模式导出完成，共 {len(Base.metadata.tables)} 个表")
            
            return result
            
        except Exception as e:
            logger.error(f"导出数据库模式失败: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e),
                'timestamp': datetime.now().isoformat(),
            }


def create_database_tables() -> Dict[str, Any]:
    """
    创建数据库表（便捷函数）
    
    Returns:
        Dict[str, Any]: 创建结果
    """
    migrator = DatabaseMigrator()
    return migrator.create_tables()


def check_database_status() -> Dict[str, Any]:
    """
    检查数据库状态（便捷函数）
    
    Returns:
        Dict[str, Any]: 检查结果
    """
    migrator = DatabaseMigrator()
    return migrator.check_tables()


def reset_database_tables() -> Dict[str, Any]:
    """
    重置数据库表（便捷函数）
    
    Returns:
        Dict[str, Any]: 重置结果
    """
    migrator = DatabaseMigrator()
    return migrator.reset_database()