"""
AKShare数据源适配器

实现AKShare数据源的数据收集功能。
"""

import time
import pandas as pd
from datetime import datetime, date, timedelta
from typing import Dict, List, Any, Optional, Union
import akshare as ak

from .base import (
    DataSourceAdapter, DataSourceConfig, DataCollectionResult,
    DataType, DataSourceStatus
)
from ...utils.logging import get_logger

logger = get_logger(__name__)


class AKShareAdapter(DataSourceAdapter):
    """AKShare数据源适配器"""
    
    def __init__(self, config: DataSourceConfig):
        """
        初始化AKShare适配器
        
        Args:
            config: 数据源配置
        """
        super().__init__(config)
        self.akshare_client = None
        self._init_akshare()
    
    def _init_akshare(self) -> None:
        """初始化AKShare客户端"""
        try:
            # AKShare不需要显式初始化，这里可以设置一些默认参数
            self.akshare_client = ak
            logger.info("AKShare客户端初始化完成")
        except Exception as e:
            logger.error(f"AKShare客户端初始化失败: {e}")
            self.akshare_client = None
    
    def connect(self) -> bool:
        """
        连接到AKShare数据源
        
        Returns:
            bool: 连接是否成功
        """
        try:
            # AKShare不需要显式连接，这里测试一个简单的API调用
            test_result = ak.stock_zh_a_spot_em()
            if test_result is not None and len(test_result) > 0:
                self.status = DataSourceStatus.CONNECTED
                self.last_connection_time = datetime.now()
                logger.info("AKShare数据源连接成功")
                return True
            else:
                self.status = DataSourceStatus.ERROR
                logger.error("AKShare数据源连接测试失败")
                return False
        except Exception as e:
            self.status = DataSourceStatus.ERROR
            logger.error(f"AKShare数据源连接失败: {e}")
            return False
    
    def disconnect(self) -> bool:
        """
        断开AKShare数据源连接
        
        Returns:
            bool: 断开是否成功
        """
        # AKShare不需要显式断开连接
        self.status = DataSourceStatus.DISCONNECTED
        logger.info("AKShare数据源已断开")
        return True
    
    def get_status(self) -> Dict[str, Any]:
        """
        获取AKShare数据源状态
        
        Returns:
            Dict[str, Any]: 状态信息
        """
        return {
            "source": "akshare",
            "status": self.status.value,
            "last_connection": self.last_connection_time,
            "error_count": self.error_count,
            "total_requests": self.total_requests,
            "success_rate": (
                self.successful_requests / self.total_requests 
                if self.total_requests > 0 else 0
            ),
            "akshare_version": ak.__version__ if hasattr(ak, '__version__') else "unknown",
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
                raise ConnectionError("AKShare数据源未连接")
            
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
                        stock_data = ak.stock_zh_a_hist(
                            symbol=symbol,
                            period="daily",
                            start_date=start_date_str,
                            end_date=end_date_str,
                            adjust="qfq"  # 前复权
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
                logger.warning("未指定股票代码，将收集A股市场所有股票数据")
                
                try:
                    # 获取A股实时行情数据
                    spot_data = ak.stock_zh_a_spot_em()
                    
                    if not spot_data.empty:
                        # 转换为日度数据格式
                        spot_data['日期'] = datetime.now().strftime("%Y-%m-%d")
                        spot_data['symbol'] = spot_data['代码']
                        all_data.append(spot_data)
                        
                        logger.info(f"收集到 {len(spot_data)} 条实时行情数据")
                    else:
                        logger.warning("未找到实时行情数据")
                        
                except Exception as e:
                    logger.error(f"收集实时行情数据失败: {e}")
            
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
                        "symbol_count": len(symbols) if symbols else "all",
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
                raise ConnectionError("AKShare数据源未连接")
            
            logger.info("开始收集股票基本信息")
            
            # 收集数据
            if symbols:
                # 收集指定股票的信息
                all_data = []
                
                for symbol in symbols:
                    try:
                        logger.debug(f"收集股票 {symbol} 的基本信息")
                        
                        # 获取股票基本信息
                        # 这里使用实时行情数据作为基本信息
                        spot_data = ak.stock_zh_a_spot_em()
                        
                        if not spot_data.empty:
                            # 过滤指定股票
                            stock_info = spot_data[spot_data['代码'] == symbol]
                            
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
                    # 获取A股实时行情数据
                    combined_data = ak.stock_zh_a_spot_em()
                    
                    if not combined_data.empty:
                        # 添加symbol字段
                        combined_data = combined_data.copy()
                        combined_data['symbol'] = combined_data['代码']
                        
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
                raise ConnectionError("AKShare数据源未连接")
            
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
                        fund_data = ak.fund_em_open_fund_info(
                            fund=symbol,
                            indicator="单位净值走势"
                        )
                        
                        if not fund_data.empty:
                            # 过滤日期范围
                            fund_data['净值日期'] = pd.to_datetime(fund_data['净值日期'])
                            mask = (fund_data['净值日期'] >= pd.Timestamp(start_date_str)) & \
                                   (fund_data['净值日期'] <= pd.Timestamp(end_date_str))
                            filtered_data = fund_data[mask]
                            
                            if not filtered_data.empty:
                                # 添加基金代码信息
                                filtered_data = filtered_data.copy()
                                filtered_data['symbol'] = symbol
                                all_data.append(filtered_data)
                                
                                logger.debug(f"基金 {symbol} 收集到 {len(filtered_data)} 条记录")
                            else:
                                logger.warning(f"基金 {symbol} 在指定日期范围内未找到数据")
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
                    # 获取热门基金列表
                    hot_funds = ak.fund_em_open_fund_rank()
                    
                    if not hot_funds.empty:
                        # 取前10个热门基金
                        top_funds = hot_funds.head(10)['基金代码'].tolist()
                        
                        for symbol in top_funds:
                            try:
                                logger.debug(f"收集热门基金 {symbol} 的净值数据")
                                
                                fund_data = ak.fund_em_open_fund_info(
                                    fund=symbol,
                                    indicator="单位净值走势"
                                )
                                
                                if not fund_data.empty:
                                    # 过滤日期范围
                                    fund_data['净值日期'] = pd.to_datetime(fund_data['净值日期'])
                                    mask = (fund_data['净值日期'] >= pd.Timestamp(start_date_str)) & \
                                           (fund_data['净值日期'] <= pd.Timestamp(end_date_str))
                                    filtered_data = fund_data[mask]
                                    
                                    if not filtered_data.empty:
                                        filtered_data = filtered_data.copy()
                                        filtered_data['symbol'] = symbol
                                        all_data.append(filtered_data)
                                
                                time.sleep(1 / self.config.rate_limit)
                                
                            except Exception as e:
                                logger.error(f"收集热门基金 {symbol} 数据失败: {e}")
                                continue
                    
                    logger.info(f"收集到 {len(all_data)} 个基金的数据")
                    
                except Exception as e:
                    logger.error(f"收集热门基金列表失败: {e}")
            
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
                        "fund_count": len(symbols) if symbols else "hot_10",
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
                raise ConnectionError("AKShare数据源未连接")
            
            logger.info("开始收集基金基本信息")
            
            # 收集数据
            if symbols:
                # 收集指定基金的信息
                all_data = []
                
                for symbol in symbols:
                    try:
                        logger.debug(f"收集基金 {symbol} 的基本信息")
                        
                        # 获取基金基本信息
                        fund_info = ak.fund_em_open_fund_info(
                            fund=symbol,
                            indicator="基金概况"
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
                    # 获取基金排名数据
                    fund_rank = ak.fund_em_open_fund_rank()
                    
                    if not fund_rank.empty:
                        # 取前20个基金
                        top_funds = fund_rank.head(20)
                        
                        # 重命名列以匹配我们的数据模型
                        top_funds = top_funds.copy()
                        top_funds['symbol'] = top_funds['基金代码']
                        top_funds['name'] = top_funds['基金简称']
                        
                        combined_data = top_funds
                        logger.info(f"收集到 {len(combined_data)} 条基金基本信息")
                    else:
                        logger.warning("未找到基金排名数据")
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
                raise ConnectionError("AKShare数据源未连接")
            
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
                # 获取债券实时行情数据
                bond_data = ak.bond_zh_hs_cov_spot()
                
                if not bond_data.empty:
                    # 添加日期信息
                    bond_data = bond_data.copy()
                    bond_data['trade_date'] = datetime.now().strftime("%Y-%m-%d")
                    
                    # 如果指定了symbols，进行过滤
                    if symbols:
                        bond_data = bond_data[bond_data['symbol'].isin(symbols)]
                    
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
                raise ConnectionError("AKShare数据源未连接")
            
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
            default_indices = ['sh000001', 'sz399001', 'sh000300', 'sh000905']
            
            indices_to_collect = symbols if symbols else default_indices
            
            for index_symbol in indices_to_collect:
                try:
                    logger.debug(f"收集指数 {index_symbol} 的日度行情数据")
                    
                    # 获取指数日度数据
                    index_data = ak.stock_zh_index_daily(
                        symbol=index_symbol
                    )
                    
                    if not index_data.empty:
                        # 过滤日期范围
                        index_data.index = pd.to_datetime(index_data.index)
                        mask = (index_data.index >= pd.Timestamp(start_date_str)) & \
                               (index_data.index <= pd.Timestamp(end_date_str))
                        filtered_data = index_data[mask]
                        
                        if not filtered_data.empty:
                            # 重置索引并添加指数代码信息
                            filtered_data = filtered_data.reset_index()
                            filtered_data = filtered_data.copy()
                            filtered_data['symbol'] = index_symbol
                            all_data.append(filtered_data)
                            
                            logger.debug(f"指数 {index_symbol} 收集到 {len(filtered_data)} 条记录")
                        else:
                            logger.warning(f"指数 {index_symbol} 在指定日期范围内未找到数据")
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
                    "source": "akshare",
                    "source_id": f"{row.get('symbol', '')}_{row.get('日期', '')}",
                    "symbol": row.get('symbol', ''),
                    "trade_date": row.get('日期', ''),
                    "timestamp": datetime.now().isoformat(),
                    "open_price": float(row.get('开盘', 0)) if pd.notna(row.get('开盘')) else None,
                    "high_price": float(row.get('最高', 0)) if pd.notna(row.get('最高')) else None,
                    "low_price": float(row.get('最低', 0)) if pd.notna(row.get('最低')) else None,
                    "close_price": float(row.get('收盘', 0)) if pd.notna(row.get('收盘')) else None,
                    "volume": float(row.get('成交量', 0)) if pd.notna(row.get('成交量')) else None,
                    "amount": float(row.get('成交额', 0)) if pd.notna(row.get('成交额')) else None,
                    "amplitude": float(row.get('振幅', 0)) if pd.notna(row.get('振幅')) else None,
                    "pct_change": float(row.get('涨跌幅', 0)) if pd.notna(row.get('涨跌幅')) else None,
                    "change_amount": float(row.get('涨跌额', 0)) if pd.notna(row.get('涨跌额')) else None,
                    "turnover_rate": float(row.get('换手率', 0)) if pd.notna(row.get('换手率')) else None,
                    "is_deleted": False,
                    "metadata": {
                        "data_source": "akshare",
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
                    "source": "akshare",
                    "source_id": row.get('symbol', ''),
                    "symbol": row.get('symbol', ''),
                    "name": row.get('名称', ''),
                    "market": self._determine_market(row.get('symbol', '')),
                    "exchange": self._determine_exchange(row.get('symbol', '')),
                    "listing_date": None,  # AKShare实时数据中没有上市日期
                    "is_active": True,
                    "is_deleted": False,
                    "metadata": {
                        "data_source": "akshare",
                        "collection_time": datetime.now().isoformat(),
                        "latest_price": float(row.get('最新价', 0)) if pd.notna(row.get('最新价')) else None,
                        "market_cap": float(row.get('总市值', 0)) if pd.notna(row.get('总市值')) else None,
                        "circulating_market_cap": float(row.get('流通市值', 0)) if pd.notna(row.get('流通市值')) else None,
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
                    "source": "akshare",
                    "source_id": f"{row.get('symbol', '')}_{row.get('净值日期', '')}",
                    "symbol": row.get('symbol', ''),
                    "trade_date": row.get('净值日期', ''),
                    "timestamp": datetime.now().isoformat(),
                    "unit_nav": float(row.get('单位净值', 0)) if pd.notna(row.get('单位净值')) else None,
                    "accumulated_nav": float(row.get('累计净值', 0)) if pd.notna(row.get('累计净值')) else None,
                    "daily_return": float(row.get('日增长率', 0)) if pd.notna(row.get('日增长率')) else None,
                    "is_deleted": False,
                    "metadata": {
                        "data_source": "akshare",
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
                    "source": "akshare",
                    "source_id": row.get('symbol', ''),
                    "symbol": row.get('symbol', ''),
                    "name": row.get('name', row.get('基金简称', '')),
                    "fund_type": row.get('基金类型', ''),
                    "company": row.get('基金管理人', ''),
                    "establishment_date": None,  # AKShare排名数据中没有成立日期
                    "is_active": True,
                    "is_deleted": False,
                    "metadata": {
                        "data_source": "akshare",
                        "collection_time": datetime.now().isoformat(),
                        "net_asset": float(row.get('基金规模', 0)) if pd.notna(row.get('基金规模')) else None,
                        "return_1y": float(row.get('近1年', 0)) if pd.notna(row.get('近1年')) else None,
                        "return_3y": float(row.get('近3年', 0)) if pd.notna(row.get('近3年')) else None,
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
                    "source": "akshare",
                    "source_id": f"{row.get('symbol', '')}_{row.get('trade_date', '')}",
                    "symbol": row.get('symbol', ''),
                    "trade_date": row.get('trade_date', ''),
                    "timestamp": datetime.now().isoformat(),
                    "clean_price": float(row.get('净价', 0)) if pd.notna(row.get('净价')) else None,
                    "full_price": float(row.get('全价', 0)) if pd.notna(row.get('全价')) else None,
                    "accrued_interest": float(row.get('应计利息', 0)) if pd.notna(row.get('应计利息')) else None,
                    "yield_to_maturity": float(row.get('到期收益率', 0)) if pd.notna(row.get('到期收益率')) else None,
                    "volume": float(row.get('成交量', 0)) if pd.notna(row.get('成交量')) else None,
                    "amount": float(row.get('成交额', 0)) if pd.notna(row.get('成交额')) else None,
                    "is_deleted": False,
                    "metadata": {
                        "data_source": "akshare",
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
                    "source": "akshare",
                    "source_id": f"{row.get('symbol', '')}_{row.get('date', '')}",
                    "symbol": row.get('symbol', ''),
                    "trade_date": row.get('date', ''),
                    "timestamp": datetime.now().isoformat(),
                    "open_price": float(row.get('open', 0)) if pd.notna(row.get('open')) else None,
                    "high_price": float(row.get('high', 0)) if pd.notna(row.get('high')) else None,
                    "low_price": float(row.get('low', 0)) if pd.notna(row.get('low')) else None,
                    "close_price": float(row.get('close', 0)) if pd.notna(row.get('close')) else None,
                    "volume": float(row.get('volume', 0)) if pd.notna(row.get('volume')) else None,
                    "amount": float(row.get('amount', 0)) if pd.notna(row.get('amount')) else None,
                    "is_deleted": False,
                    "metadata": {
                        "data_source": "akshare",
                        "collection_time": datetime.now().isoformat(),
                    }
                }
                
                processed.append(processed_row)
                
            except Exception as e:
                logger.error(f"处理指数日度数据行失败: {e}")
                continue
        
        return processed
    
    # 辅助方法
    def _determine_market(self, symbol: str) -> str:
        """
        根据股票代码确定市场
        
        Args:
            symbol: 股票代码
            
        Returns:
            str: 市场名称
        """
        if symbol.startswith('6'):
            return '上海'
        elif symbol.startswith('0') or symbol.startswith('3'):
            return '深圳'
        elif symbol.startswith('8'):
            return '北京'
        else:
            return '未知'
    
    def _determine_exchange(self, symbol: str) -> str:
        """
        根据股票代码确定交易所
        
        Args:
            symbol: 股票代码
            
        Returns:
            str: 交易所代码
        """
        if symbol.startswith('6'):
            return 'SSE'
        elif symbol.startswith('0') or symbol.startswith('3'):
            return 'SZSE'
        elif symbol.startswith('8'):
            return 'BSE'
        else:
            return 'UNKNOWN'