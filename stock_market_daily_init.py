#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
完整版AData、AUShare数据采集脚本
获取所有股票数据并保存到MySQL数据库
包含：股票信息、历史行情、实时行情、概念信息、交易日历、指数信息等
"""

import adata
import pymysql
import logging
from datetime import datetime, timedelta
import time
import sys
import json
import os
import threading
import tushare as ts


# 设置aushare接口token
ts.set_token('d74c40bf7bb33a39e27a8e8f47d1d628b09560c652f9caf713dc9db0')
pro = ts.pro_api()


# 条件导入concurrent.futures
try:
    from concurrent.futures import ThreadPoolExecutor, as_completed
    HAS_CONCURRENT = True
except ImportError:
    HAS_CONCURRENT = False
    print("警告: 无法导入concurrent.futures，将使用顺序处理模式")

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('adata_full_insert.log', encoding='utf-8'),
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
    'database': 'adata',
    'charset': 'utf8mb4'
}

# 全局配置
CONFIG = {
    'batch_size': 100,  # 批处理大小
    'request_delay': 0.1,  # 请求延迟(秒)
    'max_workers': 5,  # 最大线程数
    'start_date': '1990-01-01',  # 历史数据起始日期
    'end_date': '2025-07-27', #历史数据结束日期
    'retry_times': 3,  # 重试次数
    'enable_parallel': True,  # 是否启用并行处理
}

class FullADataMySQLInserter:
    def __init__(self):
        self.connection = None
        self.cursor = None
        self.lock = threading.Lock()
        self.processed_count = 0
        self.failed_count = 0
        
    def connect(self):
        """连接数据库"""
        try:
            self.connection = pymysql.connect(**DB_CONFIG)
            self.cursor = self.connection.cursor()
            logger.info("✓ 成功连接到MySQL数据库")
            return True
        except Exception as e:
            logger.error(f"✗ 连接数据库失败: {str(e)}")
            return False
    
    def close(self):
        """关闭数据库连接"""
        if self.cursor:
            self.cursor.close()
        if self.connection:
            self.connection.close()
        logger.info("数据库连接已关闭")
    
    def save_progress(self, stage, data):
        """保存进度到文件"""
        progress_file = f"progress_{stage}.json"
        try:
            with open(progress_file, 'w', encoding='utf-8') as f:
                json.dump({
                    'timestamp': datetime.now().isoformat(),
                    'stage': stage,
                    'data': data
                }, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"保存进度失败: {str(e)}")
    
    def load_progress(self, stage):
        """从文件加载进度"""
        progress_file = f"progress_{stage}.json"
        try:
            if os.path.exists(progress_file):
                with open(progress_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception as e:
            logger.warning(f"加载进度失败: {str(e)}")
        return None
    
    def insert_all_stock_info(self):
        """重新初始化股票基本信息"""
        try:
            
            logger.info("清空股票基本信息...")
            
            # 清空表
            self.cursor.execute("DELETE FROM stock_info")
            
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
                    if len(batch_data) >= CONFIG['batch_size']:
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
            self.save_progress('stock_info', {'total': insert_count})
            
        except Exception as e:
            logger.error(f"✗ 插入股票基本信息失败: {str(e)}")
            if self.connection:
                self.connection.rollback()
    
    def insert_current_market_data_all(self):
        """插入所有股票的当前市场数据"""
        try:
            logger.info("🚀 开始获取所有股票的实时行情...")
            
            # 获取所有股票代码
            self.cursor.execute("SELECT stock_code FROM stock_info ORDER BY stock_code")
            all_stock_codes = [row[0] for row in self.cursor.fetchall()]
            logger.info(f"📊 准备获取 {len(all_stock_codes)} 只股票的实时行情")
            
            # 清空表
            self.cursor.execute("DELETE FROM stock_market_current")
            
            # 分批处理
            total_inserted = 0
            batch_size = 100  # 实时行情API一次最多处理的股票数量
            
            for i in range(0, len(all_stock_codes), batch_size):
                batch_codes = all_stock_codes[i:i+batch_size]
                batch_num = i // batch_size + 1
                total_batches = (len(all_stock_codes) + batch_size - 1) // batch_size
                
                try:
                    logger.info(f"📈 正在处理第 {batch_num}/{total_batches} 批，股票数量: {len(batch_codes)}")
                    
                    # 获取实时行情数据
                    df = adata.stock.market.list_market_current(code_list=batch_codes)
                    
                    if not df.empty:
                        insert_count = self._insert_current_market_batch(df)
                        total_inserted += insert_count
                        logger.info(f"✅ 第 {batch_num} 批成功插入 {insert_count} 只股票实时行情")
                    else:
                        logger.warning(f"⚠️ 第 {batch_num} 批未获取到实时行情数据")
                    
                    # 避免请求过于频繁
                    time.sleep(CONFIG['request_delay'])
                    
                except Exception as e:
                    logger.error(f"✗ 第 {batch_num} 批处理失败: {str(e)}")
                    continue
            
            logger.info(f"✅ 总共成功插入 {total_inserted} 只股票实时行情")
            self.save_progress('current_market', {'total': total_inserted})
            
        except Exception as e:
            logger.error(f"✗ 插入实时行情数据失败: {str(e)}")
            if self.connection:
                self.connection.rollback()
    
    def _insert_current_market_batch(self, df):
        """插入一批实时行情数据"""
        insert_sql = """
        INSERT INTO stock_market_current 
        (stock_code, short_name, current_price, change_amount, change_percent, 
         open, high, low, pre_close, volume, amount, update_time) 
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        
        batch_data = []
        for _, row in df.iterrows():
            try:
                batch_data.append((
                    str(row.get('stock_code', '')),
                    str(row.get('short_name', '')),
                    float(row.get('current_price', 0)) if row.get('current_price') else None,
                    float(row.get('change_amount', 0)) if row.get('change_amount') else None,
                    float(row.get('change_percent', 0)) if row.get('change_percent') else None,
                    float(row.get('open', 0)) if row.get('open') else None,
                    float(row.get('high', 0)) if row.get('high') else None,
                    float(row.get('low', 0)) if row.get('low') else None,
                    float(row.get('pre_close', 0)) if row.get('pre_close') else None,
                    int(row.get('volume', 0)) if row.get('volume') else None,
                    float(row.get('amount', 0)) if row.get('amount') else None,
                    datetime.now()
                ))
            except Exception as e:
                logger.warning(f"处理股票 {row.get('stock_code', 'unknown')} 实时行情失败: {str(e)}")
                continue
        
        if batch_data:
            self.cursor.executemany(insert_sql, batch_data)
            self.connection.commit()
            return len(batch_data)
        return 0
    
    def insert_market_data_parallel(self, max_stocks=None):
        """并行插入股票历史行情数据"""
        try:
            # 获取还没有行情数据的股票代码
            if max_stocks:
                self.cursor.execute(f"SELECT concat(a.stock_code,'.',exchange)  FROM stock_info a left join adata.stock_market_daily b on concat(a.stock_code,'.',exchange) = b.stock_code where b.stock_code is null ORDER BY a.stock_code LIMIT {max_stocks}")
            else:
                self.cursor.execute("SELECT concat(a.stock_code,'.',exchange)  FROM stock_info a left join adata.stock_market_daily b on concat(a.stock_code,'.',exchange) = b.stock_code where b.stock_code is null ORDER BY a.stock_code")
            
            all_stock_codes = [row[0] for row in self.cursor.fetchall()]
            total_stocks = len(all_stock_codes)
            
            logger.info(f"🚀 开始获取 {total_stocks} 只股票的历史行情数据...")
            logger.info(f"📅 数据起始日期: {CONFIG['start_date']}")
            
            # 清空表
            # self.cursor.execute("DELETE FROM stock_market_daily")
            # self.connection.commit()
            
            # 检查是否有进度文件
            progress = self.load_progress('market_data')
            processed_stocks = []
            if progress:
                processed_stocks = progress.get('data', {}).get('processed_stocks', [])
                logger.info(f"📂 发现进度文件，已处理 {len(processed_stocks)} 只股票")
            
            # 过滤已处理的股票
            remaining_stocks = [code for code in all_stock_codes if code not in processed_stocks]
            logger.info(f"📊 剩余待处理股票: {len(remaining_stocks)} 只")
            
            if CONFIG['enable_parallel'] and HAS_CONCURRENT:
                self._insert_market_data_with_threads(remaining_stocks, processed_stocks)
            else:
                logger.info("使用顺序处理模式")
                self._insert_market_data_sequential(remaining_stocks, processed_stocks)
                
        except Exception as e:
            logger.error(f"✗ 插入历史行情数据失败: {str(e)}")
    
    def _insert_market_data_with_threads(self, stock_codes, processed_stocks):
        """使用线程池并行处理历史行情数据"""
        if not HAS_CONCURRENT:
            logger.error("并发处理模块不可用，切换到顺序处理")
            return self._insert_market_data_sequential(stock_codes, processed_stocks)
        
        with ThreadPoolExecutor(max_workers=CONFIG['max_workers']) as executor:
            futures = []
            
            for stock_code in stock_codes:
                future = executor.submit(self._process_single_stock_market_data, stock_code)
                futures.append((future, stock_code))
            
            for future, stock_code in as_completed([(f, s) for f, s in futures]):
                try:
                    result = future.result(timeout=30)  # 30秒超时
                    if result:
                        with self.lock:
                            self.processed_count += 1
                            processed_stocks.append(stock_code)
                            
                            # 每处理100只股票保存一次进度
                            if self.processed_count % 100 == 0:
                                logger.info(f"📊 已处理 {self.processed_count} 只股票")
                                self.save_progress('market_data', {
                                    'processed_stocks': processed_stocks,
                                    'total_processed': self.processed_count,
                                    'total_failed': self.failed_count
                                })
                    else:
                        with self.lock:
                            self.failed_count += 1
                            
                except Exception as e:
                    logger.error(f"✗ 处理股票 {stock_code} 时发生异常: {str(e)}")
                    with self.lock:
                        self.failed_count += 1
        
        logger.info(f"✅ 历史行情数据处理完成！成功: {self.processed_count}, 失败: {self.failed_count}")
    
    def _insert_market_data_sequential(self, stock_codes, processed_stocks):
        """顺序处理历史行情数据"""
        for i, stock_code in enumerate(stock_codes, 1):
            try:
                logger.info(f"📈 [{i}/{len(stock_codes)}] 正在处理 {stock_code}...")
                
                result = self._process_single_stock_market_data(stock_code)
                if result:
                    self.processed_count += 1
                    processed_stocks.append(stock_code)
                else:
                    self.failed_count += 1
                
                # 每处理50只股票保存一次进度
                if i % 50 == 0:
                    logger.info(f"📊 已处理 {i} 只股票")
                    self.save_progress('market_data', {
                        'processed_stocks': processed_stocks,
                        'total_processed': self.processed_count,
                        'total_failed': self.failed_count
                    })
                
                time.sleep(CONFIG['request_delay'])
                
            except Exception as e:
                logger.error(f"✗ 处理股票 {stock_code} 失败: {str(e)}")
                self.failed_count += 1
                continue
        
        logger.info(f"✅ 历史行情数据处理完成！成功: {self.processed_count}, 失败: {self.failed_count}")
    
    def _process_single_stock_market_data(self, stock_code):
        """处理单只股票的历史行情数据"""
        try:
            # 默认从AUSAHRE获取
            data_source = 'AUSHARE'
            
            # 获取历史行情数据
            
            # 获取AUSHARE历史数据
            df = pro.daily(ts_code=stock_code, 
                start_date=CONFIG['start_date'], 
                end_date=CONFIG['end_date']
            )
            
            df = df.fillna(value='None')
            
            if df.empty:
                logger.warning(f"⚠️ AUSHARE{stock_code} 无历史行情数据")
                return False      
            
            # 创建独立的数据库连接
            connection = pymysql.connect(**DB_CONFIG)
            cursor = connection.cursor()
            
            try:
                insert_sql = """
                INSERT INTO stock_market_daily 
                (stock_code, trade_date, open, high, low, close, pre_close, 
                 change_amount, change_pct, volume, amount, update_time,data_source) 
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """
                
                batch_data = []
                for _, row in df.iterrows():
                    try:
                        # 处理日期
                        trade_date = str(row.get('trade_date')) if row.get('trade_date') else None
                        
                        batch_data.append((
                            str(row.get('ts_code', stock_code)),
                            trade_date,
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
                    except Exception as e:
                        logger.warning(f"处理 {stock_code} 某条数据失败: {str(e)}")
                        continue
                
                if batch_data:
                    cursor.executemany(insert_sql, batch_data)
                    connection.commit()
                    logger.info(f"✅ {stock_code} 成功插入 {len(batch_data)} 条历史数据")
                    return True
                
            finally:
                cursor.close()
                connection.close()
                
        except Exception as e:
            logger.error(f"✗ 获取 {stock_code} 历史行情失败: {str(e)}")
            return False
        
        return False
    
    def insert_concept_data(self):
        """插入概念信息数据"""
        try:
            logger.info("🚀 开始获取概念信息...")
            
            # 清空表
            self.cursor.execute("DELETE FROM concept_info")
            
            total_inserted = 0
            
            # 获取同花顺概念信息
            try:
                logger.info("📊 获取同花顺概念信息...")
                df_ths = adata.stock.info.all_concept_code_ths()
                
                insert_sql = """
                INSERT INTO concept_info (concept_code, concept_name, source, update_time) 
                VALUES (%s, %s, %s, %s)
                """
                
                batch_data = []
                for _, row in df_ths.iterrows():
                    batch_data.append((
                        str(row.get('concept_code', '')),
                        str(row.get('concept_name', '')),
                        'ths',
                        datetime.now()
                    ))
                
                if batch_data:
                    self.cursor.executemany(insert_sql, batch_data)
                    self.connection.commit()
                    total_inserted += len(batch_data)
                    logger.info(f"✅ 成功插入 {len(batch_data)} 条同花顺概念信息")
                
            except Exception as e:
                logger.error(f"✗ 获取同花顺概念信息失败: {str(e)}")
            
            # 获取东方财富概念信息
            try:
                logger.info("📊 获取东方财富概念信息...")
                df_east = adata.stock.info.all_concept_code_east()
                
                batch_data = []
                for _, row in df_east.iterrows():
                    batch_data.append((
                        str(row.get('concept_code', '')),
                        str(row.get('concept_name', '')),
                        'east',
                        datetime.now()
                    ))
                
                if batch_data:
                    self.cursor.executemany(insert_sql, batch_data)
                    self.connection.commit()
                    total_inserted += len(batch_data)
                    logger.info(f"✅ 成功插入 {len(batch_data)} 条东方财富概念信息")
                
            except Exception as e:
                logger.error(f"✗ 获取东方财富概念信息失败: {str(e)}")
            
            logger.info(f"✅ 概念信息插入完成，总计: {total_inserted} 条")
            self.save_progress('concept_data', {'total': total_inserted})
            
        except Exception as e:
            logger.error(f"✗ 插入概念信息失败: {str(e)}")
            if self.connection:
                self.connection.rollback()
    
    def insert_trade_calendar(self):
        """插入交易日历数据"""
        try:
            logger.info("🚀 开始获取交易日历...")
            
            # 清空表
            self.cursor.execute("DELETE FROM trade_calendar")
            
            # 获取多年的交易日历
            all_calendar = []
            for year in range(2020, 2026):
                try:
                    logger.info(f"📅 获取 {year} 年交易日历...")
                    df = adata.stock.info.trade_calendar(year=year)
                    if not df.empty:
                        all_calendar.append(df)
                        logger.info(f"✅ {year} 年交易日历获取成功，{len(df)} 条记录")
                    time.sleep(CONFIG['request_delay'])
                except Exception as e:
                    logger.error(f"✗ 获取 {year} 年交易日历失败: {str(e)}")
                    continue
            
            if all_calendar:
                # 合并所有数据
                import pandas as pd
                df_all = pd.concat(all_calendar, ignore_index=True)
                
                insert_sql = """
                INSERT INTO trade_calendar (trade_date, is_trading_day, year, month, day, weekday, update_time) 
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """
                
                batch_data = []
                for _, row in df_all.iterrows():
                    try:
                        trade_date = str(row.get('trade_date')) if row.get('trade_date') else None
                        if trade_date:
                            date_obj = datetime.strptime(trade_date, '%Y-%m-%d')
                            batch_data.append((
                                trade_date,
                                1,  # 默认为交易日
                                date_obj.year,
                                date_obj.month,
                                date_obj.day,
                                date_obj.weekday() + 1,  # 1-7
                                datetime.now()
                            ))
                    except Exception as e:
                        logger.warning(f"处理交易日历数据失败: {str(e)}")
                        continue
                
                if batch_data:
                    self.cursor.executemany(insert_sql, batch_data)
                    self.connection.commit()
                    logger.info(f"✅ 成功插入 {len(batch_data)} 条交易日历数据")
                    self.save_progress('trade_calendar', {'total': len(batch_data)})
            
        except Exception as e:
            logger.error(f"✗ 插入交易日历失败: {str(e)}")
            if self.connection:
                self.connection.rollback()
    
    def insert_index_data(self):
        """插入指数信息数据"""
        try:
            logger.info("🚀 开始获取指数信息...")
            
            # 清空表
            self.cursor.execute("DELETE FROM index_info")
            
            # 获取指数信息
            df = adata.stock.info.all_index_code()
            logger.info(f"📊 获取到 {len(df)} 条指数信息")
            
            insert_sql = """
            INSERT INTO index_info (index_code, index_name, update_time) 
            VALUES (%s, %s, %s)
            """
            
            batch_data = []
            for _, row in df.iterrows():
                batch_data.append((
                    str(row.get('index_code', '')),
                    str(row.get('index_name', '')),
                    datetime.now()
                ))
            
            if batch_data:
                self.cursor.executemany(insert_sql, batch_data)
                self.connection.commit()
                logger.info(f"✅ 成功插入 {len(batch_data)} 条指数信息")
                self.save_progress('index_data', {'total': len(batch_data)})
            
        except Exception as e:
            logger.error(f"✗ 插入指数信息失败: {str(e)}")
            if self.connection:
                self.connection.rollback()
    
    def show_final_summary(self):
        """显示最终数据统计"""
        try:
            logger.info("=" * 50)
            logger.info("🎉 全量数据采集完成！最终统计:")
            logger.info("=" * 50)
            
            tables = [
                ('stock_info', '股票基本信息'),
                ('stock_market_daily', '股票历史行情'),
                ('stock_market_current', '股票实时行情'),
                ('concept_info', '概念板块信息'),
                ('stock_concepts', '股票概念关系'),
                ('trade_calendar', '交易日历'),
                ('index_info', '指数信息'),
                ('index_market_daily', '指数历史行情')
            ]
            
            total_records = 0
            for table_name, description in tables:
                self.cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
                count = self.cursor.fetchone()[0]
                total_records += count
                logger.info(f"📊 {description}: {count:,} 条记录")
            
            logger.info(f"📈 总计: {total_records:,} 条记录")
            logger.info("=" * 50)
            
        except Exception as e:
            logger.error(f"✗ 显示统计信息失败: {str(e)}")
    
    def run_full_collection(self, include_history=True, max_history_stocks=None):
        """运行完整的数据采集"""
        logger.info("🚀 开始AData全量数据采集...")
        logger.info(f"⚙️ 配置信息: {CONFIG}")
        
        start_time = datetime.now()
        
        try:
            # 1. 插入股票基本信息
            # logger.info("\n" + "="*50)
            # logger.info("📊 阶段1: 股票基本信息")
            # logger.info("="*50)
            # self.insert_all_stock_info()
            
            # 2. 插入交易日历
            # logger.info("\n" + "="*50)
            # logger.info("📅 阶段2: 交易日历")
            # logger.info("="*50)
            # self.insert_trade_calendar()
            
            # 3. 插入指数信息
            # logger.info("\n" + "="*50)
            # logger.info("📈 阶段3: 指数信息")
            # logger.info("="*50)
            # self.insert_index_data()
            
            # 4. 插入概念信息
            # logger.info("\n" + "="*50)
            # logger.info("🏷️ 阶段4: 概念板块信息")
            # logger.info("="*50)
            # self.insert_concept_data()
            
            # 5. 插入实时行情
            # logger.info("\n" + "="*50)
            # logger.info("💹 阶段5: 实时行情数据")
            # logger.info("="*50)
            # self.insert_current_market_data_all()
            
            # 6. 插入历史行情（可选，耗时较长）
            if include_history:
                logger.info("\n" + "="*50)
                logger.info("📈 阶段6: 历史行情数据（耗时较长）")
                logger.info("="*50)
                self.insert_market_data_parallel(max_history_stocks)
            else:
                logger.info("\n⏭️ 跳过历史行情数据采集")
            
            # 7. 显示最终统计
            self.show_final_summary()
            
            end_time = datetime.now()
            duration = end_time - start_time
            logger.info(f"⏱️ 总耗时: {duration}")
            logger.info("🎊 全量数据采集任务完成！")
            
        except Exception as e:
            logger.error(f"✗ 全量数据采集失败: {str(e)}")

def main():
    """主函数"""
    logger.info("🌟 AData全量数据采集程序启动")
    
    # 询问用户是否包含历史行情数据
    print("\n" + "="*60)
    print("📊 AData全量数据采集配置")
    print("="*60)
    print("注意：历史行情数据量巨大，可能需要数小时甚至更长时间")
    print("建议分阶段采集：")
    print("1. 基础数据（股票信息、实时行情、概念等）- 约10-30分钟")
    print("2. 历史行情数据 - 约几小时到十几小时")
    print("="*60)
    
    include_history = input("是否包含历史行情数据？(y/n，默认n): ").strip().lower()
    include_history = include_history in ['y', 'yes', '是']
    
    max_history_stocks = None
    if include_history:
        limit_input = input("限制历史数据股票数量？(输入数字或回车表示全部): ").strip()
        if limit_input.isdigit():
            max_history_stocks = int(limit_input)
            logger.info(f"⚙️ 限制历史数据采集股票数量: {max_history_stocks}")
    
    inserter = FullADataMySQLInserter()
    
    try:
        # 连接数据库
        if not inserter.connect():
            logger.error("无法连接数据库，退出程序")
            return
        
        # 运行全量采集
        inserter.run_full_collection(include_history, max_history_stocks)
        
    finally:
        # 关闭连接
        inserter.close()

if __name__ == '__main__':
    main() 