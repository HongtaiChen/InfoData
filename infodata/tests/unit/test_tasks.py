"""
任务系统单元测试
"""

import pytest
import time
import threading
from datetime import datetime, timedelta
from unittest.mock import Mock, patch, MagicMock

from infodata.tasks.base import (
    BaseTask, TaskConfig, TaskResult, TaskStatus, TaskPriority,
    TaskFactory, SimpleTask
)
from infodata.tasks.scheduler import TaskScheduler
from infodata.tasks.manager import TaskManager, TaskDependency
from infodata.config.schemas import SchedulerConfig, TaskScheduleConfig
from infodata.utils.exceptions import TaskError, TaskNotFoundError, TaskExecutionError


class TestTask(BaseTask):
    """测试任务类"""
    
    def __init__(self, action: str = "success", **kwargs):
        super().__init__(**kwargs)
        self.action = action
        self.execution_count = 0
    
    def _execute(self) -> TaskResult:
        self.execution_count += 1
        
        if self.action == "success":
            return self._create_result(
                TaskStatus.SUCCESS,
                data={"action": self.action, "count": self.execution_count},
                metrics={"executions": self.execution_count},
            )
        elif self.action == "error":
            raise ValueError(f"模拟错误: {self.action}")
        elif self.action == "timeout":
            time.sleep(2)  # 超过1秒超时
            return self._create_result(TaskStatus.SUCCESS)
        else:
            raise ValueError(f"未知动作: {self.action}")


class TestBaseTask:
    """测试任务基类"""
    
    def test_task_creation(self):
        """测试任务创建"""
        task = TestTask(action="success")
        
        assert task.name == "TestTask"
        assert task.description == "测试任务类"
        assert task.task_id is not None
        assert task.config is not None
        assert not task.is_running
        assert not task.is_completed
        assert task.result is None
    
    def test_task_execution_success(self):
        """测试任务执行成功"""
        task = TestTask(action="success")
        result = task.execute()
        
        assert result.status == TaskStatus.SUCCESS
        assert result.data["action"] == "success"
        assert result.data["count"] == 1
        assert result.metrics["executions"] == 1
        assert task.is_completed
        assert task.result == result
    
    def test_task_execution_error(self):
        """测试任务执行错误"""
        task = TestTask(action="error")
        result = task.execute()
        
        assert result.status == TaskStatus.FAILED
        assert "模拟错误" in result.error
        assert task.is_completed
    
    def test_task_config(self):
        """测试任务配置"""
        config = TaskConfig(
            timeout=10,
            max_retries=3,
            retry_delay=1,
            priority=TaskPriority.HIGH,
            parameters={"param1": "value1"}
        )
        
        task = TestTask(config=config, action="success")
        
        assert task.config.timeout == 10
        assert task.config.max_retries == 3
        assert task.config.retry_delay == 1
        assert task.config.priority == TaskPriority.HIGH
        assert task.config.parameters["param1"] == "value1"
    
    def test_task_validation(self):
        """测试任务验证"""
        # 有效配置
        config = TaskConfig(timeout=10, max_retries=3)
        task = TestTask(config=config)
        assert task.validate()
        
        # 无效配置 - 超时为0
        config = TaskConfig(timeout=0)
        task = TestTask(config=config)
        assert not task.validate()
        
        # 无效配置 - 最大重试次数为负数
        config = TaskConfig(max_retries=-1)
        task = TestTask(config=config)
        assert not task.validate()
    
    def test_task_cancel(self):
        """测试任务取消"""
        task = TestTask(action="success")
        task.cancel()
        
        assert task._cancelled
        result = task.execute()
        assert result.status == TaskStatus.CANCELLED
    
    def test_task_retry(self):
        """测试任务重试"""
        config = TaskConfig(max_retries=2, retry_delay=0.01)
        task = TestTask(config=config, action="error")
        
        # 第一次执行应该失败
        result1 = task.execute()
        assert result1.status == TaskStatus.FAILED
        
        # 重试应该再次执行
        result2 = task.retry()
        assert result2.status == TaskStatus.FAILED
        assert task.execution_count == 2
    
    def test_task_factory(self):
        """测试任务工厂"""
        # 注册任务
        TaskFactory.register(TestTask)
        
        # 创建任务
        task = TaskFactory.create("TestTask", action="success")
        assert isinstance(task, TestTask)
        assert task.action == "success"
        
        # 列出任务
        tasks = TaskFactory.list_tasks()
        assert "TestTask" in tasks
        
        # 获取任务类
        task_class = TaskFactory.get_task_class("TestTask")
        assert task_class == TestTask
        
        # 测试未找到任务
        with pytest.raises(TaskNotFoundError):
            TaskFactory.create("NonExistentTask")
        
        with pytest.raises(TaskNotFoundError):
            TaskFactory.get_task_class("NonExistentTask")


class TestTaskScheduler:
    """测试任务调度器"""
    
    @pytest.fixture
    def scheduler_config(self):
        """调度器配置fixture"""
        return SchedulerConfig(
            timezone="UTC",
            max_instances=3,
            misfire_grace_time=30,
        )
    
    @pytest.fixture
    def scheduler(self, scheduler_config):
        """调度器fixture"""
        scheduler = TaskScheduler(scheduler_config)
        scheduler.initialize()
        yield scheduler
        scheduler.shutdown()
    
    def test_scheduler_initialization(self, scheduler_config):
        """测试调度器初始化"""
        scheduler = TaskScheduler(scheduler_config)
        scheduler.initialize()
        
        assert scheduler._initialized
        assert scheduler.scheduler is not None
        
        status = scheduler.get_status()
        assert status["status"] == "stopped"
        
        scheduler.shutdown()
    
    def test_scheduler_start_stop(self, scheduler):
        """测试调度器启动和停止"""
        # 启动调度器
        scheduler.start()
        time.sleep(0.1)
        
        status = scheduler.get_status()
        assert status["status"] == "running"
        
        # 停止调度器
        scheduler.shutdown()
        time.sleep(0.1)
        
        status = scheduler.get_status()
        assert status["status"] == "stopped"
    
    def test_add_remove_task(self, scheduler):
        """测试添加和移除任务"""
        scheduler.start()
        
        # 创建任务
        task = TestTask(action="success")
        
        # 创建调度配置
        schedule_config = TaskScheduleConfig(
            schedule="* * * * *",
            timeout=30,
        )
        
        # 添加任务
        job_id = scheduler.add_task(task, schedule_config)
        assert job_id == task.task_id
        
        # 验证任务已添加
        task_info = scheduler.get_task(job_id)
        assert task_info is not None
        assert task_info["id"] == job_id
        
        tasks = scheduler.list_tasks()
        assert len(tasks) == 1
        assert tasks[0]["id"] == job_id
        
        # 移除任务
        scheduler.remove_task(job_id)
        
        tasks = scheduler.list_tasks()
        assert len(tasks) == 0
        
        scheduler.shutdown()
    
    def test_run_task_now(self, scheduler):
        """测试立即运行任务"""
        scheduler.start()
        
        # 创建任务
        task = TestTask(action="success")
        
        # 创建调度配置
        schedule_config = TaskScheduleConfig(
            schedule="* * * * *",
            timeout=30,
        )
        
        # 添加任务
        job_id = scheduler.add_task(task, schedule_config)
        
        # 立即运行任务
        result = scheduler.run_task_now(job_id)
        
        assert result.status == TaskStatus.SUCCESS
        assert result.task_id == job_id
        
        # 验证结果已保存
        stored_result = scheduler.get_task_result(job_id)
        assert stored_result is not None
        assert stored_result.task_id == job_id
        
        scheduler.shutdown()
    
    def test_task_callbacks(self, scheduler):
        """测试任务回调"""
        scheduler.start()
        
        # 创建回调记录器
        callback_results = []
        
        def callback(result):
            callback_results.append(result)
        
        # 创建任务
        task = TestTask(action="success")
        
        # 创建调度配置
        schedule_config = TaskScheduleConfig(
            schedule="* * * * *",
            timeout=30,
        )
        
        # 添加任务和回调
        job_id = scheduler.add_task(task, schedule_config)
        scheduler.add_task_callback(job_id, callback)
        
        # 立即运行任务
        result = scheduler.run_task_now(job_id)
        
        # 验证回调被调用
        assert len(callback_results) == 1
        assert callback_results[0].task_id == job_id
        
        # 移除回调
        scheduler.remove_task_callback(job_id, callback)
        
        # 再次运行任务
        result = scheduler.run_task_now(job_id)
        
        # 验证回调未被调用
        assert len(callback_results) == 1
        
        scheduler.shutdown()
    
    def test_scheduler_pause_resume(self, scheduler):
        """测试调度器暂停和恢复"""
        scheduler.start()
        
        # 暂停调度器
        scheduler.pause()
        status = scheduler.get_status()
        # 注意：APScheduler的pause()不会改变running状态
        
        # 恢复调度器
        scheduler.resume()
        
        scheduler.shutdown()


class TestTaskManager:
    """测试任务管理器"""
    
    @pytest.fixture
    def scheduler(self):
        """调度器fixture"""
        config = SchedulerConfig(timezone="UTC")
        scheduler = TaskScheduler(config)
        scheduler.initialize()
        yield scheduler
        scheduler.shutdown()
    
    @pytest.fixture
    def manager(self, scheduler):
        """任务管理器fixture"""
        manager = TaskManager(scheduler)
        
        # 注册测试任务
        manager.register_task(TestTask)
        manager.register_task(SimpleTask)
        
        yield manager
        
        # 停止执行队列
        manager.stop_execution_queue()
    
    def test_task_registration(self, manager):
        """测试任务注册"""
        tasks = manager.list_tasks()
        assert "TestTask" in tasks
        assert "SimpleTask" in tasks
    
    def test_task_dependencies(self, manager):
        """测试任务依赖"""
        # 添加依赖
        manager.add_dependency(
            "TestTask",
            "SimpleTask",
            TaskStatus.SUCCESS,
            timeout=10
        )
        
        # 获取依赖
        dependencies = manager.get_dependencies("TestTask")
        assert len(dependencies) == 1
        assert dependencies[0].task_name == "SimpleTask"
        assert dependencies[0].required_status == TaskStatus.SUCCESS
        assert dependencies[0].timeout == 10
        
        # 检查依赖（应该不满足）
        satisfied, reasons = manager.check_dependencies("TestTask")
        assert not satisfied
        assert "未执行" in reasons[0]
        
        # 执行依赖任务
        manager.execute_task("SimpleTask", action="echo")
        
        # 再次检查依赖（现在应该满足）
        satisfied, reasons = manager.check_dependencies("TestTask")
        assert satisfied
    
    def test_task_execution(self, manager):
        """测试任务执行"""
        # 执行任务
        result = manager.execute_task("TestTask", action="success")
        
        assert result.status == TaskStatus.SUCCESS
        assert result.data["action"] == "success"
        
        # 验证历史记录
        history = manager.get_task_history("TestTask")
        assert len(history) == 1
        assert history[0].task_id == result.task_id
        
        # 验证指标
        metrics = manager.get_task_metrics("TestTask")
        assert metrics["total_executions"] == 1
        assert metrics["success_count"] == 1
    
    def test_sequential_execution(self, manager):
        """测试顺序执行"""
        # 添加依赖
        manager.add_dependency("TestTask", "SimpleTask", TaskStatus.SUCCESS)
        
        # 顺序执行（应该失败，因为依赖未满足）
        with pytest.raises(TaskExecutionError):
            manager.execute_tasks_sequential(["SimpleTask", "TestTask"], stop_on_error=True)
        
        # 清除依赖
        manager._task_dependencies.clear()
        
        # 顺序执行（应该成功）
        results = manager.execute_tasks_sequential(
            ["SimpleTask", "TestTask"],
            stop_on_error=True
        )
        
        assert len(results) == 2
        assert results["SimpleTask"].status == TaskStatus.SUCCESS
        assert results["TestTask"].status == TaskStatus.SUCCESS
    
    def test_task_info(self, manager):
        """测试任务信息"""
        # 执行任务
        manager.execute_task("TestTask", action="success")
        
        # 获取任务信息
        info = manager.get_task_info("TestTask")
        
        assert info["name"] == "TestTask"
        assert info["execution_count"] == 1
        assert "description" in info
        assert "metrics" in info
    
    def test_execution_queue(self, manager):
        """测试执行队列"""
        # 添加任务到队列
        queue_id = manager.add_to_queue("TestTask", action="success")
        assert queue_id is not None
        
        # 启动执行队列
        manager.start_execution_queue()
        
        # 等待任务执行
        time.sleep(0.5)
        
        # 检查队列状态
        queue_status = manager.get_queue_status()
        assert queue_status["queue_size"] == 0
        assert queue_status["worker_running"]
        
        # 停止执行队列
        manager.stop_execution_queue()
        
        # 验证任务已执行
        history = manager.get_task_history("TestTask")
        assert len(history) > 0
    
    def test_clear_history(self, manager):
        """测试清除历史"""
        # 执行任务
        manager.execute_task("TestTask", action="success")
        
        # 验证历史存在
        history = manager.get_task_history("TestTask")
        assert len(history) == 1
        
        # 清除历史
        manager.clear_history("TestTask")
        
        # 验证历史已清除
        history = manager.get_task_history("TestTask")
        assert len(history) == 0
        
        # 验证指标已清除
        metrics = manager.get_task_metrics("TestTask")
        assert metrics["total_executions"] == 0


class TestIntegration:
    """测试集成功能"""
    
    def test_global_functions(self):
        """测试全局函数"""
        from infodata.tasks import register_task, execute_task
        
        # 注册任务
        register_task(TestTask)
        
        # 执行任务
        result = execute_task("TestTask", action="success")
        
        assert result.status == TaskStatus.SUCCESS
        assert isinstance(result, TaskResult)
    
    def test_scheduler_context(self):
        """测试调度器上下文管理器"""
        from infodata.tasks import scheduler_context
        
        config = SchedulerConfig(timezone="UTC")
        
        with scheduler_context(config) as scheduler:
            assert scheduler._initialized
            assert scheduler.scheduler is not None
            
            status = scheduler.get_status()
            assert status["status"] == "running"
        
        # 调度器应该已关闭
        assert not scheduler._initialized


if __name__ == "__main__":
    pytest.main([__file__, "-v"])