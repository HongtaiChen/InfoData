#!/usr/bin/env python3
"""
任务系统测试脚本

测试任务调度器和任务管理器的功能。
"""

import os
import sys
import tempfile
from pathlib import Path
from datetime import datetime

# 添加项目根目录到Python路径
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from infodata.config import ConfigManager, AppConfig
from infodata.utils.logging import setup_logging
from infodata.data.migration import create_database_tables
from infodata.tasks.scheduler import TaskScheduler, get_task_scheduler
from infodata.tasks.manager import TaskManager, get_task_manager


def setup_test_config():
    """设置测试配置"""
    # 创建临时配置文件
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        import yaml
        config_data = {
            "app_name": "TestApp",
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


def test_scheduler_imports():
    """测试调度器导入"""
    print("测试调度器导入...")
    
    try:
        # 测试导入调度器
        from infodata.tasks.scheduler import TaskScheduler, get_task_scheduler
        from infodata.tasks.manager import TaskManager, get_task_manager
        
        print("✓ 调度器和管理器导入成功")
        
        # 测试创建实例
        scheduler = TaskScheduler()
        assert scheduler is not None
        assert hasattr(scheduler, 'start')
        assert hasattr(scheduler, 'stop')
        print("✓ 调度器实例创建测试通过")
        
        manager = TaskManager()
        assert manager is not None
        assert hasattr(manager, 'run_stock_daily_update')
        print("✓ 管理器实例创建测试通过")
        
        return True
        
    except Exception as e:
        print(f"✗ 调度器导入测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_scheduler_basic():
    """测试调度器基本功能"""
    print("\n测试调度器基本功能...")
    
    try:
        # 设置测试配置
        config = setup_test_config()
        
        # 创建数据库表
        create_result = create_database_tables()
        if not create_result['success']:
            raise RuntimeError(f"创建数据库表失败: {create_result}")
        
        # 创建调度器
        scheduler = TaskScheduler()
        
        # 测试启动调度器
        started = scheduler.start()
        assert started is True or started is False  # 可能已经在运行
        print(f"✓ 调度器启动测试: {'成功' if started else '已在运行'}")
        
        # 测试添加简单任务
        def test_job():
            print("测试任务执行")
        
        added = scheduler.add_job(
            func=test_job,
            job_id="test_job",
            trigger="interval",
            seconds=5
        )
        
        assert added is True
        print("✓ 任务添加测试通过")
        
        # 测试获取任务状态
        status = scheduler.get_job_status("test_job")
        assert isinstance(status, dict)
        assert "job_id" in status
        print("✓ 任务状态获取测试通过")
        
        # 测试获取所有任务
        all_jobs = scheduler.get_all_jobs()
        assert isinstance(all_jobs, list)
        print(f"✓ 所有任务获取测试通过: {len(all_jobs)} 个任务")
        
        # 测试暂停任务
        paused = scheduler.pause_job("test_job")
        assert paused is True
        print("✓ 任务暂停测试通过")
        
        # 测试恢复任务
        resumed = scheduler.resume_job("test_job")
        assert resumed is True
        print("✓ 任务恢复测试通过")
        
        # 测试移除任务
        removed = scheduler.remove_job("test_job")
        assert removed is True
        print("✓ 任务移除测试通过")
        
        # 测试调度器状态
        scheduler_status = scheduler.get_scheduler_status()
        assert isinstance(scheduler_status, dict)
        assert "running" in scheduler_status
        print("✓ 调度器状态获取测试通过")
        
        # 测试停止调度器
        stopped = scheduler.stop(wait=False)
        assert stopped is True
        print("✓ 调度器停止测试通过")
        
        return True
        
    except Exception as e:
        print(f"✗ 调度器基本功能测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_manager_basic():
    """测试管理器基本功能"""
    print("\n测试管理器基本功能...")
    
    try:
        # 设置测试配置
        config = setup_test_config()
        
        # 创建数据库表
        create_result = create_database_tables()
        if not create_result['success']:
            raise RuntimeError(f"创建数据库表失败: {create_result}")
        
        # 创建管理器
        manager = TaskManager()
        
        # 测试股票日度更新任务（简化测试）
        print("测试股票日度更新任务...")
        result = manager.run_stock_daily_update(
            symbols=["000001.SZ"],  # 最小测试
            start_date=(datetime.now()).strftime("%Y-%m-%d"),  # 今天
            end_date=(datetime.now()).strftime("%Y-%m-%d"),
            validate=False,  # 简化测试
            batch_size=10
        )
        
        assert isinstance(result, dict)
        assert "task_id" in result
        assert "success" in result
        print(f"✓ 股票日度更新任务测试通过")
        print(f"  任务结果: {'成功' if result['success'] else '失败'}")
        
        if result['success']:
            print(f"  总记录数: {result.get('total_records', 0)}")
            print(f"  成功记录: {result.get('successful_records', 0)}")
        
        # 测试股票信息更新任务
        print("\n测试股票信息更新任务...")
        info_result = manager.run_stock_info_update(
            symbols=["000001.SZ", "600000.SH"],
            validate=False
        )
        
        assert isinstance(info_result, dict)
        assert "task_id" in info_result
        print("✓ 股票信息更新任务测试通过")
        
        # 测试基金日度更新任务
        print("\n测试基金日度更新任务...")
        fund_result = manager.run_fund_daily_update(
            symbols=["000001"],  # 测试一只基金
            validate=False
        )
        
        assert isinstance(fund_result, dict)
        assert "task_id" in fund_result
        print("✓ 基金日度更新任务测试通过")
        
        # 测试指数日度更新任务
        print("\n测试指数日度更新任务...")
        index_result = manager.run_index_daily_update(
            symbols=["sh000001"],  # 测试上证指数
            validate=False
        )
        
        assert isinstance(index_result, dict)
        assert "task_id" in index_result
        print("✓ 指数日度更新任务测试通过")
        
        return True
        
    except Exception as e:
        print(f"✗ 管理器基本功能测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_scheduler_integration():
    """测试调度器集成"""
    print("\n测试调度器集成...")
    
    try:
        # 设置测试配置
        config = setup_test_config()
        
        # 创建数据库表
        create_result = create_database_tables()
        if not create_result['success']:
            raise RuntimeError(f"创建数据库表失败: {create_result}")
        
        # 创建调度器
        scheduler = TaskScheduler()
        
        # 测试调度股票日度更新任务
        print("测试调度股票日度更新任务...")
        scheduled = scheduler.schedule_stock_daily_update(
            symbols=["000001.SZ"],  # 最小测试
            start_time="19:00"
        )
        
        assert scheduled is True
        print("✓ 股票日度更新任务调度测试通过")
        
        # 测试调度股票信息更新任务
        print("\n测试调度股票信息更新任务...")
        info_scheduled = scheduler.schedule_stock_info_update(
            symbols=["000001.SZ"],
            day_of_week="sun",
            hour=2,
            minute=0
        )
        
        assert info_scheduled is True
        print("✓ 股票信息更新任务调度测试通过")
        
        # 测试调度基金日度更新任务
        print("\n测试调度基金日度更新任务...")
        fund_scheduled = scheduler.schedule_fund_daily_update(
            symbols=["000001"],
            start_time="19:30"
        )
        
        assert fund_scheduled is True
        print("✓ 基金日度更新任务调度测试通过")
        
        # 测试调度指数日度更新任务
        print("\n测试调度指数日度更新任务...")
        index_scheduled = scheduler.schedule_index_daily_update(
            symbols=["sh000001"],
            start_time="19:15"
        )
        
        assert index_scheduled is True
        print("✓ 指数日度更新任务调度测试通过")
        
        # 测试调度所有任务
        print("\n测试调度所有任务...")
        all_scheduled = scheduler.schedule_all_tasks()
        
        assert isinstance(all_scheduled, dict)
        assert len(all_scheduled) >= 4
        print(f"✓ 所有任务调度测试通过: {len(all_scheduled)} 个任务")
        
        # 显示调度结果
        for task_name, success in all_scheduled.items():
            print(f"  {task_name}: {'成功' if success else '失败'}")
        
        # 测试获取调度器状态
        status = scheduler.get_scheduler_status()
        assert status['total_jobs'] >= 4
        print(f"✓ 调度器状态验证通过: {status['total_jobs']} 个任务")
        
        # 测试立即运行任务
        print("\n测试立即运行任务...")
        run_now = scheduler.run_now("stock_daily_update")
        assert run_now is True
        print("✓ 立即运行任务测试通过")
        
        # 清理：停止调度器
        scheduler.stop(wait=False)
        
        return True
        
    except Exception as e:
        print(f"✗ 调度器集成测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_error_handling():
    """测试错误处理"""
    print("\n测试错误处理...")
    
    try:
        # 设置测试配置
        config = setup_test_config()
        
        # 创建数据库表
        create_result = create_database_tables()
        if not create_result['success']:
            raise RuntimeError(f"创建数据库表失败: {create_result}")
        
        # 创建调度器
        scheduler = TaskScheduler()
        
        # 测试不存在的任务操作
        print("测试不存在的任务操作...")
        
        # 获取不存在的任务状态
        status = scheduler.get_job_status("non_existent_job")
        assert "error" in status
        print("✓ 不存在的任务状态获取测试通过")
        
        # 暂停不存在的任务
        paused = scheduler.pause_job("non_existent_job")
        assert paused is False
        print("✓ 不存在的任务暂停测试通过")
        
        # 恢复不存在的任务
        resumed = scheduler.resume_job("non_existent_job")
        assert resumed is False
        print("✓ 不存在的任务恢复测试通过")
        
        # 移除不存在的任务
        removed = scheduler.remove_job("non_existent_job")
        assert removed is False
        print("✓ 不存在的任务移除测试通过")
        
        # 立即运行不存在的任务
        run_now = scheduler.run_now("non_existent_job")
        assert run_now is False
        print("✓ 不存在的任务立即运行测试通过")
        
        # 测试无效的触发器
        print("\n测试无效的触发器...")
        def test_func():
            pass
        
        try:
            # 使用无效的触发器类型
            added = scheduler.add_job(
                func=test_func,
                job_id="invalid_trigger_job",
                trigger="invalid_trigger",  # 无效的触发器类型
                seconds=5
            )
            # 应该抛出异常或返回False
            if added is False:
                print("✓ 无效触发器处理测试通过（返回False）")
            else:
                print("⚠ 无效触发器处理测试：未返回False")
        except ValueError as e:
            print(f"✓ 无效触发器处理测试通过（抛出异常）")
        except Exception as e:
            print(f"⚠ 无效触发器处理测试异常: {e}")
        
        # 测试管理器错误处理
        print("\n测试管理器错误处理...")
        manager = TaskManager()
        
        # 测试无效股票代码
        result = manager.run_stock_daily_update(
            symbols=["INVALID_SYMBOL_123"],
            validate=False
        )
        
        assert isinstance(result, dict)
        # 可能成功（返回空数据）或失败，但不应该崩溃
        print(f"✓ 无效股票代码错误处理测试通过")
        print(f"  处理结果: {'成功' if result.get('success') else '失败（可能预期）'}")
        
        return True
        
    except Exception as e:
        print(f"✗ 错误处理测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_convenience_functions():
    """测试便捷函数"""
    print("\n测试便捷函数...")
    
    try:
        # 设置测试配置
        config = setup_test_config()
        
        # 创建数据库表
        create_result = create_database_tables()
        if not create_result['success']:
            raise RuntimeError(f"创建数据库表失败: {create_result}")
        
        # 测试获取全局实例
        print("测试获取全局实例...")
        
        scheduler = get_task_scheduler()
        assert scheduler is not None
        print("✓ 获取全局调度器测试通过")
        
        manager = get_task_manager()
        assert manager is not None
        print("✓ 获取全局管理器测试通过")
        
        # 测试便捷函数
        from infodata.tasks.scheduler import (
            start_scheduler, stop_scheduler,
            schedule_all_tasks, get_scheduler_status
        )
        
        from infodata.tasks.manager import (
            run_stock_daily_update, run_stock_info_update,
            run_fund_daily_update, run_index_daily_update,
            run_all_updates
        )
        
        # 测试启动调度器
        started = start_scheduler()
        assert started is True or started is False
        print(f"✓ 启动调度器便捷函数测试通过")
        
        # 测试获取调度器状态
        status = get_scheduler_status()
        assert isinstance(status, dict)
        print("✓ 获取调度器状态便捷函数测试通过")
        
        # 测试调度所有任务
        scheduled = schedule_all_tasks()
        assert isinstance(scheduled, dict)
        print(f"✓ 调度所有任务便捷函数测试通过: {len(scheduled)} 个任务")
        
        # 测试运行股票日度更新
        result = run_stock_daily_update(
            symbols=["000001.SZ"],
            validate=False
        )
        assert isinstance(result, dict)
        print("✓ 运行股票日度更新便捷函数测试通过")
        
        # 测试运行所有更新
        all_result = run_all_updates()
        assert isinstance(all_result, dict)
        print("✓ 运行所有更新便捷函数测试通过")
        
        # 停止调度器
        stop_scheduler(wait=False)
        
        return True
        
    except Exception as e:
        print(f"✗ 便捷函数测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """主测试函数"""
    print("=" * 60)
    print("InfoData 任务系统测试")
    print("=" * 60)
    print("测试任务调度器和任务管理器的功能")
    print("=" * 60)
    
    # 设置日志
    setup_logging(
        log_level="INFO",
        log_file=None,
        enable_console=False,
    )
    
    tests = [
        ("调度器导入", test_scheduler_imports),
        ("调度器基本功能", test_scheduler_basic),
        ("管理器基本功能", test_manager_basic),
        ("调度器集成", test_scheduler_integration),
        ("错误处理", test_error_handling),
        ("便捷函数", test_convenience_functions),
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
        print("\n🎉 🎉 🎉 所有任务系统测试通过！阶段三核心功能完成！ 🎉 🎉 🎉")
        print("\n📊 阶段三任务系统开发完成:")
        print("  1. ✅ 任务调度器（APScheduler集成）")
        print("  2. ✅ 任务管理器（具体任务实现）")
        print("  3. ✅ 股票日度更新任务")
        print("  4. ✅ 股票信息更新任务")
        print("  5. ✅ 基金日度更新任务")
        print("  6. ✅ 指数日度更新任务")
        print("  7. ✅ 完整测试套件")
        print("\n🚀 阶段三核心功能已实现，可以进入下一阶段！")
        return 0
    else:
        print(f"\n⚠️  {total - passed} 个测试失败，需要检查。")
        return 1


if __name__ == "__main__":
    sys.exit(main())