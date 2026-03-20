"""
AKShare客户端封装

封装AKShare常用函数，提供统一的接口、错误处理和类型提示。
遵循金融数据处理的最佳实践。
"""

import akshare as ak
import pandas as pd
from datetime import datetime, date
from typing import Optional, List, Dict, Any, Union
from .base import BaseDataClient, DataCollectionError, DataValidationError


class AKShareClient(BaseDataClient):
    """AKShare客户端封装
    
    提供统一的AKShare接口，包含错误处理、重试机制和速率限制。
    """
    
    def __init__(
        self,
        rate_limit_config=None,
        max_retries: int = 3,
        retry_delay: float = 1.0,
        logger=None
    ):
        """初始化AKShare客户端
        
        Args:
            rate_limit_config: 速率限制配置
            max_retries: 最大重试次数
            retry_delay: 重试延迟（秒）
            logger: 日志记录器
        """
        super().__init__(
            name="AKShare",
            rate_limit_config=rate_limit_config,
            max_retries=max_retries,
            retry_delay=retry_delay,
            logger=logger
        )
    
    def test_connection(self) -> bool:
        """测试AKShare连接
        
        Returns:
            连接是否成功
        """
        try:
            # 尝试获取简单的数据来测试连接
            df = self.execute_with_retry(
                ak.stock_zh_a_spot_em
            )
            return df is not None
        except Exception as e:
            self.logger.error(f"AKShare连接测试失败: {e}")
            return False
    
    def get_stock_spot(self) -> pd.DataFrame:
        """获取A股实时行情数据
        
        Returns:
            包含实时行情数据的DataFrame
        """
        def _get_stock_spot():
            return ak.stock_zh_a_spot_em()
        
        df = self.execute_with_retry(_get_stock_spot)
        
        # 验证数据
        expected_columns = ["代码", "名称", "最新价", "涨跌幅", "涨跌额", "成交量", "成交额"]
        self._validate_dataframe(df, expected_columns)
        
        return df
    
    def get_stock_historical(
        self,
        symbol: str,
        start_date: Union[str, date, datetime],
        end_date: Union[str, date, datetime],
        period: str = "daily",
        adjust: str = ""
    ) -> pd.DataFrame:
        """获取股票历史数据
        
        Args:
            symbol: 股票代码（如"000001"）
            start_date: 开始日期
            end_date: 结束日期
            period: 周期，可选值：daily, weekly, monthly
            adjust: 复权类型，可选值：qfq（前复权）, hfq（后复权）, 空字符串表示不复权
            
        Returns:
            包含历史数据的DataFrame
        """
        # 格式化日期
        if isinstance(start_date, (date, datetime)):
            start_date_str = start_date.strftime("%Y%m%d")
        else:
            start_date_str = str(start_date)
            
        if isinstance(end_date, (date, datetime)):
            end_date_str = end_date.strftime("%Y%m%d")
        else:
            end_date_str = str(end_date)
        
        def _get_stock_historical():
            return ak.stock_zh_a_hist(
                symbol=symbol,
                period=period,
                start_date=start_date_str,
                end_date=end_date_str,
                adjust=adjust
            )
        
        df = self.execute_with_retry(_get_stock_historical)
        
        # 验证数据
        if not df.empty:
            expected_columns = ["日期", "开盘", "收盘", "最高", "最低", "成交量", "成交额", "振幅", "涨跌幅", "涨跌额", "换手率"]
            self._validate_dataframe(df, expected_columns)
        
        return df
    
    def get_index_historical(
        self,
        symbol: str,
        start_date: Union[str, date, datetime],
        end_date: Union[str, date, datetime],
        period: str = "daily"
    ) -> pd.DataFrame:
        """获取指数历史数据
        
        Args:
            symbol: 指数代码（如"000001"）
            start_date: 开始日期
            end_date: 结束日期
            period: 周期，可选值：daily, weekly, monthly
            
        Returns:
            包含指数历史数据的DataFrame
        """
        # 格式化日期
        if isinstance(start_date, (date, datetime)):
            start_date_str = start_date.strftime("%Y%m%d")
        else:
            start_date_str = str(start_date)
            
        if isinstance(end_date, (date, datetime)):
            end_date_str = end_date.strftime("%Y%m%d")
        else:
            end_date_str = str(end_date)
        
        def _get_index_historical():
            return ak.index_zh_a_hist(
                symbol=symbol,
                period=period,
                start_date=start_date_str,
                end_date=end_date_str
            )
        
        df = self.execute_with_retry(_get_index_historical)
        
        # 验证数据
        if not df.empty:
            expected_columns = ["日期", "开盘", "收盘", "最高", "最低", "成交量", "成交额", "振幅", "涨跌幅", "涨跌额"]
            self._validate_dataframe(df, expected_columns)
        
        return df
    
    def get_stock_dividend_history(self) -> pd.DataFrame:
        """获取股票分红历史
        
        Returns:
            包含分红历史数据的DataFrame
        """
        def _get_dividend_history():
            return ak.stock_history_dividend()
        
        df = self.execute_with_retry(_get_dividend_history)
        
        # 验证数据
        if not df.empty:
            expected_columns = ["代码", "名称", "分红年度", "预案公告日", "股权登记日", "除权除息日"]
            self._validate_dataframe(df, expected_columns)
        
        return df
    
    def get_fund_list(self) -> pd.DataFrame:
        """获取基金列表
        
        Returns:
            包含基金列表的DataFrame
        """
        def _get_fund_list():
            return ak.fund_name_em()
        
        df = self.execute_with_retry(_get_fund_list)
        
        # 验证数据
        if not df.empty:
            expected_columns = ["基金代码", "基金简称", "基金类型", "基金经理", "成立日期", "最新规模"]
            self._validate_dataframe(df, expected_columns)
        
        return df
    
    def get_bond_us_rate(
        self,
        start_date: Union[str, date, datetime],
        end_date: Optional[Union[str, date, datetime]] = None
    ) -> pd.DataFrame:
        """获取中国债券与美国国债利差
        
        Args:
            start_date: 开始日期
            end_date: 结束日期（可选，默认使用开始日期）
            
        Returns:
            包含利差数据的DataFrame
        """
        # 格式化日期
        if isinstance(start_date, (date, datetime)):
            start_date_str = start_date.strftime("%Y%m%d")
        else:
            start_date_str = str(start_date)
            
        end_date_str = start_date_str
        if end_date:
            if isinstance(end_date, (date, datetime)):
                end_date_str = end_date.strftime("%Y%m%d")
            else:
                end_date_str = str(end_date)
        
        def _get_bond_us_rate():
            return ak.bond_zh_us_rate(start_date=start_date_str)
        
        df = self.execute_with_retry(_get_bond_us_rate)
        
        # 验证数据
        if not df.empty:
            expected_columns = ["日期", "中国国债收益率", "美国国债收益率", "利差"]
            self._validate_dataframe(df, expected_columns)
        
        return df
    
    def get_institutional_trading(
        self,
        date_str: Union[str, date, datetime]
    ) -> pd.DataFrame:
        """获取机构调研数据
        
        Args:
            date_str: 日期
            
        Returns:
            包含机构调研数据的DataFrame
        """
        # 格式化日期
        if isinstance(date_str, (date, datetime)):
            date_formatted = date_str.strftime("%Y%m%d")
        else:
            date_formatted = str(date_str)
        
        def _get_institutional_trading():
            return ak.stock_jgdy_tj_em(date=date_formatted)
        
        df = self.execute_with_retry(_get_institutional_trading)
        
        # 验证数据
        if not df.empty:
            expected_columns = ["代码", "名称", "调研机构数量", "调研日期", "公告日期"]
            self._validate_dataframe(df, expected_columns)
        
        return df
    
    def get_index_list(self) -> pd.DataFrame:
        """获取指数列表
        
        Returns:
            包含指数列表的DataFrame
        """
        def _get_index_list():
            return ak.index_all_cni()
        
        df = self.execute_with_retry(_get_index_list)
        
        # 验证数据
        if not df.empty:
            expected_columns = ["index_code", "display_name", "publish_date", "type", "base_date"]
            self._validate_dataframe(df, expected_columns)
        
        return df
    
    def get_futures_spot_price(self, date_str: Union[str, date, datetime]) -> pd.DataFrame:
        """获取期货现货价格
        
        Args:
            date_str: 日期
            
        Returns:
            包含期货现货价格数据的DataFrame
        """
        # 格式化日期
        if isinstance(date_str, (date, datetime)):
            date_formatted = date_str.strftime("%Y%m%d")
        else:
            date_formatted = str(date_str)
        
        def _get_futures_spot_price():
            return ak.futures_spot_price_previous(date_formatted)
        
        df = self.execute_with_retry(_get_futures_spot_price)
        
        # 验证数据
        if not df.empty:
            expected_columns = ["商品名称", "现货价格", "期货价格", "基差", "基差率"]
            self._validate_dataframe(df, expected_columns)
        
        return df
    
    def get_stock_dividend_detail(self, symbol: str) -> pd.DataFrame:
        """获取股票分红详情
        
        Args:
            symbol: 股票代码
            
        Returns:
            包含分红详情的DataFrame
        """
        def _get_dividend_detail():
            return ak.stock_fhps_detail_ths(symbol=symbol)
        
        df = self.execute_with_retry(_get_dividend_detail)
        
        # 验证数据
        if not df.empty:
            expected_columns = ["分红年度", "分红方案", "除权除息日", "股权登记日", "派息日"]
            self._validate_dataframe(df, expected_columns)
        
        return df
    
    def get_fund_stock_report(self, report_date: Union[str, date, datetime]) -> pd.DataFrame:
        """获取基金股票持仓报告
        
        Args:
            report_date: 报告日期
            
        Returns:
            包含基金持仓报告的DataFrame
        """
        # 格式化日期
        if isinstance(report_date, (date, datetime)):
            report_date_str = report_date.strftime("%Y%m%d")
        else:
            report_date_str = str(report_date)
        
        def _get_fund_stock_report():
            return ak.fund_report_stock_cninfo(date=report_date_str)
        
        df = self.execute_with_retry(_get_fund_stock_report)
        
        # 验证数据
        if not df.empty:
            expected_columns = ["基金代码", "基金简称", "股票代码", "股票名称", "持仓市值", "占净值比例"]
            self._validate_dataframe(df, expected_columns)
        
        return df