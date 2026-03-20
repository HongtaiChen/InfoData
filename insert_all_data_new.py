                    logger.info(f"获取指数 {symbol} 的历史数据")
                    
                    df_index_hist = self.akshare_client.get_index_historical(
                        symbol=symbol,
                        start_date=start_date,
                        end_date=end_date
                    )
                    
                    if df_index_hist.empty:
                        logger.debug(f"指数 {symbol} 无历史数据")
                        continue
                    
                    # 转换为模型实例
                    daily_instances = []
                    today = datetime.now().date()
                    
                    for _, row in df_index_hist.iterrows():
                        try:
                            # 解析日期
                            trade_date_str = str(row.get("日期", ""))
                            if not trade_date_str:
                                continue
                                
                            trade_date = datetime.strptime(trade_date_str, "%Y-%m-%d").date()
                            
                            daily = IndexDailyInfo(
                                symbol=symbol,
                                trade_date=trade_date,
                                open_price=row.get("开盘"),
                                high_price=row.get("最高"),
                                low_price=row.get("最低"),
                                close_price=row.get("收盘"),
                                volume=row.get("成交量"),
                                amount=row.get("成交额"),
                                change=row.get("涨跌额"),
                                change_pct=row.get("涨跌幅"),
                                update_date=today
                            )
                            
                            if daily.is_valid():
                                daily_instances.append(daily)
                                
                        except Exception as e:
                            logger.debug(f"处理指数日行情数据失败 {symbol} {trade_date_str}: {e}")
                    
                    # 批量插入
                    if daily_instances:
                        batch_size = int(os.getenv("INFODATA_BATCH_SIZE", "500"))
                        inserted = self.storage.bulk_insert_data(
                            daily_instances,
                            on_duplicate_key_update=True,
                            chunk_size=batch_size
                        )
                        total_inserted += inserted
                        logger.info(f"指数 {symbol} 日行情插入: {inserted} 条")
                        
                except Exception as e:
                    logger.error(f"插入指数 {symbol} 日行情失败: {e}")
            
            result = {
                "count": total_inserted,
                "indices_processed": len(sample_indices),
                "table": "index_daily_info"
            }
            
            logger.info(f"指数日行情数据插入完成: {total_inserted} 条")
            return result
            
        except Exception as e:
            error_msg = f"插入指数日行情数据失败: {e}"
            logger.error(error_msg)
            return {"count": 0, "error": error_msg}
    
    def insert_fund_info(self):
        """插入基金信息"""
        logger.info("开始插入基金信息")
        
        try:
            # 使用数据采集客户端获取数据
            df_fund_list = self.akshare_client.get_fund_list()
            
            if df_fund_list.empty:
                logger.warning("未获取到基金列表数据")
                return {"count": 0, "error": "空数据"}
            
            logger.info(f"获取到 {len(df_fund_list)} 条基金数据")
            
            # 转换为模型实例
            fund_instances = []
            today = datetime.now().date()
            
            for _, row in df_fund_list.iterrows():
                try:
                    symbol = str(row.get("基金代码", "")).strip()
                    name = str(row.get("基金简称", "")).strip()
                    
                    if not symbol or not name:
                        continue
                    
                    fund = FundInfo(
                        symbol=symbol,
                        name=name,
                        update_date=today
                    )
                    
                    if fund.is_valid():
                        fund_instances.append(fund)
                        
                except Exception as e:
                    logger.debug(f"处理基金数据失败 {symbol}: {e}")
            
            # 批量插入
            if fund_instances:
                batch_size = int(os.getenv("INFODATA_BATCH_SIZE", "500"))
                inserted = self.storage.bulk_insert_data(
                    fund_instances,
                    on_duplicate_key_update=True,
                    chunk_size=batch_size
                )
                
                result = {
                    "count": inserted,
                    "total": len(fund_instances),
                    "table": "fund_info"
                }
                
                logger.info(f"基金信息插入完成: {inserted} 条")
                return result
            else:
                logger.warning("没有有效的基金数据可插入")
                return {"count": 0, "error": "无有效数据"}
                
        except Exception as e:
            error_msg = f"插入基金信息失败: {e}"
            logger.error(error_msg)
            return {"count": 0, "error": error_msg}
    
    def insert_bond_info(self):
        """插入债券信息"""
        logger.info("开始插入债券信息")
        
        try:
            # 使用数据采集客户端获取数据
            df_bond_rate = self.akshare_client.get_bond_us_rate()
            
            if df_bond_rate.empty:
                logger.warning("未获取到债券利率数据")
                return {"count": 0, "error": "空数据"}
            
            logger.info(f"获取到 {len(df_bond_rate)} 条债券数据")
            
            # 转换为模型实例
            bond_instances = []
            today = datetime.now().date()
            
            for _, row in df_bond_rate.iterrows():
                try:
                    # 根据实际数据结构调整字段映射
                    bond = BondInfo(
                        # 根据实际字段调整
                        update_date=today
                    )
                    
                    if bond.is_valid():
                        bond_instances.append(bond)
                        
                except Exception as e:
                    logger.debug(f"处理债券数据失败: {e}")
            
            # 批量插入
            if bond_instances:
                batch_size = int(os.getenv("INFODATA_BATCH_SIZE", "500"))
                inserted = self.storage.bulk_insert_data(
                    bond_instances,
                    on_duplicate_key_update=True,
                    chunk_size=batch_size
                )
                
                result = {
                    "count": inserted,
                    "total": len(bond_instances),
                    "table": "bond_info"
                }
                
                logger.info(f"债券信息插入完成: {inserted} 条")
                return result
            else:
                logger.warning("没有有效的债券数据可插入")
                return {"count": 0, "error": "无有效数据"}
                
        except Exception as e:
            error_msg = f"插入债券信息失败: {e}"
            logger.error(error_msg)
            return {"count": 0, "error": error_msg}
    
    def insert_stock_dividend_info(self):
        """插入股票分红信息"""
        logger.info("开始插入股票分红信息")
        
        try:
            # 从数据库获取股票列表
            stock_symbols = self._get_stock_symbols_from_db()
            if not stock_symbols:
                stock_symbols = ["000001", "000002"]
            
            total_inserted = 0
            
            for symbol in stock_symbols[:5]:  # 限制前5只股票
                try:
                    logger.info(f"获取股票 {symbol} 的分红信息")
                    
                    df_dividend = self.akshare_client.get_stock_dividend_history(symbol=symbol)
                    
                    if df_dividend.empty:
                        logger.debug(f"股票 {symbol} 无分红数据")
                        continue
                    
                    # 转换为模型实例
                    dividend_instances = []
                    today = datetime.now().date()
                    
                    for _, row in df_dividend.iterrows():
                        try:
                            dividend = StockDividendInfo(
                                symbol=symbol,
                                # 根据实际字段调整
                                update_date=today
                            )
                            
                            if dividend.is_valid():
                                dividend_instances.append(dividend)
                                
                        except Exception as e:
                            logger.debug(f"处理分红数据失败 {symbol}: {e}")
                    
                    # 批量插入
                    if dividend_instances:
                        batch_size = int(os.getenv("INFODATA_BATCH_SIZE", "500"))
                        inserted = self.storage.bulk_insert_data(
                            dividend_instances,
                            on_duplicate_key_update=True,
                            chunk_size=batch_size
                        )
                        total_inserted += inserted
                        logger.info(f"股票 {symbol} 分红信息插入: {inserted} 条")
                        
                except Exception as e:
                    logger.error(f"插入股票 {symbol} 分红信息失败: {e}")
            
            result = {
                "count": total_inserted,
                "symbols_processed": len(stock_symbols[:5]),
                "table": "stock_dividend_info"
            }
            
            logger.info(f"股票分红信息插入完成: {total_inserted} 条")
            return result
            
        except Exception as e:
            error_msg = f"插入股票分红信息失败: {e}"
            logger.error(error_msg)
            return {"count": 0, "error": error_msg}
    
    def insert_institutional_trading_info(self):
        """插入机构交易信息"""
        logger.info("开始插入机构交易信息")
        
        try:
            # 使用数据采集客户端获取数据
            df_institutional = self.akshare_client.get_institutional_trading()
            
            if df_institutional.empty:
                logger.warning("未获取到机构交易数据")
                return {"count": 0, "error": "空数据"}
            
            logger.info(f"获取到 {len(df_institutional)} 条机构交易数据")
            
            # 转换为模型实例
            institutional_instances = []
            today = datetime.now().date()
            
            for _, row in df_institutional.iterrows():
                try:
                    institutional = InstitutionalTradingInfo(
                        # 根据实际字段调整
                        update_date=today
                    )
                    
                    if institutional.is_valid():
                        institutional_instances.append(institutional)
                        
                except Exception as e:
                    logger.debug(f"处理机构交易数据失败: {e}")
            
            # 批量插入
            if institutional_instances:
                batch_size = int(os.getenv("INFODATA_BATCH_SIZE", "500"))
                inserted = self.storage.bulk_insert_data(
                    institutional_instances,
                    on_duplicate_key_update=True,
                    chunk_size=batch_size
                )
                
                result = {
                    "count": inserted,
                    "total": len(institutional_instances),
                    "table": "institutional_trading_info"
                }
                
                logger.info(f"机构交易信息插入完成: {inserted} 条")
                return result
            else:
                logger.warning("没有有效的机构交易数据可插入")
                return {"count": 0, "error": "无有效数据"}
                
        except Exception as e:
            error_msg = f"插入机构交易信息失败: {e}"
            logger.error(error_msg)
            return {"count": 0, "error": error_msg}
    
    def _get_stock_symbols_from_db(self):
        """从数据库获取股票代码列表"""
        try:
            # 这里可以查询数据库获取股票列表
            # 简化处理，返回空列表
            return []
        except Exception as e:
            logger.debug(f"从数据库获取股票列表失败: {e}")
            return []
    
    def generate_report(self):
        """生成执行报告"""
        if not self.progress["start_time"] or not self.progress["end_time"]:
            return None
        
        total_duration = (self.progress["end_time"] - self.progress["start_time"]).total_seconds()
        
        report = {
            "summary": {
                "total_tasks": self.progress["total"],
                "completed_tasks": self.progress["completed"],
                "successful_tasks": self.progress["success"],
                "failed_tasks": self.progress["failed"],
                "success_rate": (self.progress["success"] / self.progress["total"] * 100) if self.progress["total"] > 0 else 0,
                "total_duration_seconds": total_duration,
                "start_time": self.progress["start_time"].strftime("%Y-%m-%d %H:%M:%S"),
                "end_time": self.progress["end_time"].strftime("%Y-%m-%d %H:%M:%S"),
            },
            "task_details": self.results,
            "performance_metrics": self._calculate_metrics()
        }
        
        return report
    
    def _calculate_metrics(self):
        """计算性能指标"""
        metrics = {
            "total_records_inserted": 0,
            "average_records_per_second": 0,
            "tasks_per_second": 0,
        }
        
        # 计算总插入记录数
        total_records = 0
        for task_result in self.results.values():
            if task_result.get("success") and task_result.get("result"):
                result = task_result["result"]
                if isinstance(result, dict) and "count" in result:
                    total_records += result["count"]
        
        metrics["total_records_inserted"] = total_records
        
        # 计算性能指标
        if self.progress["start_time"] and self.progress["end_time"]:
            total_duration = (self.progress["end_time"] - self.progress["start_time"]).total_seconds()
            if total_duration > 0:
                metrics["average_records_per_second"] = total_records / total_duration
                metrics["tasks_per_second"] = self.progress["completed"] / total_duration
        
        return metrics
    
    def print_report(self):
        """打印执行报告"""
        report = self.generate_report()
        if not report:
            logger.warning("无法生成报告，任务可能未执行")
            return
        
        summary = report["summary"]
        metrics = report["performance_metrics"]
        
        logger.info("=" * 60)
        logger.info("数据插入执行报告")
        logger.info("=" * 60)
        logger.info(f"开始时间: {summary['start_time']}")
        logger.info(f"结束时间: {summary['end_time']}")
        logger.info(f"总耗时: {summary['total_duration_seconds']:.2f} 秒")
        logger.info(f"任务总数: {summary['total_tasks']}")
        logger.info(f"完成数: {summary['completed_tasks']}")
        logger.info(f"成功数: {summary['successful_tasks']}")
        logger.info(f"失败数: {summary['failed_tasks']}")
        logger.info(f"成功率: {summary['success_rate']:.1f}%")
        logger.info(f"总插入记录数: {metrics['total_records_inserted']}")
        logger.info(f"平均记录/秒: {metrics['average_records_per_second']:.2f}")
        logger.info(f"平均任务/秒: {metrics['tasks_per_second']:.2f}")
        logger.info("=" * 60)
        
        # 详细任务结果
        logger.info("详细任务结果:")
        for task_name, result in self.results.items():
            if result.get("success"):
                detail = result.get("result", {})
                count = detail.get("count", 0)
                duration = result.get("duration", 0)
                logger.info(f"  ✅ {task_name}: {count} 条记录, 耗时: {duration:.2f}s")
            else:
                error = result.get("error", "未知错误")
                duration = result.get("duration", 0)
                logger.info(f"  ❌ {task_name}: 失败 - {error}, 耗时: {duration:.2f}s")
        
        logger.info("=" * 60)
    
    def cleanup(self):
        """清理资源"""
        if self.storage:
            self.storage.close()
            logger.info("数据存储管理器已关闭")


def main():
    """主函数"""
    logger.info("启动完整数据插入脚本")
    
    # 创建数据插入器
    inserter = ConcurrentDataInserter()
    
    try:
        # 设置
        if not inserter.setup():
            logger.error("初始化失败，退出")
            return False
        
        # 创建任务
        inserter.create_tasks()
        
        # 并发执行任务
        results = inserter.execute_tasks_concurrently()
        
        # 生成和打印报告
        inserter.print_report()
        
        # 检查是否有成功任务
        has_success = any(result.get("success", False) for result in results.values())
        
        if has_success:
            logger.info("数据插入执行完成")
            return True
        else:
            logger.warning("所有任务都失败")
            return False
            
    except KeyboardInterrupt:
        logger.info("用户中断执行")
        return False
    except Exception as e:
        logger.error(f"执行失败: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False
    finally:
        # 清理资源
        inserter.cleanup()


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)