"""
基金数据模型

定义基金相关的数据模型，包括日度净值、基本信息、基金经理等。
"""

from datetime import datetime
from sqlalchemy import Column, String, DateTime, Float, Integer, Boolean, Text, DECIMAL
from sqlalchemy.dialects.mysql import LONGTEXT
from .base import BaseModel, TimeSeriesModel, FinancialInstrumentModel


class FundInfo(FinancialInstrumentModel):
    """基金基本信息"""
    
    __tablename__ = "fund_info"
    
    # 基金特定字段
    fund_type = Column(String(50), nullable=True, comment="基金类型")
    fund_category = Column(String(100), nullable=True, comment="基金类别")
    fund_style = Column(String(50), nullable=True, comment="投资风格")
    
    # 基金管理
    fund_company = Column(String(200), nullable=True, comment="基金管理公司")
    fund_company_code = Column(String(50), nullable=True, comment="基金公司代码")
    custodian_bank = Column(String(200), nullable=True, comment="托管银行")
    
    # 基金规模
    fund_size = Column(DECIMAL(20, 2), nullable=True, comment="基金规模")
    fund_size_date = Column(DateTime, nullable=True, comment="规模统计日期")
    
    # 成立信息
    establishment_date = Column(DateTime, nullable=True, comment="成立日期")
    duration = Column(String(50), nullable=True, comment="存续期")
    maturity_date = Column(DateTime, nullable=True, comment="到期日")
    
    # 投资信息
    investment_objective = Column(Text, nullable=True, comment="投资目标")
    investment_strategy = Column(Text, nullable=True, comment="投资策略")
    benchmark_index = Column(String(200), nullable=True, comment="业绩比较基准")
    
    # 费用信息
    management_fee = Column(DECIMAL(8, 4), nullable=True, comment="管理费率")
    custody_fee = Column(DECIMAL(8, 4), nullable=True, comment="托管费率")
    subscription_fee = Column(DECIMAL(8, 4), nullable=True, comment="申购费率")
    redemption_fee = Column(DECIMAL(8, 4), nullable=True, comment="赎回费率")
    
    # 风险等级
    risk_level = Column(String(50), nullable=True, comment="风险等级")
    risk_rating = Column(String(50), nullable=True, comment="风险评级")
    
    # 更新信息
    info_updated_at = Column(DateTime, nullable=True, comment="信息更新时间")
    next_update_date = Column(DateTime, nullable=True, comment="下次更新日期")


class FundDaily(TimeSeriesModel):
    """基金日度净值"""
    
    __tablename__ = "fund_daily"
    
    # 净值字段
    nav = Column(DECIMAL(10, 4), nullable=True, comment="单位净值")
    accum_nav = Column(DECIMAL(10, 4), nullable=True, comment="累计净值")
    adjusted_nav = Column(DECIMAL(10, 4), nullable=True, comment="复权净值")
    
    # 涨跌字段
    nav_change = Column(DECIMAL(10, 4), nullable=True, comment="净值涨跌额")
    nav_pct_change = Column(DECIMAL(8, 4), nullable=True, comment="净值涨跌幅")
    
    # 交易信息
    subscription_status = Column(String(50), nullable=True, comment="申购状态")
    redemption_status = Column(String(50), nullable=True, comment="赎回状态")
    min_subscription = Column(DECIMAL(12, 2), nullable=True, comment="最低申购金额")
    
    # 分红信息
    dividend_per_share = Column(DECIMAL(10, 4), nullable=True, comment="每份分红")
    dividend_date = Column(DateTime, nullable=True, comment="分红日期")
    
    # 估值信息
    estimated_nav = Column(DECIMAL(10, 4), nullable=True, comment="估算净值")
    estimated_pct_change = Column(DECIMAL(8, 4), nullable=True, comment="估算涨跌幅")
    
    # 折溢价
    premium_rate = Column(DECIMAL(8, 4), nullable=True, comment="溢价率")
    discount_rate = Column(DECIMAL(8, 4), nullable=True, comment="折价率")
    
    @classmethod
    def get_fund_performance(cls, session, fund_symbol: str, 
                           start_date: datetime, end_date: datetime) -> dict:
        """
        获取基金在指定期间的表现
        
        Args:
            session: 数据库会话
            fund_symbol: 基金代码
            start_date: 开始日期
            end_date: 结束日期
            
        Returns:
            dict: 基金表现数据
        """
        from sqlalchemy import func
        
        # 获取期初和期末净值
        start_nav = session.query(cls.nav).filter(
            cls.source_id == fund_symbol,
            cls.trade_date >= start_date,
            cls.trade_date <= end_date,
            cls.is_deleted == False
        ).order_by(cls.trade_date).first()
        
        end_nav = session.query(cls.nav).filter(
            cls.source_id == fund_symbol,
            cls.trade_date >= start_date,
            cls.trade_date <= end_date,
            cls.is_deleted == False
        ).order_by(cls.trade_date.desc()).first()
        
        # 计算统计信息
        stats = session.query(
            func.count(cls.id).label('total_days'),
            func.avg(cls.nav_pct_change).label('avg_daily_return'),
            func.stddev(cls.nav_pct_change).label('daily_volatility'),
            func.max(cls.nav_pct_change).label('max_daily_return'),
            func.min(cls.nav_pct_change).label('min_daily_return'),
        ).filter(
            cls.source_id == fund_symbol,
            cls.trade_date >= start_date,
            cls.trade_date <= end_date,
            cls.is_deleted == False,
            cls.nav_pct_change.isnot(None)
        ).first()
        
        start_nav_value = float(start_nav[0]) if start_nav and start_nav[0] else None
        end_nav_value = float(end_nav[0]) if end_nav and end_nav[0] else None
        
        total_return = None
        if start_nav_value and end_nav_value and start_nav_value > 0:
            total_return = (end_nav_value - start_nav_value) / start_nav_value
        
        return {
            'fund_symbol': fund_symbol,
            'period': {'start': start_date, 'end': end_date},
            'start_nav': start_nav_value,
            'end_nav': end_nav_value,
            'total_return': total_return,
            'total_days': stats.total_days or 0,
            'avg_daily_return': float(stats.avg_daily_return or 0),
            'daily_volatility': float(stats.daily_volatility or 0),
            'max_daily_return': float(stats.max_daily_return or 0),
            'min_daily_return': float(stats.min_daily_return or 0),
        }


class FundNetValue(TimeSeriesModel):
    """基金净值历史"""
    
    __tablename__ = "fund_net_value"
    
    # 净值信息
    unit_nav = Column(DECIMAL(10, 4), nullable=False, comment="单位净值")
    accum_nav = Column(DECIMAL(10, 4), nullable=True, comment="累计净值")
    
    # 分红信息
    dividend = Column(DECIMAL(10, 4), nullable=True, comment="分红")
    split = Column(DECIMAL(10, 6), nullable=True, comment="拆分")
    
    # 增长率
    daily_growth_rate = Column(DECIMAL(8, 4), nullable=True, comment="日增长率")
    weekly_growth_rate = Column(DECIMAL(8, 4), nullable=True, comment="周增长率")
    monthly_growth_rate = Column(DECIMAL(8, 4), nullable=True, comment="月增长率")
    quarterly_growth_rate = Column(DECIMAL(8, 4), nullable=True, comment="季增长率")
    yearly_growth_rate = Column(DECIMAL(8, 4), nullable=True, comment="年增长率")
    
    # 排名信息
    rank_in_category = Column(Integer, nullable=True, comment="同类排名")
    total_in_category = Column(Integer, nullable=True, comment="同类总数")
    rank_percentile = Column(DECIMAL(8, 4), nullable=True, comment="排名百分位")


class FundManager(BaseModel):
    """基金经理信息"""
    
    __tablename__ = "fund_manager"
    
    # 经理信息
    manager_name = Column(String(100), nullable=False, comment="经理姓名")
    manager_gender = Column(String(10), nullable=True, comment="性别")
    manager_birth_year = Column(Integer, nullable=True, comment="出生年份")
    
    # 教育背景
    education = Column(String(200), nullable=True, comment="学历")
    university = Column(String(200), nullable=True, comment="毕业院校")
    major = Column(String(200), nullable=True, comment="专业")
    
    # 从业信息
    qualification_number = Column(String(50), nullable=True, comment="资格证书编号")
    qualification_date = Column(DateTime, nullable=True, comment="资格取得日期")
    experience_years = Column(Integer, nullable=True, comment="从业年限")
    
    # 任职信息
    appointment_date = Column(DateTime, nullable=True, comment="任职日期")
    departure_date = Column(DateTime, nullable=True, comment="离任日期")
    is_current = Column(Boolean, default=True, comment="是否现任")
    
    # 管理基金
    managed_funds = Column(Text, nullable=True, comment="管理基金")
    total_managed_assets = Column(DECIMAL(20, 2), nullable=True, comment="管理总资产")
    
    # 业绩表现
    best_fund = Column(String(100), nullable=True, comment="最佳管理基金")
    best_return = Column(DECIMAL(10, 4), nullable=True, comment="最佳回报率")
    avg_annual_return = Column(DECIMAL(8, 4), nullable=True, comment="平均年化回报")
    
    # 投资风格
    investment_style = Column(String(100), nullable=True, comment="投资风格")
    risk_preference = Column(String(50), nullable=True, comment="风险偏好")
    
    # 获奖情况
    awards = Column(Text, nullable=True, comment="获奖情况")
    honors = Column(Text, nullable=True, comment="荣誉")