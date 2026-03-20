"""
数据存储层 - 数据库抽象层

提供统一的数据库操作接口，支持MySQL、SQLite等数据库。
遵循项目宪法：数据完整性与安全。
"""

__version__ = "0.1.0"
__all__ = ["DatabaseManager", "DatabaseError", "get_database_manager"]