"""
Tushare客户端封装

封装Tushare Pro接口，提供统一的接口、错误处理和类型提示。
遵循金融数据处理的最佳实践。
"""

import tushare as ts
import pandas as pd
from datetime import datetime, date
from typing import Optional, List, Dict, Any, Union
from .base import BaseDataClient, DataCollectionError, DataValidationError


class TushareClient(BaseDataClient):
    """Tushare客户端封装
    
    提供统一的Tushare Pro接口，包含错误处理、重试机制和速率限制。
    """
    
    def __init__(
        self,
        token: Optional[str] = None,
        rate_limit_config=None,
        max_retries: int = 3,
        retry_delay: float = 1.0,
        logger=None
    ):
        """初始化Tushare客户端
        
        Args:
            token: Tushare Pro token（如果为None，则使用环境变量或已设置的token）
            rate_limit_config: 速率限制配置
            max_retries: 最大重试次数
            retry_delay: 重试延迟（秒）
            logger: 日志记录器
        """
        super().__init__(
            name="Tushare",
            rate_limit_config=rate_limit_config,
            max_retries=max_retries,
            retry_delay=retry_delay,
            logger=logger
        )
        
        # 设置token
        if token:
            ts.set_token(token)
        
        # 初始化Pro接口
        self.pro = ts.pro_api()
    
    def test_connection(self) -> bool:
        """测试Tushare连接
        
        Returns:
            连接是否成功
        """
        try:
            # 尝试获取交易日历来测试连接
            df = self.execute_with_retry(
                self.pro.trade_cal,
                exchange='SSE',
                start_date='20240101',
                end_date='20240110'
            )
            return df is not None
        except Exception as e:
            self.logger.error(f"Tushare连接测试失败: {e}")
            return False
    
    def get_daily_data(
        self,
        ts_code: str,
        start_date: Union[str, date, datetime],
        end_date: Union[str, date, datetime],
        adj: Optional[str] = None
    ) -> pd.DataFrame:
        """获取日线行情数据
        
        Args:
            ts_code: 股票代码（格式：000001.SZ）
            start_date: 开始日期
            end_date: 结束日期
            adj: 复权类型，可选值：None（不复权）, qfq（前复权）, hfq（后复权）
            
        Returns:
            包含日线数据的DataFrame
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
        
        def _get_daily_data():
            return self.pro.daily(
                ts_code=ts_code,
                start_date=start_date_str,
                end_date=end_date_str
            )
        
        df = self.execute_with_retry(_get_daily_data)
        
        # 验证数据
        if not df.empty:
            expected_columns = ["ts_code", "trade_date", "open", "high", "low", "close", "pre_close", "change", "pct_chg", "vol", "amount"]
            self._validate_dataframe(df, expected_columns)
        
        return df
    
    def get_stock_basic(
        self,
        exchange: Optional[str] = None,
        list_status: str = 'L'
    ) -> pd.DataFrame:
        """获取股票基本信息
        
        Args:
            exchange: 交易所，可选值：SSE（上交所）, SZSE（深交所）, BSE（北交所）, 空表示全部
            list_status: 上市状态，L（上市）, D（退市）, P（暂停上市）
            
        Returns:
            包含股票基本信息的DataFrame
        """
        def _get_stock_basic():
            return self.pro.stock_basic(
                exchange=exchange,
                list_status=list_status,
                fields='ts_code,symbol,name,area,industry,fullname,enname,market,exchange,curr_type,list_status,list_date,delist_date,is_hs'
            )
        
        df = self.execute_with_retry(_get_stock_basic)
        
        # 验证数据
        if not df.empty:
            expected_columns = ["ts_code", "symbol", "name", "area", "industry"]
            self._validate_dataframe(df, expected_columns)
        
        return df
    
    def get_trade_calendar(
        self,
        exchange: str = 'SSE',
        start_date: Union[str, date, datetime],
        end_date: Union[str, date, datetime],
        is_open: Optional[int] = None
    ) -> pd.DataFrame:
        """获取交易日历
        
        Args:
            exchange: 交易所，SSE（上交所）, SZSE（深交所）
            start_date: 开始日期
            end_date: 结束日期
            is_open: 是否开市，1（开市）, 0（闭市）, None（全部）
            
        Returns:
            包含交易日历的DataFrame
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
        
        def _get_trade_calendar():
            return self.pro.trade_cal(
                exchange=exchange,
                start_date=start_date_str,
                end_date=end_date_str,
                is_open=is_open
            )
        
        df = self.execute_with_retry(_get_trade_calendar)
        
        # 验证数据
        if not df.empty:
            expected_columns = ["exchange", "cal_date", "is_open", "pretrade_date"]
            self._validate_dataframe(df, expected_columns)
        
        return df
    
    def get_daily_basic(
        self,
        ts_code: Optional[str] = None,
        trade_date: Optional[Union[str, date, datetime]] = None,
        start_date: Optional[Union[str, date, datetime]] = None,
        end_date: Optional[Union[str, date, datetime]] = None
    ) -> pd.DataFrame:
        """获取每日指标
        
        Args:
            ts_code: 股票代码
            trade_date: 交易日期（与ts_code二选一）
            start_date: 开始日期（与end_date配合使用）
            end_date: 结束日期（与start_date配合使用）
            
        Returns:
            包含每日指标的DataFrame
        """
        # 格式化日期
        params = {}
        
        if ts_code:
            params['ts_code'] = ts_code
        
        if trade_date:
            if isinstance(trade_date, (date, datetime)):
                params['trade_date'] = trade_date.strftime("%Y%m%d")
            else:
                params['trade_date'] = str(trade_date)
        
        if start_date:
            if isinstance(start_date, (date, datetime)):
                params['start_date'] = start_date.strftime("%Y%m%d")
            else:
                params['start_date'] = str(start_date)
        
        if end_date:
            if isinstance(end_date, (date, datetime)):
                params['end_date'] = end_date.strftime("%Y%m%d")
            else:
                params['end_date'] = str(end_date)
        
        def _get_daily_basic():
            return self.pro.daily_basic(**params)
        
        df = self.execute_with_retry(_get_daily_basic)
        
        # 验证数据
        if not df.empty:
            expected_columns = ["ts_code", "trade_date", "close", "turnover_rate", "turnover_rate_f", "volume_ratio", "pe", "pe_ttm", "pb", "ps", "ps_ttm", "dv_ratio", "dv_ttm", "total_share", "float_share", "free_share", "total_mv", "circ_mv"]
            # 只检查关键列
            key_columns = ["ts_code", "trade_date", "close", "turnover_rate", "pe", "pb"]
            self._validate_dataframe(df, key_columns)
        
        return df
    
    def get_fund_basic(
        self,
        market: str = "O"
    ) -> pd.DataFrame:
        """获取基金基本信息
        
        Args:
            market: 市场类型，E（场内）, O（场外）
            
        Returns:
            包含基金基本信息的DataFrame
        """
        def _get_fund_basic():
            return self.pro.fund_basic(
                market=market,
                status='L'
            )
        
        df = self.execute_with_retry(_get_fund_basic)
        
        # 验证数据
        if not df.empty:
            expected_columns = ["ts_code", "name", "management", "custodian", "fund_type", "found_date", "due_date", "list_date", "issue_date", "delist_date", "issue_amount", "m_fee", "c_fee", "duration_year", "p_value", "min_amount", "exp_return", "benchmark", "status", "invest_type", "type", "trustee", "purc_startdate", "redm_startdate", "market"]
            # 只检查关键列
            key_columns = ["ts_code", "name", "fund_type", "management", "custodian"]
            self._validate_dataframe(df, key_columns)
        
        return df
    
    def get_index_basic(
        self,
        market: str = "SSE"
    ) -> pd.DataFrame:
        """获取指数基本信息
        
        Args:
            market: 市场类型，SSE（上交所）, SZSE（深交所）, CSI（中证）, CICC（中金）
            
        Returns:
            包含指数基本信息的DataFrame
        """
        def _get_index_basic():
            return self.pro.index_basic(
                market=market
            )
        
        df = self.execute_with_retry(_get_index_basic)
        
        # 验证数据
        if not df.empty:
            expected_columns = ["ts_code", "name", "fullname", "market", "publisher", "index_type", "category", "base_date", "base_point", "list_date", "weight_rule", "desc", "exp_date"]
            # 只检查关键列
            key_columns = ["ts_code", "name", "market", "publisher", "index_type"]
            self._validate_dataframe(df, key_columns)
        
        return df
    
    def get_income_statement(
        self,
        ts_code: str,
        period: Optional[str] = None,
        start_date: Optional[Union[str, date, datetime]] = None,
        end_date: Optional[Union[str, date, datetime]] = None
    ) -> pd.DataFrame:
        """获取利润表
        
        Args:
            ts_code: 股票代码
            period: 报告期（YYYYMMDD格式）
            start_date: 开始报告期
            end_date: 结束报告期
            
        Returns:
            包含利润表数据的DataFrame
        """
        params = {"ts_code": ts_code}
        
        if period:
            params['period'] = str(period)
        
        if start_date:
            if isinstance(start_date, (date, datetime)):
                params['start_date'] = start_date.strftime("%Y%m%d")
            else:
                params['start_date'] = str(start_date)
        
        if end_date:
            if isinstance(end_date, (date, datetime)):
                params['end_date'] = end_date.strftime("%Y%m%d")
            else:
                params['end_date'] = str(end_date)
        
        def _get_income_statement():
            return self.pro.income(**params)
        
        df = self.execute_with_retry(_get_income_statement)
        
        # 验证数据
        if not df.empty:
            expected_columns = ["ts_code", "ann_date", "f_ann_date", "end_date", "report_type", "comp_type", "basic_eps", "diluted_eps", "total_revenue", "revenue", "int_income", "prem_earned", "comm_income", "n_commis_income", "n_oth_income", "n_oth_b_income", "prem_income", "out_prem", "une_prem_reser", "reins_income", "n_sec_tb_income", "n_sec_uw_income", "n_asset_mg_income", "oth_b_income", "fv_value_chg_gain", "invest_income", "ass_invest_income", "forex_gain", "total_cogs", "oper_cost", "int_exp", "comm_exp", "biz_tax_surchg", "sell_exp", "admin_exp", "fin_exp", "assets_impair_loss", "prem_refund", "compens_payout", "reser_insur_liab", "div_payt", "reins_exp", "oper_exp", "compens_payout_refu", "insur_reser_refu", "reins_cost_refund", "other_bus_cost", "operate_profit", "non_oper_income", "non_oper_exp", "nca_disploss", "total_profit", "income_tax", "n_income", "n_income_attr_p", "minority_gain", "oth_compr_income", "t_compr_income", "compr_inc_attr_p", "compr_inc_attr_m_s", "ebit", "ebitda", "insurance_exp", "undist_profit", "distable_profit"]
            # 只检查关键列
            key_columns = ["ts_code", "end_date", "total_revenue", "operate_profit", "total_profit", "n_income", "basic_eps"]
            self._validate_dataframe(df, key_columns)
        
        return df
    
    def get_balance_sheet(
        self,
        ts_code: str,
        period: Optional[str] = None,
        start_date: Optional[Union[str, date, datetime]] = None,
        end_date: Optional[Union[str, date, datetime]] = None
    ) -> pd.DataFrame:
        """获取资产负债表
        
        Args:
            ts_code: 股票代码
            period: 报告期（YYYYMMDD格式）
            start_date: 开始报告期
            end_date: 结束报告期
            
        Returns:
            包含资产负债表数据的DataFrame
        """
        params = {"ts_code": ts_code}
        
        if period:
            params['period'] = str(period)
        
        if start_date:
            if isinstance(start_date, (date, datetime)):
                params['start_date'] = start_date.strftime("%Y%m%d")
            else:
                params['start_date'] = str(start_date)
        
        if end_date:
            if isinstance(end_date, (date, datetime)):
                params['end_date'] = end_date.strftime("%Y%m%d")
            else:
                params['end_date'] = str(end_date)
        
        def _get_balance_sheet():
            return self.pro.balancesheet(**params)
        
        df = self.execute_with_retry(_get_balance_sheet)
        
        # 验证数据
        if not df.empty:
            key_columns = ["ts_code", "end_date", "total_assets", "total_liab", "total_equity", "monetary_cap", "accounts_receiv", "inventories", "fixed_assets"]
            self._validate_dataframe(df, key_columns)
        
        return df