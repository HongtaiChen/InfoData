"""
数据处理服务

处理从数据源收集的数据，包括数据清洗、转换、验证和存储。
"""

from datetime import datetime, date
from typing import Dict, List, Any, Optional, Union
import pandas as pd

from ..utils.logging import get_logger
from ..utils.database import get_db_manager, session_scope
from .sources.manager import get_data_source_manager, DataCollectionResult, DataType
from ..models.stock import StockDaily, StockInfo
from ..models.fund import FundDaily, FundInfo
from ..models.bond import BondDaily, BondInfo
from ..models.index import IndexDaily, IndexInfo
from ..models.task import TaskExecution, TaskMetric
from ..models.quality import DataQualityMetric, DataValidationRule

logger = get_logger(__name__)


class DataProcessor:
    """数据处理服务"""
    
    def __init__(self, db_manager=None):
        """
        初始化数据处理服务
        
        Args:
            db_manager: 数据库管理器，如果为None则使用默认实例
        """
        self.db_manager = db_manager or get_db_manager()
        self.data_source_manager = get_data_source_manager()
        
        logger.info("数据处理服务初始化完成")
    
    def process_stock_daily(
        self,
        symbols: Optional[List[str]] = None,
        start_date: Optional[Union[str, date, datetime]] = None,
        end_date: Optional[Union[str, date, datetime]] = None,
        source_name: Optional[str] = None,
        validate: bool = True,
        **kwargs
    ) -> Dict[str, Any]:
        """
        处理股票日度行情数据
        
        Args:
            symbols: 股票代码列表
            start_date: 开始日期
            end_date: 结束日期
            source_name: 数据源名称
            validate: 是否验证数据
            **kwargs: 其他参数
            
        Returns:
            Dict[str, Any]: 处理结果
        """
        start_time = datetime.now()
        
        try:
            logger.info(f"开始处理股票日度行情数据")
            
            # 1. 从数据源收集数据
            collection_result = self.data_source_manager.collect_data(
                data_type=DataType.STOCK_DAILY,
                source_name=source_name,
                symbols=symbols,
                start_date=start_date,
                end_date=end_date,
                **kwargs
            )
            
            if not collection_result.success:
                raise ValueError(f"数据收集失败: {collection_result.error_message}")
            
            logger.info(f"数据收集完成，共 {collection_result.records_collected} 条记录")
            
            # 2. 数据验证
            validation_results = []
            if validate and collection_result.processed_data:
                validation_results = self._validate_stock_daily_data(
                    collection_result.processed_data
                )
                
                failed_validations = [r for r in validation_results if not r.get('passed', True)]
                if failed_validations:
                    logger.warning(f"数据验证发现 {len(failed_validations)} 个问题")
            
            # 3. 数据存储
            storage_result = self._store_stock_daily_data(
                collection_result.processed_data,
                source_name or collection_result.source_name
            )
            
            # 4. 记录任务执行
            task_result = self._record_task_execution(
                task_name="process_stock_daily",
                task_type="data_processing",
                execution_status="success" if storage_result['success'] else "failed",
                input_parameters={
                    "symbols": symbols,
                    "start_date": start_date.isoformat() if isinstance(start_date, (date, datetime)) else start_date,
                    "end_date": end_date.isoformat() if isinstance(end_date, (date, datetime)) else end_date,
                    "source_name": source_name,
                    "validate": validate,
                },
                output_result={
                    "collection_result": {
                        "records_collected": collection_result.records_collected,
                        "records_processed": collection_result.records_processed,
                        "success": collection_result.success,
                    },
                    "storage_result": storage_result,
                    "validation_results": validation_results,
                },
                records_processed=storage_result.get('records_processed', 0),
                records_succeeded=storage_result.get('records_succeeded', 0),
                records_failed=storage_result.get('records_failed', 0),
            )
            
            # 5. 记录数据质量指标
            quality_metrics = self._record_data_quality_metrics(
                data_source=source_name or collection_result.source_name,
                data_type="stock_daily",
                records_collected=collection_result.records_collected,
                records_processed=storage_result.get('records_processed', 0),
                records_succeeded=storage_result.get('records_succeeded', 0),
                records_failed=storage_result.get('records_failed', 0),
                validation_results=validation_results,
            )
            
            end_time = datetime.now()
            duration = (end_time - start_time).total_seconds()
            
            result = {
                "success": storage_result['success'],
                "task_id": task_result.get('task_id'),
                "duration_seconds": duration,
                "collection": {
                    "source": collection_result.source_name,
                    "records_collected": collection_result.records_collected,
                    "records_processed": collection_result.records_processed,
                },
                "storage": storage_result,
                "validation": {
                    "total_validations": len(validation_results),
                    "failed_validations": len([r for r in validation_results if not r.get('passed', True)]),
                },
                "quality": quality_metrics,
                "timestamp": end_time.isoformat(),
            }
            
            logger.info(
                f"股票日度行情数据处理完成: "
                f"收集 {collection_result.records_collected} 条, "
                f"存储 {storage_result.get('records_succeeded', 0)} 条, "
                f"耗时 {duration:.2f} 秒"
            )
            
            return result
            
        except Exception as e:
            end_time = datetime.now()
            duration = (end_time - start_time).total_seconds()
            
            logger.error(f"股票日度行情数据处理失败: {e}", exc_info=True)
            
            # 记录失败的任务执行
            self._record_task_execution(
                task_name="process_stock_daily",
                task_type="data_processing",
                execution_status="failed",
                input_parameters={
                    "symbols": symbols,
                    "start_date": start_date.isoformat() if isinstance(start_date, (date, datetime)) else start_date,
                    "end_date": end_date.isoformat() if isinstance(end_date, (date, datetime)) else end_date,
                    "source_name": source_name,
                    "validate": validate,
                },
                error_message=str(e),
                records_processed=0,
                records_succeeded=0,
                records_failed=0,
            )
            
            return {
                "success": False,
                "error": str(e),
                "duration_seconds": duration,
                "timestamp": end_time.isoformat(),
            }
    
    def process_stock_info(
        self,
        symbols: Optional[List[str]] = None,
        source_name: Optional[str] = None,
        validate: bool = True,
        **kwargs
    ) -> Dict[str, Any]:
        """
        处理股票基本信息
        
        Args:
            symbols: 股票代码列表
            source_name: 数据源名称
            validate: 是否验证数据
            **kwargs: 其他参数
            
        Returns:
            Dict[str, Any]: 处理结果
        """
        start_time = datetime.now()
        
        try:
            logger.info(f"开始处理股票基本信息")
            
            # 1. 从数据源收集数据
            collection_result = self.data_source_manager.collect_data(
                data_type=DataType.STOCK_INFO,
                source_name=source_name,
                symbols=symbols,
                **kwargs
            )
            
            if not collection_result.success:
                raise ValueError(f"数据收集失败: {collection_result.error_message}")
            
            logger.info(f"数据收集完成，共 {collection_result.records_collected} 条记录")
            
            # 2. 数据验证
            validation_results = []
            if validate and collection_result.processed_data:
                validation_results = self._validate_stock_info_data(
                    collection_result.processed_data
                )
                
                failed_validations = [r for r in validation_results if not r.get('passed', True)]
                if failed_validations:
                    logger.warning(f"数据验证发现 {len(failed_validations)} 个问题")
            
            # 3. 数据存储
            storage_result = self._store_stock_info_data(
                collection_result.processed_data,
                source_name or collection_result.source_name
            )
            
            # 4. 记录任务执行
            task_result = self._record_task_execution(
                task_name="process_stock_info",
                task_type="data_processing",
                execution_status="success" if storage_result['success'] else "failed",
                input_parameters={
                    "symbols": symbols,
                    "source_name": source_name,
                    "validate": validate,
                },
                output_result={
                    "collection_result": {
                        "records_collected": collection_result.records_collected,
                        "records_processed": collection_result.records_processed,
                        "success": collection_result.success,
                    },
                    "storage_result": storage_result,
                    "validation_results": validation_results,
                },
                records_processed=storage_result.get('records_processed', 0),
                records_succeeded=storage_result.get('records_succeeded', 0),
                records_failed=storage_result.get('records_failed', 0),
            )
            
            # 5. 记录数据质量指标
            quality_metrics = self._record_data_quality_metrics(
                data_source=source_name or collection_result.source_name,
                data_type="stock_info",
                records_collected=collection_result.records_collected,
                records_processed=storage_result.get('records_processed', 0),
                records_succeeded=storage_result.get('records_succeeded', 0),
                records_failed=storage_result.get('records_failed', 0),
                validation_results=validation_results,
            )
            
            end_time = datetime.now()
            duration = (end_time - start_time).total_seconds()
            
            result = {
                "success": storage_result['success'],
                "task_id": task_result.get('task_id'),
                "duration_seconds": duration,
                "collection": {
                    "source": collection_result.source_name,
                    "records_collected": collection_result.records_collected,
                    "records_processed": collection_result.records_processed,
                },
                "storage": storage_result,
                "validation": {
                    "total_validations": len(validation_results),
                    "failed_validations": len([r for r in validation_results if not r.get('passed', True)]),
                },
                "quality": quality_metrics,
                "timestamp": end_time.isoformat(),
            }
            
            logger.info(
                f"股票基本信息处理完成: "
                f"收集 {collection_result.records_collected} 条, "
                f"存储 {storage_result.get('records_succeeded', 0)} 条, "
                f"耗时 {duration:.2f} 秒"
            )
            
            return result
            
        except Exception as e:
            end_time = datetime.now()
            duration = (end_time - start_time).total_seconds()
            
            logger.error(f"股票基本信息处理失败: {e}", exc_info=True)
            
            # 记录失败的任务执行
            self._record_task_execution(
                task_name="process_stock_info",
                task_type="data_processing",
                execution_status="failed",
                input_parameters={
                    "symbols": symbols,
                    "source_name": source_name,
                    "validate": validate,
                },
                error_message=str(e),
                records_processed=0,
                records_succeeded=0,
                records_failed=0,
            )
            
            return {
                "success": False,
                "error": str(e),
                "duration_seconds": duration,
                "timestamp": end_time.isoformat(),
            }
    
    def process_fund_daily(
        self,
        symbols: Optional[List[str]] = None,
        start_date: Optional[Union[str, date, datetime]] = None,
        end_date: Optional[Union[str, date, datetime]] = None,
        source_name: Optional[str] = None,
        validate: bool = True,
        **kwargs
    ) -> Dict[str, Any]:
        """
        处理基金日度净值数据
        
        Args:
            symbols: 基金代码列表
            start_date: 开始日期
            end_date: 结束日期
            source_name: 数据源名称
            validate: 是否验证数据
            **kwargs: 其他参数
            
        Returns:
            Dict[str, Any]: 处理结果
        """
        start_time = datetime.now()
        
        try:
            logger.info(f"开始处理基金日度净值数据")
            
            # 1. 从数据源收集数据
            collection_result = self.data_source_manager.collect_data(
                data_type=DataType.FUND_DAILY,
                source_name=source_name,
                symbols=symbols,
                start_date=start_date,
                end_date=end_date,
                **kwargs
            )
            
            if not collection_result.success:
                raise ValueError(f"数据收集失败: {collection_result.error_message}")
            
            logger.info(f"数据收集完成，共 {collection_result.records_collected} 条记录")
            
            # 2. 数据验证
            validation_results = []
            if validate and collection_result.processed_data:
                validation_results = self._validate_fund_daily_data(
                    collection_result.processed_data
                )
                
                failed_validations = [r for r in validation_results if not r.get('passed', True)]
                if failed_validations:
                    logger.warning(f"数据验证发现 {len(failed_validations)} 个问题")
            
            # 3. 数据存储
            storage_result = self._store_fund_daily_data(
                collection_result.processed_data,
                source_name or collection_result.source_name
            )
            
            # 4. 记录任务执行
            task_result = self._record_task_execution(
                task_name="process_fund_daily",
                task_type="data_processing",
                execution_status="success" if storage_result['success'] else "failed",
                input_parameters={
                    "symbols": symbols,
                    "start_date": start_date.isoformat() if isinstance(start_date, (date, datetime)) else start_date,
                    "end_date": end_date.isoformat() if isinstance(end_date, (date, datetime)) else end_date,
                    "source_name": source_name,
                    "validate": validate,
                },
                output_result={
                    "collection_result": {
                        "records_collected": collection_result.records_collected,
                        "records_processed": collection_result.records_processed,
                        "success": collection_result.success,
                    },
                    "storage_result": storage_result,
                    "validation_results": validation_results,
                },
                records_processed=storage_result.get('records_processed', 0),
                records_succeeded=storage_result.get('records_succeeded', 0),
                records_failed=storage_result.get('records_failed', 0),
            )
            
            # 5. 记录数据质量指标
            quality_metrics = self._record_data_quality_metrics(
                data_source=source_name or collection_result.source_name,
                data_type="fund_daily",
                records_collected=collection_result.records_collected,
                records_processed=storage_result.get('records_processed', 0),
                records_succeeded=storage_result.get('records_succeeded', 0),
                records_failed=storage_result.get('records_failed', 0),
                validation_results=validation_results,
            )
            
            end_time = datetime.now()
            duration = (end_time - start_time).total_seconds()
            
            result = {
                "success": storage_result['success'],
                "task_id": task_result.get('task_id'),
                "duration_seconds": duration,
                "collection": {
                    "source": collection_result.source_name,
                    "records_collected": collection_result.records_collected,
                    "records_processed": collection_result.records_processed,
                },
                "storage": storage_result,
                "validation": {
                    "total_validations": len(validation_results),
                    "failed_validations": len([r for r in validation_results if not r.get('passed', True)]),
                },
                "quality": quality_metrics,
                "timestamp": end_time.isoformat(),
            }
            
            logger.info(
                f"基金日度净值数据处理完成: "
                f"收集 {collection_result.records_collected} 条, "
                f"存储 {storage_result.get('records_succeeded', 0)} 条, "
                f"耗时 {duration:.2f} 秒"
            )
            
            return result
            
        except Exception as e:
            end_time = datetime.now()
            duration = (end_time - start_time).total_seconds()
            
            logger.error(f"基金日度净值数据处理失败: {e}", exc_info=True)
            
            # 记录失败的任务执行
            self._record_task_execution(
                task_name="process_fund_daily",
                task_type="data_processing",
                execution_status="failed",
                input_parameters={
                    "symbols": symbols,
                    "start_date": start_date.isoformat() if isinstance(start_date, (date, datetime)) else start_date,
                    "end_date": end_date.isoformat() if isinstance(end_date, (date, datetime)) else end_date,
                    "source_name": source_name,
                    "validate": validate,
                },
                error_message=str(e),
                records_processed=0,
                records_succeeded=0,
                records_failed=0,
            )
            
            return {
                "success": False,
                "error": str(e),
                "duration_seconds": duration,
                "timestamp": end_time.isoformat(),
            }
    
    def process_index_daily(
        self,
        symbols: Optional[List[str]] = None,
        start_date: Optional[Union[str, date, datetime]] = None,
        end_date: Optional[Union[str, date, datetime]] = None,
        source_name: Optional[str] = None,
        validate: bool = True,
        **kwargs
    ) -> Dict[str, Any]:
        """
        处理指数日度行情数据
        
        Args:
            symbols: 指数代码列表
            start_date: 开始日期
            end_date: 结束日期
            source_name: 数据源名称
            validate: