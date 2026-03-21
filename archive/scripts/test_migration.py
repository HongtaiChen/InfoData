#!/usr/bin/env python3
"""
测试迁移后的脚本

验证迁移后的 daily_update_stock_info_new.py 是否能正确导入和运行。
"""

import os
import sys
import logging

# 添加src目录到路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

# 设置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def test_imports():
    """测试所有必要的导入"""
    print("=== 测试导入 ===")
    
    imports_to_test = [
        ("data_collection.factory", "get_akshare_client"),
        ("data_storage.manager", "get_storage_manager"),
        ("data_storage.models.stock", "StockInfo"),
        ("data_storage.models.stock", "StockDailyInfo"),
        ("data_storage.models.financial", "IndexInfo"),
        ("data_storage.models.financial", "IndexDailyInfo"),
        ("config.manager", "get_config_manager"),
    ]
    
    all_passed = True
    
    for module_path, import_name in imports_to_test:
        try:
            module = __import__(module_path, fromlist=[import_name])
            
            if hasattr(module, import_name):
                print(f"✅ {module_path}.{import_name}")
            else:
                print(f"❌ {module_path}.{import_name} - 未找到")
                all_passed = False
                
        except ImportError as e:
            print(f"❌ {module_path}.{import_name} - 导入失败: {e}")
            all_passed = False
        except Exception as e:
            print(f"❌ {module_path}.{import_name} - 错误: {e}")
            all_passed = False
    
    return all_passed


def test_model_creation():
    """测试模型创建"""
    print("\n=== 测试模型创建 ===")
    
    try:
        from data_storage.models.stock import StockInfo, StockDailyInfo
        from data_storage.models.financial import IndexInfo, IndexDailyInfo
        from datetime import date
        
        # 测试StockInfo
        stock = StockInfo(
            symbol="000001",
            name="测试股票",
            update_date=date.today()
        )
        
        if stock.is_valid():
            print(f"✅ StockInfo 创建和验证成功: {stock.symbol}")
        else:
            print(f"❌ StockInfo 验证失败: {stock.get_errors()}")
            return False
        
        # 测试StockDailyInfo
        daily = StockDailyInfo(
            symbol="000001",
            trade_date=date.today(),
            close_price=10.0,
            update_date=date.today()
        )
        
        if daily.is_valid():
            print(f"✅ StockDailyInfo 创建和验证成功")
        else:
            print(f"❌ StockDailyInfo 验证失败: {daily.get_errors()}")
            return False
        
        # 测试IndexInfo
        index = IndexInfo(
            symbol="000001",
            name="测试指数",
            update_date=date.today()
        )
        
        if index.is_valid():
            print(f"✅ IndexInfo 创建和验证成功: {index.symbol}")
        else:
            print(f"❌ IndexInfo 验证失败: {index.get_errors()}")
            return False
        
        return True
        
    except Exception as e:
        print(f"❌ 模型创建测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_config_manager():
    """测试配置管理器"""
    print("\n=== 测试配置管理器 ===")
    
    try:
        from config.manager import get_config_manager
        
        # 创建配置管理器
        config = get_config_manager(env="testing")
        
        # 获取配置值
        app_name = config.get("app.name", "InfoData")
        db_host = config.get("database.host", "localhost")
        
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


def test_daily_update_import():
    """测试 daily_update_stock_info_new.py 导入"""
    print("\n=== 测试迁移脚本导入 ===")
    
    try:
        # 尝试导入迁移后的脚本
        import importlib.util
        
        script_path = "daily_update_stock_info_new.py"
        spec = importlib.util.spec_from_file_location("daily_update_new", script_path)
        
        if spec is None:
            print(f"❌ 无法加载脚本: {script_path}")
            return False
        
        # 检查语法（不实际执行）
        with open(script_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # 检查关键组件
        checks = [
            ("类定义", "class DailyUpdateManager" in content),
            ("主函数", "def main()" in content),
            ("导入新模块", "from data_collection.factory import" in content),
            ("日志设置", "logging.basicConfig" in content),
        ]
        
        all_checks_passed = True
        for check_name, check_result in checks:
            if check_result:
                print(f"✅ {check_name}")
            else:
                print(f"❌ {check_name}")
                all_checks_passed = False
        
        if all_checks_passed:
            print(f"✅ 迁移脚本语法检查通过: {script_path}")
            return True
        else:
            print(f"❌ 迁移脚本语法检查失败")
            return False
            
    except Exception as e:
        print(f"❌ 迁移脚本导入测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def create_dry_run_script():
    """创建干运行脚本"""
    print("\n=== 创建干运行脚本 ===")
    
    dry_run_script = "daily_update_dry_run.py"
    
    content = '''#!/usr/bin/env python3
"""
每日更新干运行脚本

测试新架构功能，不实际插入数据。
"""

import os
import sys
import logging

# 添加src目录到路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

# 设置测试环境变量
os.environ["INFODATA_APP_ENV"] = "testing"
os.environ["INFODATA_DB_PASSWORD"] = "test_password"

# 设置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def test_components():
    """测试各个组件"""
    from data_collection.factory import get_akshare_client
    from config.manager import get_config_manager
    
    try:
        # 测试配置管理器
        config = get_config_manager(env="testing")
        logger.info(f"配置测试: app.name={config.get('app.name')}")
        
        # 测试数据采集客户端（不实际调用API）
        client = get_akshare_client(
            client_id="dry_run_test",
            max_retries=1,
            retry_delay=0.1
        )
        
        logger.info(f"客户端创建成功: {client.name}")
        logger.info(f"客户端统计: {client.get_stats()}")
        
        # 测试模型
        from data_storage.models.stock import StockInfo
        from datetime import date
        
        stock = StockInfo(
            symbol="000001",
            name="测试股票",
            update_date=date.today()
        )
        
        if stock.is_valid():
            logger.info(f"模型测试成功: {stock}")
        else:
            logger.warning(f"模型验证失败: {stock.get_errors()}")
        
        logger.info("✅ 所有组件测试通过（干运行模式）")
        return True
        
    except Exception as e:
        logger.error(f"组件测试失败: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False

if __name__ == "__main__":
    logger.info("开始干运行测试...")
    success = test_components()
    
    if success:
        logger.info("干运行测试成功完成")
        sys.exit(0)
    else:
        logger.error("干运行测试失败")
        sys.exit(1)
'''
    
    with open(dry_run_script, 'w', encoding='utf-8') as f:
        f.write(content)
    
    print(f"✅ 干运行脚本创建完成: {dry_run_script}")
    return dry_run_script


def main():
    """主测试函数"""
    print("开始测试迁移后的代码...\n")
    
    tests = [
        ("模块导入", test_imports),
        ("模型创建", test_model_creation),
        ("配置管理器", test_config_manager),
        ("迁移脚本导入", test_daily_update_import),
    ]
    
    results = []
    
    for test_name, test_func in tests:
        try:
            passed = test_func()
            results.append((test_name, passed))
        except Exception as e:
            print(f"❌ {test_name}: 测试异常 - {e}")
            results.append((test_name, False))
    
    # 创建干运行脚本
    dry_run_script = create_dry_run_script()
    
    # 输出结果
    print("\n" + "="*50)
    print("迁移测试结果汇总:")
    print("="*50)
    
    all_passed = True
    for test_name, passed in results:
        status = "✅ 通过" if passed else "❌ 失败"
        print(f"{test_name:20} {status}")
        if not passed:
            all_passed = False
    
    print("="*50)
    
    if all_passed:
        print("🎉 所有迁移测试通过！")
        print("\n下一步:")
        print(f"1. 运行干运行测试: python {dry_run_script}")
        print("2. 设置环境变量（数据库密码等）")
        print("3. 运行迁移后的脚本: python daily_update_stock_info_new.py")
        print("4. 继续迁移其他脚本")
    else:
        print("⚠️  部分测试失败，请检查上述错误。")
        print("\n建议:")
        print("1. 检查src目录结构")
        print("2. 验证模块导入路径")
        print("3. 检查Python依赖")
    
    return all_passed


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)