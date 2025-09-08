#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
每周数据更新脚本
自动更新股票实时行情、本日K线、交易日历等关键信息
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
        
        # 添加本日记录
        history.append(log_data)
        
        # 保留最近30天的记录
        history = history[-30:]
        
        # 保存记录
        with open(log_file, 'w', encoding='utf-8') as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
            
    def insert_stock_market_current(self):
        """更新最新股票价格数据"""
        logger.info(f"📊 开始更新最新股票价格数据）...")
        
        try:
            # 获取所有股票  
            self.cursor.execute("truncate table stock_market_current;")
            df = ak.stock_zh_a_spot_em()

            # 插入本日新数据
            insert_sql = """
                INSERT INTO stock_market_current 
                (stock_code, stock_name, `new`, change_pct, change_amount, volume, amount, amplitude, high, low, `open`, pre_close, volume_ratio, turnover_ratio, dynamic_pe, pb, total_captital, float_captital, rise_speed, `5m_change_pct`, `60d_change_pct`, ytd_change_pct, update_time, data_source) 
                VALUES (%s, %s, %s,  %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """
            
            for _, row in df.iterrows():
                self.cursor.execute(insert_sql, (
                    str(row.get('代码')) if pd.notna(row.get('代码')) else None,
                    str(row.get('名称')) if pd.notna(row.get('名称')) else None,
                    float(row.get('最新价', 0)) if pd.notna(row.get('最新价', 0))  else None,
                    float(row.get('涨跌幅', 0)) if pd.notna(row.get('涨跌幅', 0))  else None,
                    float(row.get('涨跌额', 0)) if pd.notna(row.get('涨跌额', 0))  else None,
                    float(row.get('成交量', 0)) if pd.notna(row.get('成交量', 0))  else None,                            
                    float(row.get('成交额', 0)) if pd.notna(row.get('成交额', 0))  else None,
                    float(row.get('振幅', 0)) if pd.notna(row.get('振幅', 0))  else None,
                    float(row.get('最高', 0)) if pd.notna(row.get('最高', 0))  else None,
                    float(row.get('最低', 0)) if pd.notna(row.get('最低', 0))  else None,
                    float(row.get('今开', 0)) if pd.notna(row.get('今开', 0))  else None,
                    float(row.get('昨收', 0)) if pd.notna(row.get('昨收', 0))  else None,
                    float(row.get('量比', 0)) if pd.notna(row.get('量比', 0))  else None,
                    float(row.get('换手率', 0)) if pd.notna(row.get('换手率', 0))  else None,
                    float(row.get('市盈率-动态', 0)) if pd.notna(row.get('市盈率-动态', 0))  else None,                            
                    float(row.get('市净率', 0)) if pd.notna(row.get('市净率', 0))  else None,
                    float(row.get('总市值', 0)) if pd.notna(row.get('总市值', 0))  else None,
                    float(row.get('流通市值', 0)) if pd.notna(row.get('流通市值', 0))  else None,
                    float(row.get('涨速', 0)) if pd.notna(row.get('涨速', 0))  else None,
                    float(row.get('5分钟涨跌', 0)) if pd.notna(row.get('5分钟涨跌', 0))  else None,
                    float(row.get('60日涨跌幅', 0)) if pd.notna(row.get('60日涨跌幅', 0))  else None,
                    float(row.get('年初至今涨跌幅', 0)) if pd.notna(row.get('年初至今涨跌幅', 0))  else None,
                    datetime.now(),
                    'AKSHARE'
                ))
            
            self.connection.commit() # type: ignore
            logger.info(f"📈 已处理完成 ")
   
            
            
        except Exception as e:
            error_msg = f"实时K线更新失败: {str(e)}"
            logger.warning(f"⚠️ {error_msg}")
  

            


    def run_daily_update(self):
        """执行每周数据更新"""
        logger.info("🚀 开始每周数据更新...")
        logger.info("="*60)
        
        if not self.connect():
            return False
        
        try:
            
            # # 1. 重新插入所有股票基本信息
            self.insert_stock_market_current()
            
            
            # 生成统计报告
            self.show_update_summary()
            
            # 保存更新日志
            self.save_update_log()
            
            logger.info("🎉 本日数据更新完成！")
            return True
            
        except Exception as e:
            error_msg = f"本日更新执行失败: {str(e)}"
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
        logger.info("📊 本日数据更新统计")
        logger.info("="*60)
        logger.info(f"⏱️  更新耗时: {duration}")
        logger.info(f"📈 实时行情: {self.update_stats['current_market']:,} 条")
        logger.info(f"📊 本日K线: {self.update_stats['daily_kline']:,} 条")
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