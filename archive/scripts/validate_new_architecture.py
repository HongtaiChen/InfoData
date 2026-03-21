#!/usr/bin/env python3
"""
验证新架构模块

验证所有新创建的模块能否正确导入和初始化。
"""

import sys
import os

# 添加路径
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

def test_module_imports():
    """测试模块导入"""
    print("=== 测试模块导入 ===")
    
    modules_to_test = [
        ("data_collection.base", ["BaseDataClient", "RateLimitConfig", "DataCollectionError"]),
        ("data_collection.akshare_client", ["AKShareClient"]),
        ("data_collection.tushare_client", ["TushareClient"]),
        ("data_collection.factory", ["DataClientFactory", "get_akshare_client", "get_tushare_client"]),
        ("config.manager", ["ConfigManager", "get_config_manager"]),
        ("data_storage.database", ["MySQLDatabaseManager", "DatabaseError"]),
        ("data_storage.base", ["BaseDatabaseManager"]),
        ("data_storage.models.base", ["BaseModel", "ValidationError"]),
        ("data_storage.models.stock", ["StockInfo", "StockDailyInfo"]),
        ("data_storage.models.financial", ["IndexInfo", "FundInfo", "BondInfo"]),
        ("data_storage.models.manager", ["TableManager"]),
        ("data_storage.manager", ["DataStorageManager", "get_storage_manager"]),
    ]
    
    all_passed = True
    
    for module_path, expected_classes in modules_to_test:
        try:
            module = __import__(module_path, fromlist=expected_classes)
            
            # 检查类是否存在
            missing_classes = []
            for class_name in expected_classes:
                if not hasattr(module, class_name):
                    missing_classes.append(class_name)
            
            if missing_classes:
                print(f"❌ {module_path}: 缺少类 {missing_classes}")
                all_passed = False
            else:
                print(f"✅ {module_path}: 导入成功")
                
        except ImportError as e:
            print(f"❌ {module_path}: 导入失败 - {e}")
            all_passed = False
        except Exception as e:
            print(f"❌ {module_path}: 错误 - {e}")
            all_passed = False
    
    return all_passed


def test_model_definitions():
    """测试模型定义"""
    print("\n=== 测试模型定义 ===")
    
    try:
        from data_storage.models.stock import StockInfo, StockDailyInfo
        from data_storage.models.financial import IndexInfo, FundInfo, BondInfo
        
        models_to_test = [
            ("StockInfo", StockInfo),
            ("StockDailyInfo", StockDailyInfo),
            ("IndexInfo", IndexInfo),
            ("FundInfo", FundInfo),
            ("BondInfo", BondInfo),
        ]
        
        all_passed = True
        
        for model_name, model_class in models_to_test:
            try:
                # 检查必需属性
                required_attrs = ["TABLE_NAME", "COLUMNS", "PRIMARY_KEY"]
                missing_attrs = []
                
                for attr in required_attrs:
                    if not hasattr(model_class, attr):
                        missing_attrs.append(attr)
                
                if missing_attrs:
                    print(f"❌ {model_name}: 缺少属性 {missing_attrs}")
                    all_passed = False
                else:
                    table_name = model_class.TABLE_NAME
                    columns = list(model_class.COLUMNS.keys())
                    print(f"✅ {model_name}: {table_name} ({len(columns)} 列)")
                    
            except Exception as e:
                print(f"❌ {model_name}: 测试失败 - {e}")
                all_passed = False
        
        return all_passed
        
    except Exception as e:
        print(f"❌ 模型定义测试失败: {e}")
        return False


def test_config_manager():
    """测试配置管理器"""
    print("\n=== 测试配置管理器 ===")
    
    try:
        from config.manager import ConfigManager
        
        # 创建配置管理器
        config = ConfigManager(env="testing")
        
        # 获取配置值
        app_name = config.get("app.name")
        db_host = config.get("database.host")
        
        print(f"✅ 配置管理器创建成功")
        print(f"   应用名称: {app_name}")
        print(f"   数据库主机: {db_host}")
        
        # 测试配置验证
        is_valid = config.validate()
        print(f"   配置验证: {'通过' if is_valid else '失败'}")
        
        return True
        
    except Exception as e:
        print(f"❌ 配置管理器测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_data_client_factory():
    """测试数据客户端工厂"""
    print("\n=== 测试数据客户端工厂 ===")
    
    try:
        from data_collection.factory import DataClientFactory
        
        # 创建工厂
        factory = DataClientFactory()
        
        # 创建AKShare客户端
        akshare_client = factory.create_akshare_client(client_id="test_client")
        
        print(f"✅ 客户端工厂创建成功")
        print(f"   创建AKShare客户端: {akshare_client.name}")
        
        # 测试客户端统计
        stats = akshare_client.get_stats()
        print(f"   客户端统计: {stats['name']}, 总请求: {stats['total_requests']}")
        
        return True
        
    except Exception as e:
        print(f"❌ 数据客户端工厂测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_table_manager():
    """测试表管理器（不连接数据库）"""
    print("\n=== 测试表管理器（无数据库连接） ===")
    
    try:
        from data_storage.models.manager import TableManager
        from data_storage.models.stock import StockInfo
        
        # 创建模拟的数据库管理器
        class MockDBManager:
            def table_exists(self, table_name):
                return False
        
        # 创建表管理器
        table_manager = TableManager(db_manager=MockDBManager())
        
        # 注册模型
        table_manager.register_model(StockInfo)
        
        print(f"✅ 表管理器创建成功")
        print(f"   注册模型: {StockInfo.__name__}")
        
        # 获取模型
        model = table_manager.get_model("A_stock_info")
        if model:
            print(f"   获取模型成功: {model.__name__}")
        else:
            print(f"❌ 获取模型失败")
            return False
        
        return True
        
    except Exception as e:
        print(f"❌ 表管理器测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """主测试函数"""
    print("开始验证新架构模块...\n")
    
    tests = [
        ("模块导入", test_module_imports),
        ("模型定义", test_model_definitions),
        ("配置管理器", test_config_manager),
        ("数据客户端工厂", test_data_client_factory),
        ("表管理器", test_table_manager),
    ]
    
    results = []
    
    for test_name, test_func in tests:
        try:
            passed = test_func()
            results.append((test_name, passed))
        except Exception as e:
            print(f"❌ {test_name}: 测试异常 - {e}")
            results.append((test_name, False))
    
    # 输出结果
    print("\n" + "="*50)
    print("验证结果汇总:")
    print("="*50)
    
    all_passed = True
    for test_name, passed in results:
        status = "✅ 通过" if passed else "❌ 失败"
        print(f"{test_name:20} {status}")
        if not passed:
            all_passed = False
    
    print("="*50)
    
    if all_passed:
        print("🎉 所有模块验证通过！")
        print("\n下一步建议:")
        print("1. 设置环境变量（数据库密码、Tushare Token等）")
        print("2. 运行 migrate_to_new_architecture.py 查看完整演示")
        print("3. 按照 MIGRATION_GUIDE.md 开始迁移现有代码")
    else:
        print("⚠️  部分模块验证失败，请检查上述错误。")
    
    return all_passed


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)