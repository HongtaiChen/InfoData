#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
每周数据更新脚本
自动更新基金实时行情、本周K线、交易日历等关键信息
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
    log_file = f"daily_update_fund_info_{datetime.now().strftime('%Y%m%d')}.log"  
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
    def __init__(self, config_file='daily_update_fund_info_config.ini'):
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
        
        log_file = 'daily_update_fund_info_history.json'
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
            
                   
    def insert_all_fund_info(self):
        """重新初始化基金基本信息"""
        try:
            
            logger.info("清空基金基本信息...")
            
            # 清空表
            self.cursor.execute("truncate table fund_info")
            
            # 批量插入
            insert_sql = """
            INSERT INTO fund_info (fund_code, fund_name, fund_type, data_source, update_time) 
            VALUES (%s, %s, %s, %s, %s)
            """
            
            logger.info("🚀 开始获取ADATA所有基金基本信息...")
            
            # 获取ADATA数据
            df = ak.fund_name_em()
            logger.info(f"📊 获取到 {len(df)} 只基金信息")
            
            batch_data = []
            insert_count = 0
            
            for _, row in df.iterrows():
                try:
                    
                    batch_data.append((
                        str(row.get('基金代码')) if row.get('基金代码') else None,
                        str(row.get('基金简称')) if row.get('基金简称') else None,
                        str(row.get('基金类型')) if row.get('基金类型') else None,
                        'AKSHARE',
                        datetime.now()
                    ))
                    
                    # 批量插入
                    if len(batch_data) >= self.batch_size:
                        self.cursor.executemany(insert_sql, batch_data)
                        self.connection.commit()
                        insert_count += len(batch_data)
                        logger.info(f"📈 已插入 {insert_count} 只基金信息")
                        batch_data = []
                        
                except Exception as e:
                    logger.warning(f"处理基金 {row['基金代码']} 失败: {str(e)}")
                    continue
            
            # 插入剩余数据
            if batch_data:
                self.cursor.executemany(insert_sql, batch_data)
                self.connection.commit()
                insert_count += len(batch_data)
            
            logger.info(f"✅ 成功插入 {insert_count} 只基金基本信息")
            
        except Exception as e:
            logger.error(f"✗ 插入基金基本信息失败: {str(e)}")
            if self.connection:
                self.connection.rollback()           


    def update_stock_hold_by_fund(self):
        """更新近1个季度基金重仓股票数据"""
        logger.info(f"📊 开始更新近1个季度基金重仓股票数据）...")
        
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


    def run_daily_update(self):
        """执行每周数据更新"""
        logger.info("🚀 开始每周数据更新...")
        logger.info("="*60)
        
        if not self.connect():
            return False
        
        try:
            #1. 初始化基金信息表
            self.insert_all_fund_info()
            
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