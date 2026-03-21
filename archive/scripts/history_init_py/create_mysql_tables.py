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
        # create_stock_market_current_table(cursor)
        
        # 4. 创建同花顺概念板块信息表
        create_ths_concept_info_table(cursor)
        
        # 5. 创建同花顺股票概念关系表
        create_ths_stock_concepts_table(cursor)
        
        # 6. 创建交易日历表
        create_trade_calendar_table(cursor)
        
        # 7. 创建同花顺指数信息表
        # create_ths_index_info_table(cursor)
        
        # 8. 创建指数历史数据表
        # create_index_market_daily_table(cursor)
        
        # 9. 创建股票股本信息表
        create_stock_shares_table(cursor)
        
        # 10. 创建股票申万行业信息表
        create_stock_industry_sw_table(cursor)
        
        # 11. 创建股票指数关系表
        # create_ths_stock_index_table(cursor)
        
        # 提交事务
        connection.commit()
        
        cursor.close()
        connection.close()
        
        logger.info("✓ 所有数据表创建成功")
        return True
        
    except Exception as e:
        logger.error(f"✗ 创建数据表失败: {str(e)}")
        return False


def create_finance_calendar_table(cursor):
    """创建财经日历数据表"""
    sql = """
    CREATE TABLE IF NOT EXISTS finance_calendar (
        id INT AUTO_INCREMENT PRIMARY KEY,
        event_date DATE NOT NULL COMMENT '日期',
        title VARCHAR(200) COMMENT '事件标题',
        content VARCHAR(400) COMMENT '事件内容',
        update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
        data_source varchar(100) COMMENT '数据来源',
        INDEX idx_event_date (event_date)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='创建财经日历数据表'
    """
    cursor.execute(sql)
    logger.info("✓ 创建财经日历数据表(stock_info)创建成功")

def create_finance_concept_analysis_table(cursor):
    """创建财经事件与概念关联分析结果"""
    sql = """
    CREATE TABLE IF NOT EXISTS finance_concept_analysis (
        `id` int NOT NULL AUTO_INCREMENT,
        event_date DATE NOT NULL COMMENT '日期',
        title VARCHAR(200) COMMENT '事件标题',
        content VARCHAR(400) COMMENT '事件内容',
        `concept_code` varchar(20) NOT NULL COMMENT '概念代码',
        `concept_name` varchar(100) DEFAULT NULL COMMENT '概念名称',
        `relation_type` varchar(10) DEFAULT NULL COMMENT '关联类型(利好/利空)',
        `relation_degree` int DEFAULT NULL COMMENT '关联程度(1-10)',
        `analysis` text COMMENT '分析依据',
        `update_time` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (`id`),
        KEY `idx_event_date` (`event_date`),
        KEY `idx_concept_code` (`concept_code`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='财经事件与概念关联分析结果';
    """
    cursor.execute(sql)
    logger.info("✓ 创建财经事件与概念关联分析结果")

# todo    
def create_finance_news_main_table(cursor):
    """创建财经主要事件信息表"""
    sql = """
    CREATE TABLE IF NOT EXISTS finance_news_main (
        `id` int NOT NULL AUTO_INCREMENT,
        event_date DATE NOT NULL COMMENT '日期',
        summary text COMMENT '新闻概览',
        interval_time timestamp COMMENT '事件内容',
        interval_time timestamp COMMENT '事件内容',
        url varchar(100) COMMENT '事件内容',
        update_time timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        data_source varchar(100) COMMENT '数据来源',
        KEY `idx_event_date` (`event_date`),
        KEY `idx_concept_code` (`concept_code`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='财经主要事件信息表';
    """
    cursor.execute(sql)
    logger.info("✓ 创建财经主要事件信息表")

def create_bond_profit_daily_table(cursor):
    """创建国债收益率数据表"""
    sql = """
    CREATE TABLE IF NOT EXISTS bond_profit_daily (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        trade_date DATE NOT NULL COMMENT '交易日期',
        cn_bond_2y DECIMAL(8,2) COMMENT '中国国债收益率2年',
        cn_bond_5y DECIMAL(8,2) COMMENT '中国国债收益率5年',
        cn_bond_10y DECIMAL(8,2) COMMENT '中国国债收益率10年',
        cn_bond_30y DECIMAL(8,2) COMMENT '中国国债收益率30年',
        cn_bond_10y_2y_spread DECIMAL(8,2) COMMENT '中国国债收益率10年-2年',
        us_bond_2y DECIMAL(8,2) COMMENT '美国国债收益率2年',
        us_bond_5y DECIMAL(8,2) COMMENT '美国国债收益率5年',
        us_bond_10y DECIMAL(8,2) COMMENT '美国国债收益率10年',
        us_bond_30y DECIMAL(8,2) COMMENT '美国国债收益率30年',
        us_bond_10y_2y_spread DECIMAL(8,2) COMMENT '美国国债收益率10年-2年',
        update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
        data_source varchar(100) COMMENT '数据来源',
        INDEX idx_trade_date (trade_date),
        INDEX idx_update_time (update_time)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='国债收益率数据表'
    """
    cursor.execute(sql)
    logger.info("✓ 国债收益率表(bond_profit_daily)创建成功")

def create_futures_spot_price_table(cursor):
    """创建现货期货价格表"""
    sql = """
    CREATE TABLE IF NOT EXISTS futures_spot_price (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        trade_date DATE NOT NULL COMMENT '交易日期',
        good_name varchar(20) COMMENT '商品名称',
        spot_price DECIMAL(8,2) COMMENT '现货价格',
        main_contract_code varchar(20) COMMENT '主力合约代码',
        main_contract_price DECIMAL(8,2) COMMENT '主力合约价格',
        main_contract_basis DECIMAL(8,2) COMMENT '主力合约基差',
        main_contract_change_pct DECIMAL(8,2) COMMENT '主力合约变动百分比',
        main_basis_high_180d DECIMAL(8,2) COMMENT '180日内主力基差最高',
        main_basis_low_180d DECIMAL(8,2) COMMENT '180日内主力基差最低',
        main_basis_avg_180d DECIMAL(8,2) COMMENT '180日内主力基差平均',
        update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
        data_source varchar(100) COMMENT '数据来源',
        INDEX idx_trade_date (trade_date),
        INDEX idx_good_name (good_name),
        INDEX idx_update_time (update_time)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='现货期货价格表'
    """
    cursor.execute(sql)
    logger.info("✓ 现货期货价格表(futures_spot_price)创建成功")


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

def create_stock_info_ex_table(cursor):
    """创建股票扩展信息表"""
    sql = """
CREATE TABLE IF NOT EXISTS stock_info_ex (
        id INT AUTO_INCREMENT PRIMARY KEY,
        stock_code VARCHAR(10) NOT NULL COMMENT '股票代码',
        short_name VARCHAR(50) COMMENT '股票简称',
        type_name VARCHAR(50) COMMENT '股票分类',
        exchange VARCHAR(5) COMMENT '交易所(SZ/SH/BJ)',
        list_date DATE COMMENT '上市日期',
        update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
        INDEX idx_stock_code (stock_code),
        INDEX idx_exchange (exchange),
        data_source varchar(100) COMMENT '数据来源',
        INDEX idx_update_time (update_time)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='股票信息拓展表'
    """
    cursor.execute(sql)
    logger.info("✓ 股票信息拓展表(stock_info_ex)创建成功")

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
        data_source varchar(100) COMMENT '数据来源',
        UNIQUE KEY uk_stock_date (stock_code, trade_date),
        INDEX idx_stock_code (stock_code),
        INDEX idx_trade_date (trade_date),
        INDEX idx_update_time (update_time)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='股票日K线数据表'
    """
    cursor.execute(sql)
    logger.info("✓ 股票日K线数据表(stock_market_daily)创建成功")




def create_stock_market_current_table(cursor):
    """创建股票实时行情表"""
    sql = """
     CREATE TABLE IF NOT EXISTS stock_market_current (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        stock_code VARCHAR(10) NOT NULL COMMENT '股票代码',
        stock_name VARCHAR(200) NOT NULL COMMENT '股票名称',
        new DECIMAL(10,3) COMMENT '最新价',
        change_pct DECIMAL(8,4) COMMENT '涨跌幅(%)',
        change_amount DECIMAL(10,3) COMMENT '涨跌额',
        volume BIGINT COMMENT '成交量(手)',
        amount DECIMAL(15,2) COMMENT '成交额(元)',
        amplitude DECIMAL(8,4) COMMENT '振幅(%)',
        high DECIMAL(10,3) COMMENT '最高价',
        low DECIMAL(10,3) COMMENT '最低价',
        open DECIMAL(10,3) COMMENT '开盘价',
        pre_close DECIMAL(10,3) COMMENT '昨收价',
        volume_ratio DECIMAL(8,4) COMMENT '量比',
        turnover_ratio DECIMAL(8,4) COMMENT '换手率(%)',
        dynamic_pe DECIMAL(24,4) COMMENT '市盈率-动态',
        pb DECIMAL(8,4) COMMENT '市净率',
        total_captital DECIMAL(15,2) COMMENT '总市值(元)',
        float_captital DECIMAL(15,2) COMMENT '流通市值(元)',
        rise_speed DECIMAL(8,4) COMMENT '涨速',
        5m_change_pct DECIMAL(8,4) COMMENT '5分钟涨跌幅(%)',
        60d_change_pct DECIMAL(8,4) COMMENT '60日涨跌幅(%)',
        ytd_change_pct DECIMAL(8,4) COMMENT '年初至今涨跌幅(%)',
        update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
        data_source varchar(100) COMMENT '数据来源',
        INDEX idx_stock_code (stock_code),
        INDEX idx_update_time (update_time)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='股票每日最新数据表'
    """
    cursor.execute(sql)
    logger.info("✓ 股票实时行情表(stock_market_current)创建成功")
    

def create_stock_jgdy_detail_table(cursor):
    """创建机构调研详细表"""
    sql = """
     CREATE TABLE IF NOT EXISTS stock_jgdy_detail (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        stock_code VARCHAR(10) NOT NULL COMMENT '股票代码',
        stock_name VARCHAR(200) NOT NULL COMMENT '股票名称',
        new DECIMAL(10,3) COMMENT '最新价',
        change_pct DECIMAL(8,4) COMMENT '涨跌幅(%)',
        received_institution_count BIGINT COMMENT '接待机构数量',
        received_method text COMMENT'接待方式',
        receptionist_name text COMMENT'接待人员',
        receptionist_place text COMMENT'接待地点',
        receptionist_date DATE COMMENT'接待日期',
        announcement_date DATE COMMENT'公告日期',
        update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
        data_source varchar(100) COMMENT '数据来源',
        INDEX idx_receptionist_date_stock_code (stock_code,receptionist_date),
        INDEX idx_stock_code (stock_code),
        INDEX idx_receptionist_date(receptionist_date),
        INDEX idx_update_time (update_time)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='机构调研详细表'
    """
    cursor.execute(sql)
    logger.info("✓ 机构调研详细表(stock_jgdy_detail)创建成功")
    
def create_stock_capital_flow_table(cursor):
    """创建日度资金流向表"""
    sql = """
    CREATE TABLE IF NOT EXISTS stock_capital_flow (
        id INT AUTO_INCREMENT PRIMARY KEY,
        stock_code VARCHAR(10) NOT NULL COMMENT '股票代码',
        short_name VARCHAR(50) COMMENT '股票简称',
        trade_date DATE NOT NULL COMMENT '交易日期',
        main_net_inflow DECIMAL(24,4) COMMENT '主力资金净流入(元)',
        max_net_inflow DECIMAL(24,4) COMMENT '特大单净流入(元)',
        lg_net_inflow DECIMAL(24,4) COMMENT '大单净流入(元)',
        mid_net_inflow DECIMAL(24,4) COMMENT '中单净流入(元)',
        sm_net_inflow DECIMAL(24,4) COMMENT '小单净流入(元)',
        update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
        data_source varchar(100) COMMENT '数据来源',
        INDEX idx_stock_code (stock_code),
        INDEX idx_trade_date (trade_date),
        INDEX idx_update_time (update_time)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='日度资金流向表'
    """
    cursor.execute(sql)
    logger.info("✓ 日度资金流向表(stock_market_currstock_capital_flowent)创建成功")

def create_stock_finance_table(cursor):
    """创建股票财务核心指标表"""
    sql = """
    CREATE TABLE IF NOT EXISTS stock_finance (
        id INT AUTO_INCREMENT PRIMARY KEY,
        stock_code VARCHAR(10) NOT NULL COMMENT '股票代码',
        short_name VARCHAR(50) COMMENT '股票简称',
        report_date DATE COMMENT '报告日期',
        report_type VARCHAR(10) COMMENT '报告类型',
        notice_date DATE COMMENT '公布日期',
        basic_eps DECIMAL(24,4)  COMMENT '基本每股收益(元)',
        diluted_eps DECIMAL(24,4)  COMMENT '稀释每股收益(元)',
        non_gaap_eps DECIMAL(24,4)  COMMENT '扣非每股收益(元)',
        net_asset_ps DECIMAL(24,4)  COMMENT '每股净资产(元)',
        cap_reserve_ps DECIMAL(24,4)  COMMENT '每股公积金(元)',
        undist_profit_ps DECIMAL(24,4)  COMMENT '每股未分配利润(元)',
        oper_cf_ps DECIMAL(24,4)  COMMENT '每股经营现金流(元)',
        total_rev DECIMAL(24,4)  COMMENT '营业总收入(元)',
        gross_profit DECIMAL(24,4)  COMMENT '毛利润(元)',
        net_profit_attr_sh DECIMAL(24,4)  COMMENT '归属净利润(元)',
        non_gaap_net_profit DECIMAL(24,4)  COMMENT '扣非净利润(元)',
        total_rev_yoy_gr DECIMAL(24,4)  COMMENT '营业总收入同比增长(%)',
        net_profit_yoy_gr DECIMAL(24,4)  COMMENT '归属净利润同比增长(%)',
        non_gaap_net_profit_yoy_gr DECIMAL(24,4)  COMMENT '扣非净利润同比增长(%)',
        total_rev_qoq_gr DECIMAL(24,4)  COMMENT '营业总收入滚动环比增长(%)',
        net_profit_qoq_gr DECIMAL(24,4)  COMMENT '归属净利润滚动环比增长(%)',
        non_gaap_net_profit_qoq_gr DECIMAL(24,4)  COMMENT '扣非净利润滚动环比增长(%)',
        roe_wtd DECIMAL(24,4) COMMENT '净资产收益率(加权)(%)',
        roe_non_gaap_wtd DECIMAL(24,4) COMMENT '净资产收益率(扣非/加权)(%)',
        roa_wtd DECIMAL(24,4) COMMENT '总资产收益率(加权)(%)',
        gross_margin DECIMAL(24,4) COMMENT '毛利率(%)',
        net_margin DECIMAL(24,4) COMMENT '净利率(%)',
        adv_receipts_to_rev DECIMAL(24,4) COMMENT '预收账款/营业总收入',
        net_cf_sales_to_rev	DECIMAL(24,4) COMMENT '销售净现金流/营业总收入',
        oper_cf_to_rev	DECIMAL(24,4) COMMENT '经营净现金流/营业总收入',
        eff_tax_rate DECIMAL(24,4) COMMENT '实际税率(%)',
        curr_ratio DECIMAL(24,4) COMMENT '流动比率',
        quick_ratio DECIMAL(24,4) COMMENT '速动比率',
        cash_flow_ratio DECIMAL(24,4) COMMENT '现金流量比率',
        asset_liab_ratio DECIMAL(24,4) COMMENT '资产负债率(%)',
        equity_multiplier DECIMAL(24,4) COMMENT '权益系数',
        equity_ratio DECIMAL(24,4) COMMENT '产权比率',
        total_asset_turn_days DECIMAL(24,4) COMMENT '总资产周转天数(天)',
        inv_turn_days DECIMAL(24,4) COMMENT '存货周转天数(天)',
        acct_recv_turn_days DECIMAL(24,4) COMMENT '应收账款周转天数(天)',
        total_asset_turn_rate DECIMAL(24,4) COMMENT '总资产周转率(次)',
        inv_turn_rate DECIMAL(24,4) COMMENT '存货周转率(次)',
        acct_recv_turn_rate DECIMAL(24,4) COMMENT '应收账款周转率(次)',
        update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
        data_source varchar(100) COMMENT '数据来源',
        INDEX idx_stock_code (stock_code),
        INDEX idx_exchange (report_date),
        INDEX idx_update_time (update_time)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='股票财务核心指标表'
    """
    cursor.execute(sql)
    logger.info("✓ 股票财务核心指标表(stock_finance)创建成功")


def create_ths_concept_info_table(cursor):
    """创建同花顺概念板块信息表"""
    sql = """
    CREATE TABLE IF NOT EXISTS ths_concept_info (
        id INT AUTO_INCREMENT PRIMARY KEY,
        index_code VARCHAR(20) NOT NULL COMMENT '指数代码',
        concept_code VARCHAR(20) NOT NULL COMMENT '概念代码',
        concept_name VARCHAR(100) COMMENT '概念名称',
        source VARCHAR(10) COMMENT '数据源(ths/east)',
        update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
        data_source varchar(100) COMMENT '数据来源',
        INDEX idx_concept_name (concept_name),
        INDEX idx_source (source),
        INDEX idx_update_time (update_time)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='同花顺概念板块信息表'
    """
    cursor.execute(sql)
    logger.info("✓ 概念板块信息表(concept_info)创建成功")

def create_ths_concept_market_table(cursor):
    """创建同花顺概念指数K线数据表"""
    sql = """
    CREATE TABLE IF NOT EXISTS ths_concept_market (
        id INT AUTO_INCREMENT PRIMARY KEY,
        index_code VARCHAR(20) NOT NULL COMMENT '指数代码',
        concept_code VARCHAR(20) NOT NULL COMMENT '概念代码',
        concept_name VARCHAR(100) COMMENT '概念名称',
        trade_date DATE NOT NULL COMMENT '交易日期',
        open DECIMAL(10,3) COMMENT '开盘价',
        close DECIMAL(10,3) COMMENT '收盘价',
        high DECIMAL(10,3) COMMENT '最高价',
        low DECIMAL(10,3) COMMENT '最低价',
        volume BIGINT COMMENT '成交量(手)',      
        amount DECIMAL(15,2) COMMENT '成交额(元)',
        change_amount DECIMAL(10,3) COMMENT '涨跌额',
        change_pct DECIMAL(8,4) COMMENT '涨跌幅(%)',          
        update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
        data_source varchar(100) COMMENT '数据来源',
        UNIQUE KEY uk_index_date (index_code, trade_date),
        INDEX idx_index_code (index_code),
        INDEX idx_trade_date (trade_date),
        INDEX idx_update_time (update_time)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='同花顺概念指数K线数据表'
    """
    cursor.execute(sql)
    logger.info("✓ 同花顺概念指数K线数据表(ths_index_market)创建成功")


def create_cni_index_info_table(cursor):
    """创建国证指数基本信息表"""
    sql = """
    CREATE TABLE IF NOT EXISTS cni_index_info (
        id INT AUTO_INCREMENT PRIMARY KEY,
        index_code VARCHAR(20) NOT NULL COMMENT '指数代码',
        index_name VARCHAR(100) COMMENT '指数简称',
        sample_number BIGINT COMMENT '样本数',
        close DECIMAL(10,4) COMMENT '收盘点位',
        change_pct DECIMAL(8,4) COMMENT '涨跌幅(%)',    
        pe_roll  DECIMAL(12,6) COMMENT 'PE滚动',  
        volume DECIMAL(15,6) COMMENT '成交量，债券指数成交量单位为亿张，非债券指数成交量单位为万手',      
        amount DECIMAL(15,6) COMMENT '成交额，单位: 亿元',
        total_captital DECIMAL(15,6) COMMENT '总市值，单位: 亿元',      
        free_float_captital DECIMAL(15,6) COMMENT '自由流通市值，单位: 亿元',    
        update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
        data_source varchar(100) COMMENT '数据来源',
        INDEX idx_index_code (index_code),
        INDEX idx_index_name (index_name),
        INDEX idx_update_time (update_time)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='国证指数基本信息表'
    """
    cursor.execute(sql)
    logger.info("✓ 国证指数基本信息表(cni_index_info)创建成功")


def create_cni_index_market_table(cursor):
    """创建国证指数行情信息表"""
    sql = """
    CREATE TABLE IF NOT EXISTS cni_index_market (
        id INT AUTO_INCREMENT PRIMARY KEY,
        index_code VARCHAR(20) NOT NULL COMMENT '指数代码',
        index_name VARCHAR(100) COMMENT '指数简称',
        trade_date DATE NOT NULL COMMENT '交易日期',
        open DECIMAL(10,4) COMMENT '开盘价',
        high DECIMAL(10,4) COMMENT '最高价',
        low DECIMAL(10,4) COMMENT '最低价',
        close DECIMAL(10,4) COMMENT '收盘价',
        volume DECIMAL(15,6) COMMENT '成交量，单位: 万手',      
        amount DECIMAL(15,6) COMMENT '成交额，单位: 亿元',  
        update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
        data_source varchar(100) COMMENT '数据来源',
        INDEX idx_index_code (index_code),
        INDEX idx_index_name (index_name),
        INDEX idx_index_date (trade_date),
        INDEX idx_update_time (update_time)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='国证指数行情信息表'
    """
    cursor.execute(sql)
    logger.info("✓ 国证指数行情信息表(cni_index_market)创建成功")

def create_dc_index_market_table(cursor):
    """创建东财指数行情信息表"""
    sql = """
    CREATE TABLE IF NOT EXISTS dc_index_market (
        id INT AUTO_INCREMENT PRIMARY KEY,
        index_code VARCHAR(20) NOT NULL COMMENT '指数代码',
        index_name VARCHAR(100) COMMENT '指数简称',
        trade_date DATE NOT NULL COMMENT '交易日期',
        open DECIMAL(10,4) COMMENT '开盘价',
        high DECIMAL(10,4) COMMENT '最高价',
        low DECIMAL(10,4) COMMENT '最低价',
        close DECIMAL(10,4) COMMENT '收盘价',
        volume BIGINT COMMENT '成交量，单位: 手',      
        amount BIGINT COMMENT '成交额，单位: 元',
        change_amount DECIMAL(10,3) COMMENT '涨跌额',
        change_pct DECIMAL(8,4) COMMENT '涨跌幅(%)',  
        turnover_ratio DECIMAL(8,4) COMMENT '换手率(%)',
        update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
        data_source varchar(100) COMMENT '数据来源',
        INDEX idx_index_code (index_code),
        INDEX idx_index_name (index_name),
        INDEX idx_index_date (trade_date),
        INDEX idx_update_time (update_time)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='东财指数行情信息表'
    """
    cursor.execute(sql)
    logger.info("✓ 东财指数行情信息表(dc_index_market)创建成功")

def create_ths_stock_concepts_table(cursor):
    """创建同花顺股票概念关系表"""
    sql = """
    CREATE TABLE IF NOT EXISTS ths_stock_concepts (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        stock_code VARCHAR(10) NOT NULL COMMENT '股票代码',
        short_name VARCHAR(100) COMMENT '股票简称',
        concept_code VARCHAR(20)  COMMENT '概念代码',
        concept_name VARCHAR(100) COMMENT '概念名称',
        source VARCHAR(10) COMMENT '数据源(ths/east)',
        reason VARCHAR(1000) COMMENT '概念原因	',
        update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
        data_source varchar(100) COMMENT '数据来源',
        INDEX idx_stock_code (stock_code),
        INDEX idx_concept_code (concept_code),
        INDEX idx_source (source),
        INDEX idx_update_time (update_time)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='同花顺股票概念关系表'
    """
    cursor.execute(sql)
    logger.info("✓ 同花顺股票概念关系表(stock_concepts)创建成功")

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

    
def create_stock_shares_table(cursor):
    """创建股票股本信息表数据表"""
    sql = """
     CREATE TABLE IF NOT EXISTS stock_shares (
        id INT AUTO_INCREMENT PRIMARY KEY,
        stock_code VARCHAR(10) NOT NULL COMMENT '股票代码',
        change_date DATE COMMENT '变动时间',
        total_shares BIGINT COMMENT '总股本：股',
        limit_shares BIGINT COMMENT '限售股本：股',
        list_a_shares DECIMAL(24,2)  COMMENT '流通A股股本：股',
        change_reason VARCHAR(1000) COMMENT '变动原因',
        update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
        data_source varchar(100) COMMENT '数据来源',
        UNIQUE KEY uk_stock_date (stock_code, change_date),
        INDEX idx_stock_code (stock_code),
        INDEX idx_trade_date (change_date),
        INDEX idx_update_time (update_time)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='股票股本信息表'
    """
    cursor.execute(sql)
    logger.info("✓ 指数日K线数据表(index_market_daily)创建成功")   
    
def create_stock_industry_sw_table(cursor):
    """创建股票申万一二级行业信息数据表"""
    sql = """
     CREATE TABLE IF NOT EXISTS stock_industry_sw (
        id INT AUTO_INCREMENT PRIMARY KEY,
        stock_code VARCHAR(10) NOT NULL COMMENT '股票代码',
        sw_code VARCHAR(10) COMMENT '申万行业代码',
        industry_name VARCHAR(100) COMMENT '行业名称',
        industry_type VARCHAR(10) COMMENT '行业类别',
        source VARCHAR(100)  COMMENT '来源',
        update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
        data_source varchar(100) COMMENT '数据来源',
        INDEX idx_stock_code (stock_code),
        INDEX idx_trade_date (industry_name),
        INDEX idx_update_time (update_time)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='股票申万一二级行业信息数据表'
    """
    cursor.execute(sql)
    logger.info("✓ 指数日K线数据表(index_market_daily)创建成功")   

def create_securities_margin_table(cursor):
    """创建融资融券余额数据"""
    sql = """
    CREATE TABLE IF NOT EXISTS securities_margin (
        id INT AUTO_INCREMENT PRIMARY KEY,
        trade_date DATE COMMENT '交易日期',
        rzye DECIMAL(24,2)  COMMENT '融资余额（元）',
        rqye DECIMAL(24,2)  COMMENT '融券余额（元）',
        rzrqye DECIMAL(24,2)  COMMENT '融资融券余额（元）',
        rzrqyecz DECIMAL(24,2)  COMMENT '融资融券余额差值（元）',
        update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
        data_source varchar(100) COMMENT '数据来源',
        INDEX idx_trade_date (trade_date),
        INDEX idx_update_time (update_time)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='融资融券余额数据'
    """
    cursor.execute(sql)
    logger.info("✓ 融资融券余额数据(securities_margin)创建成功")

def create_stock_dividend_table(cursor):
    """创建股票分红派息信息表"""
    sql = """
    CREATE TABLE IF NOT EXISTS ths_stock_dividend (
        id INT AUTO_INCREMENT PRIMARY KEY,
        stock_code VARCHAR(10) NOT NULL COMMENT '股票代码',
        short_name VARCHAR(50) COMMENT '股票简称',
        report_period VARCHAR(20) COMMENT '报告期',
        board_date DATE COMMENT '董事会日期',
        shareholders_meeting_date DATE COMMENT '股东大会预案公告日期',
        implementation_date DATE COMMENT '实施公告日',
        dividend_plan_desc VARCHAR(500) COMMENT '分红方案说明',
        ashare_record_date DATE COMMENT 'A股股权登记日',
        ashare_ex_date DATE COMMENT 'A股除权除息日',
        dividend_amount_total VARCHAR(20) COMMENT 'AH分红总金额',
        plan_progress VARCHAR(20) COMMENT '方案进度',
        dividend_payout_ratio VARCHAR(20) COMMENT '股利支付率',
        pre_tax_dividend_ratio VARCHAR(20) COMMENT '税前分红率',
        update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
        data_source varchar(100) COMMENT '数据来源',
        INDEX idx_stockcode_exdate (stock_code,ashare_ex_date),
        INDEX idx_ashare_ex_date (ashare_ex_date),
        INDEX idx_update_time (update_time)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='股票分红派息信息表';
    """
    cursor.execute(sql)
    logger.info("✓ 股票分红派息信息表(ths_stock_dividend)创建成功")

def create_stock_history_dividend_table(cursor):
    """创建股票历史分红派息信息表"""
    sql = """
    CREATE TABLE IF NOT EXISTS stock_history_dividend (
        id INT AUTO_INCREMENT PRIMARY KEY,
        stock_code VARCHAR(10) NOT NULL COMMENT '股票代码',
        short_name VARCHAR(50) COMMENT '股票简称',
        list_date DATE COMMENT '上市日期',
        cumulative_dividends DECIMAL(10,2) COMMENT '累计股息，单位: %',
        annual_average_dividend DECIMAL(6,2) COMMENT '年均股息，单位: %',
        dividend_cnt BIGINT COMMENT '分红次数',
        finance_total DECIMAL(10,2) COMMENT '融资总额，单位: 亿',
        finance_cnt BIGINT COMMENT '融资次数	',
        update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
        data_source varchar(100) COMMENT '数据来源',
        INDEX idx_stockcode (stock_code),
        INDEX idx_update_time (update_time)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='股票历史分红派息信息表';
    """
    cursor.execute(sql)
    logger.info("✓ 股票历史分红派息信息表(stock_history_dividend)创建成功")

def create_fund_info_table(cursor):
    """创建基金基本信息表"""
    sql = """
    CREATE TABLE IF NOT EXISTS fund_info (
        id INT AUTO_INCREMENT PRIMARY KEY,
        fund_code VARCHAR(10) NOT NULL COMMENT '基金代码',
        fund_name VARCHAR(50) COMMENT '基金简称',
        fund_type VARCHAR(50) COMMENT '基金类型',
        update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
        INDEX idx_fund_code (fund_code),
        data_source varchar(100) COMMENT '数据来源',
        INDEX idx_update_time (update_time)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='基金基本信息表'
    """
    cursor.execute(sql)
    logger.info("✓ 基金基本信息表(fund_info)创建成功")


def create_stock_hold_by_fund_table(cursor):
    """创建基金股票重仓信息表"""
    sql = """
    CREATE TABLE IF NOT EXISTS stock_hold_by_fund (
        id INT AUTO_INCREMENT PRIMARY KEY,
        stock_code VARCHAR(10) NOT NULL COMMENT '股票代码',
        short_name VARCHAR(50) COMMENT '股票简称',
        report_date DATE COMMENT '报告期',
        hldfund_cnt BIGINT COMMENT '基金覆盖家数',
        total_amount BIGINT COMMENT '持股总数',
        total_asset DECIMAL(24,2) COMMENT '持股总市值',
        update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
        data_source varchar(100) COMMENT '数据来源',
        INDEX idx_stockcode (stock_code),
        INDEX idx_update_time (update_time)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='股票基金股票重仓信息表';
    """
    cursor.execute(sql)
    logger.info("✓ 股票基金股票重仓信息表(stock_hold_by_fund)创建成功")


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