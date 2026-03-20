"""
股票数据模型

定义股票相关数据模型。
"""

from datetime import datetime, date
from typing import Optional, Dict, Any
from .base import BaseModel, ValidationError


class StockInfo(BaseModel):
    """股票基本信息模型
    
    对应数据库表: A_stock_info
    """
    
    TABLE_NAME = "A_stock_info"
    
    COLUMNS = {
        "symbol": "VARCHAR(10)",
        "name": "VARCHAR(100)",
        "listing_date": "DATE",
        "total_shares": "BIGINT",
        "float_shares": "BIGINT",
        "industry": "VARCHAR(50)",
        "area": "VARCHAR(50)",
        "market_type": "VARCHAR(10)",
        "update_date": "DATE"
    }
    
    PRIMARY_KEY = ["symbol"]
    
    INDEXES = [
        {"name": "idx_name", "columns": ["name"]},
        {"name": "idx_industry", "columns": ["industry"]},
        {"name": "idx_area", "columns": ["area"]},
        {"name": "idx_update_date", "columns": ["update_date"]}
    ]
    
    REQUIRED_COLUMNS = ["symbol", "name"]
    
    def __init__(
        self,
        symbol: str,
        name: str,
        listing_date: Optional[date] = None,
        total_shares: Optional[int] = None,
        float_shares: Optional[int] = None,
        industry: Optional[str] = None,
        area: Optional[str] = None,
        market_type: Optional[str] = None,
        update_date: Optional[date] = None
    ):
        """初始化股票信息
        
        Args:
            symbol: 股票代码
            name: 股票名称
            listing_date: 上市日期
            total_shares: 总股本
            float_shares: 流通股本
            industry: 所属行业
            area: 地区
            market_type: 市场类型
            update_date: 更新日期
        """
        super().__init__(
            symbol=symbol,
            name=name,
            listing_date=listing_date,
            total_shares=total_shares,
            float_shares=float_shares,
            industry=industry,
            area=area,
            market_type=market_type,
            update_date=update_date
        )
    
    def _custom_validate(self):
        """自定义验证"""
        # 验证股票代码格式
        if self.symbol and not isinstance(self.symbol, str):
            self._errors["symbol"] = "股票代码应为字符串"
        
        # 验证市场类型
        valid_market_types = ["主板", "创业板", "科创板", "北交所", None]
        if self.market_type not in valid_market_types:
            self._errors["market_type"] = f"市场类型应为 {valid_market_types}"
        
        # 验证总股本和流通股本
        if self.total_shares is not None and self.total_shares < 0:
            self._errors["total_shares"] = "总股本不能为负数"
        
        if self.float_shares is not None and self.float_shares < 0:
            self._errors["float_shares"] = "流通股本不能为负数"
        
        if (self.total_shares is not None and self.float_shares is not None and 
            self.float_shares > self.total_shares):
            self._errors["float_shares"] = "流通股本不能大于总股本"


class StockDailyInfo(BaseModel):
    """股票日行情数据模型
    
    对应数据库表: A_stock_daily_info
    """
    
    TABLE_NAME = "A_stock_daily_info"
    
    COLUMNS = {
        "symbol": "VARCHAR(10)",
        "trade_date": "DATE",
        "open_price": "DECIMAL(10,2)",
        "high_price": "DECIMAL(10,2)",
        "low_price": "DECIMAL(10,2)",
        "close_price": "DECIMAL(10,2)",
        "volume": "BIGINT",
        "amount": "DECIMAL(20,2)",
        "change": "DECIMAL(10,2)",
        "change_pct": "DECIMAL(10,4)",
        "turnover_rate": "DECIMAL(10,4)",
        "update_date": "DATE"
    }
    
    PRIMARY_KEY = ["symbol", "trade_date"]
    
    INDEXES = [
        {"name": "idx_trade_date", "columns": ["trade_date"]},
        {"name": "idx_symbol_trade_date", "columns": ["symbol", "trade_date"]},
        {"name": "idx_update_date", "columns": ["update_date"]}
    ]
    
    REQUIRED_COLUMNS = ["symbol", "trade_date", "close_price"]
    
    def __init__(
        self,
        symbol: str,
        trade_date: date,
        open_price: Optional[float] = None,
        high_price: Optional[float] = None,
        low_price: Optional[float] = None,
        close_price: float,
        volume: Optional[int] = None,
        amount: Optional[float] = None,
        change: Optional[float] = None,
        change_pct: Optional[float] = None,
        turnover_rate: Optional[float] = None,
        update_date: Optional[date] = None
    ):
        """初始化股票日行情数据
        
        Args:
            symbol: 股票代码
            trade_date: 交易日期
            open_price: 开盘价
            high_price: 最高价
            low_price: 最低价
            close_price: 收盘价
            volume: 成交量
            amount: 成交额
            change: 涨跌额
            change_pct: 涨跌幅
            turnover_rate: 换手率
            update_date: 更新日期
        """
        super().__init__(
            symbol=symbol,
            trade_date=trade_date,
            open_price=open_price,
            high_price=high_price,
            low_price=low_price,
            close_price=close_price,
            volume=volume,
            amount=amount,
            change=change,
            change_pct=change_pct,
            turnover_rate=turnover_rate,
            update_date=update_date
        )
    
    def _custom_validate(self):
        """自定义验证"""
        # 验证价格合理性
        if self.open_price is not None and self.open_price < 0:
            self._errors["open_price"] = "开盘价不能为负数"
        
        if self.high_price is not None and self.high_price < 0:
            self._errors["high_price"] = "最高价不能为负数"
        
        if self.low_price is not None and self.low_price < 0:
            self._errors["low_price"] = "最低价不能为负数"
        
        if self.close_price < 0:
            self._errors["close_price"] = "收盘价不能为负数"
        
        # 验证价格关系
        if (self.low_price is not None and self.high_price is not None and 
            self.low_price > self.high_price):
            self._errors["low_price"] = "最低价不能大于最高价"
        
        if (self.open_price is not None and self.low_price is not None and 
            self.open_price < self.low_price):
            self._errors["open_price"] = "开盘价不能低于最低价"
        
        if (self.open_price is not None and self.high_price is not None and 
            self.open_price > self.high_price):
            self._errors["open_price"] = "开盘价不能高于最高价"
        
        # 验证成交量
        if self.volume is not None and self.volume < 0:
            self._errors["volume"] = "成交量不能为负数"
        
        # 验证成交额
        if self.amount is not None and self.amount < 0:
            self._errors["amount"] = "成交额不能为负数"
        
        # 验证换手率
        if self.turnover_rate is not None:
            if self.turnover_rate < 0 or self.turnover_rate > 100:
                self._errors["turnover_rate"] = "换手率应在0-100之间"


class StockDividendInfo(BaseModel):
    """股票分红信息模型
    
    对应数据库表: A_stock_dividend_info
    """
    
    TABLE_NAME = "A_stock_dividend_info"
    
    COLUMNS = {
        "symbol": "VARCHAR(10)",
        "dividend_date": "DATE",
        "dividend_cash": "DECIMAL(10,4)",
        "dividend_stock": "DECIMAL(10,4)",
        "dividend_type": "VARCHAR(20)",
        "announce_date": "DATE",
        "ex_dividend_date": "DATE",
        "update_date": "DATE"
    }
    
    PRIMARY_KEY = ["symbol", "dividend_date"]
    
    INDEXES = [
        {"name": "idx_dividend_date", "columns": ["dividend_date"]},
        {"name": "idx_symbol_dividend_date", "columns": ["symbol", "dividend_date"]},
        {"name": "idx_update_date", "columns": ["update_date"]}
    ]
    
    REQUIRED_COLUMNS = ["symbol", "dividend_date"]
    
    def __init__(
        self,
        symbol: str,
        dividend_date: date,
        dividend_cash: Optional[float] = None,
        dividend_stock: Optional[float] = None,
        dividend_type: Optional[str] = None,
        announce_date: Optional[date] = None,
        ex_dividend_date: Optional[date] = None,
        update_date: Optional[date] = None
    ):
        """初始化股票分红信息
        
        Args:
            symbol: 股票代码
            dividend_date: 分红日期
            dividend_cash: 现金分红（每股）
            dividend_stock: 股票分红（每股）
            dividend_type: 分红类型
            announce_date: 公告日期
            ex_dividend_date: 除权除息日
            update_date: 更新日期
        """
        super().__init__(
            symbol=symbol,
            dividend_date=dividend_date,
            dividend_cash=dividend_cash,
            dividend_stock=dividend_stock,
            dividend_type=dividend_type,
            announce_date=announce_date,
            ex_dividend_date=ex_dividend_date,
            update_date=update_date
        )
    
    def _custom_validate(self):
        """自定义验证"""
        # 验证分红金额
        if self.dividend_cash is not None and self.dividend_cash < 0:
            self._errors["dividend_cash"] = "现金分红不能为负数"
        
        if self.dividend_stock is not None and self.dividend_stock < 0:
            self._errors["dividend_stock"] = "股票分红不能为负数"
        
        # 验证日期关系
        if (self.announce_date and self.dividend_date and 
            self.announce_date > self.dividend_date):
            self._errors["announce_date"] = "公告日期不能晚于分红日期"
        
        if (self.ex_dividend_date and self.dividend_date and 
            self.ex_dividend_date > self.dividend_date):
            self._errors["ex_dividend_date"] = "除权除息日不能晚于分红日期"


class InstitutionalTradingInfo(BaseModel):
    """机构交易信息模型
    
    对应数据库表: A_institutional_trading_info
    """
    
    TABLE_NAME = "A_institutional_trading_info"
    
    COLUMNS = {
        "symbol": "VARCHAR(10)",
        "trade_date": "DATE",
        "institution_type": "VARCHAR(50)",
        "buy_volume": "BIGINT",
        "sell_volume": "BIGINT",
        "net_volume": "BIGINT",
        "buy_amount": "DECIMAL(20,2)",
        "sell_amount": "DECIMAL(20,2)",
        "net_amount": "DECIMAL(20,2)",
        "update_date": "DATE"
    }
    
    PRIMARY_KEY = ["symbol", "trade_date", "institution_type"]
    
    INDEXES = [
        {"name": "idx_trade_date", "columns": ["trade_date"]},
        {"name": "idx_symbol_trade_date", "columns": ["symbol", "trade_date"]},
        {"name": "idx_institution_type", "columns": ["institution_type"]},
        {"name": "idx_update_date", "columns": ["update_date"]}
    ]
    
    REQUIRED_COLUMNS = ["symbol", "trade_date", "institution_type"]
    
    def __init__(
        self,
        symbol: str,
        trade_date: date,
        institution_type: str,
        buy_volume: Optional[int] = None,
        sell_volume: Optional[int] = None,
        net_volume: Optional[int] = None,
        buy_amount: Optional[float] = None,
        sell_amount: Optional[float] = None,
        net_amount: Optional[float] = None,
        update_date: Optional[date] = None
    ):
        """初始化机构交易信息
        
        Args:
            symbol: 股票代码
            trade_date: 交易日期
            institution_type: 机构类型
            buy_volume: 买入量
            sell_volume: 卖出量
            net_volume: 净买入量
            buy_amount: 买入金额
            sell_amount: 卖出金额
            net_amount: 净买入金额
            update_date: 更新日期
        """
        super().__init__(
            symbol=symbol,
            trade_date=trade_date,
            institution_type=institution_type,
            buy_volume=buy_volume,
            sell_volume=sell_volume,
            net_volume=net_volume,
            buy_amount=buy_amount,
            sell_amount=sell_amount,
            net_amount=net_amount,
            update_date=update_date
        )
    
    def _custom_validate(self):
        """自定义验证"""
        # 验证交易量
        if self.buy_volume is not None and self.buy_volume < 0:
            self._errors["buy_volume"] = "买入量不能为负数"
        
        if self.sell_volume is not None and self.sell_volume < 0:
            self._errors["sell_volume"] = "卖出量不能为负数"
        
        # 验证金额
        if self.buy_amount is not None and self.buy_amount < 0:
            self._errors["buy_amount"] = "买入金额不能为负数"
        
        if self.sell_amount is not None and self.sell_amount < 0:
            self._errors["sell_amount"] = "卖出金额不能为负数"
        
        # 验证净交易量计算
        if (self.buy_volume is not None and self.sell_volume is not None and 
            self.net_volume is not None):
            calculated_net = self.buy_volume - self.sell_volume
            if self.net_volume != calculated_net:
                self._errors["net_volume"] = f"净买入量计算错误: {self.net_volume} != {calculated_net}"
        
        # 验证净金额计算
        if (self.buy_amount is not None and self.sell_amount is not None and 
            self.net_amount is not None):
            calculated_net = self.buy_amount - self.sell_amount
            if self.net_amount != calculated_net:
                self._errors["net_amount"] = f"净买入金额计算错误: {self.net_amount} != {calculated_net}"