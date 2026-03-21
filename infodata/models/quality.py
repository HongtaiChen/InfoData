"""
数据质量模型

记录数据质量指标和验证规则。
"""

from datetime import datetime
from sqlalchemy import Column, String, DateTime, Float, Integer, Boolean, Text, DECIMAL, JSON
from .base import BaseModel


class DataQualityMetric(BaseModel):
    """数据质量指标"""
    
    __tablename__ = "data_quality_metric"
    
    # 质量指标
    metric_name = Column(String(200), nullable=False, comment="指标名称")
    metric_value = Column(DECIMAL(10, 6), nullable=False, comment="指标值")
    metric_target = Column(DECIMAL(10, 6), nullable=True, comment="目标值")
    metric_threshold = Column(DECIMAL(10, 6), nullable=True, comment="阈值")
    
    # 数据源信息
    data_source = Column(String(100), nullable=False, comment="数据源")
    data_type = Column(String(100), nullable=False, comment="数据类型")
    data_period = Column(String(50), nullable=True, comment="数据期间")
    
    # 时间信息
    measurement_date = Column(DateTime, nullable=False, comment="测量日期")
    measurement_period = Column(String(50), nullable=True, comment="测量期间")
    
    # 维度信息
    dimension1 = Column(String(200), nullable=True, comment="维度1")
    dimension2 = Column(String(200), nullable=True, comment="维度2")
    dimension3 = Column(String(200), nullable=True, comment="维度3")
    
    # 质量维度
    accuracy_score = Column(DECIMAL(5, 4), nullable=True, comment="准确性评分")
    completeness_score = Column(DECIMAL(5, 4), nullable=True, comment="完整性评分")
    timeliness_score = Column(DECIMAL(5, 4), nullable=True, comment="及时性评分")
    consistency_score = Column(DECIMAL(5, 4), nullable=True, comment="一致性评分")
    validity_score = Column(DECIMAL(5, 4), nullable=True, comment="有效性评分")
    uniqueness_score = Column(DECIMAL(5, 4), nullable=True, comment="唯一性评分")
    
    # 总体评分
    overall_score = Column(DECIMAL(5, 4), nullable=True, comment="总体评分")
    quality_level = Column(String(50), nullable=True, comment="质量等级")
    
    # 问题统计
    issue_count = Column(Integer, default=0, comment="问题数量")
    critical_issues = Column(Integer, default=0, comment="严重问题")
    warning_issues = Column(Integer, default=0, comment="警告问题")
    
    # 改进信息
    improvement_suggestions = Column(Text, nullable=True, comment="改进建议")
    next_review_date = Column(DateTime, nullable=True, comment="下次评审日期")
    
    @classmethod
    def get_quality_summary(cls, session, data_source: str = None,
                          start_date: datetime = None, end_date: datetime = None) -> dict:
        """
        获取数据质量摘要
        
        Args:
            session: 数据库会话
            data_source: 数据源，如果为None则统计所有数据源
            start_date: 开始日期
            end_date: 结束日期
            
        Returns:
            dict: 质量摘要
        """
        from sqlalchemy import func
        
        # 构建查询
        query = session.query(cls)
        
        if data_source:
            query = query.filter(cls.data_source == data_source)
        
        if start_date:
            query = query.filter(cls.measurement_date >= start_date)
        
        if end_date:
            query = query.filter(cls.measurement_date <= end_date)
        
        query = query.filter(cls.is_deleted == False)
        
        # 计算统计
        total_metrics = query.count()
        
        stats = query.with_entities(
            func.avg(cls.overall_score).label('avg_score'),
            func.min(cls.overall_score).label('min_score'),
            func.max(cls.overall_score).label('max_score'),
            func.avg(cls.accuracy_score).label('avg_accuracy'),
            func.avg(cls.completeness_score).label('avg_completeness'),
            func.avg(cls.timeliness_score).label('avg_timeliness'),
            func.avg(cls.consistency_score).label('avg_consistency'),
            func.sum(cls.issue_count).label('total_issues'),
            func.sum(cls.critical_issues).label('total_critical'),
            func.sum(cls.warning_issues).label('total_warning'),
        ).first()
        
        # 按数据源分组
        source_stats = session.query(
            cls.data_source,
            func.count(cls.id).label('metric_count'),
            func.avg(cls.overall_score).label('avg_score'),
            func.sum(cls.issue_count).label('issue_count'),
        ).filter(
            cls.is_deleted == False
        )
        
        if start_date:
            source_stats = source_stats.filter(cls.measurement_date >= start_date)
        
        if end_date:
            source_stats = source_stats.filter(cls.measurement_date <= end_date)
        
        source_stats = source_stats.group_by(cls.data_source).all()
        
        # 获取质量趋势
        trend_query = session.query(
            func.date(cls.measurement_date).label('date'),
            func.avg(cls.overall_score).label('avg_score'),
            func.count(cls.id).label('metric_count'),
        ).filter(cls.is_deleted == False)
        
        if data_source:
            trend_query = trend_query.filter(cls.data_source == data_source)
        
        if start_date:
            trend_query = trend_query.filter(cls.measurement_date >= start_date)
        
        if end_date:
            trend_query = trend_query.filter(cls.measurement_date <= end_date)
        
        trend_data = trend_query.group_by(func.date(cls.measurement_date)).order_by('date').all()
        
        return {
            'data_source': data_source or 'all',
            'period': {'start': start_date, 'end': end_date},
            'summary': {
                'total_metrics': total_metrics,
                'avg_overall_score': float(stats.avg_score or 0),
                'min_overall_score': float(stats.min_score or 0),
                'max_overall_score': float(stats.max_score or 0),
                'avg_accuracy': float(stats.avg_accuracy or 0),
                'avg_completeness': float(stats.avg_completeness or 0),
                'avg_timeliness': float(stats.avg_timeliness or 0),
                'avg_consistency': float(stats.avg_consistency or 0),
                'total_issues': stats.total_issues or 0,
                'total_critical': stats.total_critical or 0,
                'total_warning': stats.total_warning or 0,
            },
            'by_source': [
                {
                    'data_source': stat.data_source,
                    'metric_count': stat.metric_count,
                    'avg_score': float(stat.avg_score or 0),
                    'issue_count': stat.issue_count or 0,
                }
                for stat in source_stats
            ],
            'trend': [
                {
                    'date': trend.date,
                    'avg_score': float(trend.avg_score or 0),
                    'metric_count': trend.metric_count,
                }
                for trend in trend_data
            ],
        }


class DataValidationRule(BaseModel):
    """数据验证规则"""
    
    __tablename__ = "data_validation_rule"
    
    # 规则信息
    rule_name = Column(String(200), nullable=False, comment="规则名称")
    rule_description = Column(Text, nullable=True, comment="规则描述")
    rule_type = Column(String(100), nullable=False, comment="规则类型")
    
    # 应用范围
    data_source = Column(String(100), nullable=False, comment="数据源")
    data_type = Column(String(100), nullable=False, comment="数据类型")
    table_name = Column(String(200), nullable=True, comment="表名")
    column_name = Column(String(200), nullable=True, comment="列名")
    
    # 规则定义
    rule_expression = Column(Text, nullable=False, comment="规则表达式")
    rule_parameters = Column(JSON, default=dict, comment="规则参数")
    
    # 严重性
    severity_level = Column(String(50), nullable=False, comment="严重级别")
    error_code = Column(String(100), nullable=True, comment="错误代码")
    error_message = Column(Text, nullable=True, comment="错误消息")
    
    # 执行配置
    is_active = Column(Boolean, default=True, comment="是否激活")
    execution_frequency = Column(String(50), nullable=True, comment="执行频率")
    last_executed = Column(DateTime, nullable=True, comment="最后执行时间")
    next_execution = Column(DateTime, nullable=True, comment="下次执行时间")
    
    # 阈值配置
    warning_threshold = Column(DECIMAL(10, 6), nullable=True, comment="警告阈值")
    critical_threshold = Column(DECIMAL(10, 6), nullable=True, comment="严重阈值")
    tolerance_percent = Column(DECIMAL(8, 4), nullable=True, comment="容忍百分比")
    
    # 统计信息
    total_checks = Column(Integer, default=0, comment="总检查次数")
    passed_checks = Column(Integer, default=0, comment="通过次数")
    failed_checks = Column(Integer, default=0, comment="失败次数")
    warning_checks = Column(Integer, default=0, comment="警告次数")
    
    # 性能信息
    avg_execution_time = Column(Float, nullable=True, comment="平均执行时间")
    max_execution_time = Column(Float, nullable=True, comment="最大执行时间")
    last_execution_time = Column(Float, nullable=True, comment="最后执行时间")
    
    # 依赖关系
    depends_on_rules = Column(JSON, default=list, comment="依赖规则")
    related_rules = Column(JSON, default=list, comment="相关规则")
    
    @classmethod
    def validate_data(cls, session, data_source: str, data_type: str,
                     data: dict, rule_names: list = None) -> dict:
        """
        验证数据
        
        Args:
            session: 数据库会话
            data_source: 数据源
            data_type: 数据类型
            data: 要验证的数据
            rule_names: 要应用的规则名称列表，如果为None则应用所有规则
            
        Returns:
            dict: 验证结果
        """
        from datetime import datetime
        
        # 获取适用的规则
        query = session.query(cls).filter(
            cls.data_source == data_source,
            cls.data_type == data_type,
            cls.is_active == True,
            cls.is_deleted == False
        )
        
        if rule_names:
            query = query.filter(cls.rule_name.in_(rule_names))
        
        rules = query.all()
        
        validation_results = {
            'data_source': data_source,
            'data_type': data_type,
            'validation_time': datetime.now(),
            'total_rules': len(rules),
            'passed_rules': 0,
            'failed_rules': 0,
            'warning_rules': 0,
            'results': [],
            'errors': [],
            'warnings': [],
        }
        
        for rule in rules:
            try:
                # 执行验证
                result = cls._execute_rule(rule, data)
                
                validation_results['results'].append(result)
                
                if result['status'] == 'passed':
                    validation_results['passed_rules'] += 1
                elif result['status'] == 'failed':
                    validation_results['failed_rules'] += 1
                    validation_results['errors'].append({
                        'rule_name': rule.rule_name,
                        'error_message': result.get('error_message'),
                        'severity': rule.severity_level,
                    })
                elif result['status'] == 'warning':
                    validation_results['warning_rules'] += 1
                    validation_results['warnings'].append({
                        'rule_name': rule.rule_name,
                        'warning_message': result.get('warning_message'),
                        'severity': rule.severity_level,
                    })
                
                # 更新规则统计
                rule.total_checks += 1
                if result['status'] == 'passed':
                    rule.passed_checks += 1
                elif result['status'] == 'failed':
                    rule.failed_checks += 1
                elif result['status'] == 'warning':
                    rule.warning_checks += 1
                
                rule.last_executed = datetime.now()
                
            except Exception as e:
                # 规则执行错误
                error_result = {
                    'rule_name': rule.rule_name,
                    'status': 'error',
                    'error_message': f"规则执行错误: {str(e)}",
                    'execution_time': None,
                }
                
                validation_results['results'].append(error_result)
                validation_results['errors'].append({
                    'rule_name': rule.rule_name,
                    'error_message': f"规则执行错误: {str(e)}",
                    'severity': 'critical',
                })
                validation_results['failed_rules'] += 1
        
        # 计算总体状态
        if validation_results['failed_rules'] > 0:
            validation_results['overall_status'] = 'failed'
        elif validation_results['warning_rules'] > 0:
            validation_results['overall_status'] = 'warning'
        else:
            validation_results['overall_status'] = 'passed'
        
        return validation_results
    
    @classmethod
    def _execute_rule(cls, rule, data: dict) -> dict:
        """
        执行单个验证规则
        
        Args:
            rule: 验证规则
            data: 要验证的数据
            
        Returns:
            dict: 验证结果
        """
        import time
        from datetime import datetime
        
        start_time = time.time()
        
        try:
            # 这里应该实现具体的规则验证逻辑
            # 目前返回一个模拟结果
            result = {
                'rule_name': rule.rule_name,
                'rule_type': rule.rule_type,
                'status': 'passed',  # 模拟通过
                'execution_time': time.time() - start_time,
                'checked_at': datetime.now(),
            }
            
            return result
            
        except Exception as e:
            return {
                'rule_name': rule.rule_name,
                'rule_type': rule.rule_type,
                'status': 'error',
                'error_message': str(e),
                'execution_time': time.time() - start_time,
                'checked_at': datetime.now(),
            }