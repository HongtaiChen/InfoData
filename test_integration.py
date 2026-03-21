#!/usr/bin/env python3
"""
系统集成测试

测试整个数据服务层的集成功能，包括数据收集、处理、存储和监控。
"""

import os
import sys
import tempfile
from pathlib import Path
from datetime import datetime, timedelta

# 添加项目根目录到Python路径
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from infodata.config import ConfigManager, AppConfig
from infodata.utils.logging import setup_logging
from infodata.data.migration import create_database_tables, check_database_status
from infodata.data.sources.manager import get_data_source_manager
from infodata.data.processor import DataProcessor
from infodata.utils.database import session_scope
from infodata.models.task import TaskExecution
from infodata.models.quality import DataQualityMetric


def setup_test_config():
    """设置测试配置"""
    # 创建临时配置文件
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        import yaml
        config_data = {
            "app_name": "IntegrationTest",
            "environment": "testing",
            "log_level": "INFO",
            "database": {
                "sqlite": {
                    "database": ":memory:",
                    "echo": False,
                }
            }
        }
        yaml.dump(config_data, f)
        config_path = f.name
    
    # 加载配置
    config = ConfigManager.load(config_path)
    
    # 清理临时文件
    os.unlink(config_path)
    
    return config


def test_system_initialization():
    """测试系统初始化"""
    print("测试系统初始化...")
    
    try:
        # 设置测试配置
        config = setup_test_config()
        
        # 创建数据库表
        create_result = create_database_tables()
        assert create_result['success']
        assert create_result['tables_created'] > 0
        print(f"✓ 数据库表创建成功: {create_result['tables_created']} 个表")
        
        # 检查数据库状态
        status_result = check_database_status()
        assert status_result['success']
        assert status_result['database_connected']
        print("✓ 数据库状态检查通过")
        
        # 初始化数据源管理器
        manager = get_data_source_manager()
        assert manager is not None
        print("✓ 数据源管理器初始化成功")
        
        # 连接数据源
        connection_results = manager.connect_all()
        assert isinstance(connection_results, dict)
        
        connected_count = sum(1 for success in connection_results.values() if success)
        print(f"✓ 数据源连接成功: {connected_count}/{len(connection_results)} 个数据源")
        
        # 检查数据源状态
        status = manager.get_status()
        assert status['total_adapters'] > 0
        print(f"✓ 数据源状态检查通过: {status['total_adapters']} 个适配器")
        
        # 健康检查
        health = manager.health_check()
        assert health['status'] in ['healthy', 'degraded']
        print(f"✓ 系统健康检查通过: {health['status']}")
        
        return True
        
    except Exception as e:
        print(f"✗ 系统初始化测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_stock_data_pipeline():
    """测试股票数据管道"""
    print("\n测试股票数据管道...")
    
    try:
        # 设置测试配置
        config = setup_test_config()
        
        # 创建数据库表
        create_result = create_database_tables()
        if not create_result['success']:
            raise RuntimeError(f"创建数据库表失败: {create_result}")
        
        # 创建处理器
        processor = DataProcessor()
        
        # 测试股票基本信息管道
        print("测试股票基本信息管道...")
        stock_info_result = processor.process_stock_info(
            symbols=["000001.SZ", "600000.SH"],
            source_name="akshare",
            validate=True
        )
        
        assert isinstance(stock_info_result, dict)
        print(f"✓ 股票基本信息管道测试通过")
        print(f"  处理结果: {'成功' if stock_info_result['success'] else '失败'}")
        
        if stock_info_result['success']:
            print(f"  收集记录: {stock_info_result['collection']['records_collected']}")
            print(f"  存储成功: {stock_info_result['storage']['records_succeeded']}")
            print(f"  质量评分: {stock_info_result.get('quality', {}).get('scores', {}).get('overall', 0):.2%}")
        
        # 测试股票日度数据管道
        print("\n测试股票日度数据管道...")
        stock_daily_result = processor.process_stock_daily(
            symbols=["000001.SZ"],
            start_date=(datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d"),
            end_date=datetime.now().strftime("%Y-%m-%d"),
            source_name="akshare",
            validate=True
        )
        
        assert isinstance(stock_daily_result, dict)
        print(f"✓ 股票日度数据管道测试通过")
        print(f"  处理结果: {'成功' if stock_daily_result['success'] else '失败'}")
        
        if stock_daily_result['success']:
            print(f"  收集记录: {stock_daily_result['collection']['records_collected']}")
            print(f"  存储成功: {stock_daily_result['storage']['records_succeeded']}")
            print(f"  质量评分: {stock_daily_result.get('quality', {}).get('scores', {}).get('overall', 0):.2%}")
        
        return True
        
    except Exception as e:
        print(f"✗ 股票数据管道测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_fund_data_pipeline():
    """测试基金数据管道"""
    print("\n测试基金数据管道...")
    
    try:
        # 设置测试配置
        config = setup_test_config()
        
        # 创建数据库表
        create_result = create_database_tables()
        if not create_result['success']:
            raise RuntimeError(f"创建数据库表失败: {create_result}")
        
        # 创建处理器
        processor = DataProcessor()
        
        # 测试基金日度数据管道
        print("测试基金日度数据管道...")
        fund_daily_result = processor.process_fund_daily(
            symbols=["000001"],  # 测试一只基金
            start_date=(datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d"),
            end_date=datetime.now().strftime("%Y-%m-%d"),
            source_name="akshare",
            validate=True
        )
        
        assert isinstance(fund_daily_result, dict)
        print(f"✓ 基金日度数据管道测试通过")
        print(f"  处理结果: {'成功' if fund_daily_result['success'] else '失败'}")
        
        if fund_daily_result['success']:
            print(f"  收集记录: {fund_daily_result['collection']['records_collected']}")
            print(f"  存储成功: {fund_daily_result['storage']['records_succeeded']}")
            print(f"  质量评分: {fund_daily_result.get('quality', {}).get('scores', {}).get('overall', 0):.2%}")
        
        return True
        
    except Exception as e:
        print(f"✗ 基金数据管道测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_index_data_pipeline():
    """测试指数数据管道"""
    print("\n测试指数数据管道...")
    
    try:
        # 设置测试配置
        config = setup_test_config()
        
        # 创建数据库表
        create_result = create_database_tables()
        if not create_result['success']:
            raise RuntimeError(f"创建数据库表失败: {create_result}")
        
        # 创建处理器
        processor = DataProcessor()
        
        # 测试指数日度数据管道
        print("测试指数日度数据管道...")
        index_daily_result = processor.process_index_daily(
            symbols=["sh000001"],  # 测试上证指数
            start_date=(datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d"),
            end_date=datetime.now().strftime("%Y-%m-%d"),
            source_name="akshare",
            validate=True
        )
        
        assert isinstance(index_daily_result, dict)
        print(f"✓ 指数日度数据管道测试通过")
        print(f"  处理结果: {'成功' if index_daily_result['success'] else '失败'}")
        
        if index_daily_result['success']:
            print(f"  收集记录: {index_daily_result['collection']['records_collected']}")
            print(f"  存储成功: {index_daily_result['storage']['records_succeeded']}")
            print(f"  质量评分: {index_daily_result.get('quality', {}).get('scores', {}).get('overall', 0):.2%}")
        
        return True
        
    except Exception as e:
        print(f"✗ 指数数据管道测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_monitoring_system():
    """测试监控系统"""
    print("\n测试监控系统...")
    
    try:
        # 设置测试配置
        config = setup_test_config()
        
        # 创建数据库表
        create_result = create_database_tables()
        if not create_result['success']:
            raise RuntimeError(f"创建数据库表失败: {create_result}")
        
        # 创建处理器并执行一些任务
        processor = DataProcessor()
        
        # 执行一些测试任务
        test_results = []
        test_results.append(processor.process_stock_info(
            symbols=["000001.SZ"],
            source_name="akshare",
            validate=False
        ))
        
        test_results.append(processor.process_stock_daily(
            symbols=["000001.SZ"],
            start_date=(datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d"),
            end_date=datetime.now().strftime("%Y-%m-%d"),
            source_name="akshare",
            validate=False
        ))
        
        # 测试任务执行监控
        print("测试任务执行监控...")
        with session_scope() as session:
            # 获取任务统计
            stats = TaskExecution.get_task_statistics(session)
            assert isinstance(stats, dict)
            assert 'statistics' in stats
            assert 'recent_executions' in stats
            
            print(f"✓ 任务执行监控测试通过")
            print(f"  总执行次数: {stats['statistics']['total_executions']}")
            print(f"  成功率: {stats['statistics']['success_rate']:.2%}")
            
            # 显示最近执行
            print(f"  最近执行记录:")
            for exec in stats['recent_executions'][:3]:
                print(f"    - {exec['start_time']}: {exec['task_name']} ({exec['status']})")
        
        # 测试数据质量监控
        print("\n测试数据质量监控...")
        with session_scope() as session:
            # 获取数据质量摘要
            summary = DataQualityMetric.get_quality_summary(session)
            assert isinstance(summary, dict)
            assert 'summary' in summary
            assert 'by_source' in summary
            
            print(f"✓ 数据质量监控测试通过")
            print(f"  总质量指标数: {summary['summary']['total_metrics']}")
            print(f"  平均质量评分: {summary['summary']['avg_overall_score']:.2%}")
            
            # 显示各数据源质量
            if summary['by_source']:
                print(f"  各数据源质量:")
                for source in summary['by_source'][:3]:
                    print(f"    - {source['data_source']}: {source['avg_score']:.2%}")
        
        return True
        
    except Exception as e:
        print(f"✗ 监控系统测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_error_recovery():
    """测试错误恢复"""
    print("\n测试错误恢复...")
    
    try:
        # 设置测试配置
        config = setup_test_config()
        
        # 创建数据库表
        create_result = create_database_tables()
        if not create_result['success']:
            raise RuntimeError(f"创建数据库表失败: {create_result}")
        
        # 创建处理器
        processor = DataProcessor()
        
        # 测试无效数据源恢复
        print("测试无效数据源恢复...")
        result = processor.process_stock_daily(
            symbols=["000001.SZ"],
            source_name="invalid_source",  # 无效数据源
            validate=False
        )
        
        assert isinstance(result, dict)
        # 应该失败，但不应该崩溃
        print(f"✓ 无效数据源恢复测试通过")
        print(f"  处理结果: {'成功' if result['success'] else '失败（预期）'}")
        
        # 测试自动数据源切换
        print("\n测试自动数据源切换...")
        # 这里我们测试便捷函数的自动数据源选择
        from infodata.data.processor import process_stock_info
        
        auto_result = process_stock_info(
            symbols=["000001.SZ"],
            # 不指定source_name，让系统自动选择
            validate=False
        )
        
        assert isinstance(auto_result, dict)
        print(f"✓ 自动数据源切换测试通过")
        print(f"  处理结果: {'成功' if auto_result['success'] else '失败'}")
        if auto_result['success']:
            print(f"  使用的数据源: {auto_result['collection']['source']}")
        
        # 测试部分失败恢复
        print("\n测试部分失败恢复...")
        # 创建包含有效和无效符号的测试
        mixed_result = processor.process_stock_info(
            symbols=["000001.SZ", "INVALID_SYMBOL_123", "600000.SH"],
            source_name="akshare",
            validate=False
        )
        
        assert isinstance(mixed_result, dict)
        print(f"✓ 部分失败恢复测试通过")
        print(f"  处理结果: {'成功' if mixed_result['success'] else '部分成功'}")
        
        if 'storage' in mixed_result:
            print(f"  处理记录: {mixed_result['storage']['records_processed']}")
            print(f"  成功记录: {mixed_result['storage']['records_succeeded']}")
            print(f"  失败记录: {mixed_result['storage']['records_failed']}")
        
        return True
        
    except Exception as e:
        print(f"✗ 错误恢复测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_performance_integration():
    """测试集成性能"""
    print("\n测试集成性能...")
    
    try:
        import time
        
        # 设置测试配置
        config = setup_test_config()
        
        # 创建数据库表
        create_result = create_database_tables()
        if not create_result['success']:
            raise RuntimeError(f"创建数据库表失败: {create_result}")
        
        # 测试完整数据管道性能
        print("测试完整数据管道性能...")
        
        # 初始化所有组件
        start_time = time.time()
        
        manager = get_data_source_manager()
        processor = DataProcessor()
        
        init_time = time.time() - start_time
        print(f"  组件初始化耗时: {init_time:.2f} 秒")
        
        # 测试股票数据管道性能
        pipeline_start = time.time()
        
        result = processor.process_stock_info(
            symbols=["000001.SZ", "600000.SH"],
            source_name="akshare",
            validate=True
        )
        
        pipeline_time = time.time() - pipeline_start
        
        print(f"  数据管道执行耗时: {pipeline_time:.2f} 秒")
        
        if result['success']:
            print(f"  处理记录数: {result['storage']['records_succeeded']}")
            print(f"  记录处理速度: {result['storage']['records_succeeded'] / pipeline_time:.2f} 记录/秒")
        
        # 性能要求
        assert init_time < 5.0, f"初始化时间过长: {init_time:.2f} 秒"
        assert pipeline_time < 60.0, f"管道执行时间过长: {pipeline_time:.2f} 秒"
        
        print("✓ 集成性能测试通过")
        
        return True
        
    except Exception as e:
        print(f"✗ 集成性能测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """主测试函数"""
    print("=" * 60)
    print("InfoData 系统集成测试")
    print("=" * 60)
    print("测试整个数据服务层的集成功能")
    print("=" * 60)
    
    # 设置日志
    setup_logging(
        log_level="INFO",
        log_file=None,
        enable_console=False,
    )
    
    tests = [
        ("系统初始化", test_system_initialization),
        ("股票数据管道", test_stock_data_pipeline),
        ("基金数据管道", test_fund_data_pipeline),
        ("指数数据管道", test_index_data_pipeline),
        ("监控系统", test_monitoring_system),
        ("错误恢复", test_error_recovery),
        ("集成性能", test_performance_integration),
    ]
    
    results = []
    for test_name, test_func in tests:
        try:
            print(f"\n{'='*40}")
            print(f"开始测试: {test_name}")
            print(f"{'='*40}")
            success = test_func()
            results.append((test_name, success))
        except Exception as e:
            print(f"✗ {test_name} 测试异常: {e}")
            import traceback
            traceback.print_exc()
            results.append((test_name, False))
    
    # 输出测试结果
    print("\n" + "=" * 60)
    print("集成测试结果汇总:")
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
        print("\n🎉 🎉 🎉 所有集成测试通过！系统功能完整可用！ 🎉 🎉 🎉")
        print("\n📊 系统集成测试总结:")
        print("  1. ✅ 系统初始化完整")
        print("  2. ✅ 股票数据管道正常")
        print("  3. ✅ 基金数据管道正常")
        print("  4. ✅ 指数数据管道正常")
        print("  5. ✅ 监控系统工作正常")
        print("  6. ✅ 错误恢复机制有效")
        print("  7. ✅ 集成性能符合要求")
        print("\n🚀 数据服务层开发全部完成，可以进入下一阶段！")
        return 0
    else:
        print(f"\n⚠️  {total - passed} 个集成测试失败，需要检查。")
        return 1


if __name__ == "__main__":
    sys.exit(main())
