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

# 设置aushare接口token
ts.set_token('d74c40bf7bb33a39e27a8e8f47d1d628b09560c652f9caf713dc9db0')
pro = ts.pro_api()


# 配置日志
def setup_logging():
    #todo:
    log_file = f"daily_update_stock_info_{datetime.now().strftime('%Y%m%d')}.log"  
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
                
    def insert_all_stock_shares(self):
        """重新初始化股票股本信息"""
        try:
            
            logger.info("清空股票股本信息...")
            
            # 清空表
            self.cursor.execute("truncate table stock_shares")
            

            
            logger.info("🚀 开始获取ADATA所有股票股本信息...")
            
            self.cursor.execute(f"SELECT stock_code,short_name  FROM stock_info a ORDER BY a.stock_code")
            stocks = self.cursor.fetchall() # type: ignore            

            success_count = 0
            
            for i, (stock_code, stock_name) in enumerate(stocks, 1):
                try:
                    data_source = 'ADATA'
                    df = adata.stock.info.get_stock_shares(stock_code=stock_code)
                    
                    if df.empty:
                        continue
                    
                    
                    # 批量插入
                    insert_sql = """
                    INSERT INTO stock_shares (stock_code, change_date, total_shares, limit_shares, list_a_shares,change_reason, update_time,data_source) 
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """
                    
                                        
                    for _, row in df.iterrows():
                        self.cursor.execute(insert_sql, (
                            stock_code,
                            str(row.get('change_date')) if row.get('change_date') else None,
                            int(row.get('total_shares', 0)) if pd.notna(row.get('total_shares', 0))  else None, 
                            int(row.get('limit_shares', 0)) if pd.notna(row.get('limit_shares', 0))  else None, 
                            float(row.get('list_a_shares', 0.0)) if pd.notna(row.get('list_a_shares', 0.0))  else None, 
                            str(row.get('change_reason', 0)) if row.get('change_reason') else None, 
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
                    error_msg = f"{stock_code} {stock_name} 股本信息: {str(e)}"
                    logger.warning(f"⚠️ {error_msg}")
                    continue
            
            self.update_stats['daily_kline'] = success_count
            logger.info(f"✅ 本周股本信息更新完成: {success_count} 条记录")
            
        except Exception as e:
            error_msg = f"更新本周股本信息失败: {str(e)}"
            logger.error(f"❌ {error_msg}")
            self.update_stats['errors'].append(error_msg)
 
 
    def insert_all_stock_finance(self):
        """重新初始化股票财务指标信息"""
        try:
            
            logger.info("清空股票财务指标信息...")
            
            # 清空表
            self.cursor.execute("truncate table stock_finance")
            

            
            logger.info("🚀 开始获取ADATA所有股票财务指标信息...")
            
            self.cursor.execute(f"SELECT stock_code,short_name  FROM stock_info a ORDER BY a.stock_code")
            stocks = self.cursor.fetchall() # type: ignore            

            success_count = 0
            
            for i, (stock_code, stock_name) in enumerate(stocks, 1):
                try:
                    data_source = 'ADATA'
                    df = adata.stock.info.get_stock_shares(stock_code=stock_code)
                    
                    if df.empty:
                        continue
                    
                    
                    # 批量插入
                    insert_sql = """
                    INSERT INTO stock_finance (stock_code, short_name, report_date, report_type, notice_date, basic_eps, diluted_eps
                                            , non_gaap_eps, net_asset_ps, cap_reserve_ps, undist_profit_ps, oper_cf_ps, total_rev
                                            , gross_profit, net_profit_attr_sh, non_gaap_net_profit, total_rev_yoy_gr, net_profit_yoy_gr
                                            , non_gaap_net_profit_yoy_gr, total_rev_qoq_gr, net_profit_qoq_gr, non_gaap_net_profit_qoq_gr
                                            , roe_wtd, roe_non_gaap_wtd, roa_wtd, gross_margin, net_margin, adv_receipts_to_rev, net_cf_sales_to_rev
                                            , oper_cf_to_rev, eff_tax_rate, curr_ratio, quick_ratio, cash_flow_ratio, asset_liab_ratio, equity_multiplier
                                            , equity_ratio, total_asset_turn_days, inv_turn_days, acct_recv_turn_days, total_asset_turn_rate, inv_turn_rate
                                            , acct_recv_turn_rate, update_time, data_source) 
                    VALUES ( %s, %s, %s, %s, %s
                            , %s, %s, %s, %s,%s
                            , %s, %s, %s, %s, %s
                            , %s, %s, %s, %s, %s
                            , %s, %s, %s, %s, %s
                            , %s, %s, %s, %s, %s
                            , %s, %s, %s, %s, %s
                            , %s, %s, %s, %s, %s
                            , %s, %s, %s, %s, %s)
                    """
                                                            
                    for _, row in df.iterrows():
                        self.cursor.execute(insert_sql, (
                            stock_code,
                            str(row.get('short_name')) if row.get('short_name') else None,
                            str(row.get('report_date')) if row.get('report_date') else None,
                            str(row.get('report_type')) if row.get('report_type') else None,
                            str(row.get('notice_date')) if row.get('notice_date') else None,
                            float(row.get('basic_eps', 0)) if pd.notna(row.get('basic_eps', 0))  else None, 
                            float(row.get('diluted_eps', 0)) if pd.notna(row.get('diluted_eps', 0))  else None, 
                            float(row.get('non_gaap_eps', 0.0)) if pd.notna(row.get('non_gaap_eps', 0.0))  else None, 
                            float(row.get('net_asset_ps', 0)) if pd.notna(row.get('net_asset_ps', 0))  else None, 
                            float(row.get('cap_reserve_ps', 0)) if pd.notna(row.get('cap_reserve_ps', 0))  else None, 
                            float(row.get('undist_profit_ps', 0)) if pd.notna(row.get('undist_profit_ps', 0))  else None, 
                            float(row.get('oper_cf_ps', 0)) if pd.notna(row.get('oper_cf_ps', 0))  else None, 
                            float(row.get('total_rev', 0)) if pd.notna(row.get('total_rev', 0))  else None, 
                            float(row.get('gross_profit', 0)) if pd.notna(row.get('gross_profit', 0))  else None,
                            float(row.get('net_profit_attr_sh', 0)) if pd.notna(row.get('net_profit_attr_sh', 0))  else None, 
                            float(row.get('non_gaap_net_profit', 0)) if pd.notna(row.get('non_gaap_net_profit', 0))  else None, 
                            float(row.get('total_rev_yoy_gr', 0)) if pd.notna(row.get('total_rev_yoy_gr', 0))  else None, 
                            float(row.get('net_profit_yoy_gr', 0)) if pd.notna(row.get('net_profit_yoy_gr', 0))  else None, 
                            float(row.get('non_gaap_net_profit_yoy_gr', 0)) if pd.notna(row.get('non_gaap_net_profit_yoy_gr', 0))  else None,
                            float(row.get('total_rev_qoq_gr', 0)) if pd.notna(row.get('total_rev_qoq_gr', 0))  else None, 
                            float(row.get('net_profit_qoq_gr', 0)) if pd.notna(row.get('net_profit_qoq_gr', 0))  else None, 
                            float(row.get('non_gaap_net_profit_qoq_gr', 0)) if pd.notna(row.get('non_gaap_net_profit_qoq_gr', 0))  else None, 
                            float(row.get('roe_wtd', 0)) if pd.notna(row.get('roe_wtd', 0))  else None, 
                            float(row.get('roe_non_gaap_wtd', 0)) if pd.notna(row.get('roe_non_gaap_wtd', 0))  else None,
                            float(row.get('roa_wtd', 0)) if pd.notna(row.get('roa_wtd', 0))  else None, 
                            float(row.get('gross_margin', 0)) if pd.notna(row.get('gross_margin', 0))  else None, 
                            float(row.get('net_margin', 0)) if pd.notna(row.get('net_margin', 0))  else None, 
                            float(row.get('adv_receipts_to_rev', 0)) if pd.notna(row.get('adv_receipts_to_rev', 0))  else None, 
                            float(row.get('net_cf_sales_to_rev', 0)) if pd.notna(row.get('net_cf_sales_to_rev', 0))  else None,
                            float(row.get('oper_cf_to_rev', 0)) if pd.notna(row.get('oper_cf_to_rev', 0))  else None, 
                            float(row.get('eff_tax_rate', 0)) if pd.notna(row.get('eff_tax_rate', 0))  else None, 
                            float(row.get('curr_ratio', 0)) if pd.notna(row.get('curr_ratio', 0))  else None, 
                            float(row.get('quick_ratio', 0)) if pd.notna(row.get('quick_ratio', 0))  else None, 
                            float(row.get('cash_flow_ratio', 0)) if pd.notna(row.get('cash_flow_ratio', 0))  else None,
                            float(row.get('asset_liab_ratio', 0)) if pd.notna(row.get('asset_liab_ratio', 0))  else None, 
                            float(row.get('equity_multiplier', 0)) if pd.notna(row.get('equity_multiplier', 0))  else None, 
                            float(row.get('equity_ratio', 0)) if pd.notna(row.get('equity_ratio', 0))  else None, 
                            float(row.get('total_asset_turn_days', 0)) if pd.notna(row.get('total_asset_turn_days', 0))  else None, 
                            float(row.get('inv_turn_days', 0)) if pd.notna(row.get('inv_turn_days', 0))  else None,  
                            float(row.get('acct_recv_turn_days', 0)) if pd.notna(row.get('acct_recv_turn_days', 0))  else None,
                            float(row.get('total_asset_turn_rate', 0)) if pd.notna(row.get('total_asset_turn_rate', 0))  else None, 
                            float(row.get('inv_turn_rate', 0)) if pd.notna(row.get('inv_turn_rate', 0))  else None, 
                            float(row.get('acct_recv_turn_rate', 0)) if pd.notna(row.get('acct_recv_turn_rate', 0))  else None, 
                            datetime.now(),
                            data_source
                        ))
                    
                    self.connection.commit() # type: ignore
                    success_count += 1
                    
                    if i % 50 == 0:
                        logger.info(f"📈 已处理 {i}/{len(stocks)} 只股票财务指标信息，成功 {success_count} 只")
                    
                    # 请求延迟
                    time.sleep(self.request_delay)
                    
                except Exception as e:
                    error_msg = f"{stock_code} {stock_name} 股票财务指标信息: {str(e)}"
                    logger.warning(f"⚠️ {error_msg}")
                    continue
            
            self.update_stats['daily_kline'] = success_count
            logger.info(f"✅ 本周股票财务指标信息更新完成: {success_count} 条记录")
            
        except Exception as e:
            error_msg = f"更新本周股票财务指标信息失败: {str(e)}"
            logger.error(f"❌ {error_msg}")
            self.update_stats['errors'].append(error_msg)
            

    def insert_all_stock_industry_sw(self):
        """重新初始化股票申万一二级行业"""
        try:
            
            logger.info("清空股票申万一二级行业...")
            
            # 清空表
            self.cursor.execute("truncate table stock_industry_sw")
            

            
            logger.info("🚀 开始获取ADATA所有股票股本信息...")
            
            self.cursor.execute(f"SELECT stock_code,short_name FROM stock_info a ORDER BY a.stock_code")
            stocks = self.cursor.fetchall() # type: ignore            

            success_count = 0
            
            for i, (stock_code, stock_name) in enumerate(stocks, 1):
                try:
                    data_source = 'ADATA'
                    df = adata.stock.info.get_industry_sw(stock_code=stock_code)
                    
                    if df.empty:
                        continue
                    
                    
                    # 批量插入
                    insert_sql = """
                    INSERT INTO stock_industry_sw (stock_code, sw_code, industry_name, industry_type, source,update_time,data_source) 
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """
                    
                                        
                    for _, row in df.iterrows():
                        self.cursor.execute(insert_sql, (
                            stock_code,
                            str(row.get('sw_code')) if row.get('sw_code') else None,
                            str(row.get('industry_name')) if row.get('industry_name') else None,
                            str(row.get('industry_type')) if row.get('industry_type') else None, 
                            str(row.get('source')) if row.get('source') else None, 
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
                    error_msg = f"{stock_code} {stock_name} 申万一二级行业信息: {str(e)}"
                    logger.warning(f"⚠️ {error_msg}")
                    continue
            
            self.update_stats['daily_kline'] = success_count
            logger.info(f"✅ 本周申万一二级行业信息更新完成: {success_count} 条记录")
            
        except Exception as e:
            error_msg = f"更新本周申万一二级行业信息失败: {str(e)}"
            logger.error(f"❌ {error_msg}")
            self.update_stats['errors'].append(error_msg)
            

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
                    
                    logger.info(f"获取ADATA概念相关股票，开始处理")
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
    
    def update_daily_kline(self):
        """更新近7个自然日K线数据"""
        logger.info(f"📊 开始更新近7个自然日K线数据）...")
        
        try:
            # 获取所有股票  
            self.cursor.execute(f"SELECT concat(a.stock_code,'.',exchange) stock_code,short_name  FROM stock_info a ORDER BY a.stock_code")
            stocks = self.cursor.fetchall() # type: ignore
            # print(stocks)
            logger.info(f"📊 准备更新 {len(stocks)} 只股票的近7个自然日K线数据")
            
            begin_date = (datetime.now() +timedelta(days=-7)).strftime('%Y%m%d')
            end_date =   datetime.now().strftime('%Y%m%d')
            success_count = 0
            
            for i, (stock_code, stock_name) in enumerate(stocks, 1):
                try:
                    data_source = 'AUSHARE'
                    df = pro.daily(
                        ts_code=stock_code, 
                        start_date=begin_date, 
                        end_date=end_date
                    )
                    
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
                        change_amount, change_pct, volume, amount, update_time,data_source) 
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """
                    
                    for _, row in df.iterrows():
                        self.cursor.execute(insert_sql, (
                            stock_code,
                            str(row.get('trade_date')) if row.get('trade_date') else None,
                            float(row.get('open', 0)) if row.get('open') else None, 
                            float(row.get('high', 0)) if row.get('high') else None, 
                            float(row.get('low', 0)) if row.get('low') else None, 
                            float(row.get('close', 0)) if row.get('close') else None, 
                            float(row.get('pre_close', 0)) if row.get('pre_close') else None, 
                            float(row.get('change', 0)) if row.get('change') else None, 
                            float(row.get('pct_chg', 0)) if row.get('pct_chg') else None, 
                            int(row.get('vol', 0)) if row.get('vol') else None, 
                            float(row.get('amount', 0)) if row.get('amount') else None, 
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

    def update_stock_dividend(self):
        """更新近7个自然日股票分红派息数据"""
        logger.info(f"📊 开始更新近7个自然日股票分红派息数据...")
        # 获取所有日历数据 
        begin_date = (datetime.now() +timedelta(days=-7)).strftime('%Y-%m-%d')
        end_date =   datetime.now().strftime('%Y-%m-%d')
        self.cursor.execute("""select date_format(trade_date,'%%Y%%m%%d') trade_date from adata.trade_calendar a where a.trade_date  >= %s  and a.trade_date <= %s """, (begin_date, end_date))
        trade_dates = self.cursor.fetchall() # type: ignore
        try:  
            data_source = 'AKSHARE'
            for i, (trade_date_tup) in enumerate(trade_dates, 1):
                trade_date = trade_date_tup[0]
                df = ak.news_trade_notify_dividend_baidu(
                    date=trade_date   # type: ignore
                )

                # 删除本周旧数据
                self.cursor.execute("""
                    DELETE FROM stock_dividend 
                    WHERE ex_date = %s 
                """, (trade_date))
                
                # 插入本日新数据
                insert_sql = """
                    INSERT INTO stock_dividend 
                    (stock_code, short_name, ex_date, dividend_amount, bonus_share, convert_share, physical_asset, exchange, report_date, update_time, data_source) 
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """
                
                for _, row in df.iterrows():
                    self.cursor.execute(insert_sql, (
                        str(row.get('股票代码')) if row.get('股票代码') else None,
                        str(row.get('股票简称')) if row.get('股票简称') else None,
                        str(row.get('除权日')) if pd.notna(row.get('除权日')) else None, 
                        str(row.get('分红')) if pd.notna(row.get('分红')) else None, 
                        str(row.get('送股')) if pd.notna(row.get('送股')) else None, 
                        str(row.get('转增')) if pd.notna(row.get('转增')) else None, 
                        str(row.get('实物')) if pd.notna(row.get('实物')) else None, 
                        str(row.get('交易所')) if row.get('交易所') else None, 
                        str(row.get('报告期')) if row.get('报告期') else None, 
                        datetime.now(),
                        data_source
                    ))
                 # 请求延迟
                time.sleep(self.request_delay)
                self.connection.commit() # type: ignore            
                logger.info(f"✅ {trade_date}股票分红派息数据更新完成")

            logger.info(f"✅ 本周股票分红派息数据更新完成")

            
        except Exception as e:
            error_msg = f"更新本周股票分红派息数据更新失败: {str(e)}"
            logger.error(f"❌ {error_msg}")
            self.update_stats['errors'].append(error_msg)   
            
            
    def update_trade_calendar(self):
        """更新交易日历"""
        logger.info("📅 开始更新交易日历...")
        
        try:
            current_year = datetime.now().year
            total_inserted = 0
            
            # 更新当年和明年的交易日历
            for year in range(1990, 2026):
                try:
                    df = adata.stock.info.trade_calendar(year=year)
                    if not df.empty:
                        # 删除该年的旧数据
                        self.cursor.execute("""
                            DELETE FROM trade_calendar 
                            WHERE trade_date LIKE %s
                        """, (f"{year}%",))
                        
                        # 插入新数据
                        insert_sql = """
                        INSERT INTO trade_calendar (trade_date, is_trading_day, update_time) 
                        VALUES (%s, %s, %s)
                        """
                        
                        for _, row in df.iterrows():
                            self.cursor.execute(insert_sql, (
                                str(row.get('trade_date')),
                                row.get('trade_status'),
                                datetime.now()
                            ))
                        
                        self.connection.commit()
                        total_inserted += len(df)
                        logger.info(f"✅ {year} 年交易日历: {len(df)} 条记录")
                    
                except Exception as e:
                    logger.warning(f"⚠️ {year} 年交易日历更新失败: {str(e)}")
            
            self.update_stats['trade_calendar'] = total_inserted
            logger.info(f"✅ 交易日历更新完成: {total_inserted} 条记录")
            
        except Exception as e:
            error_msg = f"更新交易日历失败: {str(e)}"
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
            
            # # # # 2. 更新本周K线数据
            # self.update_daily_kline()
            
            # # # 3. 更新股本信息
            # # self.insert_all_stock_shares()
            
            # # 5. 更新同花顺概念信息表
            # self.insert_all_ths_concept_code()
            
            # # 6. 更新同花顺股票概念信息表
            # self.insert_all_ths_stock_concepts()
            
            # # 7. 更新日度资金流量
            # self.update_stock_capital_flow()
            
            # # 8. 更新所有概念指数板块行情数据
            # self.update_ths_concept_market()
            
            # # # 9. 更新所有股票财务指标数据
            # # self.insert_all_stock_finance()
            
            # # 10. 更新最近7个自然日融资融券余额数据
            # self.update_securities_margin()
            
            # # 4. 更新股票申万行业一二级信息
            # # self.insert_all_stock_industry_sw()
            
            # # 3. 更新交易日历
            # # self.update_trade_calendar()
            
            # 11. 更新分红派息数据
            self.update_stock_dividend()
            
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