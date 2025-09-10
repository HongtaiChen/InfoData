#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
每周数据更新脚本
自动更新股票实时行情、本周K线、交易日历等关键信息
"""

import adata
import pymysql
import logging
import sys
import json
import os
from datetime import datetime, timedelta
import time
import configparser
import traceback
import tushare as ts
import pandas as pd
import akshare as ak
import requests


# 设置aushare接口token
ts.set_token('d74c40bf7bb33a39e27a8e8f47d1d628b09560c652f9caf713dc9db0')
pro = ts.pro_api()


# 配置日志
def setup_logging():
    #todo:
    log_file = f"D:\\Project\\ADATA\\adata\\log\\daily_update_stock_info_{datetime.now().strftime('%Y%m%d')}.log"  
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(log_file, encoding='utf-8'),
            logging.StreamHandler(sys.stdout)
        ]
    )
    return logging.getLogger(__name__)

logger = setup_logging()

class DailyDataUpdater:
    def __init__(self, config_file='daily_update_stock_info_config.ini'):
        """初始化每周数据更新器"""
        self.config = configparser.ConfigParser()
        self.config.read(config_file, encoding='utf-8')
        
        # 数据库配置
        self.db_config = {
            'host': self.config.get('database', 'host'),
            'port': self.config.getint('database', 'port'),
            'user': self.config.get('database', 'user'),
            'password': self.config.get('database', 'password'),
            'database': self.config.get('database', 'database'),
            'charset': self.config.get('database', 'charset')
        }
        
        # 更新配置
        self.batch_size = self.config.getint('collection', 'batch_size')
        self.request_delay = self.config.getfloat('daily_update','request_delay')
        
        self.connection = None
        self.cursor = None
        self.update_stats = {
            'start_time': datetime.now(),
            'stock_info': 0,
            'current_market': 0,
            'daily_kline': 0,
            'trade_calendar': 0,
            'concept_info': 0,
            'errors': []
        }
        
    def connect(self):
        """连接数据库"""
        try:
            self.connection = pymysql.connect(**self.db_config)
            self.cursor = self.connection.cursor()
            logger.info("✅ 成功连接到MySQL数据库")
            return True
        except Exception as e:
            logger.error(f"❌ 连接数据库失败: {str(e)}")
            return False
    
    def close(self):
        """关闭数据库连接"""
        if self.cursor:
            self.cursor.close()
        if self.connection:
            self.connection.close()
        logger.info("数据库连接已关闭")
    
    def save_update_log(self):
        """保存更新日志"""
        log_data = {
            'date': datetime.now().strftime('%Y-%m-%d'),
            'update_time': self.update_stats['start_time'].isoformat(),
            'duration': str(datetime.now() - self.update_stats['start_time']),
            'stats': {k: v for k, v in self.update_stats.items() if k != 'start_time'},
            'success': len(self.update_stats['errors']) == 0
        }
        
        log_file = 'daily_update_stock_info_history.json'
        history = []
        
        # 加载历史记录
        if os.path.exists(log_file):
            try:
                with open(log_file, 'r', encoding='utf-8') as f:
                    history = json.load(f)
            except:
                history = []
        
        # 添加本周记录
        history.append(log_data)
        
        # 保留最近30天的记录
        history = history[-30:]
        
        # 保存记录
        with open(log_file, 'w', encoding='utf-8') as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
            
            
    def insert_all_stock_info(self):
        """重新初始化股票基本信息"""
        try:
            
            logger.info("清空股票基本信息...")
            
            # 清空表
            self.cursor.execute("truncate table stock_info")
            
            # 批量插入
            insert_sql = """
            INSERT INTO stock_info (stock_code, short_name, exchange, list_date,data_source, update_time) 
            VALUES (%s, %s, %s, %s, %s, %s)
            """
            
            logger.info("🚀 开始获取ADATA所有股票基本信息...")
            
            # 获取ADATA数据
            df = adata.stock.info.all_code()
            logger.info(f"📊 获取到 {len(df)} 只股票信息")
            
            batch_data = []
            insert_count = 0
            
            for _, row in df.iterrows():
                try:
                    # 处理日期
                    list_date = None
                    if row['list_date'] and str(row['list_date']) != 'nan':
                        try:
                            list_date = str(row['list_date'])
                        except:
                            list_date = None
                    
                    batch_data.append((
                        str(row['stock_code']),
                        str(row['short_name']),
                        str(row['exchange']),
                        list_date,
                        'ADTA',
                        datetime.now()
                    ))
                    
                    # 批量插入
                    if len(batch_data) >= self.batch_size:
                        self.cursor.executemany(insert_sql, batch_data)
                        self.connection.commit()
                        insert_count += len(batch_data)
                        logger.info(f"📈 已插入 {insert_count} 只股票信息")
                        batch_data = []
                        
                except Exception as e:
                    logger.warning(f"处理股票 {row['stock_code']} 失败: {str(e)}")
                    continue
            
            # 插入剩余数据
            if batch_data:
                self.cursor.executemany(insert_sql, batch_data)
                self.connection.commit()
                insert_count += len(batch_data)
            
            logger.info(f"✅ 成功插入 {insert_count} 只股票基本信息")
            
        except Exception as e:
            logger.error(f"✗ 插入股票基本信息失败: {str(e)}")
            if self.connection:
                self.connection.rollback()
                    

    def insert_all_ths_concept_code(self):
        """重新初始化同花顺的概念代码信息"""
        try:
            
            logger.info("清空同花顺的概念代码信息...")
            
            # 清空表
            self.cursor.execute("truncate table ths_concept_info")
            
             # 批量插入
            insert_sql = """
            INSERT INTO ths_concept_info (index_code, concept_code, concept_name, source, update_time, data_source) 
            VALUES (%s, %s, %s, %s, %s, %s)
            """

            
            logger.info("🚀 开始获取ADATA所有同花顺的概念代码信息...")
            
            df = adata.stock.info.all_concept_code_ths()
            logger.info(f"📊 获取到 {len(df)} 只概念代码信息")
            
            batch_data = []
            insert_count = 0
            
            for _, row in df.iterrows():
                try:
                    # 处理日期
                    
                    batch_data.append((
                        str(row['index_code']),
                        str(row['concept_code']),
                        str(row['name']),                         
                        str(row['source']),
                        datetime.now(),
                        'ADTA'
                    ))
                    
                    # 批量插入
                    if len(batch_data) >= self.batch_size:
                        self.cursor.executemany(insert_sql, batch_data)
                        self.connection.commit()
                        insert_count += len(batch_data)
                        logger.info(f"📈 已插入 {insert_count} 只概念代码信息")
                        batch_data = []
                        
                except Exception as e:
                    logger.warning(f"处理只概念代码信息{row['name']} 失败: {str(e)}")
                    continue
            
            # 插入剩余数据
            if batch_data:
                self.cursor.executemany(insert_sql, batch_data)
                self.connection.commit()
                insert_count += len(batch_data)
            
            logger.info(f"✅ 成功插入 {insert_count} 只概念代码信息")
            
        except Exception as e:
            logger.error(f"✗ 插入概念代码信息失败: {str(e)}")
            if self.connection:
                self.connection.rollback()

    def insert_all_ths_stock_concepts(self):
        """重新初始化同花顺股票概念关系表"""
        try:
            
            logger.info("清空同花顺股票概念关系...")
            
            # 清空表
            self.cursor.execute("truncate table ths_stock_concepts")
            

            
            logger.info("🚀 开始获取ADATA同花顺股票概念关系...")
            
            self.cursor.execute(f"SELECT index_code,concept_code, concept_name, source FROM ths_concept_info a ORDER BY a.index_code")
            concepts = self.cursor.fetchall() # type: ignore            

            success_count = 0
            logger.info(f"获取概念代码，开始处理")
            for i, (index_code, concept_code,concept_name,source) in enumerate(concepts, 1):
                try:
                    data_source = 'ADATA'
                    df = adata.stock.info.concept_constituent_ths(index_code = index_code)
                    if df.empty:
                        continue
                    
                    # logger.info(f"获取ADATA概念相关股票，开始处理")
                    # 批量插入
                    insert_sql = """
                    INSERT INTO ths_stock_concepts (stock_code,short_name, index_code, concept_name, source, reason,update_time,data_source) 
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """
                    
                                        
                    for _, row in df.iterrows():
                        self.cursor.execute(insert_sql, (
                            str(row.get('stock_code')) if row.get('stock_code') else None,
                            str(row.get('short_name')) if row.get('short_name') else None,
                            index_code,
                            concept_name,
                            source,                      
                            None,  
                            datetime.now(),
                            data_source
                        ))
                    
                    self.connection.commit() # type: ignore
                    success_count += 1
                    
                    if i % 50 == 0:
                        logger.info(f"📈 已处理 {i}/{len(index_code)} 只概念，成功 {success_count} 只")
                    
                    # 请求延迟
                    time.sleep(self.request_delay)
                    
                except Exception as e:
                    error_msg = f"{index_code} {concept_name}同花顺股票概念关系: {str(e)}"
                    logger.warning(f"⚠️ {error_msg}")
                    continue
            
            self.update_stats['daily_kline'] = success_count
            logger.info(f"✅ 本周同花顺股票概念关系更新完成: {success_count} 条记录")
            
        except Exception as e:
            error_msg = f"更新本周同花顺股票概念关系失败: {str(e)}"
            logger.error(f"❌ {error_msg}")
            self.update_stats['errors'].append(error_msg)                  
    
    def update_future_spot_price(self):
        """更新现货期货价格数据"""
        logger.info(f"📊 开始更新近7个自然日现货期货价格数据）...")
        
        try:
            start_date_str = "20240101"
            end_date_str = "20250101"

            # 转换为datetime对象
            start_date = datetime.strptime(start_date_str, "%Y%m%d")
            end_date = datetime.strptime(end_date_str, "%Y%m%d")

            # 循环获取每一天
            current_date = start_date
            while current_date <= end_date:
                # 输出日期，格式为YYYYMMDD
                # 日期加1天
                current_date += timedelta(days=1)         
                logger.info(f"📊 准备更新{current_date}日现货期货价格数据")
                data_source = 'AKSHARE'
                df = ak.futures_spot_price_previous(current_date.strftime("%Y%m%d"))
                if df.empty:
                        continue
                # 删除本周旧数据
                self.cursor.execute("""
                    DELETE FROM futures_spot_price 
                    WHERE  trade_date = %s 
                """, (current_date.strftime("%Y%m%d")))
                
                # 插入本周新数据
                insert_sql = """
                    INSERT INTO futures_spot_price 
                    (trade_date, good_name, spot_price, main_contract_code, main_contract_price, main_contract_basis, main_contract_change_pct, main_basis_high_180d, main_basis_low_180d, main_basis_avg_180d, update_time, data_source) 
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """
                
                for _, row in df.iterrows():
                    self.cursor.execute(insert_sql, (
                            current_date.strftime("%Y-%m-%d"),
                            str(row.get('商品')) if pd.notna(row.get('商品')) else None,
                            float(row.get('现货价格', 0)) if pd.notna(row.get('现货价格', 0))  else None,
                            str(row.get('主力合约代码')) if pd.notna(row.get('主力合约代码')) else None,
                            float(row.get('主力合约价格', 0)) if pd.notna(row.get('主力合约价格', 0))  else None,
                            float(row.get('主力合约基差', 0)) if pd.notna(row.get('主力合约基差', 0))  else None,
                            float(row.get('主力合约变动百分比', 0)) if pd.notna(row.get('主力合约变动百分比', 0))  else None,
                            float(row.get('180日内主力基差最高', 0)) if pd.notna(row.get('180日内主力基差最高', 0))  else None,
                            float(row.get('180日内主力基差最低', 0)) if pd.notna(row.get('180日内主力基差最低', 0))  else None,
                            float(row.get('180日内主力基差平均', 0)) if pd.notna(row.get('180日内主力基差平均', 0))  else None,
                            datetime.now(),
                            'AKSHARE'
                        ))
                
                self.connection.commit() # type: ignore
                logger.info(f"📈 已处理{current_date}日现货期货价格数据")
            
        except Exception as e:
            eerror_msg = f"更新本周现货期货数据失败: {str(e)}"
            logger.error(f"❌ {eerror_msg}")
    

    def update_daily_kline(self):
        """更新近7个自然日K线数据"""
        logger.info(f"📊 开始更新近7个自然日K线数据）...")
        
        try:
            begin_date =  (datetime.now() +timedelta(days=-7)).strftime('%Y%m%d')
            end_date =  datetime.now().strftime('%Y%m%d')
            begin_date_del =  (datetime.now() +timedelta(days=-7)).strftime('%Y-%m-%d')
            end_date_del =  datetime.now().strftime('%Y-%m-%d')              
            # 获取所有股票  
            self.cursor.execute("with tmp as(SELECT a.stock_code,short_name, b.trade_date  FROM stock_info a left join adata.stock_market_daily b on a.stock_code = b.stock_code and b.trade_date  = '20250905' where b.trade_date is null ) select a.stock_code,a.short_name from tmp a left join stock_market_daily b on a.stock_code = b.stock_code  and b.trade_date  >= '20250822' where b.trade_date is not null group by a.stock_code,a.short_name order by a.stock_code;")
            # self.cursor.execute("SELECT a.stock_code,short_name FROM stock_info a where a.stock_code >= '688377' order by a.stock_code;")

            stocks = self.cursor.fetchall() # type: ignore
            # print(stocks)
            logger.info(f"📊 准备更新 {len(stocks)} 只股票的近7个自然日K线数据")

          
            success_count = 0
            
            for i, (stock_code, stock_name) in enumerate(stocks, 1):
                try:
                    # 请求延迟
                    time.sleep(1)
                    data_source = 'AKSHARE'
                    df = ak.stock_zh_a_hist(symbol=stock_code, period="daily", start_date=begin_date, end_date=end_date, adjust="qfq")
                    
                    if df.empty:
                        continue
                    
                    # 删除本周旧数据
                    self.cursor.execute("""
                        DELETE FROM stock_market_daily 
                        WHERE stock_code = %s AND trade_date >= %s AND trade_date <= %s
                    """, (stock_code, begin_date_del, end_date_del))
                    
                    # 插入本周新数据
                    insert_sql = """
                        INSERT INTO stock_market_daily 
                        (stock_code, trade_date, open, high, low, close, pre_close, 
                        change_amount, change_pct, volume, amount, turnover_ratio, update_time,data_source) 
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """
                    
                    for _, row in df.iterrows():
                        self.cursor.execute(insert_sql, (
                            stock_code,
                            str(row.get('日期')) if row.get('日期') else None,
                            float(row.get('开盘', 0)) if row.get('开盘') else None, 
                            float(row.get('最高', 0)) if row.get('最高') else None, 
                            float(row.get('最低', 0)) if row.get('最低') else None, 
                            float(row.get('收盘', 0)) if row.get('收盘') else None, 
                            None, 
                            float(row.get('涨跌额', 0)) if row.get('涨跌额') else None, 
                            float(row.get('涨跌幅', 0)) if row.get('涨跌幅') else None, 
                            int(row.get('成交量', 0)) if row.get('成交量') else None, 
                            float(row.get('成交额', 0)) if row.get('成交额') else None, 
                            float(row.get('换手率', 0)) if row.get('换手率') else None, 
                            datetime.now(),
                            data_source
                        ))
                    
                    self.connection.commit() # type: ignore
                    success_count += 1
                    logger.info(f"📈 已处理{stock_code} {stock_name} ")
                    if i % 50 == 0:
                        logger.info(f"📈 已处理 {i}/{len(stocks)} 只股票，成功 {success_count} 只")
                    
                    
                    
                except Exception as e:
                    error_msg = f"{stock_code} {stock_name} K线更新失败: {str(e)}"
                    logger.warning(f"⚠️ {error_msg}")
                    continue
            
            self.update_stats['daily_kline'] = success_count
            logger.info(f"✅ 本周K线更新完成: {success_count} 条记录")
            
        except Exception as e:
            error_msg = f"更新本周K线失败: {str(e)}"
            logger.error(f"❌ {error_msg}")
            self.update_stats['errors'].append(error_msg)
            


    def update_daily_kline_ex(self):
        """更新近7个自然日K线数据"""
        logger.info(f"📊 开始更新近7个自然日K线数据）...")
        
        try:
            begin_date =  (datetime.now() +timedelta(days=-7)).strftime('%Y%m%d')
            end_date =  datetime.now().strftime('%Y%m%d')
            begin_date_del =  (datetime.now() +timedelta(days=-7)).strftime('%Y-%m-%d')
            end_date_del =  datetime.now().strftime('%Y-%m-%d')             
            # 获取所有股票  
            #self.cursor.execute("SELECT a.stock_code,short_name  FROM stock_info a left join adata.stock_market_daily_ex b on a.stock_code = b.stock_code  where b.trade_date is null group by a.stock_code,a.short_name order by a.stock_code  ;")
            self.cursor.execute("with tmp as(SELECT a.stock_code,short_name, b.trade_date  FROM stock_info a left join adata.stock_market_daily_ex b on a.stock_code = b.stock_code and b.trade_date  = '20250905' where b.trade_date is null ) select a.stock_code,a.short_name from tmp a left join stock_market_daily_ex b on a.stock_code = b.stock_code  and b.trade_date  >= '20250822' where b.trade_date is not null group by a.stock_code,a.short_name order by a.stock_code;")
            stocks = self.cursor.fetchall() # type: ignore
            # print(stocks)
            logger.info(f"📊 准备更新 {len(stocks)} 只股票的近7个自然日K线数据")

          
            success_count = 0
            
            for i, (stock_code, stock_name) in enumerate(stocks, 1):
                try:
                    # 请求延迟
                    time.sleep(1)
                    data_source = 'AKSHARE'
                    df = ak.stock_zh_a_hist(symbol=stock_code, period="daily", start_date=begin_date, end_date=end_date)
                    
                    if df.empty:
                        continue
                    
                    # 删除本周旧数据
                    self.cursor.execute("""
                        DELETE FROM stock_market_daily_ex 
                        WHERE stock_code = %s AND trade_date >= %s AND trade_date <= %s
                    """, (stock_code, begin_date_del, end_date_del))
                    
                    # 插入本周新数据
                    insert_sql = """
                        INSERT INTO stock_market_daily_ex 
                        (stock_code, trade_date, open, high, low, close, pre_close, 
                        change_amount, change_pct, volume, amount, turnover_ratio, update_time,data_source) 
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """
                    
                    for _, row in df.iterrows():
                        self.cursor.execute(insert_sql, (
                            stock_code,
                            str(row.get('日期')) if row.get('日期') else None,
                            float(row.get('开盘', 0)) if row.get('开盘') else None, 
                            float(row.get('最高', 0)) if row.get('最高') else None, 
                            float(row.get('最低', 0)) if row.get('最低') else None, 
                            float(row.get('收盘', 0)) if row.get('收盘') else None, 
                            None, 
                            float(row.get('涨跌额', 0)) if row.get('涨跌额') else None, 
                            float(row.get('涨跌幅', 0)) if row.get('涨跌幅') else None, 
                            int(row.get('成交量', 0)) if row.get('成交量') else None, 
                            float(row.get('成交额', 0)) if row.get('成交额') else None, 
                            float(row.get('换手率', 0)) if row.get('换手率') else None, 
                            datetime.now(),
                            data_source
                        ))
                    
                    self.connection.commit() # type: ignore
                    success_count += 1
                    logger.info(f"📈 已处理{stock_code} {stock_name} ")
                    if i % 50 == 0:
                        logger.info(f"📈 已处理 {i}/{len(stocks)} 只股票，成功 {success_count} 只")
                    
                    
                    
                except Exception as e:
                    error_msg = f"{stock_code} {stock_name} K线更新失败: {str(e)}"
                    logger.warning(f"⚠️ {error_msg}")
                    continue
            
            self.update_stats['daily_kline'] = success_count
            logger.info(f"✅ 本周K线更新完成: {success_count} 条记录")
            
        except Exception as e:
            error_msg = f"更新本周K线失败: {str(e)}"
            logger.error(f"❌ {error_msg}")
            self.update_stats['errors'].append(error_msg)

    def update_finance_calendar(self):
        """更新过去6个月到未来6个月的财经日历数据"""
        logger.info(f"📊 开始更新财经日历数据（过去6个月至未来6个月）...")
        
        try:
            # 获取当前日期
            today = datetime.now()
            # 计算需要更新的月份范围（过去6个月到未来6个月，共13个月）
            months_to_update = []
            for i in range(-6, 7):  # -6到6包含13个月份（含当前月）
                # 计算目标年月
                target_month = today.month + i
                target_year = today.year
                # 处理月份跨年度的情况
                if target_month > 12:
                    target_year += 1
                    target_month -= 12
                elif target_month < 1:
                    target_year -= 1
                    target_month += 12
                # 格式化为"YYYY-MM"
                year_month = f"{target_year}-{target_month:02d}"
                months_to_update.append(year_month)
            
            # 遍历每个月份更新数据
            for year_month in months_to_update:
                logger.info(f"🔄 开始处理 {year_month} 的财经日历数据")
                data_list = []  # 每个月份单独维护数据列表
                # 删除本周旧数据
                logger.info(f"✅ {year_month} 数据清空")
                self.cursor.execute("""
                    DELETE FROM finance_calendar 
                    WHERE event_date LIKE %s
                """, (f"{year_month}%",))
                # 1. 构造请求
                url = "https://app.jiuyangongshe.com/jystock-app/api/v1/timeline/list"
                headers = {
                    "Host": "app.jiuyangongshe.com",
                    "Origin": "https://www.jiuyangongshe.com",
                    "Referer": "https://www.jiuyangongshe.com/",
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:140.0) Gecko/20100101 Firefox/140.0",
                    "Content-Type": "application/json",
                    "token": "1cc6380a05c652b922b3d85124c85473",  # 注意：过期需更新
                    "platform": "3",
                    "Cookie": "SESSION=NDZkNDU2ODYtODEwYi00ZGZkLWEyY2ItNjgxYzY4ZWMzZDEy",  # 过期需更新
                    "timestamp": str(int(time.time() * 1000))
                }
                payload = {"date": year_month, "grade": "0"}
                
                # 2. 发送请求并解析数据
                try:
                    response = requests.post(
                        url,
                        headers=headers,
                        data=json.dumps(payload),
                        timeout=30
                    )
                    response.raise_for_status()
                    resp_data = response.json()
                    events = resp_data.get("data", [])
                    
                    if not events:
                        logger.info(f"📭 {year_month} 暂无财经日历数据")
                        continue
                    
                    # 3. 解析数据并收集
                    for date_group in events:
                        event_date = date_group.get("date")
                        event_list = date_group.get("list", [])
                        
                        for event in event_list:
                            event_title = event.get("title", "").strip()
                            event_content = event.get("content", "").strip()
                            
                            if not event_date or not event_title:
                                logger.warning(f"🚫 {year_month} 跳过无效数据：日期={event_date}，标题={event_title}")
                                continue
                            
                            single_data = (
                                event_date,
                                event_title,
                                event_content,
                                datetime.now(),
                                "JY"
                            )
                            data_list.append(single_data)
                            # 仅打印较长标题的前30个字符，避免日志过长
                            display_title = event_title[:30] + "..." if len(event_title) > 30 else event_title
                            logger.debug(f"📥 {year_month} 收集事件：{event_date} {display_title}")
                    
                    # 4. 批量插入当前月份数据
                    if data_list:
                        insert_query = """
                            INSERT IGNORE INTO finance_calendar (
                                event_date, title, content, update_time, data_source
                            ) VALUES (%s, %s, %s, %s, %s)
                        """
                        self.cursor.executemany(insert_query, data_list)
                        self.connection.commit()
                        logger.info(f"✅ {year_month} 成功更新 {len(data_list)} 条数据")
                    else:
                        logger.info(f"📭 {year_month} 无有效数据可更新")
                
                except requests.exceptions.RequestException as e:
                    logger.error(f"⚠️ {year_month} 请求失败：{str(e)}")
                    if self.connection:
                        self.connection.rollback()
                except Exception as e:
                    logger.error(f"⚠️ {year_month} 处理失败：{str(e)}")
                    if self.connection:
                        self.connection.rollback()
            
            logger.info(f"🏁 所有月份财经日历数据更新完成")
        
        except Exception as e:
            logger.error(f"⚠️ 整体更新流程失败：{str(e)}")
            if self.connection:
                self.connection.rollback()
            
            

    def update_dc_index_market(self):
        """更新截止到最新日期关键指数行情数据"""
        logger.info(f"📊 开始更新截止到最新日期K线指数行情数据）...")
        
        try:

            begin_date =  '19900101'
            end_date = datetime.now().strftime('%Y%m%d')           
            indexs = [ ['000001','上证指数'],['399001','深证成指'],['399006','创业板'],['899050','北证50'],['000688','科创50']
                      ,['000300','沪深300'],['000852','中证1000'],['000016','上证50'],['000905','中证500'],['399330','深证100']
                      ,['000698','科创100'],['399673','创业板50'],['931775','中证全指房地产指数']] # type: ignore
            logger.info(f"📊 准备更新 {len(indexs)} 关键指数的近7个自然日K线数据")
            self.cursor.execute("truncate table dc_index_market;")          
            success_count = 0
            
            for i, (index_code, index_name) in enumerate(indexs, 1):
                try:
                    # 请求延迟
                    time.sleep(self.request_delay)
                    df = ak.index_zh_a_hist(symbol=index_code, period="daily", start_date=begin_date, end_date=end_date)                    
                    if df.empty:
                        continue
                                       
                    # 插入本周新数据
                    insert_sql = """
                        INSERT INTO dc_index_market 
                        (index_code, index_name, trade_date, open, high, low, close, volume, amount,change_amount,change_pct,turnover_ratio, update_time, data_source) 
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """
                    
                    for _, row in df.iterrows():
                        self.cursor.execute(insert_sql, (
                            index_code,
                            index_name,
                            str(row.get('日期')) if pd.notna(row.get('日期')) else None,
                            float(row.get('开盘', 0)) if pd.notna(row.get('开盘', 0))  else None,
                            float(row.get('最高', 0)) if pd.notna(row.get('最高', 0))  else None,
                            float(row.get('最低', 0)) if pd.notna(row.get('最低', 0))  else None,
                            float(row.get('收盘', 0)) if pd.notna(row.get('收盘', 0))  else None,                            
                            float(row.get('成交量', 0)) if pd.notna(row.get('成交量', 0))  else None,
                            float(row.get('成交额', 0)) if pd.notna(row.get('成交额', 0))  else None,
                            float(row.get('涨跌额', 0)) if pd.notna(row.get('涨跌额', 0))  else None,
                            float(row.get('涨跌幅', 0)) if pd.notna(row.get('涨跌幅', 0))  else None,
                            float(row.get('换手率', 0)) if pd.notna(row.get('换手率', 0))  else None,
                            datetime.now(),
                            'AKSHARE'
                        ))
                    
                    self.connection.commit() # type: ignore
                    success_count += 1
                    logger.info(f"📈 已处理{index_code} {index_name} ")
                    if i % 50 == 0:
                        logger.info(f"📈 已处理 {i}/{len(index_code)} 只指数，成功 {success_count} 只")
                    
                    
                    
                except Exception as e:
                    error_msg = f"{index_code} {index_name} K线更新失败: {str(e)}"
                    logger.warning(f"⚠️ {error_msg}")
                    continue
            
            self.update_stats['daily_kline'] = success_count
            logger.info(f"✅ 本周指数K线更新完成: {success_count} 条记录")
            
        except Exception as e:
            error_msg = f"更新本周指数K线失败: {str(e)}"
            logger.error(f"❌ {error_msg}")
            self.update_stats['errors'].append(error_msg)

    def update_daily_kline_adata(self):
            """更新近7个自然日K线数据"""
            logger.info(f"📊 开始更新近7个自然日K线数据）...")
            
            try:
                # 获取所有股票  
                self.cursor.execute(f"SELECT a.stock_code,short_name  FROM stock_info a where a.stock_code ORDER BY a.stock_code;")

                stocks = self.cursor.fetchall() # type: ignore
                # print(stocks)
                logger.info(f"📊 准备更新 {len(stocks)} 只股票的近7个自然日K线数据")

                begin_date =  (datetime.now() +timedelta(days=-7)).strftime('%Y-%m-%d')
                end_date =  datetime.now().strftime('%Y-%m-%d')         
                success_count = 0
                
                for i, (stock_code, stock_name) in enumerate(stocks, 1):
                    try:
                        # 请求延迟
                        time.sleep(0.5)
                        data_source = 'ADATA'
                        df = adata.stock.market.get_market(stock_code=stock_code, start_date=begin_date, end_date=end_date,k_type=1, adjust_type=1)
                        
                        if df.empty:
                            continue
                        
                        # 删除本周旧数据
                        self.cursor.execute("""
                            DELETE FROM stock_market_daily 
                            WHERE stock_code = %s AND trade_date >= %s AND trade_date <= %s
                        """, (stock_code, begin_date, end_date))
                        
                        # 插入本周新数据
                        insert_sql = """
                            INSERT INTO stock_market_daily 
                            (stock_code, trade_date, open, high, low, close, pre_close, 
                            change_amount, change_pct, volume, amount, turnover_ratio, update_time,data_source) 
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """
                        
                        for _, row in df.iterrows():
                            self.cursor.execute(insert_sql, (
                                stock_code,
                                str(row.get('trade_date')) if row.get('trade_date') else None,
                                float(row.get('open', 0)) if row.get('open') else None, 
                                float(row.get('high', 0)) if row.get('high') else None, 
                                float(row.get('low', 0)) if row.get('low') else None, 
                                float(row.get('close', 0)) if row.get('close') else None, 
                                None, 
                                float(row.get('change', 0)) if row.get('change') else None, 
                                float(row.get('change_pct', 0)) if row.get('change_pct') else None, 
                                int(row.get('volume', 0)) / 100 if row.get('volume') else None, 
                                float(row.get('amount', 0)) if row.get('amount') else None, 
                                float(row.get('turnover_ratio', 0)) if row.get('turnover_ratio') else None, 
                                datetime.now(),
                                data_source
                            ))
                        
                        self.connection.commit() # type: ignore
                        success_count += 1
                        logger.info(f"📈 已处理{stock_code} {stock_name} ")
                        if i % 50 == 0:
                            logger.info(f"📈 已处理 {i}/{len(stocks)} 只股票，成功 {success_count} 只")
                        
                        
                        
                    except Exception as e:
                        error_msg = f"{stock_code} {stock_name} K线更新失败: {str(e)}"
                        logger.warning(f"⚠️ {error_msg}")
                        continue
                
                self.update_stats['daily_kline'] = success_count
                logger.info(f"✅ 本周K线更新完成: {success_count} 条记录")
                
            except Exception as e:
                error_msg = f"更新本周K线失败: {str(e)}"
                logger.error(f"❌ {error_msg}")
                self.update_stats['errors'].append(error_msg)

   
    def update_stock_capital_flow(self):
        """更新近7个自然日日度资金流"""
        logger.info(f"📊 开始更新近7个自然日日度资金流）...")
        
        try:
            # 获取所有股票  
            self.cursor.execute(f"SELECT a.stock_code ,short_name  FROM stock_info a ORDER BY a.stock_code")
            stocks = self.cursor.fetchall() # type: ignore
            # print(stocks)
            logger.info(f"📊 准备更新 {len(stocks)} 只股票的近7个自然日日度资金流")
            
            begin_date = (datetime.now() +timedelta(days=-7)).strftime('%Y%m%d')
            end_date =   datetime.now().strftime('%Y%m%d')
            success_count = 0
            
            for i, (stock_code, stock_name) in enumerate(stocks, 1):
                try:
                    data_source = 'ADATA'
                    df = adata.stock.market.get_capital_flow(
                        stock_code= stock_code,
                        start_date = begin_date,
                        end_date = end_date
                    )
                    
                    if df.empty:
                        continue
                    
                    # 删除本周旧数据
                    self.cursor.execute("""
                        DELETE FROM stock_capital_flow 
                        WHERE stock_code = %s AND trade_date >= %s AND trade_date <= %s
                    """, (stock_code, begin_date, end_date))
                    
                    # 插入本周新数据
                    insert_sql = """
                        INSERT INTO stock_capital_flow 
                        (stock_code, short_name, trade_date, main_net_inflow, max_net_inflow, lg_net_inflow, mid_net_inflow, sm_net_inflow, update_time,data_source) 
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """
                    
                    for _, row in df.iterrows():
                        self.cursor.execute(insert_sql, (
                            stock_code,
                            stock_name,
                            str(row.get('trade_date')) if row.get('trade_date') else None,
                            float(row.get('main_net_inflow', 0.0)) if row.get('main_net_inflow') else None, 
                            float(row.get('max_net_inflow', 0)) if row.get('max_net_inflow') else None, 
                            float(row.get('lg_net_inflow', 0)) if row.get('lg_net_inflow') else None, 
                            float(row.get('mid_net_inflow', 0)) if row.get('mid_net_inflow') else None, 
                            float(row.get('sm_net_inflow', 0)) if row.get('sm_net_inflow') else None, 
                            datetime.now(),
                            data_source
                        ))
                    
                    self.connection.commit() # type: ignore
                    success_count += 1
                    
                    if i % 50 == 0:
                        logger.info(f"📈 已处理 {i}/{len(stocks)} 只股票，成功 {success_count} 只")
                    
                    # 请求延迟
                    time.sleep(self.request_delay)
                    
                except Exception as e:
                    error_msg = f"{stock_code} {stock_name} 日度资金流向更新失败: {str(e)}"
                    logger.warning(f"⚠️ {error_msg}")
                    continue
            
            self.update_stats['daily_kline'] = success_count
            logger.info(f"✅ 本周日度资金更新完成: {success_count} 条记录")
            
        except Exception as e:
            error_msg = f"更新本周日度资金失败: {str(e)}"
            logger.error(f"❌ {error_msg}")
            self.update_stats['errors'].append(error_msg)  
              
    def update_ths_concept_market(self):
        """重新初始化概念行情数据"""
        logger.info(f"📊 开始初始化概念行情数据...")
        
        try:
            # 删除所有概念板块行情据 
            self.cursor.execute("TRUNCATE table ths_concept_market")    
            
            # 获取所有概念板块数据 
            self.cursor.execute(f"SELECT index_code,concept_code, concept_name, source FROM ths_concept_info a ORDER BY a.index_code")
            
        
            index_code = self.cursor.fetchall() # type: ignore
            logger.info(f"📊 准备初始化 {len(index_code)} 只概念行情数据")
            # print(index_code)
            success_count = 0
            
            for i, (index_code, concept_code, concept_name, source) in enumerate(index_code, 1):
                try:
                    data_source = 'ADATA'
                    df = adata.stock.market.get_market_concept_ths(
                        index_code= index_code,
                        k_type = 1
                    )
                    if df.empty:
                        continue
                    
                    # 插入本周新数据
                    insert_sql = """
                        INSERT INTO ths_concept_market 
                        (index_code, concept_code, concept_name, trade_date, open, close, high, low, volume, amount,change_amount,change_pct,update_time,data_source) 
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """
                    
                    for _, row in df.iterrows():
                        self.cursor.execute(insert_sql, (
                            index_code,
                            concept_code,
                            concept_name,
                            str(row.get('trade_date')) if row.get('trade_date') else None,
                            float(row.get('open', 0)) if row.get('open') else None, 
                            float(row.get('close', 0)) if row.get('close') else None, 
                            float(row.get('high', 0)) if row.get('high') else None, 
                            float(row.get('low', 0)) if row.get('low') else None, 
                            float(row.get('volume', 0)) if row.get('volume') else None, 
                            float(row.get('amount', 0)) if row.get('amount') else None, 
                            float(row.get('change', 0)) if row.get('change') else None, 
                            float(row.get('change_pct', 0)) if row.get('change_pct') else None, 
                            datetime.now(),
                            data_source
                        ))
                    
                    self.connection.commit() # type: ignore
                    success_count += 1
                    
                    if i % 50 == 0:
                        logger.info(f"📈 已处理 {i}/{len(index_code)} 只概念，成功 {success_count} 只")
                    
                    # 请求延迟
                    time.sleep(self.request_delay)
                    
                except Exception as e:
                    error_msg = f"{index_code} {concept_name} 概念行情更新失败: {str(e)}"
                    logger.warning(f"⚠️ {error_msg}")
                    continue
            
            self.update_stats['daily_kline'] = success_count
            logger.info(f"✅ 本周概念行情更新完成: {success_count} 条记录")
            
        except Exception as e:
            error_msg = f"更新本周概念行情失败: {str(e)}"
            logger.error(f"❌ {error_msg}")
            self.update_stats['errors'].append(error_msg)   
    
    
    def update_securities_margin(self):
        """更新近7个自然日融资融券余额数据"""
        logger.info(f"📊 开始更新近7个自然日融资融券余额数据...")
        begin_date = (datetime.now() +timedelta(days=-7)).strftime('%Y-%m-%d')
        
        try:  
            data_source = 'ADATA'
            df = adata.sentiment.securities_margin(
                start_date= begin_date
            )
            
            # 删除本周旧数据
            self.cursor.execute("""
                DELETE FROM securities_margin 
                WHERE trade_date >= %s 
            """, (begin_date))
            
            # 插入本周新数据
            insert_sql = """
                INSERT INTO securities_margin 
                (trade_date, rzye, rqye, rzrqye, rzrqyecz, update_time, data_source) 
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """
            
            for _, row in df.iterrows():
                self.cursor.execute(insert_sql, (
                    str(row.get('trade_date')) if row.get('trade_date') else None,
                    float(row.get('rzye', 0)) if row.get('rzye') else None, 
                    float(row.get('rqye', 0)) if row.get('rqye') else None, 
                    float(row.get('rzrqye', 0)) if row.get('rzrqye') else None, 
                    float(row.get('rzrqyecz', 0)) if row.get('rzrqyecz') else None, 
                    datetime.now(),
                    data_source
                ))
            
            self.connection.commit() # type: ignore            
            
            logger.info(f"✅ 本周融资融券余额数据更新完成")

            
        except Exception as e:
            error_msg = f"更新本周融资融券余额数据更新失败: {str(e)}"
            logger.error(f"❌ {error_msg}")
            self.update_stats['errors'].append(error_msg)   

    def update_stock_jgdy_detail(self):
        """
        获取指定日期范围内的机构调研数据
        :param start_date: 开始日期，格式为'YYYYMMDD'
        :param end_date: 结束日期，格式为'YYYYMMDD'
        """
        try:
            # 转换日期格式以便迭代
            start_date = (datetime.now() +timedelta(days=-365))     
            day_count = datetime.now() - start_date
            logger.info(f"开始获取从 {start_date} 开始的机构调研数据，共 {day_count} 天")

            # 定义SQL插入语句
            insert_sql = """
            INSERT INTO stock_jgdy_detail 
            (stock_code, stock_name, new, change_pct, received_institution_count,
            received_method, receptionist_name, receptionist_place, receptionist_date,
            announcement_date, data_source)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """
            # 删除本周旧数据
            self.cursor.execute("""
                DELETE FROM stock_jgdy_detail 
                WHERE  receptionist_date >= %s 
            """, (start_date.strftime("%Y-%m-%d")))
            try:
                # 调用akshare接口获取数据
                df = ak.stock_jgdy_tj_em(date=start_date.strftime("%Y%m%d"))
                # 处理每条记录
                for _, row in df.iterrows():
                    try:
                        # 准备参数
                        params = (
                            str(row['代码']).strip() if pd.notna(row['代码']) else None,
                            str(row['名称']).strip() if pd.notna(row['名称']) else None,
                            float(row['最新价']) if pd.notna(row['最新价']) else None,
                            float(row['涨跌幅']) if pd.notna(row['涨跌幅']) else None,
                            int(row['接待机构数量']) if pd.notna(row['接待机构数量']) else None,
                            str(row['接待方式']).strip() if pd.notna(row['接待方式']) else None,
                            str(row['接待人员']).strip() if pd.notna(row['接待人员']) else None,
                            str(row['接待地点']).strip() if pd.notna(row['接待地点']) else None,
                            datetime.strptime(str(row['接待日期']), '%Y-%m-%d').date() if pd.notna(row['接待日期']) else None,
                            datetime.strptime(str(row['公告日期']), '%Y-%m-%d').date() if pd.notna(row['公告日期']) else None,
                            'AKSHARE'
                        )                   
                        # 执行插入
                        self.cursor.execute(insert_sql, params) # type: ignore
                    
                    except Exception as e:
                        logger.warning(f"处理记录 {row.get('代码', '未知代码')} 时出错: {str(e)}")
                        continue
                    self.connection.commit() # type: ignore
                logger.info(f"数据处理完成，共 {len(df)} 条记录")
                            
            except Exception as e:
                logger.error(f"获取数据时出错: {str(e)}", exc_info=True)
            
            logger.info("所有日期的数据处理完成")

        except Exception as e:
            logger.error(f"获取 的数据时出错: {str(e)}")
   

        

          
        
       
    def insert_all_stock_history_dividend(self):
        """重新初始化股票历史分红数据"""
        try:
            
            logger.info("清空股票历史分红数据信息...")
            
            # 清空表
            self.cursor.execute("truncate table stock_history_dividend")                      
            logger.info("🚀 开始获取ADATA所有股票历史分红数据信息...")
            
            df = ak.stock_history_dividend()
            data_source = 'AKSHARE'
            # 插入本周新数据
            insert_sql = """
                INSERT INTO stock_history_dividend 
                (stock_code, short_name, list_date, cumulative_dividends, annual_average_dividend, dividend_cnt, finance_total, finance_cnt, update_time, data_source) 
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """
            
            for _, row in df.iterrows():
                self.cursor.execute(insert_sql, (
                    str(row.get('代码')) if row.get('代码') else None,
                    str(row.get('名称')) if row.get('名称') else None,
                    str(row.get('上市日期')) if row.get('上市日期') else None,
                    float(row.get('累计股息', 0)) if row.get('累计股息') else None, 
                    float(row.get('年均股息', 0)) if row.get('年均股息') else None, 
                    float(row.get('分红次数', 0)) if row.get('分红次数') else None, 
                    float(row.get('融资总额', 0)) if row.get('融资总额') else None, 
                    float(row.get('融资次数', 0)) if row.get('融资次数') else None, 
                    datetime.now(),
                    data_source
                ))
            
            self.connection.commit() # type: ignore            
            
            logger.info(f"✅ 本周股票历史分红数据信息更新完成")

            
        except Exception as e:
            error_msg = f"更新本周股票历史分红数据信息更新失败: {str(e)}"
            logger.error(f"❌ {error_msg}")
            self.update_stats['errors'].append(error_msg)           
            


    def run_daily_update(self):
        """执行每周数据更新"""
        logger.info("🚀 开始每周数据更新...")
        logger.info("="*60)
        
        if not self.connect():
            return False
        
        try:
            
            # # 1. 重新插入所有股票基本信息
            # self.insert_all_stock_info()
            
            
            # 2. 更新本周K线数据
            # self.update_daily_kline()     
            # self.update_daily_kline_ex()
            
            
            # # 3.  更新关键指数数据
            # self.update_dc_index_market()    
            
            # 4. 更新财经日历数据
            # self.update_finance_calendar()  
            
            # # 5. 更新同花顺概念信息表
            # self.insert_all_ths_concept_code()
            
            # # 6. 更新同花顺股票概念信息表
            # self.insert_all_ths_stock_concepts()
            
            # 7. 更新现货期货价格
            # self.update_future_spot_price()
            
            # # 8. 更新所有概念指数板块行情数据
            # self.update_ths_concept_market()   
            
            # # # 9. 更新日度资金流量
            # self.update_stock_capital_flow()
            
            # # # 10. 更新最近7个自然日融资融券余额数据
            # self.update_securities_margin()    
            
            # 更新机构调研详细报告
            self.update_stock_jgdy_detail()       
            
            # 生成统计报告
            self.show_update_summary()
            
            # 保存更新日志
            self.save_update_log()
            
            logger.info("🎉 本周数据更新完成！")
            return True
            
        except Exception as e:
            error_msg = f"本周更新执行失败: {str(e)}"
            logger.error(f"❌ {error_msg}")
            logger.error(f"详细错误: {traceback.format_exc()}")
            self.update_stats['errors'].append(error_msg)
            return False
            
        finally:
            self.close()
    
    def show_update_summary(self):
        """显示更新统计"""
        duration = datetime.now() - self.update_stats['start_time']
        
        logger.info("="*60)
        logger.info("📊 本周数据更新统计")
        logger.info("="*60)
        logger.info(f"⏱️  更新耗时: {duration}")
        logger.info(f"📈 实时行情: {self.update_stats['current_market']:,} 条")
        logger.info(f"📊 本周K线: {self.update_stats['daily_kline']:,} 条")
        logger.info(f"📅 交易日历: {self.update_stats['trade_calendar']:,} 条")
        
        if self.update_stats['errors']:
            logger.info(f"❌ 错误数量: {len(self.update_stats['errors'])}")
            for error in self.update_stats['errors'][:5]:  # 只显示前5个错误
                logger.info(f"   • {error}")
        else:
            logger.info("✅ 无错误")
        
        logger.info("="*60)

def main():
    """主函数"""
    logger.info("🌟 每周数据更新程序启动")
    
    updater = DailyDataUpdater()
    success = updater.run_daily_update()
    
    if success:
        logger.info("✅ 每周数据更新成功完成")
        sys.exit(0)
    else:
        logger.error("❌ 每周数据更新失败")
        sys.exit(1)

if __name__ == "__main__":
    main() 