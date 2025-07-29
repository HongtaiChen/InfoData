#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
创建MySQL数据库和表结构
用于存储AData获取的股票数据
"""

import pymysql
import logging
from datetime import datetime
import sys

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('create_tables.log', encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# 数据库连接配置
DB_CONFIG = {
    'host': 'localhost',
    'port': 3306,
    'user': 'root',
    'password': 'root',
    'charset': 'utf8mb4'
}

DATABASE_NAME = 'adata'

def create_database():
    """创建数据库"""
    try:
        # 连接到MySQL服务器
        connection = pymysql.connect(**DB_CONFIG)
        cursor = connection.cursor()
        
        # 创建数据库
        cursor.execute(f"CREATE DATABASE IF NOT EXISTS {DATABASE_NAME} CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
        logger.info(f"✓ 数据库 {DATABASE_NAME} 创建成功")
        
        cursor.close()
        connection.close()
        return True
        
    except Exception as e:
        logger.error(f"✗ 创建数据库失败: {str(e)}")
        return False

def create_tables():
    """创建所有数据表"""
    try:
        # 连接到adata数据库
        config = DB_CONFIG.copy()
        config['database'] = DATABASE_NAME
        
        connection = pymysql.connect(**config)
        cursor = connection.cursor()
        
        # 1. 创建股票基本信息表
        create_stock_info_table(cursor)
        
        # 2. 创建股票日K线数据表
        create_stock_market_daily_table(cursor)
        
        # 3. 创建股票实时行情表
        create_stock_market_current_table(cursor)
        
        # 4. 创建概念板块信息表
        create_concept_info_table(cursor)
        
        # 5. 创建股票概念关系表
        create_stock_concepts_table(cursor)
        
        # 6. 创建交易日历表
        create_trade_calendar_table(cursor)
        
        # 7. 创建指数信息表
        create_index_info_table(cursor)
        
        # 8. 创建指数历史数据表
        create_index_market_daily_table(cursor)
        
        # 提交事务
        connection.commit()
        
        cursor.close()
        connection.close()
        
        logger.info("✓ 所有数据表创建成功")
        return True
        
    except Exception as e:
        logger.error(f"✗ 创建数据表失败: {str(e)}")
        return False

def create_stock_info_table(cursor):
    """创建股票基本信息表"""
    sql = """
    CREATE TABLE IF NOT EXISTS stock_info (
        id INT AUTO_INCREMENT PRIMARY KEY,
        stock_code VARCHAR(10) NOT NULL COMMENT '股票代码',
        short_name VARCHAR(50) COMMENT '股票简称',
        exchange VARCHAR(5) COMMENT '交易所(SZ/SH/BJ)',
        list_date DATE COMMENT '上市日期',
        update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
        INDEX idx_stock_code (stock_code),
        INDEX idx_exchange (exchange),
        data_source varchar(100) COMMENT '数据来源',
        INDEX idx_update_time (update_time)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='股票基本信息表'
    """
    cursor.execute(sql)
    logger.info("✓ 股票基本信息表(stock_info)创建成功")

def create_stock_market_daily_table(cursor):
    """创建股票日K线数据表"""
    sql = """
    CREATE TABLE IF NOT EXISTS stock_market_daily (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        stock_code VARCHAR(10) NOT NULL COMMENT '股票代码',
        trade_date DATE NOT NULL COMMENT '交易日期',
        open DECIMAL(10,3) COMMENT '开盘价',
        high DECIMAL(10,3) COMMENT '最高价',
        low DECIMAL(10,3) COMMENT '最低价',
        close DECIMAL(10,3) COMMENT '收盘价',
        pre_close DECIMAL(10,3) COMMENT '昨收价',
        change_amount DECIMAL(10,3) COMMENT '涨跌额',
        change_pct DECIMAL(8,4) COMMENT '涨跌幅(%)',
        volume BIGINT COMMENT '成交量(手)',
        amount DECIMAL(15,2) COMMENT '成交额(元)',
        turnover_ratio DECIMAL(8,4) COMMENT '换手率(%)',
        update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
        UNIQUE KEY uk_stock_date (stock_code, trade_date),
        INDEX idx_stock_code (stock_code),
        INDEX idx_trade_date (trade_date),
        data_source varchar(100) COMMENT '数据来源',
        INDEX idx_update_time (update_time)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='股票日K线数据表'
    """
    cursor.execute(sql)
    logger.info("✓ 股票日K线数据表(stock_market_daily)创建成功")

def create_stock_market_current_table(cursor):
    """创建股票实时行情表"""
    sql = """
    CREATE TABLE IF NOT EXISTS stock_market_current (
        id INT AUTO_INCREMENT PRIMARY KEY,
        stock_code VARCHAR(10) NOT NULL COMMENT '股票代码',
        short_name VARCHAR(50) COMMENT '股票简称',
        current_price DECIMAL(10,3) COMMENT '现价',
        change_amount DECIMAL(10,3) COMMENT '涨跌额',
        change_percent DECIMAL(8,4) COMMENT '涨跌幅(%)',
        open DECIMAL(10,3) COMMENT '开盘价',
        high DECIMAL(10,3) COMMENT '最高价',
        low DECIMAL(10,3) COMMENT '最低价',
        pre_close DECIMAL(10,3) COMMENT '昨收价',
        volume BIGINT COMMENT '成交量',
        amount DECIMAL(15,2) COMMENT '成交额',
        turnover_ratio DECIMAL(8,4) COMMENT '换手率(%)',
        pe_ratio DECIMAL(8,2) COMMENT '市盈率',
        pb_ratio DECIMAL(8,2) COMMENT '市净率',
        market_cap DECIMAL(15,2) COMMENT '总市值',
        update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
        UNIQUE KEY uk_stock_code (stock_code),
        INDEX idx_change_percent (change_percent),
        data_source varchar(100) COMMENT '数据来源',
        INDEX idx_update_time (update_time)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='股票实时行情表'
    """
    cursor.execute(sql)
    logger.info("✓ 股票实时行情表(stock_market_current)创建成功")

def create_concept_info_table(cursor):
    """创建概念板块信息表"""
    sql = """
    CREATE TABLE IF NOT EXISTS concept_info (
        id INT AUTO_INCREMENT PRIMARY KEY,
        concept_code VARCHAR(20) NOT NULL COMMENT '概念代码',
        concept_name VARCHAR(100) COMMENT '概念名称',
        source VARCHAR(10) COMMENT '数据源(ths/east)',
        stock_count INT COMMENT '成分股数量',
        update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
        UNIQUE KEY uk_concept_source (concept_code, source),
        INDEX idx_concept_name (concept_name),
        INDEX idx_source (source),
        data_source varchar(100) COMMENT '数据来源',
        INDEX idx_update_time (update_time)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='概念板块信息表'
    """
    cursor.execute(sql)
    logger.info("✓ 概念板块信息表(concept_info)创建成功")

def create_stock_concepts_table(cursor):
    """创建股票概念关系表"""
    sql = """
    CREATE TABLE IF NOT EXISTS stock_concepts (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        stock_code VARCHAR(10) NOT NULL COMMENT '股票代码',
        concept_code VARCHAR(20) NOT NULL COMMENT '概念代码',
        concept_name VARCHAR(100) COMMENT '概念名称',
        source VARCHAR(10) COMMENT '数据源(ths/east)',
        update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
        UNIQUE KEY uk_stock_concept (stock_code, concept_code, source),
        INDEX idx_stock_code (stock_code),
        INDEX idx_concept_code (concept_code),
        INDEX idx_source (source),
        data_source varchar(100) COMMENT '数据来源',
        INDEX idx_update_time (update_time)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='股票概念关系表'
    """
    cursor.execute(sql)
    logger.info("✓ 股票概念关系表(stock_concepts)创建成功")

def create_trade_calendar_table(cursor):
    """创建交易日历表"""
    sql = """
    CREATE TABLE IF NOT EXISTS trade_calendar (
        id INT AUTO_INCREMENT PRIMARY KEY,
        trade_date DATE NOT NULL COMMENT '日期',
        is_trading_day TINYINT(1) DEFAULT 1 COMMENT '是否交易日(1:是, 0:否)',
        year INT COMMENT '年份',
        month INT COMMENT '月份',
        day INT COMMENT '日',
        weekday INT COMMENT '星期几(1-7)',
        update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
        UNIQUE KEY uk_trade_date (trade_date),
        INDEX idx_year (year),
        INDEX idx_month (month),
        INDEX idx_weekday (weekday),
        data_source varchar(100) COMMENT '数据来源',
        INDEX idx_update_time (update_time)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='交易日历表'
    """
    cursor.execute(sql)
    logger.info("✓ 交易日历表(trade_calendar)创建成功")

def create_index_info_table(cursor):
    """创建指数信息表"""
    sql = """
    CREATE TABLE IF NOT EXISTS index_info (
        id INT AUTO_INCREMENT PRIMARY KEY,
        index_code VARCHAR(20) NOT NULL COMMENT '指数代码',
        index_name VARCHAR(100) COMMENT '指数名称',
        index_type VARCHAR(20) COMMENT '指数类型',
        exchange VARCHAR(10) COMMENT '交易所',
        base_date DATE COMMENT '基准日期',
        base_point DECIMAL(10,2) COMMENT '基准点数',
        update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
        UNIQUE KEY uk_index_code (index_code),
        INDEX idx_index_name (index_name),
        INDEX idx_index_type (index_type),
        INDEX idx_exchange (exchange),
        data_source varchar(100) COMMENT '数据来源',
        INDEX idx_update_time (update_time)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='指数信息表'
    """
    cursor.execute(sql)
    logger.info("✓ 指数信息表(index_info)创建成功")

def create_index_market_daily_table(cursor):
    """创建指数日K线数据表"""
    sql = """
    CREATE TABLE IF NOT EXISTS index_market_daily (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        index_code VARCHAR(20) NOT NULL COMMENT '指数代码',
        trade_date DATE NOT NULL COMMENT '交易日期',
        trade_time DATETIME COMMENT '交易时间',
        open DECIMAL(10,2) COMMENT '开盘点数',
        high DECIMAL(10,2) COMMENT '最高点数',
        low DECIMAL(10,2) COMMENT '最低点数',
        close DECIMAL(10,2) COMMENT '收盘点数',
        pre_close DECIMAL(10,2) COMMENT '昨收点数',
        change_amount DECIMAL(10,2) COMMENT '涨跌点数',
        change_pct DECIMAL(8,4) COMMENT '涨跌幅(%)',
        volume BIGINT COMMENT '成交量',
        amount DECIMAL(15,2) COMMENT '成交额',
        update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
        UNIQUE KEY uk_index_date (index_code, trade_date),
        INDEX idx_index_code (index_code),
        INDEX idx_trade_date (trade_date),
        data_source varchar(100) COMMENT '数据来源',
        INDEX idx_update_time (update_time)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='指数日K线数据表'
    """
    cursor.execute(sql)
    logger.info("✓ 指数日K线数据表(index_market_daily)创建成功")

def show_tables():
    """显示创建的表"""
    try:
        config = DB_CONFIG.copy()
        config['database'] = DATABASE_NAME
        
        connection = pymysql.connect(**config)
        cursor = connection.cursor()
        
        cursor.execute("SHOW TABLES")
        tables = cursor.fetchall()
        
        logger.info("=== 数据库中的表 ===")
        for table in tables:
            cursor.execute(f"SELECT COUNT(*) FROM {table[0]}")
            result = cursor.fetchone()
            count = result[0] if result else 0
            logger.info(f"表名: {table[0]}, 记录数: {count}")
        
        cursor.close()
        connection.close()
        
    except Exception as e:
        logger.error(f"✗ 显示表信息失败: {str(e)}")

def main():
    """主函数"""
    logger.info("开始创建AData MySQL数据库和表结构...")
    
    # 1. 创建数据库
    if not create_database():
        logger.error("创建数据库失败，退出程序")
        return
    
    # 2. 创建表
    if not create_tables():
        logger.error("创建表失败，退出程序")
        return
    
    # 3. 显示表信息
    show_tables()
    
    logger.info("🎉 数据库和表结构创建完成！")
    logger.info("您现在可以运行数据采集脚本了。")

if __name__ == '__main__':
    main() 