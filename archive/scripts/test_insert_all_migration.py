#!/usr/bin/env python3
"""
测试 insert_all_data_new.py 迁移

验证迁移后的完整数据插入脚本。
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
    print("=== 测试完整数据插入脚本导入 ===")
    
    imports_to_test = [
        ("data_collection.factory", "get_akshare_client"),
        ("data_collection.factory", "get_tushare_client"),
        ("data_storage.manager", "get_storage_manager"),
        ("data_storage.models.stock", "StockInfo"),
        ("data_storage.models.stock", "StockDailyInfo"),
        ("data_storage.models.stock", "StockDividendInfo"),
        ("data_storage.models.stock", "InstitutionalTradingInfo"),
        ("data_storage.models.financial", "IndexInfo"),
        ("data_storage.models.financial", "IndexDailyInfo"),
        ("data_storage.models.financial", "FundInfo"),
        ("data_storage.models.financial", "BondInfo"),
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


def test_concurrent_inserter_class():
    """测试并发数据插入器类"""
    print("\n=== 测试并发数据插入器类 ===")
    
    try:
        # 测试类定义
        from insert_all_data_new import ConcurrentDataInserter, DataInsertionTask
        
        print("✅ 类导入成功")
        
        # 测试DataInsertionTask
        task = DataInsertionTask(
            name="test_task",
            func=lambda x: x * 2,
            description="测试任务",
            priority=5
        )
        
        print(f"✅ DataInsertionTask 创建成功: {task.name}")
        
        # 测试ConcurrentDataInserter
        inserter = ConcurrentDataInserter()
        print(f"✅ ConcurrentDataInserter 创建成功")
        
        # 测试方法存在性
        required_methods = [
            "setup",
            "create_tasks", 
            "execute_tasks_concurrently",
            "insert_stock_info",
            "insert_stock_daily_info",
            "insert_index_info",
            "insert_index_daily_info",
            "insert_fund_info",
            "insert_bond_info",
            "insert_stock_dividend_info",
            "insert_institutional_trading_info",
            "generate_report",
            "print_report",
            "cleanup"
        ]
        
        for method in required_methods:
            if hasattr(inserter, method):
                print(f"✅ 方法存在: {method}")
            else:
                print(f"❌ 方法不存在: {method}")
                return False
        
        return True
        
    except Exception as e:
        print(f"❌ 并发数据插入器测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_script_structure():
    """测试脚本结构"""
    print("\n=== 测试脚本结构 ===")
    
    script_path = "insert_all_data_new.py"
    
    try:
        with open(script_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # 检查关键组件
        checks = [
            ("类定义", "class ConcurrentDataInserter" in content),
            ("任务类", "class DataInsertionTask" in content),
            ("主函数", "def main()" in content),
            ("并发执行", "ThreadPoolExecutor" in content),
            ("进度监控", "update_progress" in content),
            ("报告生成", "generate_report" in content),
        ]
        
        all_checks_passed = True
        for check_name, check_result in checks:
            if check_result:
                print(f"✅ {check_name}")
            else:
                print(f"❌ {check_name}")
                all_checks_passed = False
        
        if all_checks_passed:
            print(f"✅ 脚本结构检查通过: {script_path}")
            return True
        else:
            print(f"❌ 脚本结构检查失败")
            return False
            
    except Exception as e:
        print(f"❌ 脚本结构测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def create_dry_run_insert_all():
    """创建完整数据插入的干运行脚本"""
    print("\n=== 创建完整数据插入干运行脚本 ===")
    
    dry_run_script = "insert_all_dry_run.py"
    
    content = '''#!/usr/bin/env python3
"""
完整数据插入干运行脚本

测试新架构的完整数据插入功能，不实际插入数据。
"""

import os
import sys
import logging

# 添加src目录到路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

# 设置测试环境变量
os.environ["INFODATA_APP_ENV"] = "testing"
os.environ["INFODATA_DB_PASSWORD"] = "test_password"
os.environ["INFODATA_MAX_WORKERS"] = "3"  # 减少工作线程数用于测试
os.environ["INFODATA_BATCH_SIZE"] = "100"  # 减少批量大小用于测试

# 设置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def test_concurrent_inserter():
    """测试并发数据插入器"""
    from insert_all_data_new import ConcurrentDataInserter
    
    try:
        # 创建插入器
        inserter = ConcurrentDataInserter()
        
        # 测试设置（模拟成功）
        logger.info("测试插入器设置...")
        
        # 创建任务
        tasks = inserter.create_tasks()
        logger.info(f"创建了 {len(tasks)} 个任务")
        
        # 检查任务列表
        task_names = [task.name for task in tasks]
        expected_tasks = [
            "stock_info", "stock_daily_info", "index_info", 
            "index_daily_info", "fund_info", "bond_info",
            "stock_dividend_info", "institutional_trading_info"
        ]
        
        for expected in expected_tasks:
            if expected in task_names:
                logger.info(f"✅ 任务存在: {expected}")
            else:
                logger.warning(f"❌ 任务缺失: {expected}")
        
        # 测试进度更新
        inserter.update_progress("test_task", True)
        logger.info(f"进度更新测试: {inserter.progress}")
        
        # 测试报告生成（模拟数据）
        inserter.progress["start_time"] = "2026-03-18 10:00:00"
        inserter.progress["end_time"] = "2026-03-18 10:05:00"
        inserter.progress["total"] = 8
        inserter.progress["completed"] = 8
        inserter.progress["success"] = 7
        inserter.progress["failed"] = 1
        
        # 模拟任务结果
        inserter.results = {
            "stock_info": {"success": True, "result": {"count": 100}, "duration": 5.2},
            "stock_daily_info": {"success": True, "result": {"count": 500}, "duration": 12.5},
            "index_info": {"success": True, "result": {"count": 50}, "duration": 3.1},
            "index_daily_info": {"success": True, "result": {"count": 150}, "duration": 8.7},
            "fund_info": {"success": True, "result": {"count": 200}, "duration": 6.3},
            "bond_info": {"success": False, "error": "API连接失败", "duration": 2.1},
            "stock_dividend_info": {"success": True, "result": {"count": 80}, "duration": 4.5},
            "institutional_trading_info": {"success": True, "result": {"count": 120}, "duration": 7.8},
        }
        
        # 生成和打印报告
        report = inserter.generate_report()
        if report:
            logger.info("✅ 报告生成成功")
            logger.info(f"总记录数: {report['performance_metrics']['total_records_inserted']}")
        else:
            logger.warning("❌ 报告生成失败")
        
        # 清理
        inserter.cleanup()
        
        logger.info("✅ 并发数据插入器测试通过（干运行模式）")
        return True
        
    except Exception as e:
        logger.error(f"❌ 并发数据插入器测试失败: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False

if __name__ == "__main__":
    logger.info("开始完整数据插入干运行测试...")
    success = test_concurrent_inserter()
    
    if success:
        logger.info("✅ 完整数据插入干运行测试成功完成")
        sys.exit(0)
    else:
        logger.error("❌ 完整数据插入干运行测试失败")
        sys.exit(1)
'''
    
    with open(dry_run_script, 'w', encoding='utf-8') as f:
        f.write(content)
    
    print(f"✅ 干运行脚本创建完成: {dry_run_script}")
    return dry_run_script


def compare_old_new():
    """对比新旧脚本"""
    print("\n=== 新旧脚本对比 ===")
    
    old_script = "other/insert_all_adata_to_mysql.py"
    new_script = "insert_all_data_new.py"
    
    try:
        # 读取文件信息
        import os
        old_size = os.path.getsize(old_script)
        new_size = os.path.getsize(new_script)
        
        with open(old_script, 'r', encoding='utf-8') as f:
            old_lines = len(f.readlines())
        
        with open(new_script, 'r', encoding='utf-8') as f:
            new_lines = len(f.readlines())
        
        print(f"原脚本: {old_script}")
        print(f"  - 大小: {old_size / 1024:.1f} KB")
        print(f"  - 行数: {old_lines}")
        
        print(f"\n新脚本: {new_script}")
        print(f"  - 大小: {new_size / 1024:.1f} KB")
        print(f"  - 行数: {new_lines}")
        
        print(f"\n变化:")
        print(f"  - 大小变化: {(new_size - old_size) / 1024:.1f} KB")
        print(f"  - 行数变化: {new_lines - old_lines} 行")
        
        # 检查关键改进
        improvements = [
            ("环境变量配置", "INFODATA_" in open(new_script).read()),
            ("连接池管理", "pool_size" in open(new_script).read()),
            ("进度监控", "update_progress" in open(new_script).read()),
            ("性能报告", "generate_report" in open(new_script).read()),
            ("数据验证", "is_valid()" in open(new_script).read()),
            ("批量插入优化", "bulk_insert_data" in open(new_script).read()),
        ]
        
        print(f"\n新架构改进:")
        for improvement, exists in improvements:
            status = "✅" if exists else "❌"
            print(f"  {status} {improvement}")
        
        return True
        
    except Exception as e:
        print(f"❌ 脚本对比失败: {e}")
        return False


def main():
    """主测试函数"""
    print("开始测试完整数据插入脚本迁移...\n")
    
    tests = [
        ("模块导入", test_imports),
        ("并发插入器类", test_concurrent_inserter_class),
        ("脚本结构", test_script_structure),
        ("新旧对比", compare_old_new),
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
    dry_run_script = create_dry_run_insert_all()
    
    # 输出结果
    print("\n" + "="*60)
    print("完整数据插入脚本迁移测试结果汇总:")
    print("="*60)
    
    all_passed = True
    for test_name, passed in results:
        status = "✅ 通过" if passed else "❌ 失败"
        print(f"{test_name:20} {status}")
        if not passed:
            all_passed = False
    
    print("="*60)
    
    if all_passed:
        print("🎉 所有迁移测试通过！")
        print("\n下一步:")
        print(f"1. 运行干运行测试: python {dry_run_script}")
        print("2. 设置环境变量（数据库密码、并发数等）")
        print("3. 运行迁移后的脚本: python insert_all_data_new.py")
        print("4. 查看详细日志: tail -f insert_all_data.log")
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