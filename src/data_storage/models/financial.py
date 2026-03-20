"""
金融产品数据模型

定义指数、基金、债券等金融产品数据模型。
"""

from datetime import datetime, date
from typing import Optional, Dict, Any
from .base import BaseModel, ValidationError


class IndexInfo(BaseModel):
    """指数基本信息模型
    
    对应数据库表: A_index_info
    """
    
    TABLE_NAME = "A_index_info"
    
    COLUMNS = {
        "symbol": "VARCHAR(20)",
        "name": "VARCHAR(100)",
        "base_date": "DATE",
        "base_point": "DECIMAL(10,2)",
        "publisher": "VARCHAR(50)",
        "index_type": "VARCHAR(50)",
        "market": "VARCHAR(20)",
        "update_date": "DATE"
    }
    
    PRIMARY_KEY = ["symbol"]
    
    INDEXES = [
        {"name": "idx_name", "columns": ["name"]},
        {"name": "idx_index_type", "columns": ["index_type"]},
        {"name": "idx_market", "columns": ["market"]},
        {"name": "idx_update_date", "columns": ["update_date"]}
    ]
    
    REQUIRED_COLUMNS = ["symbol", "name"]
    
    def __init__(
        self,
        symbol: str,
        name: str,
        base_date: Optional[date] = None,
        base_point: Optional[float] = None,
        publisher: Optional[str] = None,
        index_type: Optional[str] = None,
        market: Optional[str] = None,
        update_date: Optional[date] = None
    ):
        """初始化指数信息
        
        Args:
            symbol: 指数代码
            name: 指数名称
            base_date: 基日
            base_point: 基点
            publisher: 发布机构
            index_type: 指数类型
            market: 市场
            update_date: 更新日期
        """
        super().__init__(
            symbol=symbol,
            name=name,
            base_date=base_date,
            base_point=base_point,
            publisher=publisher,
            index_type=index_type,
            market=market,
            update_date=update_date
        )
    
    def _custom_validate(self):
        """自定义验证"""
        # 验证基点
        if self.base_point is not None and self.base_point <= 0:
            self._errors["base_point"] = "基点必须大于0"
        
        # 验证市场类型
        valid_markets = ["上证", "深证", "中证", "国证", "其他", None]
        if self.market not in valid_markets:
            self._errors["market"] = f"市场类型应为 {valid_markets}"


class FundInfo(BaseModel):
    """基金基本信息模型
    
    对应数据库表: A_fund_info
    """
    
    TABLE_NAME = "A_fund_info"
    
    COLUMNS = {
        "symbol": "VARCHAR(20)",
        "name": "VARCHAR(100)",
        "fund_type": "VARCHAR(50)",
        "management": "VARCHAR(100)",
        "custodian": "VARCHAR(100)",
        "found_date": "DATE",
        "issue_amount": "DECIMAL(20,2)",
        "net_asset_value": "DECIMAL(10,4)",
        "accumulated_nav": "DECIMAL(10,4)",
        "update_date": "DATE"
    }
    
    PRIMARY_KEY = ["symbol"]
    
    INDEXES = [
        {"name": "idx_name", "columns": ["name"]},
        {"name": "idx_fund_type", "columns": ["fund_type"]},
        {"name": "idx_management", "columns": ["management"]},
        {"name": "idx_update_date", "columns": ["update_date"]}
    ]
    
    REQUIRED_COLUMNS = ["symbol", "name", "fund_type"]
    
    def __init__(
        self,
        symbol: str,
        name: str,
        fund_type: str,
        management: Optional[str] = None,
        custodian: Optional[str] = None,
        found_date: Optional[date] = None,
        issue_amount: Optional[float] = None,
        net_asset_value: Optional[float] = None,
        accumulated_nav: Optional[float] = None,
        update_date: Optional[date] = None
    ):
        """初始化基金信息
        
        Args:
            symbol: 基金代码
            name: 基金名称
            fund_type: 基金类型
            management: 基金管理人
            custodian: 基金托管人
            found_date: 成立日期
            issue_amount: 发行规模
            net_asset_value: 单位净值
            accumulated_nav: 累计净值
            update_date: 更新日期
        """
        super().__init__(
            symbol=symbol,
            name=name,
            fund_type=fund_type,
            management=management,
            custodian=custodian,
            found_date=found_date,
            issue_amount=issue_amount,
            net_asset_value=net_asset_value,
            accumulated_nav=accumulated_nav,
            update_date=update_date
        )
    
    def _custom_validate(self):
        """自定义验证"""
        # 验证基金类型
        valid_fund_types = ["股票型", "债券型", "混合型", "货币型", "指数型", "QDII", "其他"]
        if self.fund_type not in valid_fund_types:
            self._errors["fund_type"] = f"基金类型应为 {valid_fund_types}"
        
        # 验证净值
        if self.net_asset_value is not None and self.net_asset_value < 0:
            self._errors["net_asset_value"] = "单位净值不能为负数"
        
        if self.accumulated_nav is not None and self.accumulated_nav < 0:
            self._errors["accumulated_nav"] = "累计净值不能为负数"
        
        if (self.net_asset_value is not None and self.accumulated_nav is not None and 
            self.accumulated_nav < self.net_asset_value):
            self._errors["accumulated_nav"] = "累计净值不能小于单位净值"
        
        # 验证发行规模
        if self.issue_amount is not None and self.issue_amount < 0:
            self._errors["issue_amount"] = "发行规模不能为负数"


class BondInfo(BaseModel):
    """债券基本信息模型
    
    对应数据库表: A_bond_info
    """
    
    TABLE_NAME = "A_bond_info"
    
    COLUMNS = {
        "symbol": "VARCHAR(20)",
        "name": "VARCHAR(100)",
        "bond_type": "VARCHAR(50)",
        "issuer": "VARCHAR(100)",
        "issue_date": "DATE",
        "maturity_date": "DATE",
        "coupon_rate": "DECIMAL(10,4)",
        "face_value": "DECIMAL(10,2)",
        "issue_amount": "DECIMAL(20,2)",
        "credit_rating": "VARCHAR(10)",
        "update_date": "DATE"
    }
    
    PRIMARY_KEY = ["symbol"]
    
    INDEXES = [
        {"name": "idx_name", "columns": ["name"]},
        {"name": "idx_bond_type", "columns": ["bond_type"]},
        {"name": "idx_issuer", "columns": ["issuer"]},
        {"name": "idx_credit_rating", "columns": ["credit_rating"]},
        {"name": "idx_update_date", "columns": ["update_date"]}
    ]
    
    REQUIRED_COLUMNS = ["symbol", "name", "bond_type"]
    
    def __init__(
        self,
        symbol: str,
        name: str,
        bond_type: str,
        issuer: Optional[str] = None,
        issue_date: Optional[date] = None,
        maturity_date: Optional[date] = None,
        coupon_rate: Optional[float] = None,
        face_value: Optional[float] = None,
        issue_amount: Optional[float] = None,
        credit_rating: Optional[str] = None,
        update_date: Optional[date] = None
    ):
        """初始化债券信息
        
        Args:
            symbol: 债券代码
            name: 债券名称
            bond_type: 债券类型
            issuer: 发行人
            issue_date: 发行日期
            maturity_date: 到期日期
            coupon_rate: 票面利率
            face_value: 面值
            issue_amount: 发行规模
            credit_rating: 信用评级
            update_date: 更新日期
        """
        super().__init__(
            symbol=symbol,
            name=name,
            bond_type=bond_type,
            issuer=issuer,
            issue_date=issue_date,
            maturity_date=maturity_date,
            coupon_rate=coupon_rate,
            face_value=face_value,
            issue_amount=issue_amount,
            credit_rating=credit_rating,
            update_date=update_date
        )
    
    def _custom_validate(self):
        """自定义验证"""
        # 验证债券类型
        valid_bond_types = ["国债", "金融债", "企业债", "公司债", "可转债", "其他"]
        if self.bond_type not in valid_bond_types:
            self._errors["bond_type"] = f"债券类型应为 {valid_bond_types}"
        
        # 验证日期关系
        if self.issue_date and self.maturity_date and self.issue_date > self.maturity_date:
            self._errors["issue_date"] = "发行日期不能晚于到期日期"
        
        # 验证票面利率
        if self.coupon_rate is not None:
            if self.coupon_rate < 0 or self.coupon_rate > 100:
                self._errors["coupon_rate"] = "票面利率应在0-100之间"
        
        # 验证面值
        if self.face_value is not None and self.face_value <= 0:
            self._errors["face_value"] = "面值必须大于0"
        
        # 验证发行规模
        if self.issue_amount is not None and self.issue_amount < 0:
            self._errors["issue_amount"] = "发行规模不能为负数"
        
        # 验证信用评级格式（简单验证）
        if self.credit_rating and not isinstance(self.credit_rating, str):
            self._errors["credit_rating"] = "信用评级应为字符串"


class IndexDailyInfo(BaseModel):
    """指数日行情数据模型
    
    对应数据库表: A_index_daily_info
    """
    
    TABLE_NAME = "A_index_daily_info"
    
    COLUMNS = {
        "symbol": "VARCHAR(20)",
        "trade_date": "DATE",
        "open_price": "DECIMAL(10,2)",
        "high_price": "DECIMAL(10,2)",
        "low_price": "DECIMAL(10,2)",
        "close_price": "DECIMAL(10,2)",
        "volume": "BIGINT",
        "amount": "DECIMAL(20,2)",
        "change": "DECIMAL(10,2)",
        "change_pct": "DECIMAL(10,4)",
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
        update_date: Optional[date] = None
    ):
        """初始化指数日行情数据
        
        Args:
            symbol: 指数代码
            trade_date: 交易日期
            open_price: 开盘价
            high_price: 最高价
            low_price: 最低价
            close_price: 收盘价
            volume: 成交量
            amount: 成交额
            change: 涨跌额
            change_pct: 涨跌幅
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
            update_date=update_date
        )
    
    def _custom_validate(self):
        """自定义验证"""
        # 验证价格合理性（与股票日行情类似）
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