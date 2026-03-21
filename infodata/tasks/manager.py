"""
任务管理器

管理具体的数据收集任务执行。
"""

from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
import time

from ..utils.logging import get_logger
from ..data.processor import DataProcessor
from ..data.sources.manager import get_data_source_manager
from ..models.task import TaskExecution
from ..utils.database import session_scope

logger = get_logger(__name__)


class TaskManager:
    """任务管理器"""
    
    def __init__(self):
        """初始化任务管理器"""
        self.data_processor = DataProcessor()
        self.data_source_manager = get_data_source_manager()
        
        logger.info("任务管理器初始化完成")
    
    def run_stock_daily_update(
        self,
        symbols: Optional[List[str]] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        source_name: Optional[str] = None,
        validate: bool = True,
        batch_size: int = 100,
        **kwargs
    ) -> Dict[str, Any]:
        """
        运行股票日度更新任务
        
        Args:
            symbols: 股票代码列表，如果为None则使用默认列表
            start_date: 开始日期，如果为None则使用昨天
            end_date: 结束日期，如果为None则使用今天
            source_name: 数据源名称
            validate: 是否验证数据
            batch_size: 批量处理大小
            **kwargs: 其他参数
            
        Returns:
            Dict[str, Any]: 任务执行结果
        """
        task_start = datetime.now()
        task_id = f"stock_daily_update_{task_start.strftime('%Y%m%d_%H%M%S')}"
        
        try:
            logger.info(f"开始执行股票日度更新任务: {task_id}")
            
            # 设置默认日期
            if end_date is None:
                end_date = datetime.now().strftime("%Y-%m-%d")
            
            if start_date is None:
                start_date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
            
            # 设置默认股票列表（如果未提供）
            if symbols is None:
                # 使用主要股票列表
                symbols = [
                    "000001.SZ",  # 平安银行
                    "000002.SZ",  # 万科A
                    "000858.SZ",  # 五粮液
                    "000333.SZ",  # 美的集团
                    "000651.SZ",  # 格力电器
                    "600000.SH",  # 浦发银行
                    "600036.SH",  # 招商银行
                    "600519.SH",  # 贵州茅台
                    "601318.SH",  # 中国平安
                    "601398.SH",  # 工商银行
                ]
            
            logger.info(f"股票日度更新任务参数: 股票数={len(symbols)}, 日期范围={start_date}到{end_date}")
            
            # 分批处理股票
            all_results = []
            total_records = 0
            total_success = 0
            total_failed = 0
            
            for i in range(0, len(symbols), batch_size):
                batch_symbols = symbols[i:i + batch_size]
                batch_num = i // batch_size + 1
                total_batches = (len(symbols) + batch_size - 1) // batch_size
                
                logger.info(f"处理批次 {batch_num}/{total_batches}: {len(batch_symbols)} 只股票")
                
                try:
                    # 处理当前批次的股票
                    result = self.data_processor.process_stock_daily(
                        symbols=batch_symbols,
                        start_date=start_date,
                        end_date=end_date,
                        source_name=source_name,
                        validate=validate,
                        **kwargs
                    )
                    
                    all_results.append(result)
                    
                    if result['success']:
                        total_records += result['storage'].get('records_processed', 0)
                        total_success += result['storage'].get('records_succeeded', 0)
                        total_failed += result['storage'].get('records_failed', 0)
                        
                        logger.info(
                            f"批次 {batch_num} 处理成功: "
                            f"成功 {result['storage'].get('records_succeeded', 0)} 条, "
                            f"失败 {result['storage'].get('records_failed', 0)} 条"
                        )
                    else:
                        logger.error(f"批次 {batch_num} 处理失败: {result.get('error', '未知错误')}")
                        
                    # 批次间延迟，避免频率限制
                    if i + batch_size < len(symbols):
                        time.sleep(1)
                        
                except Exception as e:
                    logger.error(f"批次 {batch_num} 处理异常: {e}", exc_info=True)
                    all_results.append({
                        "success": False,
                        "error": str(e),
                        "batch": batch_num,
                    })
            
            # 计算总体结果
            task_end = datetime.now()
            duration = (task_end - task_start).total_seconds()
            
            successful_batches = sum(1 for r in all_results if r.get('success', False))
            total_batches = len(all_results)
            
            overall_success = successful_batches > 0  # 只要有成功的批次就认为总体成功
            
            result = {
                "task_id": task_id,
                "success": overall_success,
                "duration_seconds": duration,
                "total_batches": total_batches,
                "successful_batches": successful_batches,
                "failed_batches": total_batches - successful_batches,
                "total_records": total_records,
                "successful_records": total_success,
                "failed_records": total_failed,
                "success_rate": total_success / total_records if total_records > 0 else 0,
                "batch_results": all_results,
                "parameters": {
                    "symbol_count": len(symbols),
                    "start_date": start_date,
                    "end_date": end_date,
                    "source_name": source_name,
                    "validate": validate,
                    "batch_size": batch_size,
                },
                "timestamp": task_end.isoformat(),
            }
            
            # 记录任务执行
            self._record_task_execution(
                task_name="stock_daily_update",
                task_type="data_collection",
                execution_status="success" if overall_success else "failed",
                input_parameters=result['parameters'],
                output_result=result,
                records_processed=total_records,
                records_succeeded=total_success,
                records_failed=total_failed,
            )
            
            logger.info(
                f"股票日度更新任务完成: "
                f"批次 {successful_batches}/{total_batches} 成功, "
                f"记录 {total_success}/{total_records} 成功, "
                f"耗时 {duration:.2f} 秒"
            )
            
            return result
            
        except Exception as e:
            task_end = datetime.now()
            duration = (task_end - task_start).total_seconds()
            
            logger.error(f"股票日度更新任务执行失败: {e}", exc_info=True)
            
            # 记录失败的任务执行
            self._record_task_execution(
                task_name="stock_daily_update",
                task_type="data_collection",
                execution_status="failed",
                input_parameters={
                    "symbol_count": len(symbols) if symbols else 0,
                    "start_date": start_date,
                    "end_date": end_date,
                    "source_name": source_name,
                },
                error_message=str(e),
                records_processed=0,
                records_succeeded=0,
                records_failed=0,
            )
            
            return {
                "task_id": task_id,
                "success": False,
                "error": str(e),
                "duration_seconds": duration,
                "timestamp": task_end.isoformat(),
            }
    
    def run_stock_info_update(
        self,
        symbols: Optional[List[str]] = None,
        source_name: Optional[str] = None,
        validate: bool = True,
        batch_size: int = 200,
        **kwargs
    ) -> Dict[str, Any]:
        """
        运行股票信息更新任务
        
        Args:
            symbols: 股票代码列表，如果为None则收集所有股票
            source_name: 数据源名称
            validate: 是否验证数据
            batch_size: 批量处理大小
            **kwargs: 其他参数
            
        Returns:
            Dict[str, Any]: 任务执行结果
        """
        task_start = datetime.now()
        task_id = f"stock_info_update_{task_start.strftime('%Y%m%d_%H%M%S')}"
        
        try:
            logger.info(f"开始执行股票信息更新任务: {task_id}")
            
            # 如果未提供股票列表，从数据源获取所有股票
            if symbols is None:
                logger.info("未提供股票列表，将从数据源获取所有股票")
                
                try:
                    # 从数据源获取股票列表
                    collection_result = self.data_source_manager.collect_data(
                        data_type="stock_info",
                        source_name=source_name or "akshare",
                    )
                    
                    if collection_result.success and collection_result.processed_data:
                        symbols = [record.get('symbol') for record in collection_result.processed_data]
                        symbols = [s for s in symbols if s]  # 过滤空值
                        logger.info(f"从数据源获取到 {len(symbols)} 只股票")
                    else:
                        logger.warning("无法从数据源获取股票列表，使用默认股票列表")
                        symbols = self._get_default_stock_symbols()
                        
                except Exception as e:
                    logger.error(f"获取股票列表失败: {e}")
                    symbols = self._get_default_stock_symbols()
            
            logger.info(f"股票信息更新任务参数: 股票数={len(symbols)}")
            
            # 分批处理股票
            all_results = []
            total_records = 0
            total_success = 0
            total_failed = 0
            
            for i in range(0, len(symbols), batch_size):
                batch_symbols = symbols[i:i + batch_size]
                batch_num = i // batch_size + 1
                total_batches = (len(symbols) + batch_size - 1) // batch_size
                
                logger.info(f"处理批次 {batch_num}/{total_batches}: {len(batch_symbols)} 只股票")
                
                try:
                    # 处理当前批次的股票
                    result = self.data_processor.process_stock_info(
                        symbols=batch_symbols,
                        source_name=source_name,
                        validate=validate,
                        **kwargs
                    )
                    
                    all_results.append(result)
                    
                    if result['success']:
                        total_records += result['storage'].get('records_processed', 0)
                        total_success += result['storage'].get('records_succeeded', 0)
                        total_failed += result['storage'].get('records_failed', 0)
                        
                        logger.info(
                            f"批次 {batch_num} 处理成功: "
                            f"成功 {result['storage'].get('records_succeeded', 0)} 条, "
                            f"失败 {result['storage'].get('records_failed', 0)} 条"
                        )
                    else:
                        logger.error(f"批次 {batch_num} 处理失败: {result.get('error', '未知错误')}")
                        
                    # 批次间延迟，避免频率限制
                    if i + batch_size < len(symbols):
                        time.sleep(1)
                        
                except Exception as e:
                    logger.error(f"批次 {batch_num} 处理异常: {e}", exc_info=True)
                    all_results.append({
                        "success": False,
                        "error": str(e),
                        "batch": batch_num,
                    })
            
            # 计算总体结果
            task_end = datetime.now()
            duration = (task_end - task_start).total_seconds()
            
            successful_batches = sum(1 for r in all_results if r.get('success', False))
            total_batches = len(all_results)
            
            overall_success = successful_batches > 0  # 只要有成功的批次就认为总体成功
            
            result = {
                "task_id": task_id,
                "success": overall_success,
                "duration_seconds": duration,
                "total_batches": total_batches,
                "successful_batches": successful_batches,
                "failed_batches": total_batches - successful_batches,
                "total_records": total_records,
                "successful_records": total_success,
                "failed_records": total_failed,
                "success_rate": total_success / total_records if total_records > 0 else 0,
                "batch_results": all_results,
                "parameters": {
                    "symbol_count": len(symbols),
                    "source_name": source_name,
                    "validate": validate,
                    "batch_size": batch_size,
                },
                "timestamp": task_end.isoformat(),
            }
            
            # 记录任务执行
            self._record_task_execution(
                task_name="stock_info_update",
                task_type="data_collection",
                execution_status="success" if overall_success else "failed",
                input_parameters=result['parameters'],
                output_result=result,
                records_processed=total_records,
                records_succeeded=total_success,
                records_failed=total_failed,
            )
            
            logger.info(
                f"股票信息更新任务完成: "
                f"批次 {successful_batches}/{total_batches} 成功, "
                f"记录 {total_success}/{total_records} 成功, "
                f"耗时 {duration:.2f} 秒"
            )
            
            return result
            
        except Exception as e:
            task_end = datetime.now()
            duration = (task_end - task_start).total_seconds()
            
            logger.error(f"股票信息更新任务执行失败: {e}", exc_info=True)
            
            # 记录失败的任务执行
            self._record_task_execution(
                task_name="stock信息更新",
                task_type="data_collection",
                execution_status="failed",
                input_parameters={
                    "symbol_count": len(symbols) if symbols else 0,
                    "source_name": source_name,
                },
                error_message=str(e),
                records_processed=0,
                records_succeeded=0,
                records_failed=0,
            )
            
            return {
                "task_id": task_id,
                "success": False,
                "error": str(e),
                "duration_seconds": duration,
                "timestamp": task_end.isoformat(),
            }
    
    def run_fund_daily_update(
        self,
        symbols: Optional[List[str]] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        source_name: Optional[str] = None,
        validate: bool = True,
        **kwargs
    ) -> Dict[str, Any]:
        """
        运行基金日度更新任务
        
        Args:
            symbols: 基金代码列表，如果为None则使用默认列表
            start_date: 开始日期，如果为None则使用昨天
            end_date: 结束日期，如果为None则使用今天
            source_name: 数据源名称
            validate: 是否验证数据
            **kwargs: 其他参数
            
        Returns:
            Dict[str, Any]: 任务执行结果
        """
        task_start = datetime.now()
        task_id = f"fund_daily_update_{task_start.strftime('%Y%m%d_%H%M%S')}"
        
        try:
            logger.info(f"开始执行基金日度更新任务: {task_id}")
            
            # 设置默认日期
            if end_date is None:
                end_date = datetime.now().strftime("%Y-%m-%d")
            
            if start_date is None:
                start_date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
            
            # 设置默认基金列表（如果未提供）
            if symbols is None:
                symbols = [
                    "000001",  # 华夏成长
                    "000002",  # 华夏大盘精选
                    "000011",  # 华夏大盘精选
                    "000021",  # 华夏优势增长
                    "110022",  # 易方达消费行业
                    "110023",  # 易方达医疗保健
                    "161725",  # 招商中证白酒
                    "161726",  # 招商国证生物医药
                    "519066",  # 汇添富蓝筹稳健
                    "519068",  # 汇添富成长焦点
                ]
            
            logger.info(f"基金日度更新任务参数: 基金数={len(symbols)}, 日期范围={start_date}到{end_date}")
            
            # 处理基金数据
            result = self.data_processor.process_fund_daily(
                symbols=symbols,
                start_date=start_date,
                end_date=end_date,
                source_name=source_name,
                validate=validate,
                **kwargs
            )
            
            task_end = datetime.now()
            duration = (task_end - task_start).total_seconds()
            
            # 更新结果信息
            result.update({
                "task_id": task_id,
                "duration_seconds": duration,
                "parameters": {
                    "symbol_count": len(symbols),
                    "start_date": start_date,
                    "end_date": end_date,
                    "source_name": source_name,
                    "validate": validate,
                },
            })
            
            # 记录任务执行
            self._record_task_execution(
                task_name="fund_daily_update",
                task_type="data_collection",
                execution_status="success" if result['success'] else "failed",
                input_parameters=result['parameters'],
                output_result=result,
                records_processed=result.get('storage', {}).get('records_processed', 0),
                records_succeeded=result.get('storage', {}).get('records_succeeded', 0),
                records_failed=result.get('storage', {}).get('records_failed', 0),
            )
            
            if result['success']:
                logger.info(
                    f"基金日度更新任务完成: "
                    f"成功 {result.get('storage', {}).get('records_succeeded', 0)} 条, "
                    f"失败 {result.get('storage', {}).get('records_failed', 0)} 条, "
                    f"耗时 {duration:.2f} 秒"
                )
            else:
                logger.error(f"基金日度更新任务失败: {result.get('error', '未知错误')}")
            
            return result
            
        except Exception as e:
            task_end = datetime.now()
            duration = (task_end - task_start).total_seconds()
            
            logger.error(f"基金日度更新任务执行失败: {e}", exc_info=True)
            
            # 记录失败的任务执行
            self._record_task_execution(
                task_name="fund_daily_update",
                task_type="data_collection",
                execution_status="failed",
                input_parameters={
                    "symbol_count": len(symbols) if symbols else 0,
                    "start_date": start_date,
                    "end_date": end_date,
                    "source_name": source_name,
                },
                error_message=str(e),
                records_processed=0,
                records_succeeded=0,
                records_failed=0,
            )
            
            return {
                "task_id": task_id,
                "success": False,
                "error": str(e),
                "duration_seconds": duration,
                "timestamp": task_end.isoformat(),
            }
    
    def run_index_daily_update(
        self,
        symbols: Optional[List[str]] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        source_name: Optional[str] = None,
        validate: bool = True,
        **kwargs
    ) -> Dict[str, Any]:
        """
        运行指数日度更新任务
        
        Args:
            symbols: 指数代码列表，如果为None则使用默认列表
            start_date: 开始日期，如果为None则使用昨天
            end_date: 结束日期，如果为None则使用今天
            source_name: 数据源名称
            validate: 是否验证数据
            **kwargs: 其他参数
            
        Returns:
            Dict[str, Any]: 任务执行结果
        """
        task_start = datetime.now()
        task_id = f"index_daily_update_{task_start.strftime('%Y%m%d_%H%M%S')}"
        
        try:
            logger.info(f"开始执行指数日度更新任务: {task_id}")
            
            # 设置默认日期
            if end_date is None:
                end_date = datetime.now().strftime("%Y-%m-%d")
            
            if start_date is None:
                start_date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
            
            # 设置默认指数列表（如果未提供）
            if symbols is None:
                symbols = [
                    "sh000001",  # 上证指数
                    "sz399001",  # 深证成指
                    "sh000300",  # 沪深300
                    "sh000905",  # 中证500
                    "sz399006",  # 创业板指
                    "sh000016",  # 上证50
                    "sh000010",  # 上证180
                    "sz399005",  # 中小板指
                ]
            
            logger.info(f"指数日度更新任务参数: 指数数={len(symbols)}, 日期范围={start_date}到{end_date}")
            
            # 处理指数数据
            result = self.data_processor.process_index_daily(
                symbols=symbols,
                start_date=start_date,
                end_date=end_date,
                source_name=source_name,
                validate=validate,
                **kwargs
            )
            
            task_end = datetime.now()
            duration = (task_end - task_start).total_seconds()
            
            # 更新结果信息
            result.update({
                "task_id": task_id,
                "duration_seconds": duration,
                "parameters": {
                    "symbol_count": len(symbols),
                    "start_date": start_date,
                    "end_date": end_date,
                    "source_name": source_name,
                    "validate": validate,
                },
            })
            
            # 记录任务执行
            self._record_task_execution(
                task_name="index_daily_update",
                task_type="data_collection",
                execution_status="success" if result['success'] else "failed",
                input_parameters=result['parameters'],
                output_result=result,
                records_processed=result.get('storage', {}).get('records_processed', 0),
                records_succeeded=result.get('storage', {}).get('records_succeeded', 0),
                records_failed=result.get('storage', {}).get('records_failed', 0),
            )
            
            if result['success']:
                logger.info(
                    f"指数日度更新任务完成: "
                    f"成功 {result.get('storage', {}).get('records_succeeded', 0)} 条, "
                    f"失败 {result.get('storage', {}).get('records_failed', 0)} 条, "
                    f"耗时 {duration:.2f} 秒"
                )
            else:
                logger.error(f"指数日度更新任务失败: {result.get('error', '未知错误')}")
            
            return result
            
        except Exception as e:
            task_end = datetime.now()
            duration = (task_end - task_start).total_seconds()
            
            logger.error(f"指数日度更新任务执行失败: {e}", exc_info=True)
            
            # 记录失败的任务执行
            self._record_task_execution(
                task_name="index_daily_update",
                task_type="data_collection",
                execution_status="failed",
                input_parameters={
                    "symbol_count": len(symbols) if symbols else 0,
                    "start_date": start_date,
                    "end_date": end_date,
                    "source_name": source_name,
                },
                error_message=str(e),
                records_processed=0,
                records_succeeded=0,
                records_failed=0,
            )
            
            return {
                "task_id": task_id,
                "success": False,
                "error": str(e),
                "duration_seconds": duration,
                "timestamp": task_end.isoformat(),
            }
    
    def run_all_updates(
        self,
        stock_symbols: Optional[List[str]] = None,
        fund_symbols: Optional[List[str]] = None,
        index_symbols: Optional[List[str]] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        运行所有更新任务
        
        Args:
            stock_symbols: 股票代码列表
            fund_symbols: 基金代码列表
            index_symbols: 指数代码列表
            **kwargs: 其他参数
            
        Returns:
            Dict[str, Any]: 所有任务执行结果
        """
        task_start = datetime.now()
        task_id = f"all_updates_{task_start.strftime('%Y%m%d_%H%M%S')}"
        
        try:
            logger.info(f"开始执行所有更新任务: {task_id}")
            
            results = {}
            
            # 1. 运行股票日度更新
            logger.info("开始执行股票日度更新...")
            stock_result = self.run_stock_daily_update(
                symbols=stock_symbols,
                **kwargs
            )
            results['stock_daily'] = stock_result
            
            # 2. 运行基金日度更新
            logger.info("开始执行基金日度更新...")
            fund_result = self.run_fund_daily_update(
                symbols=fund_symbols,
                **kwargs
            )
            results['fund_daily'] = fund_result
            
            # 3. 运行指数日度更新
            logger.info("开始执行指数日度更新...")
            index_result = self.run_index_daily_update(
                symbols=index_symbols,
                **kwargs
            )
            results['index_daily'] = index_result
            
            # 计算总体结果
            task_end = datetime.now()
            duration = (task_end - task_start).total_seconds()
            
            successful_tasks = sum(1 for r in results.values() if r.get('success', False))
            total_tasks = len(results)
            
            overall_success = successful_tasks == total_tasks
            
            result = {
                "task_id": task_id,
                "success": overall_success,
                "duration_seconds": duration,
                "total_tasks": total_tasks,
                "successful_tasks": successful_tasks,
                "failed_tasks": total_tasks - successful_tasks,
                "task_results": results,
                "timestamp": task_end.isoformat(),
            }
            
            # 记录总体任务执行
            self._record_task_execution(
                task_name="all_updates",
                task_type="batch",
                execution_status="success" if overall_success else "partial",
                input_parameters={
                    "stock_symbol_count": len(stock_symbols) if stock_symbols else "default",
                    "fund_symbol_count": len(fund_symbols) if fund_symbols else "default",
                    "index_symbol_count": len(index_symbols) if index_symbols else "default",
                },
                output_result=result,
                records_processed=0,  # 由子任务记录
                records_succeeded=0,
                records_failed=0,
            )
            
            logger.info(
                f"所有更新任务完成: "
                f"任务 {successful_tasks}/{total_tasks} 成功, "
                f"耗时 {duration:.2f} 秒"
            )
            
            return result
            
        except Exception as e:
            task_end = datetime.now()
            duration = (task_end - task_start).total_seconds()
            
            logger.error(f"所有更新任务执行失败: {e}", exc_info=True)
            
            # 记录失败的任务执行
            self._record_task_execution(
                task_name="all_updates",
                task_type="batch",
                execution_status="failed",
                input_parameters={
                    "stock_symbol_count": len(stock_symbols) if stock_symbols else "default",
                    "fund_symbol_count": len(fund_symbols) if fund_symbols else "default",
                    "index_symbol_count": len(index_symbols) if index_symbols else "default",
                },
                error_message=str(e),
                records_processed=0,
                records_succeeded=0,
                records_failed=0,
            )
            
            return {
                "task_id": task_id,
                "success": False,
                "error": str(e),
                "duration_seconds": duration,
                "timestamp": task_end.isoformat(),
            }
    
    def _get_default_stock_symbols(self) -> List[str]:
        """
        获取默认股票代码列表
        
        Returns:
            List[str]: 默认股票代码列表
        """
        return [
            "000001.SZ", "000002.SZ", "000858.SZ", "000333.SZ", "000651.SZ",
            "600000.SH", "600036.SH", "600519.SH", "601318.SH", "601398.SH",
            "000063.SZ", "000725.SZ", "000100.SZ", "000568.SZ", "000538.SZ",
            "600887.SH", "600104.SH", "600276.SH", "600309.SH", "600585.SH",
        ]
    
    def _record_task_execution(
        self,
        task_name: str,
        task_type: str,
        execution_status: str,
        input_parameters: Dict[str, Any],
        output_result: Optional[Dict[str, Any]] = None,
        error_message: Optional[str] = None,
        records_processed: int = 0,
        records_succeeded: int = 0,
        records_failed: int = 0,
    ) -> Dict[str, Any]:
        """
        记录任务执行
        
        Args:
            task_name: 任务名称
            task_type: 任务类型
            execution_status: 执行状态
            input_parameters: 输入参数
            output_result: 输出结果
            error_message: 错误信息
            records_processed: 处理记录数
            records_succeeded: 成功记录数
            records_failed: 失败记录数
            
        Returns:
            Dict[str, Any]: 任务记录结果
        """
        try:
            with session_scope() as session:
                task = TaskExecution(
                    source="task_manager",
                    source_id=f"{task_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
                    task_name=task_name,
                    task_type=task_type,
                    execution_start=datetime.now(),
                    execution_end=datetime.now(),
                    execution_status=execution_status,
                    exit_code=0 if execution_status == "success" else 1,
                    error_message=error_message,
                    input_parameters=input_parameters,
                    output_result=output_result or {},
                    records_processed=records_processed,
                    records_succeeded=records_succeeded,
                    records_failed=records_failed,
                )
                
                session.add(task)
                session.commit()
                
                return {
                    "task_id": task.id,
                    "success": True,
                }
                
        except Exception as e:
            logger.error(f"记录任务执行失败: {e}")
            return {
                "task_id": None,
                "success": False,
                "error": str(e),
            }


# 全局任务管理器实例
_global_manager: Optional[TaskManager] = None


def get_task_manager() -> TaskManager:
    """
    获取全局任务管理器
    
    Returns:
        TaskManager: 任务管理器实例
    """
    global _global_manager
    
    if _global_manager is None:
        _global_manager = TaskManager()
    
    return _global_manager


def run_stock_daily_update(
    symbols: Optional[List[str]] = None,
    **kwargs
) -> Dict[str, Any]:
    """
    运行股票日度更新任务（便捷函数）
    
    Args:
        symbols: 股票代码列表
        **kwargs: 其他参数
        
    Returns:
        Dict[str, Any]: 任务执行结果
    """
    manager = get_task_manager()
    return manager.run_stock_daily_update(symbols=symbols, **kwargs)


def run_stock_info_update(
    symbols: Optional[List[str]] = None,
    **kwargs
) -> Dict[str, Any]:
    """
    运行股票信息更新任务（便捷函数）
    
    Args:
        symbols: 股票代码列表
        **kwargs: 其他参数
        
    Returns:
        Dict[str, Any]: 任务执行结果
    """
    manager = get_task_manager()
    return manager.run_stock_info_update(symbols=symbols, **kwargs)


def run_fund_daily_update(
    symbols: Optional[List[str]] = None,
    **kwargs
) -> Dict[str, Any]:
    """
    运行基金日度更新任务（便捷函数）
    
    Args:
        symbols: 基金代码列表
        **kwargs: 其他参数
        
    Returns:
        Dict[str, Any]: 任务执行结果
    """
    manager = get_task_manager()
    return manager.run_fund_daily_update(symbols=symbols, **kwargs)


def run_index_daily_update(
    symbols: Optional[List[str]] = None,
    **kwargs
) -> Dict[str, Any]:
    """
    运行指数日度更新任务（便捷函数）
    
    Args:
        symbols: 指数代码列表
        **kwargs: 其他参数
        
    Returns:
        Dict[str, Any]: 任务执行结果
    """
    manager = get_task_manager()
    return manager.run_index_daily_update(symbols=symbols, **kwargs)


def run_all_updates(**kwargs) -> Dict[str, Any]:
    """
    运行所有更新任务（便捷函数）
    
    Args:
        **kwargs: 其他参数
        
    Returns:
        Dict[str, Any]: 所有任务执行结果
    """
    manager = get_task_manager()
    return manager.run_all_updates(**kwargs)