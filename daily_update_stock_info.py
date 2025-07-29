#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
每日数据更新脚本
自动更新股票实时行情、今日K线、交易日历等关键信息
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
        """初始化每日数据更新器"""
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
        self.request_delay = self.config.getfloat('collection', 'request_delay')
        
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
        
        # 添加今日记录
        history.append(log_data)
        
        # 保留最近30天的记录
        history = history[-30:]
        
        # 保存记录
        with open(log_file, 'w', encoding='utf-8') as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
    
    def update_daily_kline(self, limit_stocks=200):
        """更新今日K线数据"""
        logger.info(f"📊 开始更新今日K线数据（前{limit_stocks}只股票）...")
        
        try:
            # 获取活跃股票（优先选择有成交量的股票）  
            self.cursor.execute("""
                SELECT s.stock_code, s.short_name 
                FROM stock_info s 
                LEFT JOIN stock_market_current c ON s.stock_code = c.stock_code 
                WHERE s.stock_code NOT LIKE %s 
                ORDER BY IFNULL(c.volume, 0) DESC, s.stock_code 
                LIMIT %s
            """, ('%.%', limit_stocks))
            stocks = self.cursor.fetchall()
            
            logger.info(f"📊 准备更新 {len(stocks)} 只股票的今日K线数据")
            
            today = datetime.now().strftime('%Y-%m-%d')
            success_count = 0
            
            for i, (stock_code, stock_name) in enumerate(stocks, 1):
                try:
                    # 获取今日K线数据
                    df = adata.stock.market.get_market(
                        stock_code=stock_code,
                        start_date=today,
                        k_type=1
                    )
                    
                    if df.empty:
                        continue
                    
                    # 删除今日旧数据
                    self.cursor.execute("""
                        DELETE FROM stock_market_daily 
                        WHERE stock_code = %s AND trade_date = %s
                    """, (stock_code, today))
                    
                    # 插入今日新数据
                    insert_sql = """
                    INSERT INTO stock_market_daily 
                    (stock_code, trade_date, trade_time, open, high, low, close, pre_close, 
                     change_amount, change_pct, volume, amount, turnover_ratio, update_time) 
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """
                    
                    for _, row in df.iterrows():
                        self.cursor.execute(insert_sql, (
                            stock_code,
                            str(row.get('trade_date')),
                            str(row.get('trade_time')),
                            float(row.get('open')) if row.get('open') is not None else None,
                            float(row.get('high')) if row.get('high') is not None else None,
                            float(row.get('low')) if row.get('low') is not None else None,
                            float(row.get('close')) if row.get('close') is not None else None,
                            float(row.get('pre_close')) if row.get('pre_close') is not None else None,
                            float(row.get('change')) if row.get('change') is not None else None,
                            float(row.get('change_pct')) if row.get('change_pct') is not None else None,
                            int(row.get('volume')) if row.get('volume') is not None else None,
                            float(row.get('amount')) if row.get('amount') is not None else None,
                            float(row.get('turnover_ratio')) if row.get('turnover_ratio') is not None else None,
                            datetime.now()
                        ))
                    
                    self.connection.commit()
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
            logger.info(f"✅ 今日K线更新完成: {success_count} 条记录")
            
        except Exception as e:
            error_msg = f"更新今日K线失败: {str(e)}"
            logger.error(f"❌ {error_msg}")
            self.update_stats['errors'].append(error_msg)
    

    def run_daily_update(self):
        """执行每日数据更新"""
        logger.info("🚀 开始每日数据更新...")
        logger.info("="*60)
        
        if not self.connect():
            return False
        
        try:
            
            # 1. 更新今日K线数据
            self.update_daily_kline()
            
            # 2. 生成统计报告
            self.show_update_summary()
            
            # 3. 保存更新日志
            self.save_update_log()
            
            logger.info("🎉 每日数据更新完成！")
            return True
            
        except Exception as e:
            error_msg = f"每日更新执行失败: {str(e)}"
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
        logger.info("📊 每日数据更新统计")
        logger.info("="*60)
        logger.info(f"⏱️  更新耗时: {duration}")
        logger.info(f"📈 实时行情: {self.update_stats['current_market']:,} 条")
        logger.info(f"📊 今日K线: {self.update_stats['daily_kline']:,} 条")
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
    logger.info("🌟 每日数据更新程序启动")
    
    updater = DailyDataUpdater()
    success = updater.run_daily_update()
    
    if success:
        logger.info("✅ 每日数据更新成功完成")
        sys.exit(0)
    else:
        logger.error("❌ 每日数据更新失败")
        sys.exit(1)

if __name__ == "__main__":
    main() 