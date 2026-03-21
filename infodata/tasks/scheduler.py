"""
任务调度模块

基于APScheduler实现定时任务调度。
"""

import time
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional, Callable
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.events import EVENT_JOB_EXECUTED, EVENT_JOB_ERROR

from ..utils.logging import get_logger
from ..utils.database import session_scope
from .manager import TaskManager
from ..models.task import TaskExecution

logger = get_logger(__name__)


class TaskScheduler:
    """任务调度器"""
    
    def __init__(self, task_manager: Optional[TaskManager] = None):
        """
        初始化任务调度器
        
        Args:
            task_manager: 任务管理器，如果为None则创建新实例
        """
        self.task_manager = task_manager or TaskManager()
        self.scheduler = BackgroundScheduler()
        self._setup_event_listeners()
        
        logger.info("任务调度器初始化完成")
    
    def _setup_event_listeners(self) -> None:
        """设置事件监听器"""
        # 任务执行成功事件
        self.scheduler.add_listener(
            self._on_job_executed,
            EVENT_JOB_EXECUTED
        )
        
        # 任务执行错误事件
        self.scheduler.add_listener(
            self._on_job_error,
            EVENT_JOB_ERROR
        )
    
    def _on_job_executed(self, event) -> None:
        """任务执行成功事件处理"""
        try:
            job_id = event.job_id
            job = self.scheduler.get_job(job_id)
            
            if job:
                logger.info(f"任务执行成功: {job_id}, 下次执行: {job.next_run_time}")
                
                # 记录任务执行成功
                self._record_task_execution(
                    task_name=job_id,
                    execution_status="success",
                    metadata={
                        "event": "job_executed",
                        "job_id": job_id,
                        "scheduled_time": str(event.scheduled_run_time),
                        "actual_time": str(event.scheduled_run_time),
                    }
                )
                
        except Exception as e:
            logger.error(f"处理任务执行成功事件失败: {e}")
    
    def _on_job_error(self, event) -> None:
        """任务执行错误事件处理"""
        try:
            job_id = event.job_id
            job = self.scheduler.get_job(job_id)
            
            if job:
                error_msg = str(event.exception) if event.exception else "未知错误"
                logger.error(f"任务执行失败: {job_id}, 错误: {error_msg}")
                
                # 记录任务执行失败
                self._record_task_execution(
                    task_name=job_id,
                    execution_status="failed",
                    error_message=error_msg,
                    metadata={
                        "event": "job_error",
                        "job_id": job_id,
                        "scheduled_time": str(event.scheduled_run_time),
                        "exception": str(event.exception),
                        "traceback": event.traceback if hasattr(event, 'traceback') else None,
                    }
                )
                
        except Exception as e:
            logger.error(f"处理任务执行错误事件失败: {e}")
    
    def _record_task_execution(
        self,
        task_name: str,
        execution_status: str,
        error_message: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        记录任务执行
        
        Args:
            task_name: 任务名称
            execution_status: 执行状态
            error_message: 错误信息
            metadata: 元数据
        """
        try:
            with session_scope() as session:
                task = TaskExecution(
                    source="task_scheduler",
                    source_id=f"{task_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
                    task_name=task_name,
                    task_type="scheduled",
                    execution_start=datetime.now(),
                    execution_end=datetime.now(),
                    execution_status=execution_status,
                    exit_code=0 if execution_status == "success" else 1,
                    error_message=error_message,
                    input_parameters={},
                    output_result={"metadata": metadata or {}},
                )
                
                session.add(task)
                session.commit()
                
        except Exception as e:
            logger.error(f"记录任务执行失败: {e}")
    
    def start(self) -> bool:
        """
        启动调度器
        
        Returns:
            bool: 启动是否成功
        """
        try:
            if not self.scheduler.running:
                self.scheduler.start()
                logger.info("任务调度器已启动")
                return True
            else:
                logger.warning("任务调度器已经在运行")
                return False
                
        except Exception as e:
            logger.error(f"启动任务调度器失败: {e}")
            return False
    
    def stop(self, wait: bool = True) -> bool:
        """
        停止调度器
        
        Args:
            wait: 是否等待正在执行的任务完成
            
        Returns:
            bool: 停止是否成功
        """
        try:
            if self.scheduler.running:
                self.scheduler.shutdown(wait=wait)
                logger.info("任务调度器已停止")
                return True
            else:
                logger.warning("任务调度器未在运行")
                return False
                
        except Exception as e:
            logger.error(f"停止任务调度器失败: {e}")
            return False
    
    def add_job(
        self,
        func: Callable,
        job_id: str,
        trigger: str,
        **trigger_args
    ) -> bool:
        """
        添加任务
        
        Args:
            func: 要执行的函数
            job_id: 任务ID
            trigger: 触发器类型 ('cron', 'interval', 'date')
            **trigger_args: 触发器参数
            
        Returns:
            bool: 添加是否成功
        """
        try:
            # 创建触发器
            if trigger == 'cron':
                trigger_obj = CronTrigger(**trigger_args)
            elif trigger == 'interval':
                trigger_obj = IntervalTrigger(**trigger_args)
            elif trigger == 'date':
                from apscheduler.triggers.date import DateTrigger
                trigger_obj = DateTrigger(**trigger_args)
            else:
                raise ValueError(f"不支持的触发器类型: {trigger}")
            
            # 添加任务
            self.scheduler.add_job(
                func=func,
                trigger=trigger_obj,
                id=job_id,
                name=job_id,
                replace_existing=True,
            )
            
            job = self.scheduler.get_job(job_id)
            if job:
                logger.info(f"任务添加成功: {job_id}, 下次执行: {job.next_run_time}")
                return True
            else:
                logger.error(f"任务添加失败: {job_id}")
                return False
                
        except Exception as e:
            logger.error(f"添加任务失败: {job_id}, 错误: {e}")
            return False
    
    def remove_job(self, job_id: str) -> bool:
        """
        移除任务
        
        Args:
            job_id: 任务ID
            
        Returns:
            bool: 移除是否成功
        """
        try:
            if self.scheduler.get_job(job_id):
                self.scheduler.remove_job(job_id)
                logger.info(f"任务移除成功: {job_id}")
                return True
            else:
                logger.warning(f"任务不存在: {job_id}")
                return False
                
        except Exception as e:
            logger.error(f"移除任务失败: {job_id}, 错误: {e}")
            return False
    
    def pause_job(self, job_id: str) -> bool:
        """
        暂停任务
        
        Args:
            job_id: 任务ID
            
        Returns:
            bool: 暂停是否成功
        """
        try:
            job = self.scheduler.get_job(job_id)
            if job:
                job.pause()
                logger.info(f"任务暂停成功: {job_id}")
                return True
            else:
                logger.warning(f"任务不存在: {job_id}")
                return False
                
        except Exception as e:
            logger.error(f"暂停任务失败: {job_id}, 错误: {e}")
            return False
    
    def resume_job(self, job_id: str) -> bool:
        """
        恢复任务
        
        Args:
            job_id: 任务ID
            
        Returns:
            bool: 恢复是否成功
        """
        try:
            job = self.scheduler.get_job(job_id)
            if job:
                job.resume()
                logger.info(f"任务恢复成功: {job_id}")
                return True
            else:
                logger.warning(f"任务不存在: {job_id}")
                return False
                
        except Exception as e:
            logger.error(f"恢复任务失败: {job_id}, 错误: {e}")
            return False
    
    def get_job_status(self, job_id: str) -> Dict[str, Any]:
        """
        获取任务状态
        
        Args:
            job_id: 任务ID
            
        Returns:
            Dict[str, Any]: 任务状态
        """
        try:
            job = self.scheduler.get_job(job_id)
            
            if job:
                return {
                    "job_id": job_id,
                    "name": job.name,
                    "next_run_time": job.next_run_time,
                    "previous_run_time": job.previous_fire_time if hasattr(job, 'previous_fire_time') else None,
                    "trigger": str(job.trigger),
                    "paused": job.pending,
                }
            else:
                return {
                    "job_id": job_id,
                    "error": "任务不存在",
                }
                
        except Exception as e:
            logger.error(f"获取任务状态失败: {job_id}, 错误: {e}")
            return {
                "job_id": job_id,
                "error": str(e),
            }
    
    def get_all_jobs(self) -> List[Dict[str, Any]]:
        """
        获取所有任务
        
        Returns:
            List[Dict[str, Any]]: 所有任务状态
        """
        try:
            jobs = self.scheduler.get_jobs()
            
            result = []
            for job in jobs:
                result.append({
                    "job_id": job.id,
                    "name": job.name,
                    "next_run_time": job.next_run_time,
                    "previous_run_time": job.previous_fire_time if hasattr(job, 'previous_fire_time') else None,
                    "trigger": str(job.trigger),
                    "paused": job.pending,
                })
            
            return result
            
        except Exception as e:
            logger.error(f"获取所有任务失败: {e}")
            return []
    
    def schedule_stock_daily_update(
        self,
        symbols: Optional[List[str]] = None,
        start_time: str = "19:00",
        max_duration_hours: int = 3,
        **kwargs
    ) -> bool:
        """
        调度股票日度更新任务
        
        Args:
            symbols: 股票代码列表，如果为None则使用默认列表
            start_time: 开始时间（格式: "HH:MM"）
            max_duration_hours: 最大执行时长（小时）
            **kwargs: 其他参数
            
        Returns:
            bool: 调度是否成功
        """
        try:
            # 解析开始时间
            hour, minute = map(int, start_time.split(':'))
            
            # 创建任务函数
            def stock_daily_update_job():
                logger.info(f"开始执行股票日度更新任务")
                
                try:
                    # 调用任务管理器执行股票日度更新
                    result = self.task_manager.run_stock_daily_update(
                        symbols=symbols,
                        **kwargs
                    )
                    
                    if result['success']:
                        logger.info(f"股票日度更新任务执行成功: {result}")
                    else:
                        logger.error(f"股票日度更新任务执行失败: {result.get('error', '未知错误')}")
                        
                except Exception as e:
                    logger.error(f"股票日度更新任务执行异常: {e}", exc_info=True)
            
            # 添加定时任务
            # 每天19:00执行
            success = self.add_job(
                func=stock_daily_update_job,
                job_id="stock_daily_update",
                trigger="cron",
                hour=hour,
                minute=minute,
                day_of_week="mon-fri",  # 周一到周五
            )
            
            if success:
                logger.info(f"股票日度更新任务调度成功: 每天 {start_time} 执行")
            else:
                logger.error(f"股票日度更新任务调度失败")
            
            return success
            
        except Exception as e:
            logger.error(f"调度股票日度更新任务失败: {e}")
            return False
    
    def schedule_stock_info_update(
        self,
        symbols: Optional[List[str]] = None,
        day_of_week: str = "sun",  # 默认周日更新
        hour: int = 2,
        minute: int = 0,
        **kwargs
    ) -> bool:
        """
        调度股票信息更新任务
        
        Args:
            symbols: 股票代码列表，如果为None则使用默认列表
            day_of_week: 星期几更新（cron格式: "mon", "tue", "wed", "thu", "fri", "sat", "sun"）
            hour: 小时
            minute: 分钟
            **kwargs: 其他参数
            
        Returns:
            bool: 调度是否成功
        """
        try:
            # 创建任务函数
            def stock_info_update_job():
                logger.info(f"开始执行股票信息更新任务")
                
                try:
                    # 调用任务管理器执行股票信息更新
                    result = self.task_manager.run_stock_info_update(
                        symbols=symbols,
                        **kwargs
                    )
                    
                    if result['success']:
                        logger.info(f"股票信息更新任务执行成功: {result}")
                    else:
                        logger.error(f"股票信息更新任务执行失败: {result.get('error', '未知错误')}")
                        
                except Exception as e:
                    logger.error(f"股票信息更新任务执行异常: {e}", exc_info=True)
            
            # 添加定时任务
            # 每周日02:00执行
            success = self.add_job(
                func=stock_info_update_job,
                job_id="stock_info_update",
                trigger="cron",
                day_of_week=day_of_week,
                hour=hour,
                minute=minute,
            )
            
            if success:
                logger.info(f"股票信息更新任务调度成功: 每周{day_of_week} {hour:02d}:{minute:02d} 执行")
            else:
                logger.error(f"股票信息更新任务调度失败")
            
            return success
            
        except Exception as e:
            logger.error(f"调度股票信息更新任务失败: {e}")
            return False
    
    def schedule_fund_daily_update(
        self,
        symbols: Optional[List[str]] = None,
        start_time: str = "19:30",
        **kwargs
    ) -> bool:
        """
        调度基金日度更新任务
        
        Args:
            symbols: 基金代码列表，如果为None则使用默认列表
            start_time: 开始时间（格式: "HH:MM"）
            **kwargs: 其他参数
            
        Returns:
            bool: 调度是否成功
        """
        try:
            # 解析开始时间
            hour, minute = map(int, start_time.split(':'))
            
            # 创建任务函数
            def fund_daily_update_job():
                logger.info(f"开始执行基金日度更新任务")
                
                try:
                    # 调用任务管理器执行基金日度更新
                    result = self.task_manager.run_fund_daily_update(
                        symbols=symbols,
                        **kwargs
                    )
                    
                    if result['success']:
                        logger.info(f"基金日度更新任务执行成功: {result}")
                    else:
                        logger.error(f"基金日度更新任务执行失败: {result.get('error', '未知错误')}")
                        
                except Exception