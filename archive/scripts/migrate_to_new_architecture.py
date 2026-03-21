#!/usr/bin/env python3
"""
迁移到新架构示例

演示如何将现有代码迁移到新的数据采集和存储架构。
"""

import sys
import os
import logging
from datetime import datetime, date
from typing import List, Dict, Any, Optional

# 添加src目录到路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

# 设置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def demonstrate_data_collection():
    """演示数据采集模块使用"""
    logger.info("=== 演示数据采集模块 ===")
    
    try:
        from data_collection.factory import get_akshare_client
        from data_collection.base import RateLimitConfig
        
        # 创建客户端
        rate_limit = RateLimitConfig(
            max_requests_per_minute=10,
            max_requests_per_hour=100,
            delay_between_requests=0.1
        )
        
        client = get_akshare_client(
            client_id="migration_demo",
            rate_limit_config=rate_limit,
            max_retries=2,
            retry_delay=0.5
        )
        
        logger.info(f"使用客户端: {client.name}")
        
        # 示例1：获取股票实时行情
        logger.info("示例1：获取股票实时行情")
        try:
            df_stock_spot = client.get_stock_spot()
            logger.info(f"获取到 {len(df_stock_spot)} 条股票实时行情数据")
            
            # 转换为模型实例
            from data_storage.models.stock import StockInfo
            from datetime import date as dt_date
            
            stock_instances = []
            for _, row in df_stock_spot.head(5).iterrows():  # 只处理前5条作为示例
                stock = StockInfo(
                    symbol=row["代码"],
                    name=row["名称"],
                    update_date=dt_date.today()
                )
                stock_instances.append(stock)
                logger.debug(f"创建股票模型: {stock.symbol} - {stock.name}")
            
            logger.info(f"创建了 {len(stock_instances)} 个股票模型实例")
            
        except Exception as e:
            logger.warning(f"示例1异常: {e}")
        
        # 示例2：获取股票历史数据
        logger.info("示例2：获取股票历史数据")
        try:
            from datetime import timedelta
            
            end_date = datetime.now()
            start_date = end_date - timedelta(days=30)
            
            df_stock_hist = client.get_stock_historical(
                symbol="000001",  # 上证指数
                start_date=start_date,
                end_date=end_date
            )
            
            if not df_stock_hist.empty:
                logger.info(f"获取到 {len(df_stock_hist)} 条股票历史数据")
                
                # 转换为模型实例
                from data_storage.models.stock import StockDailyInfo
                
                daily_instances = []
                for _, row in df_stock_hist.iterrows():
                    # 解析日期（假设格式为 YYYY-MM-DD）
                    trade_date = datetime.strptime(str(row["日期"]), "%Y-%m-%d").date()
                    
                    daily = StockDailyInfo(
                        symbol=row["代码"],
                        trade_date=trade_date,
                        open_price=row.get("开盘"),
                        high_price=row.get("最高"),
                        low_price=row.get("最低"),
                        close_price=row.get("收盘"),
                        volume=row.get("成交量"),
                        amount=row.get("成交额"),
                        change=row.get("涨跌额"),
                        change_pct=row.get("涨跌幅"),
                        update_date=dt_date.today()
                    )
                    daily_instances.append(daily)
                
                logger.info(f"创建了 {len(daily_instances)} 个股票日行情模型实例")
                
        except Exception as e:
            logger.warning(f"示例2异常: {e}")
        
        # 示例3：获取基金列表
        logger.info("示例3：获取基金列表")
        try:
            df_funds = client.get_fund_list()
            logger.info(f"获取到 {len(df_funds)} 条基金数据")
            
        except Exception as e:
            logger.warning(f"示例3异常: {e}")
        
        logger.info("数据采集模块演示完成")
        return True
        
    except Exception as e:
        logger.error(f"数据采集模块演示失败: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False


def demonstrate_data_storage():
    """演示数据存储模块使用"""
    logger.info("=== 演示数据存储模块 ===")
    
    try:
        # 导入配置管理器
        from config.manager import get_config_manager
        
        # 获取配置
        config = get_config_manager(env="development")
        
        # 从配置获取数据库连接信息
        db_host = config.get("database.host", "localhost")
        db_port = config.get("database.port", 3306)
        db_user = config.get("database.user", "")
        db_password = config.get("database.password", "")
        db_name = config.get("database.name", "infodata_test")
        
        logger.info(f"数据库配置: {db_host}:{db_port}/{db_name}")
        
        # 初始化数据存储管理器
        from data_storage.manager import setup_data_storage
        
        storage = setup_data_storage(
            host=db_host,
            port=db_port,
            user=db_user,
            password=db_password,
            database=db_name,
            create_tables=True  # 自动创建表
        )
        
        # 检查表状态
        table_status = storage.get_table_status()
        logger.info(f"表状态检查完成，共 {len(table_status)} 个表")
        
        for table_name, status in table_status.items():
            if "error" in status:
                logger.warning(f"表 {table_name} 状态检查错误: {status['error']}")
            else:
                logger.info(f"表 {table_name}: 存在={status['exists']}, 模型={status['model']}")
        
        # 示例：插入股票数据
        logger.info("示例：插入股票数据")
        try:
            from data_storage.models.stock import StockInfo
            from datetime import date as dt_date
            
            # 创建示例股票数据
            sample_stock = StockInfo(
                symbol="000001",
                name="平安银行",
                listing_date=dt_date(1991, 4, 3),
                total_shares=19400000000,
                float_shares=19400000000,
                industry="银行",
                area="深圳",
                market_type="主板",
                update_date=dt_date.today()
            )
            
            # 验证数据
            if sample_stock.is_valid():
                logger.info(f"股票数据验证通过: {sample_stock}")
                
                # 插入数据
                success = storage.insert_data(sample_stock, on_duplicate_key_update=True)
                if success:
                    logger.info("股票数据插入成功")
                else:
                    logger.warning("股票数据插入失败")
            else:
                logger.warning(f"股票数据验证失败: {sample_stock.get_errors()}")
                
        except Exception as e:
            logger.warning(f"插入股票数据异常: {e}")
        
        # 示例：批量插入数据
        logger.info("示例：批量插入数据")
        try:
            from data_storage.models.stock import StockDailyInfo
            from datetime import date as dt_date, timedelta
            
            # 创建示例日行情数据
            daily_instances = []
            base_date = dt_date.today()
            
            for i in range(5):
                trade_date = base_date - timedelta(days=i)
                daily = StockDailyInfo(
                    symbol="000001",
                    trade_date=trade_date,
                    open_price=10.0 + i * 0.1,
                    high_price=10.5 + i * 0.1,
                    low_price=9.5 + i * 0.1,
                    close_price=10.2 + i * 0.1,
                    volume=1000000 + i * 100000,
                    amount=10000000 + i * 1000000,
                    change=0.1 + i * 0.01,
                    change_pct=1.0 + i * 0.1,
                    turnover_rate=2.5 + i * 0.1,
                    update_date=base_date
                )
                
                if daily.is_valid():
                    daily_instances.append(daily)
                else:
                    logger.warning(f"日行情数据验证失败: {daily.get_errors()}")
            
            if daily_instances:
                inserted_rows = storage.bulk_insert_data(
                    daily_instances,
                    on_duplicate_key_update=True,
                    chunk_size=100
                )
                logger.info(f"批量插入了 {inserted_rows} 条日行情数据")
                
        except Exception as e:
            logger.warning(f"批量插入数据异常: {e}")
        
        # 示例：执行查询
        logger.info("示例：执行查询")
        try:
            # 查询股票表
            query = "SELECT COUNT(*) as count FROM A_stock_info"
            result = storage.execute_query(query, fetch_all=True)
            
            if result:
                logger.info(f"股票表记录数: {result[0]['count']}")
            
            # 查询表信息
            table_info = storage.get_table_info("A_stock_info")
            logger.info(f"股票表信息: {len(table_info['columns'])} 列, {table_info['row_count']} 行")
            
        except Exception as e:
            logger.warning(f"执行查询异常: {e}")
        
        # 关闭连接
        storage.close()
        logger.info("数据存储模块演示完成")
        return True
        
    except Exception as e:
        logger.error(f"数据存储模块演示失败: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False


def demonstrate_migration_pattern():
    """演示迁移模式"""
    logger.info("=== 演示迁移模式 ===")
    
    # 展示如何迁移旧代码到新架构
    migration_examples = """
    ## 迁移模式示例
    
    ### 旧代码模式 (insert_to_DB.py):
    ```
    import pymysql
    import akshare as ak
    
    def conn_db():
        return pymysql.connect(host='localhost', user='root', password='root', database='infodata')
    
    def insert_stock_info():
        # 直接调用AKShare
        df = ak.stock_zh_a_spot_em()
        
        # 直接数据库操作
        conn = conn_db()
        cursor = conn.cursor()
        for _, row in df.iterrows():
            sql = "INSERT INTO A_stock_info ..."
            cursor.execute(sql, (...))
        conn.commit()
    ```
    
    ### 新架构模式:
    ```
    # 1. 使用统一的数据采集客户端
    from data_collection.factory import get_akshare_client
    client = get_akshare_client()
    df = client.get_stock_spot()  # 带错误处理和速率限制
    
    # 2. 使用数据模型
    from data_storage.models.stock import StockInfo
    from data_storage.manager import get_storage_manager
    
    # 转换为模型实例
    stock_instances = []
    for _, row in df.iterrows():
        stock = StockInfo(
            symbol=row["代码"],
            name=row["名称"],
            # ... 其他字段
        )
        if stock.is_valid():  # 自动验证
            stock_instances.append(stock)
    
    # 3. 使用数据存储管理器
    storage = get_storage_manager()
    inserted = storage.bulk_insert_data(stock_instances)  # 批量插入，带连接池
    
    # 或者直接使用DataFrame
    storage.insert_dataframe("A_stock_info", df)
    ```
    
    ### 优势:
    1. **错误处理**: 自动重试、速率限制、数据验证
    2. **安全性**: 敏感配置通过环境变量管理
    3. **可维护性**: 统一的接口、清晰的模型定义
    4. **性能**: 连接池、批量操作
    5. **可测试性**: 模块化设计，易于单元测试
    """
    
    logger.info(migration_examples)
    return True


def create_migration_guide():
    """创建迁移指南"""
    logger.info("=== 创建迁移指南 ===")
    
    guide_content = """
    # InfoData 项目迁移指南
    
    ## 1. 环境设置
    
    ### 1.1 安装依赖
    ```bash
    # 基础依赖（已安装）
    pip install akshare tushare pymysql pandas
    
    # 开发依赖（建议安装）
    pip install pytest coverage black flake8 mypy
    ```
    
    ### 1.2 环境变量配置
    ```bash
    # 数据库配置（替代硬编码密码）
    export INFODATA_DB_PASSWORD="your_secure_password"
    export INFODATA_DB_HOST="localhost"
    export INFODATA_DB_PORT="3306"
    export INFODATA_DB_NAME="infodata"
    
    # Tushare Token
    export INFODATA_TUSHARE_TOKEN="your_tushare_token"
    
    # 应用配置
    export INFODATA_APP_ENV="development"  # development, testing, production
    export INFODATA_APP_LOG_LEVEL="INFO"
    ```
    
    ## 2. 代码迁移步骤
    
    ### 2.1 更新导入语句
    ```python
    # 旧代码
    import akshare as ak
    import tushare as ts
    import pymysql
    
    # 新代码
    from data_collection.factory import get_akshare_client, get_tushare_client
    from data_storage.manager import get_storage_manager
    from data_storage.models.stock import StockInfo, StockDailyInfo
    ```
    
    ### 2.2 替换数据采集调用
    ```python
    # 旧代码
    df = ak.stock_zh_a_spot_em()
    
    # 新代码
    client = get_akshare_client()
    df = client.get_stock_spot()  # 自动错误处理和速率限制
    ```
    
    ### 2.3 替换数据库操作
    ```python
    # 旧代码
    conn = pymysql.connect(host='localhost', user='root', password='root', database='infodata')
    cursor = conn.cursor()
    for _, row in df.iterrows():
        sql = "INSERT INTO table (...) VALUES (...)"
        cursor.execute(sql, (...))
    conn.commit()
    
    # 新代码
    storage = get_storage_manager()
    
    # 方法1: 使用DataFrame直接插入
    inserted = storage.insert_dataframe("table_name", df)
    
    # 方法2: 使用数据模型（推荐）
    instances = []
    for _, row in df.iterrows():
        model_instance = StockInfo(...)  # 创建模型实例
        if model_instance.is_valid():
            instances.append(model_instance)
    
    inserted = storage.bulk_insert_data(instances)  # 批量插入
    ```
    
    ### 2.4 添加数据验证
    ```python
    # 旧代码：无验证
    
    # 新代码：自动验证
    model_instance = StockInfo(symbol="000001", name="测试", ...)
    
    try:
        model_instance.validate()  # 验证数据
        storage.insert_data(model_instance)
    except ValidationError as e:
        logger.error(f"数据验证失败: {e}")
        # 处理验证错误
    ```
    
    ## 3. 迁移现有脚本
    
    ### 3.1 insert_to_DB.py
    将各个插入函数迁移到对应的模型和存储管理器。
    
    ### 3.2 daily_update_stock_info.py
    使用新的数据采集客户端替换直接的AKShare调用。
    
    ### 3.3 monthly_update_*.py
    同理迁移其他更新脚本。
    
    ## 4. 测试迁移
    
    ### 4.1 运行测试
    ```bash
    cd /root/.openclaw/workspace/InfoData
    python -m pytest src/tests/ -v
    ```
    
    ### 4.2 运行迁移示例
    ```bash
    python migrate_to_new_architecture.py
    ```
    
    ## 5. 部署注意事项
    
    1. **数据库备份**: 迁移前备份现有数据库
    2. **逐步迁移**: 先迁移非关键功能，验证后再迁移核心功能
    3. **监控日志**: 新架构会记录更详细的日志，注意监控
    4. **性能调优**: 根据实际情况调整连接池大小、批量操作大小等参数
    
    ## 6. 遇到问题
    
    1. **查看日志**: 所有模块都有详细的日志记录
    2. **检查配置**: 确认环境变量和配置文件正确
    3. **验证连接**: 使用测试脚本验证数据库和数据源连接
    4. **回滚计划**: 保留旧代码，确保可以回滚
    """
    
    # 将指南写入文件
    guide_path = os.path.join(os.path.dirname(__file__), "MIGRATION_GUIDE.md")
    with open(guide_path, "w", encoding="utf-8") as f:
        f.write(guide_content)
    
    logger.info(f"迁移指南已创建: {guide_path}")
    return True


def main():
    """主函数"""
    logger.info("开始演示新架构迁移...")
    
    results = []
    
    # 演示数据采集模块
    results.append(("数据采集模块", demonstrate_data_collection()))
    
    # 演示数据存储模块
    results.append(("数据存储模块", demonstrate_data_storage()))
    
    # 演示迁移模式
    results.append(("迁移模式", demonstrate_migration_pattern()))
    
    # 创建迁移指南
    results.append(("迁移指南", create_migration_guide()))
    
    # 输出结果
    logger.info("=== 迁移演示结果 ===")
    all_success = True
    for module_name, success in results:
        status = "成功" if success else "失败"
        logger.info(f"{module_name}: {status}")
        if not success:
            all_success = False
    
    if all_success:
        logger.info("所有演示成功完成！")
        logger.info("下一步：按照 MIGRATION_GUIDE.md 开始迁移现有代码。")
    else:
        logger.warning("部分演示失败，请检查日志。")
    
    return all_success


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)