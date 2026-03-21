#!/usr/bin/env python3
"""
任务系统测试脚本

测试任务基类、调度器和任务管理器的完整功能。
"""

import os
import sys
import time
import threading
from pathlib import Path
from datetime import datetime, timedelta

# 添加项目根目录到Python路径
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from infodata.config import ConfigManager, AppConfig, SchedulerConfig, TaskScheduleConfig, TaskConfig
from infodata.utils.logging import setup_logging, get_logger
from infodata.tasks import (
    BaseTask, TaskConfig as TaskConfigModel, TaskResult, TaskStatus, TaskPriority,
    TaskFactory, SimpleTask, TaskScheduler, TaskManager, register_task, execute_task
)


class TestTask(BaseTask):
    """测试任务"""
    
    def __init__(self, action: str = "success", delay: float = 0.1, **kwargs):
        """
        初始化测试任务
        
        Args:
            action: 执行动作（success, error, timeout）
            delay: 执行延迟（秒）
            **kwargs: 额外参数
        """
        super().__init__(**kwargs)
        self.action = action
        self.delay = delay
        self._progress = 0.0
    
    def _execute(self) -> TaskResult:
        """执行测试任务"""
        self.logger.info(f"开始执行测试任务: {self.action}")
        
        if self.action == "success":
            # 模拟成功任务
            for i in range(10):
                time.sleep(self.delay)
                self._progress = (i + 1) * 10
                self.logger.progress(i + 1, 10, "任务进度")
            
            return self._create_result(
                TaskStatus.SUCCESS,
                data={"message": "任务执行成功", "action": self.action},
                metrics={"iterations": 10, "delay": self.delay},
            )
        
        elif self.action == "error":
            # 模拟错误任务
            time.sleep(self.delay)
            raise ValueError("这是模拟的任务错误")
        
        elif self.action == "timeout":
            # 模拟超时任务
            time.sleep(self.delay * 20)  # 超过默认超时时间
            return self._create_result(TaskStatus.SUCCESS)
        
        else:
            raise ValueError(f"未知的动作: {self.action}")
    
    def get_progress(self) -> float:
        """获取任务进度"""
        return self._progress


def test_task_base_class():
    """测试任务基类"""
    print("测试任务基类...")
    
    try:
        # 测试成功任务
        task = TestTask(action="success", delay=0.01)
        result = task.execute()
        
        assert result.status == TaskStatus.SUCCESS
        assert result.data["message"] == "任务执行成功"
        print("✓ 成功任务测试通过")
        
        # 测试错误任务
        task = TestTask(action="error", delay=0.01)
        result = task.execute()
        
        assert result.status == TaskStatus.FAILED
        assert "模拟的任务错误" in result.error
        print("✓ 错误任务测试通过")
        
        # 测试任务配置
        config = TaskConfigModel(
            timeout=1,
            max_retries=2,
            retry_delay=0.1,
            priority=TaskPriority.HIGH,
        )
        
        task = TestTask(action="success", config=config, delay=0.01)
        assert task.config.timeout == 1
        assert task.config.max_retries == 2
        assert task.config.priority == TaskPriority.HIGH
        print("✓ 任务配置测试通过")
        
        # 测试任务工厂
        TaskFactory.register(TestTask)
        task_from_factory = TaskFactory.create("TestTask", action="success")
        assert isinstance(task_from_factory, TestTask)
        print("✓ 任务工厂测试通过")
        
        return True
        
    except Exception as e:
        print(f"✗ 任务基类测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_task_scheduler():
    """测试任务调度器"""
    print("\n测试任务调度器...")
    
    try:
        # 创建调度器配置
        config = SchedulerConfig(
            timezone="Asia/Shanghai",
            max_instances=5,
            misfire_grace_time=60,
        )
        
        # 创建调度器
        scheduler = TaskScheduler(config)
        scheduler.initialize()
        
        # 测试调度器状态
        status = scheduler.get_status()
        assert status["status"] == "stopped"
        print("✓ 调度器初始化测试通过")
        
        # 启动调度器
        scheduler.start()
        time.sleep(0.5)  # 等待调度器启动
        
        status = scheduler.get_status()
        assert status["status"] == "running"
        print("✓ 调度器启动测试通过")
        
        # 创建任务
        task = TestTask(action="success", delay=0.1)
        
        # 创建调度配置
        schedule_config = TaskScheduleConfig(
            schedule="* * * * *",  # 每分钟执行
            timeout=30,
            retry_count=1,
        )
        
        # 添加任务到调度器
        job_id = scheduler.add_task(task, schedule_config)
        assert job_id == task.task_id
        print("✓ 添加任务测试通过")
        
        # 获取任务信息
        task_info = scheduler.get_task(job_id)
        assert task_info is not None
        assert task_info["id"] == job_id
        print("✓ 获取任务信息测试通过")
        
        # 列出所有任务
        tasks = scheduler.list_tasks()
        assert len(tasks) == 1
        assert tasks[0]["id"] == job_id
        print("✓ 列出任务测试通过")
        
        # 立即运行任务
        result = scheduler.run_task_now(job_id)
        assert result.status == TaskStatus.SUCCESS
        print("✓ 立即运行任务测试通过")
        
        # 获取任务结果
        stored_result = scheduler.get_task_result(job_id)
        assert stored_result is not None
        assert stored_result.task_id == job_id
        print("✓ 获取任务结果测试通过")
        
        # 移除任务
        scheduler.remove_task(job_id)
        tasks = scheduler.list_tasks()
        assert len(tasks) == 0
        print("✓ 移除任务测试通过")
        
        # 关闭调度器
        scheduler.shutdown()
        time.sleep(0.5)  # 等待调度器关闭
        
        status = scheduler.get_status()
        assert status["status"] == "stopped"
        print("✓ 调度器关闭测试通过")
        
        return True
        
    except Exception as e:
        print(f"✗ 任务调度器测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_task_manager():
    """测试任务管理器"""
    print("\n测试任务管理器...")
    
    try:
        # 创建调度器配置
        scheduler_config = SchedulerConfig(
            timezone="Asia/Shanghai",
            max_instances=5,
        )
        
        # 创建调度器
        scheduler = TaskScheduler(scheduler_config)
        scheduler.initialize()
        
        # 创建任务管理器
        manager = TaskManager(scheduler)
        
        # 注册任务
        manager.register_task(TestTask)
        manager.register_task(SimpleTask)
        
        tasks = manager.list_tasks()
        assert "TestTask" in tasks
        assert "SimpleTask" in tasks
        print("✓ 任务注册测试通过")
        
        # 添加任务依赖
        manager.add_dependency("TestTask", "SimpleTask", TaskStatus.SUCCESS)
        
        dependencies = manager.get_dependencies("TestTask")
        assert len(dependencies) == 1
        assert dependencies[0].task_name == "SimpleTask"
        print("✓ 任务依赖测试通过")
        
        # 检查依赖（应该不满足）
        satisfied, reasons = manager.check_dependencies("TestTask")
        assert not satisfied
        assert "未执行" in reasons[0]
        print("✓ 依赖检查测试通过")
        
        # 执行简单任务
        simple_result = manager.execute_task("SimpleTask", action="echo")
        assert simple_result.status == TaskStatus.SUCCESS
        print("✓ 执行简单任务测试通过")
        
        # 再次检查依赖（现在应该满足）
        satisfied, reasons = manager.check_dependencies("TestTask")
        assert satisfied
        print("✓ 依赖满足测试通过")
        
        # 执行测试任务
        test_result = manager.execute_task("TestTask", action="success", delay=0.01)
        assert test_result.status == TaskStatus.SUCCESS
        print("✓ 执行测试任务测试通过")
        
        # 获取任务历史
        history = manager.get_task_history("TestTask")
        assert len(history) == 1
        assert history[0].task_id == test_result.task_id
        print("✓ 任务历史测试通过")
        
        # 获取任务指标
        metrics = manager.get_task_metrics("TestTask")
        assert metrics["total_executions"] == 1
        assert metrics["success_count"] == 1
        print("✓ 任务指标测试通过")
        
        # 获取任务信息
        task_info = manager.get_task_info("TestTask")
        assert task_info["name"] == "TestTask"
        assert task_info["execution_count"] == 1
        print("✓ 任务信息测试通过")
        
        # 测试顺序执行
        results = manager.execute_tasks_sequential(
            ["SimpleTask", "TestTask"],
            stop_on_error=True,
        )
        
        assert len(results) == 2
        assert results["SimpleTask"].status == TaskStatus.SUCCESS
        assert results["TestTask"].status == TaskStatus.SUCCESS
        print("✓ 顺序执行测试通过")
        
        # 测试执行队列
        queue_id = manager.add_to_queue("SimpleTask", action="echo")
        assert queue_id is not None
        
        # 启动执行队列
        manager.start_execution_queue()
        time.sleep(0.5)  # 等待任务执行
        
        queue_status = manager.get_queue_status()
        assert queue_status["queue_size"] == 0
        print("✓ 执行队列测试通过")
        
        # 停止执行队列
        manager.stop_execution_queue()
        
        # 清除历史
        manager.clear_history("TestTask")
        history = manager.get_task_history("TestTask")
        assert len(history) == 0
        print("✓ 清除历史测试通过")
        
        # 关闭调度器
        scheduler.shutdown()
        
        return True
        
    except Exception as e:
        print(f"✗ 任务管理器测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_integration():
    """测试集成功能"""
    print("\n测试集成功能...")
    
    try:
        # 设置日志
        setup_logging(
            log_level="INFO",
            log_file=None,
            enable_console=False,  # 避免测试输出干扰
        )
        
        # 使用便捷函数
        from infodata.tasks import register_task, execute_task
        
        # 注册任务
        register_task(TestTask)
        
        # 执行任务
        result = execute_task("TestTask", action="success", delay=0.01)
        
        assert result.status == TaskStatus.SUCCESS
        print("✓ 集成功能测试通过")
        
        return True
        
    except Exception as e:
        print(f"✗ 集成功能测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_performance():
    """测试性能"""
    print("\n测试性能...")
    
    try:
        import time
        
        # 创建简单任务
        task = SimpleTask(action="echo")
        
        # 测试执行时间
        start_time = time.time()
        result = task.execute()
        end_time = time.time()
        
        execution_time = end_time - start_time
        assert execution_time < 0.1  # 应该在0.1秒内完成
        print(f"✓ 性能测试通过 (执行时间: {execution_time:.3f}秒)")
        
        return True
        
    except Exception as e:
        print(f"✗ 性能测试失败: {e}")
        return False


def main():
    """主测试函数"""
    print("=" * 60)
    print("InfoData 任务系统测试")
    print("=" * 60)
    
    tests = [
        ("任务基类", test_task_base_class),
        ("任务调度器", test_task_scheduler),
        ("任务管理器", test_task_manager),
        ("集成功能", test_integration),
        ("性能", test_performance),
    ]
    
    results = []
    for test_name, test_func in tests:
        try:
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
        print("\n🎉 所有测试通过！任务系统功能正常。")
        return 0
    else:
        print(f"\n⚠️  {total - passed} 个测试失败，需要检查。")
        return 1


if __name__ == "__main__":
    sys.exit(main())