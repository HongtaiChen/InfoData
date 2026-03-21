"""
债券数据模型

定义债券相关的数据模型，包括日度行情、基本信息、收益率曲线等。
"""

from datetime import datetime
from sqlalchemy import Column, String, DateTime, Float, Integer, Boolean, Text, DECIMAL
from sqlalchemy.dialects.mysql import LONGTEXT
from .base import BaseModel, TimeSeriesModel, FinancialInstrumentModel


class BondInfo(FinancialInstrumentModel):
    """债券基本信息"""
    
    __tablename__ = "bond_info"
    
    # 债券特定字段
    bond_type = Column(String(50), nullable=True, comment="债券类型")
    bond_category = Column(String(100), nullable=True, comment="债券类别")
    
    # 发行信息
    issuer = Column(String(200), nullable=True, comment="发行人")
    issuer_type = Column(String(50), nullable=True, comment="发行人类型")
    underwriter = Column(String(200), nullable=True, comment="承销商")
    
    # 面值信息
    face_value = Column(DECIMAL(12, 2), nullable=True, comment="面值")
    issue_price = Column(DECIMAL(12, 4), nullable=True, comment="发行价格")
    issue_size = Column(DECIMAL(20, 2), nullable=True, comment="发行规模")
    
    # 期限信息
    issue_date = Column(DateTime, nullable=True, comment="发行日期")
    maturity_date = Column(DateTime, nullable=True, comment="到期日期")
    term = Column(String(50), nullable=True, comment="期限")
    term_years = Column(DECIMAL(8, 4), nullable=True, comment="年限")
    
    # 利率信息
    coupon_rate = Column(DECIMAL(8, 4), nullable=True, comment="票面利率")
    coupon_type = Column(String(50), nullable=True, comment="计息方式")
    payment_frequency = Column(String(50), nullable=True, comment="付息频率")
    next_payment_date = Column(DateTime, nullable=True, comment="下次付息日")
    
    # 信用信息
    credit_rating = Column(String(50), nullable=True, comment="信用评级")
    rating_agency = Column(String(100), nullable=True, comment="评级机构")
    rating_date = Column(DateTime, nullable=True, comment="评级日期")
    
    # 担保信息
    is_guaranteed = Column(Boolean, default=False, comment="是否有担保")
    guarantor = Column(String(200), nullable=True, comment="担保人")
    
    # 交易信息
    listing_date = Column(DateTime, nullable=True, comment="上市日期")
    delisting_date = Column(DateTime, nullable=True, comment="摘牌日期")
    is_tradable = Column(Boolean, default=True, comment="是否可交易")
    
    # 更新信息
    info_updated_at = Column(DateTime, nullable=True, comment="信息更新时间")
    next_update_date = Column(DateTime, nullable=True, comment="下次更新日期")


class BondDaily(TimeSeriesModel):
    """债券日度行情"""
    
    __tablename__ = "bond_daily"
    
    # 价格字段
    clean_price = Column(DECIMAL(12, 4), nullable=True, comment="净价")
    dirty_price = Column(DECIMAL(12, 4), nullable=True, comment="全价")
    accrued_interest = Column(DECIMAL(12, 4), nullable=True, comment="应计利息")
    
    # 收益率字段
    yield_to_maturity = Column(DECIMAL(8, 4), nullable=True, comment="到期收益率")
    yield_to_call = Column(DECIMAL(8, 4), nullable=True, comment="赎回收益率")
    yield_to_put = Column(DECIMAL(8, 4), nullable=True, comment="回售收益率")
    yield_to_worst = Column(DECIMAL(8, 4), nullable=True, comment="最差收益率")
    
    # 利差字段
    credit_spread = Column(DECIMAL(8, 4), nullable=True, comment="信用利差")
    option_adjusted_spread = Column(DECIMAL(8, 4), nullable=True, comment="期权调整利差")
    z_spread = Column(DECIMAL(8, 4), nullable=True, comment="Z利差")
    
    # 久期和凸性
    macaulay_duration = Column(DECIMAL(8, 4), nullable=True, comment="麦考利久期")
    modified_duration = Column(DECIMAL(8, 4), nullable=True, comment="修正久期")
    effective_duration = Column(DECIMAL(8, 4), nullable=True, comment="有效久期")
    convexity = Column(DECIMAL(8, 4), nullable=True, comment="凸性")
    
    # 交易量
    volume = Column(DECIMAL(20, 2), nullable=True, comment="成交量")
    amount = Column(DECIMAL(20, 2), nullable=True, comment="成交额")
    turnover_rate = Column(DECIMAL(8, 4), nullable=True, comment="换手率")
    
    # 买卖报价
    bid_price = Column(DECIMAL(12, 4), nullable=True, comment="买价")
    ask_price = Column(DECIMAL(12, 4), nullable=True, comment="卖价")
    bid_yield = Column(DECIMAL(8, 4), nullable=True, comment="买收益率")
    ask_yield = Column(DECIMAL(8, 4), nullable=True, comment="卖收益率")
    
    # 状态信息
    is_suspended = Column(Boolean, default=False, comment="是否停牌")
    is_default = Column(Boolean, default=False, comment="是否违约")
    
    @classmethod
    def get_bond_yield_curve(cls, session, curve_type: str, 
                            curve_date: datetime) -> list:
        """
        获取债券收益率曲线
        
        Args:
            session: 数据库会话
            curve_type: 曲线类型（国债、信用债等）
            curve_date: 曲线日期
            
        Returns:
            list: 收益率曲线数据
        """
        from sqlalchemy import func
        
        # 按期限分组计算平均收益率
        results = session.query(
            cls.term_years,
            func.avg(cls.yield_to_maturity).label('avg_yield'),
            func.count(cls.id).label('bond_count'),
            func.min(cls.yield_to_maturity).label('min_yield'),
            func.max(cls.yield_to_maturity).label('max_yield'),
        ).filter(
            cls.trade_date == curve_date,
            cls.is_deleted == False,
            cls.yield_to_maturity.isnot(None),
            cls.term_years.isnot(None)
        ).group_by(cls.term_years).order_by(cls.term_years).all()
        
        curve_data = []
        for result in results:
            curve_data.append({
                'term_years': float(result.term_years),
                'avg_yield': float(result.avg_yield),
                'bond_count': result.bond_count,
                'min_yield': float(result.min_yield),
                'max_yield': float(result.max_yield),
                'spread': float(result.max_yield - result.min_yield) if result.max_yield and result.min_yield else None,
            })
        
        return {
            'curve_type': curve_type,
            'curve_date': curve_date,
            'data_points': len(curve_data),
            'curve_data': curve_data,
        }


class BondYield(TimeSeriesModel):
    """债券收益率曲线"""
    
    __tablename__ = "bond_yield"
    
    # 曲线信息
    curve_type = Column(String(50), nullable=False, comment="曲线类型")
    curve_name = Column(String(100), nullable=False, comment="曲线名称")
    
    # 期限点
    term = Column(String(50), nullable=False, comment="期限")
    term_months = Column(Integer, nullable=True, comment="期限月数")
    term_years = Column(DECIMAL(8, 4), nullable=True, comment="期限年数")
    
    # 收益率
    yield_value = Column(DECIMAL(8, 4), nullable=False, comment="收益率")
    yield_change = Column(DECIMAL(8, 4), nullable=True, comment="收益率变化")
    
    # 利差
    spread_to_gov = Column(DECIMAL(8, 4), nullable=True, comment="与国债利差")
    spread_to_benchmark = Column(DECIMAL(8, 4), nullable=True, comment="与基准利差")
    
    # 曲线参数
    is_key_point = Column(Boolean, default=False, comment="是否关键点")
    interpolation_method = Column(String(50), nullable=True, comment="插值方法")


class BondRating(BaseModel):
    """债券评级信息"""
    
    __tablename__ = "bond_rating"
    
    # 评级信息
    rating_agency = Column(String(100), nullable=False, comment="评级机构")
    rating = Column(String(50), nullable=False, comment="评级")
    rating_outlook = Column(String(50), nullable=True, comment="评级展望")
    
    # 评级日期
    rating_date = Column(DateTime, nullable=False, comment="评级日期")
    effective_date = Column(DateTime, nullable=True, comment="生效日期")
    expiry_date = Column(DateTime, nullable=True, comment="失效日期")
    
    # 评级变化
    previous_rating = Column(String(50), nullable=True, comment="先前评级")
    rating_change = Column(String(50), nullable=True, comment="评级变化")
    change_reason = Column(Text, nullable=True, comment="变化原因")
    
    # 评级方法
    rating_methodology = Column(String(200), nullable=True, comment="评级方法")
    rating_criteria = Column(Text, nullable=True, comment="评级标准")
    
    # 分析师
    analyst_name = Column(String(100), nullable=True, comment="分析师姓名")
    analyst_contact = Column(String(200), nullable=True, comment="分析师联系方式")
    
    # 报告信息
    report_title = Column(String(500), nullable=True, comment="报告标题")
    report_url = Column(String(500), nullable=True, comment="报告链接")
    report_date = Column(DateTime, nullable=True, comment="报告日期")