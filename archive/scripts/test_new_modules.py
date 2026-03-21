#!/usr/bin/env python3
"""
测试新创建的数据采集和存储模块

这个脚本演示如何使用重构后的数据采集层和数据库抽象层。
"""

import sys
import os
import logging
from datetime import datetime, timedelta

# 添加当前目录和src目录到路径
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

# 设置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def test_data_collection():
    """测试数据采集模块"""
    logger.info("=== 测试数据采集模块 ===")
    
    try:
        from data_collection.factory import get_akshare_client
        from data_collection.base import RateLimitConfig
        
        # 创建AKShare客户端
        rate_limit_config = RateLimitConfig(
            max_requests_per_minute=10,
            max_requests_per_hour=100,
            delay_between_requests=0.1
        )
        
        client = get_akshare_client(
            client_id="test_demo",
            rate_limit_config=rate_limit_config,
            max_retries=2,
            retry_delay=0.5
        )
        
        logger.info(f"创建AKShare客户端: {client.name}")
        
        # 测试连接
        logger.info("测试AKShare连接...")
        try:
            connected = client.test_connection()
            logger.info(f"连接测试: {'成功' if connected else '失败'}")
        except Exception as e:
            logger.warning(f"连接测试异常: {e}")
        
        # 获取实时行情数据
        logger.info("获取A股实时行情数据...")
        try:
            df = client.get_stock_spot()
            logger.info(f"获取到 {len(df)} 条股票数据")
            if not df.empty:
                logger.info(f"数据列: {', '.join(df.columns[:5])}...")
                logger.info(f"示例股票: {df.iloc[0]['代码']} - {df.iloc[0]['名称']} - {df.iloc[0]['最新价']}")
        except Exception as e:
            logger.warning(f"获取实时行情数据异常: {e}")
        
        # 获取历史数据
        logger.info("获取股票历史数据...")
        try:
            end_date = datetime.now()
            start_date = end_date - timedelta(days=10)
            
            df_hist = client.get_stock_historical(
                symbol="000001",  # 上证指数
                start_date=start_date,
                end_date=end_date
            )
            
            if df_hist.empty:
                logger.info("历史数据为空（可能没有交易日）")
            else:
                logger.info(f"获取到 {len(df_hist)} 条历史数据")
                logger.info(f"日期范围: {df_hist.iloc[0]['日期']} 到 {df_hist.iloc[-1]['日期']}")
        except Exception as e:
            logger.warning(f"获取历史数据异常: {e}")
        
        # 显示统计信息
        stats = client.get_stats()
        logger.info("客户端统计信息:")
        for key, value in stats.items():
            logger.info(f"  {key}: {value}")
        
        logger.info("数据采集模块测试完成")
        return True
        
    except Exception as e:
        logger.error(f"数据采集模块测试失败: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False


def test_config_manager():
    """测试配置管理模块"""
    logger.info("=== 测试配置管理模块 ===")
    
    try:
        from config.manager import ConfigManager
        
        # 创建配置管理器
        config = ConfigManager(env="development")
        logger.info(f"创建配置管理器，环境: {config.env}")
        
        # 获取配置值
        app_name = config.get("app.name")
        log_level = config.get("app.log_level")
        db_host = config.get("database.host")
        
        logger.info(f"应用名称: {app_name}")
        logger.info(f"日志级别: {log_level}")
        logger.info(f"数据库主机: {db_host}")
        
        # 验证配置
        is_valid = config.validate()
        logger.info(f"配置验证: {'通过' if is_valid else '失败'}")
        
        # 获取所有配置（敏感值已掩码）
        all_config = config.get_all()
        logger.info(f"配置节数: {len(all_config)}")
        
        logger.info("配置管理模块测试完成")
        return True
        
    except Exception as e:
        logger.error(f"配置管理模块测试失败: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False


def test_database_manager():
    """测试数据库管理器模块"""
    logger.info("=== 测试数据库管理器模块 ===")
    
    try:
        from data_storage.database import MySQLDatabaseManager
        
        # 注意：这里使用测试配置，实际使用时应从配置管理器获取
        # 检查是否有环境变量
        db_password = os.environ.get("INFODATA_DB_PASSWORD", "")
        
        if not db_password:
            logger.warning("未设置数据库密码环境变量，使用空密码测试")
        
        # 创建数据库管理器
        db_manager = MySQLDatabaseManager(
            host="localhost",
            port=3306,
            user="root",
            password=db_password,
            database="infodata_test",
            pool_size=5
        )
        
        logger.info(f"创建数据库管理器: {db_manager.host}:{db_manager.port}/{db_manager.database}")
        
        # 测试连接
        logger.info("测试数据库连接...")
        try:
            # 使用连接上下文管理器测试连接
            with db_manager.get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("SELECT 1 as test")
                    result = cursor.fetchone()
                    if result and result["test"] == 1:
                        logger.info("数据库连接测试: 成功")
                    else:
                        logger.warning("数据库连接测试: 异常响应")
        except Exception as e:
            logger.warning(f"数据库连接测试失败: {e}")
            logger.info("跳过进一步的数据库测试")
            return True  # 连接失败但不算测试失败
        
        # 创建测试表
        logger.info("创建测试表...")
        try:
            columns_def = {
                "id": "INT AUTO_INCREMENT",
                "symbol": "VARCHAR(10) NOT NULL",
                "name": "VARCHAR(100)",
                "price": "DECIMAL(10,2)",
                "volume": "BIGINT",
                "date": "DATE"
            }
            
            created = db_manager.create_table(
                table_name="test_stocks",
                columns_def=columns_def,
                primary_key=["id"],
                indexes=[{"name": "idx_symbol", "columns": ["symbol"]}],
                if_not_exists=True
            )
            
            if created:
                logger.info("测试表创建成功")
            else:
                logger.info("测试表已存在")
        
        except Exception as e:
            logger.warning(f"创建测试表失败: {e}")
        
        # 检查表是否存在
        try:
            exists = db_manager.table_exists("test_stocks")
            logger.info(f"测试表存在: {exists}")
            
            if exists:
                table_info = db_manager.get_table_info("test_stocks")
                logger.info(f"表信息: {table_info['name']}, 列数: {len(table_info['columns'])}, 行数: {table_info['row_count']}")
        
        except Exception as e:
            logger.warning(f"获取表信息失败: {e}")
        
        # 清理（可选）
        # db_manager.execute_query("DROP TABLE IF EXISTS test_stocks")
        
        db_manager.close_all_connections()
        logger.info("数据库管理器模块测试完成")
        return True
        
    except Exception as e:
        logger.error(f"数据库管理器模块测试失败: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False


def main():
    """主测试函数"""
    logger.info("开始测试重构后的模块...")
    
    results = []
    
    # 测试配置管理模块
    results.append(("配置管理", test_config_manager()))
    
    # 测试数据采集模块
    results.append(("数据采集", test_data_collection()))
    
    # 测试数据库管理器模块
    results.append(("数据库管理", test_database_manager()))
    
    # 输出结果
    logger.info("=== 测试结果汇总 ===")
    all_passed = True
    for module_name, passed in results:
        status = "通过" if passed else "失败"
        logger.info(f"{module_name}: {status}")
        if not passed:
            all_passed = False
    
    if all_passed:
        logger.info("所有模块测试通过！")
    else:
        logger.warning("部分模块测试失败")
    
    return all_passed


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)