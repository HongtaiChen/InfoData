"""
Tushare数据源适配器

实现Tushare数据源的数据收集功能。
"""

import time
import pandas as pd
from datetime import datetime, date, timedelta
from typing import Dict, List, Any, Optional, Union
import tushare as ts

from .base import (
    DataSourceAdapter, DataSourceConfig, DataCollectionResult,
    DataType, DataSourceStatus
)
from ...utils.logging import get_logger

logger = get_logger(__name__)


class TushareAdapter(DataSourceAdapter):
    """Tushare数据源适配器"""
    
    def __init__(self, config: DataSourceConfig):
        """
        初始化Tushare适配器
        
        Args:
            config: 数据源配置
        """
        super().__init__(config)
        self.tushare_client = None
        self._init_tushare()
    
    def _init_tushare(self) -> None:
        """初始化Tushare客户端"""
        try:
            # 从配置中获取API密钥
            api_key = self.config.api_key or self.config.metadata.get('api_key')
            
            if not api_key:
                logger.warning("Tushare API密钥未配置，将使用默认密钥")
                # 使用项目中的默认密钥
                api_key = "d74c40bf7bb33a39e27a8e8f47d1d628b09560c652f9caf713dc9db0"
            
            # 设置Tushare令牌
            ts.set_token(api_key)
            self.tushare_client = ts.pro_api()
            
            logger.info("Tushare客户端初始化完成")
        except Exception as e:
            logger.error(f"Tushare客户端初始化失败: {e}")
            self.tushare_client = None
    
    def connect(self) -> bool:
        """
        连接到Tushare数据源
        
        Returns:
            bool: 连接是否成功
        """
        try:
            if self.tushare_client is None:
                self._init_tushare()
            
            # 测试API连接
            test_result = self.tushare_client.query('trade_cal', start_date='20250101', end_date='20250110')
            if test_result is not None:
                self.status = DataSourceStatus.CONNECTED
                self.last_connection_time = datetime.now()
                logger.info("Tushare数据源连接成功")
                return True
            else:
                self.status = DataSourceStatus.ERROR
                logger.error("Tushare数据源连接测试失败")
                return False
        except Exception as e:
            self.status = DataSourceStatus.ERROR
            logger.error(f"Tushare数据源连接失败: {e}")
            return False
    
    def disconnect(self) -> bool:
        """
        断开Tushare数据源连接
        
        Returns:
            bool: 断开是否成功
        """
        # Tushare不需要显式断开连接
        self.status = DataSourceStatus.DISCONNECTED
        logger.info("Tushare数据源已断开")
        return True
    
    def get_status(self) -> Dict[str, Any]:
        """
        获取Tushare数据源状态
        
        Returns:
            Dict[str, Any]: 状态信息
        """
        return {
            "source": "tushare",
            "status": self.status.value,
            "last_connection": self.last_connection_time,
            "error_count": self.error_count,
            "total_requests": self.total_requests,
            "success_rate": (
                self.successful_requests / self.total_requests 
                if self.total_requests > 0 else 0
            ),
            "tushare_version": ts.__version__ if hasattr(ts, '__version__') else "unknown",
        }
    
    def collect_stock_daily(
        self, 
        symbols: Optional[List[str]] = None,
        start_date: Optional[Union[str, date, datetime]] = None,
        end_date: Optional[Union[str, date, datetime]] = None,
        **kwargs
    ) -> DataCollectionResult:
        """
        收集股票日度行情数据
        
        Args:
            symbols: 股票代码列表，如果为None则收集所有股票
            start_date: 开始日期
            end_date: 结束日期
            **kwargs: 其他参数
            
        Returns:
            DataCollectionResult: 收集结果
        """
        start_time = datetime.now()
        
        try:
            # 验证连接
            if not self._validate_connection():
                raise ConnectionError("Tushare数据源未连接")
            
            # 设置默认日期
            if end_date is None:
                end_date = datetime.now()
            
            if start_date is None:
                start_date = end_date - timedelta(days=30)
            
            # 格式化日期
            start_date_str = self._format_date(start_date)
            end_date_str = self._format_date(end_date)
            
            logger.info(f"开始收集股票日度行情数据: {start_date_str} 到 {end_date_str}")
            
            # 收集数据
            all_data = []
            
            if symbols:
                # 收集指定股票的数据
                for symbol in symbols:
                    try:
                        logger.debug(f"收集股票 {symbol} 的日度行情数据")
                        
                        # 获取股票日度数据
                        stock_data = self.tushare_client.daily(
                            ts_code=symbol,
                            start_date=start_date_str,
                            end_date=end_date_str
                        )
                        
                        if not stock_data.empty:
                            # 添加股票代码信息
                            stock_data['symbol'] = symbol
                            all_data.append(stock_data)
                            
                            logger.debug(f"股票 {symbol} 收集到 {len(stock_data)} 条记录")
                        else:
                            logger.warning(f"股票 {symbol} 未找到数据")
                        
                        # 遵守频率限制
                        time.sleep(1 / self.config.rate_limit)
                        
                    except Exception as e:
                        logger.error(f"收集股票 {symbol} 数据失败: {e}")
                        continue
            else:
                # 收集所有股票的数据（这里实现一个简化的版本）
                logger.warning("未指定股票代码，将收集A股市场主要股票数据")
                
                try:
                    # 获取股票列表
                    stock_list = self.tushare_client.stock_basic(
                        exchange='',
                        list_status='L',
                        fields='ts_code,symbol,name,area,industry,list_date'
                    )
                    
                    if not stock_list.empty:
                        # 取前50只股票
                        top_stocks = stock_list.head(50)['ts_code'].tolist()
                        
                        for symbol in top_stocks:
                            try:
                                logger.debug(f"收集主要股票 {symbol} 的日度数据")
                                
                                stock_data = self.tushare_client.daily(
                                    ts_code=symbol,
                                    start_date=start_date_str,
                                    end_date=end_date_str
                                )
                                
                                if not stock_data.empty:
                                    stock_data['symbol'] = symbol
                                    all_data.append(stock_data)
                                
                                time.sleep(1 / self.config.rate_limit)
                                
                            except Exception as e:
                                logger.error(f"收集股票 {symbol} 数据失败: {e}")
                                continue
                    
                    logger.info(f"收集到 {len(all_data)} 只股票的数据")
                    
                except Exception as e:
                    logger.error(f"收集股票列表失败: {e}")
            
            # 合并所有数据
            if all_data:
                combined_data = pd.concat(all_data, ignore_index=True)
                
                # 数据转换和标准化
                processed_data = self._process_stock_daily_data(combined_data)
                
                result = DataCollectionResult(
                    data_type=DataType.STOCK_DAILY,
                    source_name=self.config.name,
                    start_time=start_time,
                    end_time=datetime.now(),
                    records_collected=len(combined_data),
                    records_processed=len(processed_data),
                    success=True,
                    raw_data=combined_data.to_dict('records'),
                    processed_data=processed_data,
                    metadata={
                        "start_date": start_date_str,
                        "end_date": end_date_str,
                        "symbol_count": len(symbols) if symbols else "top_50",
                    }
                )
            else:
                result = DataCollectionResult(
                    data_type=DataType.STOCK_DAILY,
                    source_name=self.config.name,
                    start_time=start_time,
                    end_time=datetime.now(),
                    success=False,
                    error_message="未收集到任何数据",
                    metadata={
                        "start_date": start_date_str,
                        "end_date": end_date_str,
                    }
                )
            
            return result
            
        except Exception as e:
            self._handle_error(e)
            return DataCollectionResult(
                data_type=DataType.STOCK_DAILY,
                source_name=self.config.name,
                start_time=start_time,
                end_time=datetime.now(),
                success=False,
                error_message=str(e),
                metadata={"error_type": type(e).__name__}
            )
    
    def collect_stock_info(
        self,
        symbols: Optional[List[str]] = None,
        **kwargs
    ) -> DataCollectionResult:
        """
        收集股票基本信息
        
        Args:
            symbols: 股票代码列表，如果为None则收集所有股票
            **kwargs: 其他参数
            
        Returns:
            DataCollectionResult: 收集结果
        """
        start_time = datetime.now()
        
        try:
            # 验证连接
            if not self._validate_connection():
                raise ConnectionError("Tushare数据源未连接")
            
            logger.info("开始收集股票基本信息")
            
            # 收集数据
            if symbols:
                # 收集指定股票的信息
                all_data = []
                
                for symbol in symbols:
                    try:
                        logger.debug(f"收集股票 {symbol} 的基本信息")
                        
                        # 获取股票基本信息
                        stock_info = self.tushare_client.stock_basic(
                            ts_code=symbol,
                            fields='ts_code,symbol,name,area,industry,list_date,market,is_hs'
                        )
                        
                        if not stock_info.empty:
                            # 添加股票代码信息
                            stock_info = stock_info.copy()
                            stock_info['symbol'] = symbol
                            all_data.append(stock_info)
                            
                            logger.debug(f"股票 {symbol} 基本信息收集成功")
                        else:
                            logger.warning(f"股票 {symbol} 未找到基本信息")
                        
                        # 遵守频率限制
                        time.sleep(1 / self.config.rate_limit)
                        
                    except Exception as e:
                        logger.error(f"收集股票 {symbol} 基本信息失败: {e}")
                        continue
                
                if all_data:
                    combined_data = pd.concat(all_data, ignore_index=True)
                else:
                    combined_data = pd.DataFrame()
                    
            else:
                # 收集所有股票的基本信息
                logger.info("收集所有A股股票的基本信息")
                
                try:
                    # 获取股票列表
                    combined_data = self.tushare_client.stock_basic(
                        exchange='',
                        list_status='L',
                        fields='ts_code,symbol,name,area,industry,list_date,market,is_hs'
                    )
                    
                    if not combined_data.empty:
                        # 添加symbol字段
                        combined_data = combined_data.copy()
                        combined_data['symbol'] = combined_data['ts_code']
                        
                        logger.info(f"收集到 {len(combined_data)} 条股票基本信息")
                    else:
                        logger.warning("未找到股票基本信息")
                        combined_data = pd.DataFrame()
                        
                except Exception as e:
                    logger.error(f"收集股票基本信息失败: {e}")
                    combined_data = pd.DataFrame()
            
            # 数据处理
            if not combined_data.empty:
                processed_data = self._process_stock_info_data(combined_data)
                
                result = DataCollectionResult(
                    data_type=DataType.STOCK_INFO,
                    source_name=self.config.name,
                    start_time=start_time,
                    end_time=datetime.now(),
                    records_collected=len(combined_data),
                    records_processed=len(processed_data),
                    success=True,
                    raw_data=combined_data.to_dict('records'),
                    processed_data=processed_data,
                    metadata={
                        "symbol_count": len(symbols) if symbols else "all",
                    }
                )
            else:
                result = DataCollectionResult(
                    data_type=DataType.STOCK_INFO,
                    source_name=self.config.name,
                    start_time=start_time,
                    end_time=datetime.now(),
                    success=False,
                    error_message="未收集到任何股票基本信息",
                    metadata={}
                )
            
            return result
            
        except Exception as e:
            self._handle_error(e)
            return DataCollectionResult(
                data_type=DataType.STOCK_INFO,
                source_name=self.config.name,
                start_time=start_time,
                end_time=datetime.now(),
                success=False,
                error_message=str(e),
                metadata={"error_type": type(e).__name__}
            )
    
    def collect_fund_daily(
        self,
        symbols: Optional[List[str]] = None,
        start_date: Optional[Union[str, date, datetime]] = None,
        end_date: Optional[Union[str, date, datetime]] = None,
        **kwargs
    ) -> DataCollectionResult:
        """
        收集基金日度净值数据
        
        Args:
            symbols: 基金代码列表，如果为None则收集所有基金
            start_date: 开始日期
            end_date: 结束日期
            **kwargs: 其他参数
            
        Returns:
            DataCollectionResult: 收集结果
        """
        start_time = datetime.now()
        
        try:
            # 验证连接
            if not self._validate_connection():
                raise ConnectionError("Tushare数据源未连接")
            
            # 设置默认日期
            if end_date is None:
                end_date = datetime.now()
            
            if start_date is None:
                start_date = end_date - timedelta(days=30)
            
            # 格式化日期
            start_date_str = self._format_date(start_date)
            end_date_str = self._format_date(end_date)
            
            logger.info(f"开始收集基金日度净值数据: {start_date_str} 到 {end_date_str}")
            
            # 收集数据
            all_data = []
            
            if symbols:
                # 收集指定基金的数据
                for symbol in symbols:
                    try:
                        logger.debug(f"收集基金 {symbol} 的日度净值数据")
                        
                        # 获取基金净值数据
                        fund_data = self.tushare_client.fund_nav(
                            ts_code=symbol,
                            start_date=start_date_str,
                            end_date=end_date_str
                        )
                        
                        if not fund_data.empty:
                            # 添加基金代码信息
                            fund_data = fund_data.copy()
                            fund_data['symbol'] = symbol
                            all_data.append(fund_data)
                            
                            logger.debug(f"基金 {symbol} 收集到 {len(fund_data)} 条记录")
                        else:
                            logger.warning(f"基金 {symbol} 未找到净值数据")
                        
                        # 遵守频率限制
                        time.sleep(1 / self.config.rate_limit)
                        
                    except Exception as e:
                        logger.error(f"收集基金 {symbol} 数据失败: {e}")
                        continue
            else:
                # 收集热门基金的数据
                logger.info("收集热门基金的日度净值数据")
                
                try:
                    # 获取基金列表
                    fund_list = self.tushare_client.fund_basic(
                        market='E',
                        status='L'
                    )
                    
                    if not fund_list.empty:
                        # 取前10个基金
                        top_funds = fund_list.head(10)['ts_code'].tolist()
                        
                        for symbol in top_funds:
                            try:
                                logger.debug(f"收集热门基金 {symbol} 的净值数据")
                                
                                fund_data = self.tushare_client.fund_nav(
                                    ts_code=symbol,
                                    start_date=start_date_str,
                                    end_date=end_date_str
                                )
                                
                                if not fund_data.empty:
                                    fund_data = fund_data.copy()
                                    fund_data['symbol'] = symbol
                                    all_data.append(fund_data)
                                
                                time.sleep(1 / self.config.rate_limit)
                                
                            except Exception as e:
                                logger.error(f"收集热门基金 {symbol} 数据失败: {e}")
                                continue
                    
                    logger.info(f"收集到 {len(all_data)} 个基金的数据")
                    
                except Exception as e:
                    logger.error(f"收集基金列表失败: {e}")
            
            # 合并所有数据
            if all_data:
                combined_data = pd.concat(all_data, ignore_index=True)
                
                # 数据转换和标准化
                processed_data = self._process_fund_daily_data(combined_data)
                
                result = DataCollectionResult(
                    data_type=DataType.FUND_DAILY,
                    source_name=self.config.name,
                    start_time=start_time,
                    end_time=datetime.now(),
                    records_collected=len(combined_data),
                    records_processed=len(processed_data),
                    success=True,
                    raw_data=combined_data.to_dict('records'),
                    processed_data=processed_data,
                    metadata={
                        "start_date": start_date_str,
                        "end_date": end_date_str,
                        "fund_count": len(symbols) if symbols else "top_10",
                    }
                )
            else:
                result = DataCollectionResult(
                    data_type=DataType.FUND_DAILY,
                    source_name=self.config.name,
                    start_time=start_time,
                    end_time=datetime.now(),
                    success=False,
                    error_message="未收集到任何基金净值数据",
                    metadata={
                        "start_date": start_date_str,
                        "end_date": end_date_str,
                    }
                )
            
            return result
            
        except Exception as e:
            self._handle_error(e)
            return DataCollectionResult(
                data_type=DataType.FUND_DAILY,
                source_name=self.config.name,
                start_time=start_time,
                end_time=datetime.now(),
                success=False,
                error_message=str(e),
                metadata={"error_type": type(e).__name__}
            )
    
    def collect_fund_info(
        self,
        symbols: Optional[List[str]] = None,
        **kwargs
    ) -> DataCollectionResult:
        """
        收集基金基本信息
        
        Args:
            symbols: 基金代码列表，如果为None则收集所有基金
            **kwargs: 其他参数
            
        Returns:
            DataCollectionResult: 收集结果
        """
        start_time = datetime.now()
        
        try:
            # 验证连接
            if not self._validate_connection():
                raise ConnectionError("Tushare数据源未连接")
            
            logger.info("开始收集基金基本信息")
            
            # 收集数据
            if symbols:
                # 收集指定基金的信息
                all_data = []
                
                for symbol in symbols:
                    try:
                        logger.debug(f"收集基金 {symbol} 的基本信息")
                        
                        # 获取基金基本信息
                        fund_info = self.tushare_client.fund_basic(
                            ts_code=symbol,
                            fields='ts_code,name,management,found_date,market,status'
                        )
                        
                        if not fund_info.empty:
                            # 添加基金代码信息
                            fund_info = fund_info.copy()
                            fund_info['symbol'] = symbol
                            all_data.append(fund_info)
                            
                            logger.debug(f"基金 {symbol} 基本信息收集成功")
                        else:
                            logger.warning(f"基金 {symbol} 未找到基本信息")
                        
                        # 遵守频率限制
                        time.sleep(1 / self.config.rate_limit)
                        
                    except Exception as e:
                        logger.error(f"收集基金 {symbol} 基本信息失败: {e}")
                        continue
                
                if all_data:
                    combined_data = pd.concat(all_data, ignore_index=True)
                else:
                    combined_data = pd.DataFrame()
                    
            else:
                # 收集热门基金的基本信息
                logger.info("收集热门基金的基本信息")
                
                try:
                    # 获取基金列表
                    fund_list = self.tushare_client.fund_basic(
                        market='E',
                        status='L',
                        fields='ts_code,name,management,found_date,market,status'
                    )
                    
                    if not fund_list.empty:
                        # 取前20个基金
                        top_funds = fund_list.head(20)
                        
                        # 重命名列以匹配我们的数据模型
                        top_funds = top_funds.copy()
                        top_funds['symbol'] = top_funds['ts_code']
                        top_funds['name'] = top_funds['name']
                        
                        combined_data = top_funds
                        logger.info(f"收集到 {len(combined_data)} 条基金基本信息")
                    else:
                        logger.warning("未找到基金列表数据")
                        combined_data = pd.DataFrame()
                        
                except Exception as e:
                    logger.error(f"收集基金基本信息失败: {e}")
                    combined_data = pd.DataFrame()
            
            # 数据处理
            if not combined_data.empty:
                processed_data = self._process_fund_info_data(combined_data)
                
                result = DataCollectionResult(
                    data_type=DataType.FUND_INFO,
                    source_name=self.config.name,
                    start_time=start_time,
                    end_time=datetime.now(),
                    records_collected=len(combined_data),
                    records_processed=len(processed_data),
                    success=True,
                    raw_data=combined_data.to_dict('records'),
                    processed_data=processed_data,
                    metadata={
                        "fund_count": len(symbols) if symbols else "top_20",
                    }
                )
            else:
                result = DataCollectionResult(
                    data_type=DataType.FUND_INFO,
                    source_name=self.config.name,
                    start_time=start_time,
                    end_time=datetime.now(),
                    success=False,
                    error_message="未收集到任何基金基本信息",
                    metadata={}
                )
            
            return result
            
        except Exception as e:
            self._handle_error(e)
            return DataCollectionResult(
                data_type=DataType.FUND_INFO,
                source_name=self.config.name,
                start_time=start_time,
                end_time=datetime.now(),
                success=False,
                error_message=str(e),
                metadata={"error_type": type(e).__name__}
            )
    
    def collect_bond_daily(
        self,
        symbols: Optional[List[str]] = None,
        start_date: Optional[Union[str, date, datetime]] = None,
        end_date: Optional[Union[str, date, datetime]] = None,
        **kwargs
    ) -> DataCollectionResult:
        """
        收集债券日度行情数据
        
        Args:
            symbols: 债券代码列表，如果为None则收集所有债券
            start_date: 开始日期
            end_date: 结束日期
            **kwargs: 其他参数
            
        Returns:
            DataCollectionResult: 收集结果
        """
        start_time = datetime.now()
        
        try:
            # 验证连接
            if not self._validate_connection():
                raise ConnectionError("Tushare数据源未连接")
            
            # 设置默认日期
            if end_date is None:
                end_date = datetime.now()
            
            if start_date is None:
                start_date = end_date - timedelta(days=30)
            
            # 格式化日期
            start_date_str = self._format_date(start_date)
            end_date_str = self._format_date(end_date)
            
            logger.info(f"开始收集债券日度行情数据: {start_date_str} 到 {end_date_str}")
            
            # 收集数据
            try:
                # 获取债券日度行情数据
                bond_data = self.tushare_client.bond_daily(
                    ts_code=','.join(symbols) if symbols else '',
                    start_date=start_date_str,
                    end_date=end_date_str
                )
                
                if not bond_data.empty:
                    logger.info(f"收集到 {len(bond_data)} 条债券行情数据")
                else:
                    logger.warning("未找到债券行情数据")
                    bond_data = pd.DataFrame()
                    
            except Exception as e:
                logger.error(f"收集债券行情数据失败: {e}")
                bond_data = pd.DataFrame()
            
            # 数据处理
            if not bond_data.empty:
                processed_data = self._process_bond_daily_data(bond_data)
                
                result = DataCollectionResult(
                    data_type=DataType.BOND_DAILY,
                    source_name=self.config.name,
                    start_time=start_time,
                    end_time=datetime.now(),
                    records_collected=len(bond_data),
                    records_processed=len(processed_data),
                    success=True,
                    raw_data=bond_data.to_dict('records'),
                    processed_data=processed_data,
                    metadata={
                        "start_date": start_date_str,
                        "end_date": end_date_str,
                        "bond_count": len(symbols) if symbols else "all",
                    }
                )
            else:
                result = DataCollectionResult(
                    data_type=DataType.BOND_DAILY,
                    source_name=self.config.name,
                    start_time=start_time,
                    end_time=datetime.now(),
                    success=False,
                    error_message="未收集到任何债券行情数据",
                    metadata={
                        "start_date": start_date_str,
                        "end_date": end_date_str,
                    }
                )
            
            return result
            
        except Exception as e:
            self._handle_error(e)
            return DataCollectionResult(
                data_type=DataType.BOND_DAILY,
                source_name=self.config.name,
                start_time=start_time,
                end_time=datetime.now(),
                success=False,
                error_message=str(e),
                metadata={"error_type": type(e).__name__}
            )
    
    def collect_index_daily(
        self,
        symbols: Optional[List[str]] = None,
        start_date: Optional[Union[str, date, datetime]] = None,
        end_date: Optional[Union[str, date, datetime]] = None,
        **kwargs
    ) -> DataCollectionResult:
        """
        收集指数日度行情数据
        
        Args:
            symbols: 指数代码列表，如果为None则收集所有指数
            start_date: 开始日期
            end_date: 结束日期
            **kwargs: 其他参数
            
        Returns:
            DataCollectionResult: 收集结果
        """
        start_time = datetime.now()
        
        try:
            # 验证连接
            if not self._validate_connection():
                raise ConnectionError("Tushare数据源未连接")
            
            # 设置默认日期
            if end_date is None:
                end_date = datetime.now()
            
            if start_date is None:
                start_date = end_date - timedelta(days=30)
            
            # 格式化日期
            start_date_str = self._format_date(start_date)
            end_date_str = self._format_date(end_date)
            
            logger.info(f"开始收集指数日度行情数据: {start_date_str} 到 {end_date_str}")
            
            # 收集数据
            all_data = []
            
            # 默认收集的主要指数
            default_indices = ['000001.SH', '399001.SZ', '000300.SH', '000905.SH']
            
            indices_to_collect = symbols if symbols else default_indices
            
            for index_symbol in indices_to_collect:
                try:
                    logger.debug(f"收集指数 {index_symbol} 的日度行情数据")
                    
                    # 获取指数日度数据
                    index_data = self.tushare_client.index_daily(
                        ts_code=index_symbol,
                        start_date=start_date_str,
                        end_date=end_date_str
                    )
                    
                    if not index_data.empty:
                        # 添加指数代码信息
                        index_data = index_data.copy()
                        index_data['symbol'] = index_symbol
                        all_data.append(index_data)
                        
                        logger.debug(f"指数 {index_symbol} 收集到 {len(index_data)} 条记录")
                    else:
                        logger.warning(f"指数 {index_symbol} 未找到数据")
                    
                    # 遵守频率限制
                    time.sleep(1 / self.config.rate_limit)
                    
                except Exception as e:
                    logger.error(f"收集指数 {index_symbol} 数据失败: {e}")
                    continue
            
            # 合并所有数据
            if all_data:
                combined_data = pd.concat(all_data, ignore_index=True)
                
                # 数据转换和标准化
                processed_data = self._process_index_daily_data(combined_data)
                
                result = DataCollectionResult(
                    data_type=DataType.INDEX_DAILY,
                    source_name=self.config.name,
                    start_time=start_time,
                    end_time=datetime.now(),
                    records_collected=len(combined_data),
                    records_processed=len(processed_data),
                    success=True,
                    raw_data=combined_data.to_dict('records'),
                    processed_data=processed_data,
                    metadata={
                        "start_date": start_date_str,
                        "end_date": end_date_str,
                        "index_count": len(indices_to_collect),
                    }
                )
            else:
                result = DataCollectionResult(
                    data_type=DataType.INDEX_DAILY,
                    source_name=self.config.name,
                    start_time=start_time,
                    end_time=datetime.now(),
                    success=False,
                    error_message="未收集到任何指数行情数据",
                    metadata={
                        "start_date": start_date_str,
                        "end_date": end_date_str,
                    }
                )
            
            return result
            
        except Exception as e:
            self._handle_error(e)
            return DataCollectionResult(
                data_type=DataType.INDEX_DAILY,
                source_name=self.config.name,
                start_time=start_time,
                end_time=datetime.now(),
                success=False,
                error_message=str(e),
                metadata={"error_type": type(e).__name__}
            )
    
    # 数据处理方法
    def _process_stock_daily_data(self, raw_data: pd.DataFrame) -> List[Dict[str, Any]]:
        """
        处理股票日度数据
        
        Args:
            raw_data: 原始数据
            
        Returns:
            List[Dict[str, Any]]: 处理后的数据
        """
        processed = []
        
        for _, row in raw_data.iterrows():
            try:
                # 标准化数据格式
                processed_row = {
                    "source": "tushare",
                    "source_id": f"{row.get('ts_code', '')}_{row.get('trade_date', '')}",
                    "symbol": row.get('ts_code', ''),
                    "trade_date": row.get('trade_date', ''),
                    "timestamp": datetime.now().isoformat(),
                    "open_price": float(row.get('open', 0)) if pd.notna(row.get('open')) else None,
                    "high_price": float(row.get('high', 0)) if pd.notna(row.get('high')) else None,
                    "low_price": float(row.get('low', 0)) if pd.notna(row.get('low')) else None,
                    "close_price": float(row.get('close', 0)) if pd.notna(row.get('close')) else None,
                    "pre_close": float(row.get('pre_close', 0)) if pd.notna(row.get('pre_close')) else None,
                    "change": float(row.get('change', 0)) if pd.notna(row.get('change')) else None,
                    "pct_change": float(row.get('pct_chg', 0)) if pd.notna(row.get('pct_chg')) else None,
                    "volume": float(row.get('vol', 0)) if pd.notna(row.get('vol')) else None,
                    "amount": float(row.get('amount', 0)) if pd.notna(row.get('amount')) else None,
                    "is_deleted": False,
                    "metadata": {
                        "data_source": "tushare",
                        "collection_time": datetime.now().isoformat(),
                    }
                }
                
                processed.append(processed_row)
                
            except Exception as e:
                logger.error(f"处理股票日度数据行失败: {e}")
                continue
        
        return processed
    
    def _process_stock_info_data(self, raw_data: pd.DataFrame) -> List[Dict[str, Any]]:
        """
        处理股票基本信息数据
        
        Args:
            raw_data: 原始数据
            
        Returns:
            List[Dict[str, Any]]: 处理后的数据
        """
        processed = []
        
        for _, row in raw_data.iterrows():
            try:
                # 标准化数据格式
                processed_row = {
                    "source": "tushare",
                    "source_id": row.get('ts_code', ''),
                    "symbol": row.get('ts_code', ''),
                    "name": row.get('name', ''),
                    "market": row.get('market', ''),
                    "exchange": self._determine_exchange(row.get('ts_code', '')),
                    "listing_date": row.get('list_date', ''),
                    "is_active": row.get('list_status', '') == 'L',
                    "is_deleted": False,
                    "metadata": {
                        "data_source": "tushare",
                        "collection_time": datetime.now().isoformat(),
                        "area": row.get('area', ''),
                        "industry": row.get('industry', ''),
                        "is_hs": row.get('is_hs', ''),
                    }
                }
                
                processed.append(processed_row)
                
            except Exception as e:
                logger.error(f"处理股票信息数据行失败: {e}")
                continue
        
        return processed
    
    def _process_fund_daily_data(self, raw_data: pd.DataFrame) -> List[Dict[str, Any]]:
        """
        处理基金日度数据
        
        Args:
            raw_data: 原始数据
            
        Returns:
            List[Dict[str, Any]]: 处理后的数据
        """
        processed = []
        
        for _, row in raw_data.iterrows():
            try:
                # 标准化数据格式
                processed_row = {
                    "source": "tushare",
                    "source_id": f"{row.get('ts_code', '')}_{row.get('end_date', '')}",
                    "symbol": row.get('ts_code', ''),
                    "trade_date": row.get('end_date', ''),
                    "timestamp": datetime.now().isoformat(),
                    "unit_nav": float(row.get('unit_nav', 0)) if pd.notna(row.get('unit_nav')) else None,
                    "accumulated_nav": float(row.get('accum_nav', 0)) if pd.notna(row.get('accum_nav')) else None,
                    "daily_return": float(row.get('daily_return', 0)) if pd.notna(row.get('daily_return')) else None,
                    "is_deleted": False,
                    "metadata": {
                        "data_source": "tushare",
                        "collection_time": datetime.now().isoformat(),
                    }
                }
                
                processed.append(processed_row)
                
            except Exception as e:
                logger.error(f"处理基金日度数据行失败: {e}")
                continue
        
        return processed
    
    def _process_fund_info_data(self, raw_data: pd.DataFrame) -> List[Dict[str, Any]]:
        """
        处理基金基本信息数据
        
        Args:
            raw_data: 原始数据
            
        Returns:
            List[Dict[str, Any]]: 处理后的数据
        """
        processed = []
        
        for _, row in raw_data.iterrows():
            try:
                # 标准化数据格式
                processed_row = {
                    "source": "tushare",
                    "source_id": row.get('ts_code', ''),
                    "symbol": row.get('ts_code', ''),
                    "name": row.get('name', ''),
                    "fund_type": row.get('market', ''),
                    "company": row.get('management', ''),
                    "establishment_date": row.get('found_date', ''),
                    "is_active": row.get('status', '') == 'L',
                    "is_deleted": False,
                    "metadata": {
                        "data_source": "tushare",
                        "collection_time": datetime.now().isoformat(),
                    }
                }
                
                processed.append(processed_row)
                
            except Exception as e:
                logger.error(f"处理基金信息数据行失败: {e}")
                continue
        
        return processed
    
    def _process_bond_daily_data(self, raw_data: pd.DataFrame) -> List[Dict[str, Any]]:
        """
        处理债券日度数据
        
        Args:
            raw_data: 原始数据
            
        Returns:
            List[Dict[str, Any]]: 处理后的数据
        """
        processed = []
        
        for _, row in raw_data.iterrows():
            try:
                # 标准化数据格式
                processed_row = {
                    "source": "tushare",
                    "source_id": f"{row.get('ts_code', '')}_{row.get('trade_date', '')}",
                    "symbol": row.get('ts_code', ''),
                    "trade_date": row.get('trade_date', ''),
                    "timestamp": datetime.now().isoformat(),
                    "clean_price": float(row.get('clean_price', 0)) if pd.notna(row.get('clean_price')) else None,
                    "full_price": float(row.get('full_price', 0)) if pd.notna(row.get('full_price')) else None,
                    "accrued_interest": float(row.get('accrued_interest', 0)) if pd.notna(row.get('accrued_interest')) else None,
                    "yield_to_maturity": float(row.get('yield_to_maturity', 0)) if pd.notna(row.get('yield_to_maturity')) else None,
                    "volume": float(row.get('vol', 0)) if pd.notna(row.get('vol')) else None,
                    "amount": float(row.get('amount', 0)) if pd.notna(row.get('amount')) else None,
                    "is_deleted": False,
                    "metadata": {
                        "data_source": "tushare",
                        "collection_time": datetime.now().isoformat(),
                    }
                }
                
                processed.append(processed_row)
                
            except Exception as e:
                logger.error(f"处理债券日度数据行失败: {e}")
                continue
        
        return processed
    
    def _process_index_daily_data(self, raw_data: pd.DataFrame) -> List[Dict[str, Any]]:
        """
        处理指数日度数据
        
        Args:
            raw_data: 原始数据
            
        Returns:
            List[Dict[str, Any]]: 处理后的数据
        """
        processed = []
        
        for _, row in raw_data.iterrows():
            try:
                # 标准化数据格式
                processed_row = {
                    "source": "tushare",
                    "source_id": f"{row.get('ts_code', '')}_{row.get('trade_date', '')}",
                    "symbol": row.get('ts_code', ''),
                    "trade_date": row.get('trade_date', ''),
                    "timestamp": datetime.now().isoformat(),
                    "open_price": float(row.get('open', 0)) if pd.notna(row.get('open')) else None,
                    "high_price": float(row.get('high', 0)) if pd.notna(row.get('high')) else None,
                    "low_price": float(row.get('low', 0)) if pd.notna(row.get('low')) else None,
                    "close_price": float(row.get('close', 0)) if pd.notna(row.get('close')) else None,
                    "pre_close": float(row.get('pre_close', 0)) if pd.notna(row.get('pre_close')) else None,
                    "change": float(row.get('change', 0)) if pd.notna(row.get('change')) else None,
                    "pct_change": float(row.get('pct_chg', 0)) if pd.notna(row.get('pct_chg')) else None,
                    "volume": float(row.get('vol', 0)) if pd.notna(row.get('vol')) else None,
                    "amount": float(row.get('amount', 0)) if pd.notna(row.get('amount')) else None,
                    "is_deleted": False,
                    "metadata": {
                        "data_source": "tushare",
                        "collection_time": datetime.now().isoformat(),
                    }
                }
                
                processed.append(processed_row)
                
            except Exception as e:
                logger.error(f"处理指数日度数据行失败: {e}")
                continue
        
        return processed
    
    # 辅助方法
    def _determine_exchange(self, ts_code: str) -> str:
        """
        根据股票代码确定交易所
        
        Args:
            ts_code: Tushare股票代码
            
        Returns:
            str: 交易所代码
        """
        if ts_code.endswith('.SH'):
            return 'SSE'
        elif ts_code.endswith('.SZ'):
            return 'SZSE'
        elif ts_code.endswith('.BJ'):
            return 'BSE'
        else:
            return 'UNKNOWN'