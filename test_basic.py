#!/usr/bin/env python3
"""
基础架构测试脚本

测试配置管理、日志系统和数据库工具的基本功能。
"""

import os
import sys
import tempfile
import yaml
from pathlib import Path

# 添加项目根目录到Python路径
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from infodata.config import ConfigManager, AppConfig
from infodata.utils.logging import setup_logging, get_logger
from infodata.utils.database import get_db_manager


def test_config_management():
    """测试配置管理"""
    print("测试配置管理...")
    
    # 创建临时配置文件
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        config_data = {
            "app_name": "TestApp",
            "environment": "testing",
            "log_level": "DEBUG",
            "database": {
                "mysql": {
                    "host": "localhost",
                    "port": 3306,
                    "user": "test",
                    "password": "test",
                    "database": "test_db",
                }
            }
        }
        yaml.dump(config_data, f)
        config_path = f.name
    
    try:
        # 测试配置加载
        config = ConfigManager.load(config_path)
        print(f"✓ 配置加载成功: {config.app_name}")
        
        # 测试配置验证
        assert config.app_name == "TestApp"
        assert config.environment == "testing"
        assert config.log_level == "DEBUG"
        print("✓ 配置验证通过")
        
        # 测试默认配置创建
        default_config = AppConfig()
        print(f"✓ 默认配置创建成功: {default_config.app_name}")
        
        return True
        
    except Exception as e:
        print(f"✗ 配置管理测试失败: {e}")
        return False
        
    finally:
        # 清理临时文件
        os.unlink(config_path)


def test_logging_system():
    """测试日志系统"""
    print("\n测试日志系统...")
    
    try:
        # 设置日志
        setup_logging(
            log_level="DEBUG",
            log_file=None,  # 不输出到文件
            enable_console=True,
            enable_json=False
        )
        
        # 获取日志记录器
        logger = get_logger("test_logger")
        
        # 测试日志记录
        logger.debug("调试信息")
        logger.info("信息消息")
        logger.warning("警告消息")
        logger.error("错误消息")
        
        print("✓ 日志系统测试通过")
        return True
        
    except Exception as e:
        print(f"✗ 日志系统测试失败: {e}")
        return False


def test_database_tools():
    """测试数据库工具"""
    print("\n测试数据库工具...")
    
    try:
        # 创建测试配置
        config = AppConfig()
        config.database.mysql = {
            "host": "localhost",
            "port": 3306,
            "user": "test",
            "password": "test",
            "database": "test_db",
            "pool_size": 2,
        }
        
        # 测试数据库管理器
        from infodata.utils.database import DatabaseManager
        db_manager = DatabaseManager(config.database)
        
        # 测试初始化（应该失败，因为数据库不存在）
        try:
            db_manager.initialize()
            print("✗ 数据库连接应该失败但成功了")
            return False
        except Exception as e:
            print(f"✓ 数据库连接失败（预期）: {type(e).__name__}")
        
        # 测试SQLite配置
        config.database.sqlite = {"database": ":memory:"}
        db_manager_sqlite = DatabaseManager(config.database)
        
        try:
            db_manager_sqlite.initialize()
            print("✓ SQLite数据库初始化成功")
            
            # 测试连接信息
            info = db_manager_sqlite.get_connection_info()
            print(f"✓ 数据库连接信息: {info}")
            
            return True
            
        except Exception as e:
            print(f"✗ SQLite数据库测试失败: {e}")
            return False
            
    except Exception as e:
        print(f"✗ 数据库工具测试失败: {e}")
        return False


def test_utils_module():
    """测试工具模块"""
    print("\n测试工具模块...")
    
    try:
        # 测试上下文管理
        from infodata.utils.context import get_context, set_context, TaskContext
        
        # 测试基本上下文
        set_context({"test_key": "test_value"})
        context = get_context()
        assert context["test_key"] == "test_value"
        print("✓ 基本上下文测试通过")
        
        # 测试任务上下文
        with TaskContext("task_123", "测试任务") as task_ctx:
            task_context = get_context()
            assert task_context["task_id"] == "task_123"
            assert task_context["task_name"] == "测试任务"
            print("✓ 任务上下文测试通过")
        
        # 验证上下文已恢复
        context_after = get_context()
        assert "task_id" not in context_after
        print("✓ 上下文恢复测试通过")
        
        return True
        
    except Exception as e:
        print(f"✗ 工具模块测试失败: {e}")
        return False


def test_exceptions():
    """测试异常系统"""
    print("\n测试异常系统...")
    
    try:
        from infodata.utils.exceptions import (
            InfoDataError, DatabaseError, TaskError,
            wrap_exception, retry_on_exception
        )
        
        # 测试基础异常
        try:
            raise InfoDataError("测试错误", code="TEST_ERROR")
        except InfoDataError as e:
            assert e.code == "TEST_ERROR"
            print("✓ 基础异常测试通过")
        
        # 测试数据库异常
        try:
            raise DatabaseError("数据库错误")
        except DatabaseError as e:
            assert e.code == "DATABASE_ERROR"
            print("✓ 数据库异常测试通过")
        
        # 测试任务异常
        try:
            raise TaskError("任务错误", task_id="task_123")
        except TaskError as e:
            assert e.code == "TASK_ERROR"
            assert e.details["task_id"] == "task_123"
            print("✓ 任务异常测试通过")
        
        # 测试异常包装
        try:
            raise ValueError("原始错误")
        except ValueError as e:
            wrapped = wrap_exception(e, DatabaseError)
            assert isinstance(wrapped, DatabaseError)
            assert "原始错误" in str(wrapped)
            print("✓ 异常包装测试通过")
        
        return True
        
    except Exception as e:
        print(f"✗ 异常系统测试失败: {e}")
        return False


def main():
    """主测试函数"""
    print("=" * 60)
    print("InfoData 基础架构测试")
    print("=" * 60)
    
    tests = [
        ("配置管理", test_config_management),
        ("日志系统", test_logging_system),
        ("数据库工具", test_database_tools),
        ("工具模块", test_utils_module),
        ("异常系统", test_exceptions),
    ]
    
    results = []
    for test_name, test_func in tests:
        try:
            success = test_func()
            results.append((test_name, success))
        except Exception as e:
            print(f"✗ {test_name} 测试异常: {e}")
            results.append((test_name, False))
    
    # 输出测试结果
    print("\n" + "=" * 60)
    print("测试结果汇总:")
    print("=" * 60)
    
    passed = 0
    total = len(results)
    
    for test_name, success in results:
        status = "✓ 通过" if success else "✗ 失败"
        print(f"{test_name:20} {status}")
        if success:
            passed += 1
    
    print("-" * 60)
    print(f"总计: {passed}/{total} 通过")
    
    if passed == total:
        print("\n🎉 所有测试通过！基础架构功能正常。")
        return 0
    else:
        print(f"\n⚠️  {total - passed} 个测试失败，需要检查。")
        return 1


if __name__ == "__main__":
    sys.exit(main())