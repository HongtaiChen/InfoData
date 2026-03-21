"""
股票数据模型

定义股票相关的数据模型，包括日度行情、基本信息、行业分类等。
"""

from datetime import datetime
from sqlalchemy import Column, String, DateTime, Float, Integer, Boolean, Text, DECIMAL
from sqlalchemy.dialects.mysql import LONGTEXT
from .base import BaseModel, TimeSeriesModel, FinancialInstrumentModel


class StockInfo(FinancialInstrumentModel):
    """股票基本信息"""
    
    __tablename__ = "stock_info"
    
    # 股票特定字段
    stock_type = Column(String(50), nullable=True, comment="股票类型")
    listing_board = Column(String(50), nullable=True, comment="上市板块")
    
    # 公司信息
    company_name = Column(String(500), nullable=True, comment="公司名称")
    company_en_name = Column(String(500), nullable=True, comment="公司英文名称")
    legal_representative = Column(String(100), nullable=True, comment="法定代表人")
    registered_capital = Column(DECIMAL(20, 2), nullable=True, comment="注册资本")
    registered_address = Column(String(500), nullable=True, comment="注册地址")
    office_address = Column(String(500), nullable=True, comment="办公地址")
    
    # 财务信息
    total_shares = Column(DECIMAL(20, 2), nullable=True, comment="总股本")
    circulating_shares = Column(DECIMAL(20, 2), nullable=True, comment="流通股本")
    market_cap = Column(DECIMAL(20, 2), nullable=True, comment="总市值")
    circulating_market_cap = Column(DECIMAL(20, 2), nullable=True, comment="流通市值")
    
    # 分类信息
    sse_sector = Column(String(100), nullable=True, comment="上交所行业")
    szse_sector = Column(String(100), nullable=True, comment="深交所行业")
    csrc_sector = Column(String(100), nullable=True, comment="证监会行业")
    
    # 指数成分
    is_sh50 = Column(Boolean, default=False, comment="是否上证50")
    is_sz50 = Column(Boolean, default=False, comment="是否深证50")
    is_hs300 = Column(Boolean, default=False, comment="是否沪深300")
    is_zz500 = Column(Boolean, default=False, comment="是否中证500")
    is_zz1000 = Column(Boolean, default=False, comment="是否中证1000")
    
    # 更新信息
    info_updated_at = Column(DateTime, nullable=True, comment="信息更新时间")
    next_update_date = Column(DateTime, nullable=True, comment="下次更新日期")


class StockDaily(TimeSeriesModel):
    """股票日度行情"""
    
    __tablename__ = "stock_daily"
    
    # 行情字段
    open_price = Column(DECIMAL(12, 4), nullable=True, comment="开盘价")
    high_price = Column(DECIMAL(12, 4), nullable=True, comment="最高价")
    low_price = Column(DECIMAL(12, 4), nullable=True, comment="最低价")
    close_price = Column(DECIMAL(12, 4), nullable=True, comment="收盘价")
    pre_close = Column(DECIMAL(12, 4), nullable=True, comment="前收盘价")
    
    # 交易量字段
    volume = Column(DECIMAL(20, 2), nullable=True, comment="成交量")
    amount = Column(DECIMAL(20, 2), nullable=True, comment="成交额")
    
    # 涨跌字段
    change = Column(DECIMAL(12, 4), nullable=True, comment="涨跌额")
    pct_change = Column(DECIMAL(8, 4), nullable=True, comment="涨跌幅")
    amplitude = Column(DECIMAL(8, 4), nullable=True, comment="振幅")
    
    # 换手率
    turnover_rate = Column(DECIMAL(8, 4), nullable=True, comment="换手率")
    
    # 调整后价格（复权）
    adj_open = Column(DECIMAL(12, 4), nullable=True, comment="复权开盘价")
    adj_high = Column(DECIMAL(12, 4), nullable=True, comment="复权最高价")
    adj_low = Column(DECIMAL(12, 4), nullable=True, comment="复权最低价")
    adj_close = Column(DECIMAL(12, 4), nullable=True, comment="复权收盘价")
    
    # 技术指标
    ma5 = Column(DECIMAL(12, 4), nullable=True, comment="5日均线")
    ma10 = Column(DECIMAL(12, 4), nullable=True, comment="10日均线")
    ma20 = Column(DECIMAL(12, 4), nullable=True, comment="20日均线")
    ma30 = Column(DECIMAL(12, 4), nullable=True, comment="30日均线")
    ma60 = Column(DECIMAL(12, 4), nullable=True, comment="60日均线")
    
    # 市场数据
    pe_ratio = Column(DECIMAL(10, 4), nullable=True, comment="市盈率")
    pb_ratio = Column(DECIMAL(10, 4), nullable=True, comment="市净率")
    ps_ratio = Column(DECIMAL(10, 4), nullable=True, comment="市销率")
    dividend_yield = Column(DECIMAL(8, 4), nullable=True, comment="股息率")
    
    # 数据质量标记
    is_suspended = Column(Boolean, default=False, comment="是否停牌")
    is_st = Column(Boolean, default=False, comment="是否ST")
    is_new = Column(Boolean, default=False, comment="是否新股")
    
    @classmethod
    def get_daily_summary(cls, session, trade_date: datetime) -> dict:
        """
        获取指定交易日的市场摘要
        
        Args:
            session: 数据库会话
            trade_date: 交易日期
            
        Returns:
            dict: 市场摘要
        """
        from sqlalchemy import func
        
        result = session.query(
            func.count(cls.id).label('total_stocks'),
            func.sum(cls.amount).label('total_amount'),
            func.avg(cls.pct_change).label('avg_change'),
            func.sum(func.case((cls.pct_change > 0, 1), else_=0)).label('up_count'),
            func.sum(func.case((cls.pct_change < 0, 1), else_=0)).label('down_count'),
            func.sum(func.case((cls.pct_change == 0, 1), else_=0)).label('flat_count'),
        ).filter(
            cls.trade_date == trade_date,
            cls.is_deleted == False,
            cls.is_suspended == False
        ).first()
        
        return {
            'trade_date': trade_date,
            'total_stocks': result.total_stocks or 0,
            'total_amount': float(result.total_amount or 0),
            'avg_change': float(result.avg_change or 0),
            'up_count': result.up_count or 0,
            'down_count': result.down_count or 0,
            'flat_count': result.flat_count or 0,
        }


class StockIndustry(BaseModel):
    """股票行业分类"""
    
    __tablename__ = "stock_industry"
    
    # 行业信息
    industry_code = Column(String(50), nullable=False, comment="行业代码")
    industry_name = Column(String(200), nullable=False, comment="行业名称")
    industry_level = Column(Integer, default=1, comment="行业层级")
    parent_industry = Column(String(50), nullable=True, comment="父行业")
    
    # 分类标准
    classification_standard = Column(String(50), nullable=False, comment="分类标准")
    classification_version = Column(String(50), nullable=True, comment="分类版本")
    
    # 统计信息
    stock_count = Column(Integer, default=0, comment="股票数量")
    total_market_cap = Column(DECIMAL(20, 2), nullable=True, comment="总市值")
    avg_pe_ratio = Column(DECIMAL(10, 4), nullable=True, comment="平均市盈率")
    
    # 描述信息
    description = Column(Text, nullable=True, comment="行业描述")
    characteristics = Column(Text, nullable=True, comment="行业特征")


class StockConcept(BaseModel):
    """股票概念板块"""
    
    __tablename__ = "stock_concept"
    
    # 概念信息
    concept_code = Column(String(50), nullable=False, comment="概念代码")
    concept_name = Column(String(200), nullable=False, comment="概念名称")
    concept_type = Column(String(50), nullable=True, comment="概念类型")
    
    # 关联信息
    related_industries = Column(Text, nullable=True, comment="相关行业")
    hot_level = Column(Integer, default=0, comment="热度等级")
    
    # 统计信息
    stock_count = Column(Integer, default=0, comment="成分股数量")
    leading_stocks = Column(Text, nullable=True, comment="龙头股")
    
    # 描述信息
    description = Column(Text, nullable=True, comment="概念描述")
    investment_logic = Column(Text, nullable=True, comment="投资逻辑")


class StockHolder(BaseModel):
    """股票股东信息"""
    
    __tablename__ = "stock_holder"
    
    # 股东信息
    holder_name = Column(String(200), nullable=False, comment="股东名称")
    holder_type = Column(String(50), nullable=True, comment="股东类型")
    holder_rank = Column(Integer, nullable=True, comment="股东排名")
    
    # 持股信息
    share_count = Column(DECIMAL(20, 2), nullable=True, comment="持股数量")
    share_ratio = Column(DECIMAL(8, 4), nullable=True, comment="持股比例")
    share_change = Column(DECIMAL(20, 2), nullable=True, comment="持股变化")
    change_ratio = Column(DECIMAL(8, 4), nullable=True, comment="变化比例")
    
    # 时间信息
    report_date = Column(DateTime, nullable=False, comment="报告日期")
    report_period = Column(String(50), nullable=True, comment="报告期")
    
    # 股东性质
    is_institution = Column(Boolean, default=False, comment="是否机构")
    is_controlling = Column(Boolean, default=False, comment="是否控股股东")
    is_executive = Column(Boolean, default=False, comment="是否高管")


class StockDividend(BaseModel):
    """股票分红信息"""
    
    __tablename__ = "stock_dividend"
    
    # 分红信息
    dividend_year = Column(Integer, nullable=False, comment="分红年度")
    dividend_date = Column(DateTime, nullable=False, comment="分红日期")
    ex_dividend_date = Column(DateTime, nullable=True, comment="除权除息日")
    record_date = Column(DateTime, nullable=True, comment="股权登记日")
    
    # 分红方案
    cash_dividend = Column(DECIMAL(10, 4), nullable=True, comment="现金分红")
    share_dividend = Column(DECIMAL(10, 4), nullable=True, comment="送股")
    transfer_share = Column(DECIMAL(10, 4), nullable=True, comment="转增股")
    
    # 分红比例
    dividend_ratio = Column(DECIMAL(8, 4), nullable=True, comment="分红比例")
    payout_ratio = Column(DECIMAL(8, 4), nullable=True, comment="派息率")
    
    # 状态信息
    dividend_status = Column(String(50), nullable=True, comment="分红状态")
    is_implemented = Column(Boolean, default=False, comment="是否已实施")
    
    # 公告信息
    announcement_date = Column(DateTime, nullable=True, comment="公告日期")
    implementation_date = Column(DateTime, nullable=True, comment="实施日期")


class StockSplit(BaseModel):
    """股票拆分信息"""
    
    __tablename__ = "stock_split"
    
    # 拆分信息
    split_date = Column(DateTime, nullable=False, comment="拆分日期")
    split_type = Column(String(50), nullable=False, comment="拆分类型")
    
    # 拆分比例
    split_ratio = Column(String(50), nullable=False, comment="拆分比例")
    from_shares = Column(Integer, nullable=True, comment="拆分前股数")
    to_shares = Column(Integer, nullable=True, comment="拆分后股数")
    
    # 价格调整
    pre_split_price = Column(DECIMAL(12, 4), nullable=True, comment="拆分前价格")
    post_split_price = Column(DECIMAL(12, 4), nullable=True, comment="拆分后价格")
    adjustment_factor = Column(DECIMAL(10, 6), nullable=True, comment="调整因子")
    
    # 状态信息
    split_status = Column(String(50), nullable=True, comment="拆分状态")
    is_completed = Column(Boolean, default=False, comment="是否已完成")
    
    # 公告信息
    announcement_date = Column(DateTime, nullable=True, comment="公告日期")
    effective_date = Column(DateTime, nullable=True, comment="生效日期")