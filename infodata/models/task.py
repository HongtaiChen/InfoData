"""
任务执行模型

记录任务执行历史、指标和状态。
"""

from datetime import datetime
from sqlalchemy import Column, String, DateTime, Float, Integer, Boolean, Text, DECIMAL, JSON
from .base import BaseModel


class TaskExecution(BaseModel):
    """任务执行记录"""
    
    __tablename__ = "task_execution"
    
    # 任务信息
    task_name = Column(String(200), nullable=False, comment="任务名称")
    task_type = Column(String(100), nullable=False, comment="任务类型")
    task_group = Column(String(100), nullable=True, comment="任务分组")
    
    # 执行信息
    execution_start = Column(DateTime, nullable=False, comment="执行开始时间")
    execution_end = Column(DateTime, nullable=True, comment="执行结束时间")
    execution_duration = Column(Float, nullable=True, comment="执行时长（秒）")
    
    # 状态信息
    execution_status = Column(String(50), nullable=False, comment="执行状态")
    exit_code = Column(Integer, nullable=True, comment="退出代码")
    error_message = Column(Text, nullable=True, comment="错误信息")
    stack_trace = Column(Text, nullable=True, comment="堆栈跟踪")
    
    # 输入输出
    input_parameters = Column(JSON, default=dict, comment="输入参数")
    output_result = Column(JSON, default=dict, comment="输出结果")
    output_files = Column(JSON, default=list, comment="输出文件")
    
    # 资源使用
    memory_usage_mb = Column(Float, nullable=True, comment="内存使用（MB）")
    cpu_usage_percent = Column(Float, nullable=True, comment="CPU使用率")
    disk_usage_mb = Column(Float, nullable=True, comment="磁盘使用（MB）")
    
    # 数据统计
    records_processed = Column(Integer, nullable=True, comment="处理记录数")
    records_succeeded = Column(Integer, nullable=True, comment="成功记录数")
    records_failed = Column(Integer, nullable=True, comment="失败记录数")
    records_skipped = Column(Integer, nullable=True, comment="跳过记录数")
    
    # 重试信息
    retry_count = Column(Integer, default=0, comment="重试次数")
    max_retries = Column(Integer, default=3, comment="最大重试次数")
    next_retry_time = Column(DateTime, nullable=True, comment="下次重试时间")
    
    # 依赖关系
    depends_on = Column(JSON, default=list, comment="依赖任务")
    triggered_by = Column(String(200), nullable=True, comment="触发任务")
    
    # 执行环境
    execution_host = Column(String(200), nullable=True, comment="执行主机")
    execution_pid = Column(Integer, nullable=True, comment="进程ID")
    python_version = Column(String(50), nullable=True, comment="Python版本")
    
    # 监控信息
    is_monitored = Column(Boolean, default=True, comment="是否监控")
    alert_sent = Column(Boolean, default=False, comment="是否发送告警")
    alert_level = Column(String(50), nullable=True, comment="告警级别")
    
    @classmethod
    def get_task_statistics(cls, session, task_name: str = None, 
                          start_date: datetime = None, end_date: datetime = None) -> dict:
        """
        获取任务执行统计
        
        Args:
            session: 数据库会话
            task_name: 任务名称，如果为None则统计所有任务
            start_date: 开始日期
            end_date: 结束日期
            
        Returns:
            dict: 任务统计
        """
        from sqlalchemy import func
        
        # 构建查询
        query = session.query(cls)
        
        if task_name:
            query = query.filter(cls.task_name == task_name)
        
        if start_date:
            query = query.filter(cls.execution_start >= start_date)
        
        if end_date:
            query = query.filter(cls.execution_start <= end_date)
        
        query = query.filter(cls.is_deleted == False)
        
        # 计算统计
        total_executions = query.count()
        
        stats = query.with_entities(
            func.count(cls.id).label('total'),
            func.sum(func.case((cls.execution_status == 'success', 1), else_=0)).label('success'),
            func.sum(func.case((cls.execution_status == 'failed', 1), else_=0)).label('failed'),
            func.sum(func.case((cls.execution_status == 'running', 1), else_=0)).label('running'),
            func.avg(cls.execution_duration).label('avg_duration'),
            func.max(cls.execution_duration).label('max_duration'),
            func.min(cls.execution_duration).label('min_duration'),
            func.avg(cls.memory_usage_mb).label('avg_memory'),
            func.avg(cls.cpu_usage_percent).label('avg_cpu'),
        ).first()
        
        # 获取最近执行
        recent_executions = query.order_by(cls.execution_start.desc()).limit(5).all()
        
        return {
            'task_name': task_name or 'all',
            'period': {
                'start': start_date,
                'end': end_date,
            },
            'statistics': {
                'total_executions': total_executions,
                'success_count': stats.success or 0,
                'failed_count': stats.failed or 0,
                'running_count': stats.running or 0,
                'success_rate': (stats.success or 0) / total_executions if total_executions > 0 else 0,
                'avg_duration': float(stats.avg_duration or 0),
                'max_duration': float(stats.max_duration or 0),
                'min_duration': float(stats.min_duration or 0),
                'avg_memory': float(stats.avg_memory or 0),
                'avg_cpu': float(stats.avg_cpu or 0),
            },
            'recent_executions': [
                {
                    'id': exec.id,
                    'start_time': exec.execution_start,
                    'duration': exec.execution_duration,
                    'status': exec.execution_status,
                    'records_processed': exec.records_processed,
                }
                for exec in recent_executions
            ],
        }


class TaskMetric(BaseModel):
    """任务执行指标"""
    
    __tablename__ = "task_metric"
    
    # 指标信息
    metric_name = Column(String(200), nullable=False, comment="指标名称")
    metric_type = Column(String(100), nullable=False, comment="指标类型")
    metric_value = Column(DECIMAL(20, 6), nullable=False, comment="指标值")
    metric_unit = Column(String(50), nullable=True, comment="指标单位")
    
    # 时间信息
    metric_timestamp = Column(DateTime, nullable=False, comment="指标时间戳")
    collection_interval = Column(Integer, nullable=True, comment="收集间隔（秒）")
    
    # 关联信息
    task_execution_id = Column(Integer, nullable=True, comment="任务执行ID")
    resource_type = Column(String(100), nullable=True, comment="资源类型")
    resource_id = Column(String(200), nullable=True, comment="资源ID")
    
    # 标签信息
    labels = Column(JSON, default=dict, comment="标签")
    annotations = Column(JSON, default=dict, comment="注解")
    
    # 质量信息
    data_quality = Column(DECIMAL(5, 4), nullable=True, comment="数据质量")
    is_anomaly = Column(Boolean, default=False, comment="是否异常")
    anomaly_score = Column(DECIMAL(5, 4), nullable=True, comment="异常分数")
    
    # 阈值信息
    warning_threshold = Column(DECIMAL(20, 6), nullable=True, comment="警告阈值")
    critical_threshold = Column(DECIMAL(20, 6), nullable=True, comment="严重阈值")
    threshold_status = Column(String(50), nullable=True, comment="阈值状态")
    
    @classmethod
    def get_metric_trend(cls, session, metric_name: str, 
                        start_time: datetime, end_time: datetime,
                        aggregation: str = 'avg') -> list:
        """
        获取指标趋势数据
        
        Args:
            session: 数据库会话
            metric_name: 指标名称
            start_time: 开始时间
            end_time: 结束时间
            aggregation: 聚合方式（avg, sum, min, max, count）
            
        Returns:
            list: 趋势数据
        """
        from sqlalchemy import func, extract
        
        # 选择聚合函数
        if aggregation == 'avg':
            agg_func = func.avg(cls.metric_value)
        elif aggregation == 'sum':
            agg_func = func.sum(cls.metric_value)
        elif aggregation == 'min':
            agg_func = func.min(cls.metric_value)
        elif aggregation == 'max':
            agg_func = func.max(cls.metric_value)
        elif aggregation == 'count':
            agg_func = func.count(cls.id)
        else:
            agg_func = func.avg(cls.metric_value)
        
        # 按时间分组
        results = session.query(
            func.date(cls.metric_timestamp).label('date'),
            agg_func.label('value'),
            func.count(cls.id).label('count'),
        ).filter(
            cls.metric_name == metric_name,
            cls.metric_timestamp >= start_time,
            cls.metric_timestamp <= end_time,
            cls.is_deleted == False
        ).group_by(func.date(cls.metric_timestamp)).order_by('date').all()
        
        trend_data = []
        for result in results:
            trend_data.append({
                'date': result.date,
                'value': float(result.value) if result.value else None,
                'count': result.count,
            })
        
        return {
            'metric_name': metric_name,
            'aggregation': aggregation,
            'period': {'start': start_time, 'end': end_time},
            'data_points': len(trend_data),
            'trend_data': trend_data,
        }