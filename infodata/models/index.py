"""
指数数据模型

定义指数相关的数据模型，包括日度行情、基本信息、成分股等。
"""

from datetime import datetime
from sqlalchemy import Column, String, DateTime, Float, Integer, Boolean, Text, DECIMAL
from sqlalchemy.dialects.mysql import LONGTEXT
from .base import BaseModel, TimeSeriesModel, FinancialInstrumentModel


class IndexInfo(FinancialInstrumentModel):
    """指数基本信息"""
    
    __tablename__ = "index_info"
    
    # 指数特定字段
    index_type = Column(String(50), nullable=True, comment="指数类型")
    index_family = Column(String(100), nullable=True, comment="指数系列")
    
    # 编制信息
    compiler = Column(String(200), nullable=True, comment="编制机构")
    compilation_method = Column(String(200), nullable=True, comment="编制方法")
    base_date = Column(DateTime, nullable=True, comment="基日")
    base_point = Column(DECIMAL(12, 4), nullable=True, comment="基点")
    
    # 样本信息
    sample_size = Column(Integer, nullable=True, comment="样本数量")
    sample_selection = Column(String(200), nullable=True, comment="样本选择")
    review_frequency = Column(String(50), nullable=True, comment="审核频率")
    next_review_date = Column(DateTime, nullable=True, comment="下次审核日期")
    
    # 计算方法
    calculation_method = Column(String(200), nullable=True, comment="计算方法")
    weighting_method = Column(String(100), nullable=True, comment="加权方法")
    adjustment_method = Column(String(200), nullable=True, comment="调整方法")
    
    # 覆盖范围
    coverage = Column(String(200), nullable=True, comment="覆盖范围")
    market_coverage = Column(DECIMAL(8, 4), nullable=True, comment="市场覆盖率")
    
    # 衍生品信息
    has_futures = Column(Boolean, default=False, comment="是否有期货")
    has_options = Column(Boolean, default=False, comment="是否有期权")
    has_etf = Column(Boolean, default=False, comment="是否有ETF")
    
    # 更新信息
    info_updated_at = Column(DateTime, nullable=True, comment="信息更新时间")
    next_update_date = Column(DateTime, nullable=True, comment="下次更新日期")


class IndexDaily(TimeSeriesModel):
    """指数日度行情"""
    
    __tablename__ = "index_daily"
    
    # 行情字段
    open_price = Column(DECIMAL(12, 4), nullable=True, comment="开盘价")
    high_price = Column(DECIMAL(12, 4), nullable=True, comment="最高价")
    low_price = Column(DECIMAL(12, 4), nullable=True, comment="最低价")
    close_price = Column(DECIMAL(12, 4), nullable=True, comment="收盘价")
    pre_close = Column(DECIMAL(12, 4), nullable=True, comment="前收盘价")
    
    # 涨跌字段
    change = Column(DECIMAL(12, 4), nullable=True, comment="涨跌额")
    pct_change = Column(DECIMAL(8, 4), nullable=True, comment="涨跌幅")
    amplitude = Column(DECIMAL(8, 4), nullable=True, comment="振幅")
    
    # 交易量
    volume = Column(DECIMAL(20, 2), nullable=True, comment="成交量")
    amount = Column(DECIMAL(20, 2), nullable=True, comment="成交额")
    turnover_rate = Column(DECIMAL(8, 4), nullable=True, comment="换手率")
    
    # 成分股统计
    advancing_issues = Column(Integer, nullable=True, comment="上涨家数")
    declining_issues = Column(Integer, nullable=True, comment="下跌家数")
    unchanged_issues = Column(Integer, nullable=True, comment="平盘家数")
    
    # 技术指标
    ma5 = Column(DECIMAL(12, 4), nullable=True, comment="5日均线")
    ma10 = Column(DECIMAL(12, 4), nullable=True, comment="10日均线")
    ma20 = Column(DECIMAL(12, 4), nullable=True, comment="20日均线")
    ma30 = Column(DECIMAL(12, 4), nullable=True, comment="30日均线")
    ma60 = Column(DECIMAL(12, 4), nullable=True, comment="60日均线")
    
    # 估值指标
    pe_ratio = Column(DECIMAL(10, 4), nullable=True, comment="市盈率")
    pb_ratio = Column(DECIMAL(10, 4), nullable=True, comment="市净率")
    dividend_yield = Column(DECIMAL(8, 4), nullable=True, comment="股息率")
    
    # 波动率
    volatility = Column(DECIMAL(8, 4), nullable=True, comment="波动率")
    beta = Column(DECIMAL(8, 4), nullable=True, comment="贝塔系数")
    
    @classmethod
    def get_index_summary(cls, session, trade_date: datetime) -> dict:
        """
        获取指定交易日的指数市场摘要
        
        Args:
            session: 数据库会话
            trade_date: 交易日期
            
        Returns:
            dict: 市场摘要
        """
        from sqlalchemy import func
        
        # 获取主要指数表现
        major_indices = ['000001.SH', '399001.SZ', '000300.SH', '000905.SH']
        
        summary = {
            'trade_date': trade_date,
            'major_indices': {},
            'market_summary': {}
        }
        
        # 查询主要指数
        for index_symbol in major_indices:
            index_data = session.query(cls).filter(
                cls.source_id == index_symbol,
                cls.trade_date == trade_date,
                cls.is_deleted == False
            ).first()
            
            if index_data:
                summary['major_indices'][index_symbol] = {
                    'close': float(index_data.close_price) if index_data.close_price else None,
                    'change': float(index_data.change) if index_data.change else None,
                    'pct_change': float(index_data.pct_change) if index_data.pct_change else None,
                    'volume': float(index_data.volume) if index_data.volume else None,
                    'amount': float(index_data.amount) if index_data.amount else None,
                }
        
        # 计算市场统计
        stats = session.query(
            func.avg(cls.pct_change).label('avg_change'),
            func.sum(func.case((cls.pct_change > 0, 1), else_=0)).label('up_count'),
            func.sum(func.case((cls.pct_change < 0, 1), else_=0)).label('down_count'),
            func.sum(func.case((cls.pct_change == 0, 1), else_=0)).label('flat_count'),
        ).filter(
            cls.trade_date == trade_date,
            cls.is_deleted == False,
            cls.pct_change.isnot(None)
        ).first()
        
        summary['market_summary'] = {
            'avg_change': float(stats.avg_change or 0),
            'up_count': stats.up_count or 0,
            'down_count': stats.down_count or 0,
            'flat_count': stats.flat_count or 0,
            'total_indices': (stats.up_count or 0) + (stats.down_count or 0) + (stats.flat_count or 0),
        }
        
        return summary


class IndexComponent(BaseModel):
    """指数成分股"""
    
    __tablename__ = "index_component"
    
    # 成分股信息
    stock_symbol = Column(String(50), nullable=False, comment="股票代码")
    stock_name = Column(String(200), nullable=False, comment="股票名称")
    
    # 权重信息
    weight = Column(DECIMAL(8, 6), nullable=True, comment="权重")
    shares = Column(DECIMAL(20, 2), nullable=True, comment="股数")
    market_cap = Column(DECIMAL(20, 2), nullable=True, comment="市值")
    
    # 加入信息
    inclusion_date = Column(DateTime, nullable=False, comment="纳入日期")
    exclusion_date = Column(DateTime, nullable=True, comment="剔除日期")
    is_current = Column(Boolean, default=True, comment="是否当前成分")
    
    # 调整信息
    adjustment_type = Column(String(50), nullable=True, comment="调整类型")
    adjustment_reason = Column(Text, nullable=True, comment="调整原因")
    
    # 行业分类
    industry = Column(String(200), nullable=True, comment="行业")
    sector = Column(String(100), nullable=True, comment="板块")
    
    # 排名信息
    weight_rank = Column(Integer, nullable=True, comment="权重排名")
    market_cap_rank = Column(Integer, nullable=True, comment="市值排名")
    
    @classmethod
    def get_index_composition(cls, session, index_symbol: str, 
                            as_of_date: datetime = None) -> dict:
        """
        获取指数成分股构成
        
        Args:
            session: 数据库会话
            index_symbol: 指数代码
            as_of_date: 截止日期，如果为None则使用当前成分
            
        Returns:
            dict: 指数构成数据
        """
        from sqlalchemy import func
        
        # 构建查询
        query = session.query(cls).filter(
            cls.source_id == index_symbol,
            cls.is_deleted == False
        )
        
        if as_of_date:
            query = query.filter(
                (cls.inclusion_date <= as_of_date) &
                ((cls.exclusion_date.is_(None)) | (cls.exclusion_date > as_of_date))
            )
        else:
            query = query.filter(cls.is_current == True)
        
        components = query.order_by(cls.weight.desc()).all()
        
        # 计算统计信息
        total_weight = sum([c.weight for c in components if c.weight])
        total_market_cap = sum([c.market_cap for c in components if c.market_cap])
        
        # 行业分布
        industry_dist = {}
        for component in components:
            if component.industry:
                industry_dist[component.industry] = industry_dist.get(component.industry, 0) + (component.weight or 0)
        
        # 前十大成分
        top10 = components[:10] if len(components) >= 10 else components
        
        return {
            'index_symbol': index_symbol,
            'as_of_date': as_of_date or datetime.now(),
            'total_components': len(components),
            'total_weight': float(total_weight) if total_weight else None,
            'total_market_cap': float(total_market_cap) if total_market_cap else None,
            'top10_weight': sum([c.weight for c in top10 if c.weight]),
            'industry_distribution': industry_dist,
            'components': [
                {
                    'stock_symbol': c.stock_symbol,
                    'stock_name': c.stock_name,
                    'weight': float(c.weight) if c.weight else None,
                    'market_cap': float(c.market_cap) if c.market_cap else None,
                    'industry': c.industry,
                    'inclusion_date': c.inclusion_date,
                }
                for c in components
            ],
        }