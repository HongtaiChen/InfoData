#!/usr/bin/env python3
"""
迁移 insert_all_adata_to_mysql.py 脚本

分析并迁移这个复杂的多线程数据插入脚本到新架构。
"""

import os
import sys
import re
import logging
from datetime import datetime

# 设置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def analyze_insert_all_script():
    """分析 insert_all_adata_to_mysql.py 脚本"""
    script_path = "other/insert_all_adata_to_mysql.py"
    
    logger.info(f"分析脚本: {script_path}")
    
    with open(script_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # 分析内容
    analysis = {
        "file_info": {
            "lines": len(content.splitlines()),
            "size_kb": len(content) / 1024,
        },
        "imports": {
            "akshare": "import akshare as ak" in content,
            "pymysql": "import pymysql" in content,
            "concurrent.futures": "import concurrent.futures" in content,
            "threading": "import threading" in content,
            "queue": "import queue" in content,
            "configparser": "import configparser" in content,
        },
        "functions": {
            "conn_db": "def conn_db()" in content,
            "insert_stock_info": "def insert_stock_info" in content,
            "insert_stock_daily_info": "def insert_stock_daily_info" in content,
            "insert_index_info": "def insert_index_info" in content,
            "insert_index_daily_info": "def insert_index_daily_info" in content,
            "insert_fund_info": "def insert_fund_info" in content,
            "insert_bond_info": "def insert_bond_info" in content,
            "insert_stock_dividend_info": "def insert_stock_dividend_info" in content,
            "insert_institutional_trading_info": "def insert_institutional_trading_info" in content,
            "worker": "def worker" in content,
            "main": "def main()" in content,
        },
        "concurrency": {
            "uses_threadpool": "ThreadPoolExecutor" in content,
            "max_workers": None,
            "uses_queue": "queue.Queue" in content,
            "uses_lock": "threading.Lock" in content,
        },
        "akshare_calls": [],
        "database_operations": [],
        "config_usage": "config.read" in content,
        "complexity": "high",  # 由于多线程和多种数据插入
    }
    
    # 查找AKShare调用
    akshare_pattern = re.compile(r'ak\.([a-zA-Z_][a-zA-Z0-9_]*)')
    akshare_calls = akshare_pattern.findall(content)
    analysis["akshare_calls"] = list(set(akshare_calls))  # 去重
    
    # 查找并发配置
    max_workers_match = re.search(r'max_workers\s*=\s*(\d+)', content)
    if max_workers_match:
        analysis["concurrency"]["max_workers"] = int(max_workers_match.group(1))
    
    # 查找数据库操作模式
    db_patterns = [
        r'cursor\.execute\([^)]*\)',
        r'conn\.commit\(\)',
        r'conn\.close\(\)',
        r'INSERT INTO',
        r'ON DUPLICATE KEY UPDATE',
    ]
    
    for pattern in db_patterns:
        matches = re.findall(pattern, content)
        if matches:
            analysis["database_operations"].extend(matches[:5])  # 只取前5个
    
    logger.info(f"分析完成: {len(analysis['akshare_calls'])} 个AKShare调用, {analysis['concurrency']['max_workers']} 个并发工作线程")
    return analysis


def create_migration_plan(analysis):
    """创建迁移计划"""
    plan = {
        "script_name": "insert_all_adata_to_mysql.py",
        "new_name": "insert_all_data_new.py",
        "migration_strategy": "逐步重构 + 并发优化",
        "estimated_time": "45-60分钟",
        "risks": [
            "并发控制复杂性",
            "数据库连接池管理",
            "错误处理和回滚",
            "性能调优"
        ],
        "phases": [
            {
                "phase": 1,
                "name": "基础架构迁移",
                "tasks": [
                    "替换AKShare导入为数据采集客户端",
                    "替换pymysql导入为数据存储管理器",
                    "移除硬编码配置",
                    "更新日志系统"
                ],
                "estimated_time": "15分钟"
            },
            {
                "phase": 2,
                "name": "数据模型集成",
                "tasks": [
                    "创建数据模型适配器",
                    "实现批量插入优化",
                    "添加数据验证",
                    "统一错误处理"
                ],
                "estimated_time": "20分钟"
            },
            {
                "phase": 3,
                "name": "并发系统重构",
                "tasks": [
                    "优化线程池配置",
                    "实现连接池管理",
                    "添加进度监控",
                    "完善错误恢复"
                ],
                "estimated_time": "25分钟"
            }
        ],
        "key_changes": [
            "并发控制从简单线程池升级为智能任务调度",
            "数据库连接从每线程独立连接改为共享连接池",
            "错误处理从简单日志记录升级为分层恢复机制",
            "性能监控从无到完整的指标收集"
        ]
    }
    
    return plan


def create_migrated_version():
    """创建迁移后的版本"""
    original_file = "other/insert_all_adata_to_mysql.py"
    migrated_file = "insert_all_data_new.py"
    
    logger.info(f"创建迁移版本: {migrated_file}")
    
    # 分析原始脚本
    analysis = analyze_insert_all_script()
    
    # 创建迁移后的内容
    migrated_content = f'''#!/usr/bin/env python3
"""
完整数据插入脚本 - 新架构版本

基于新架构的完整数据插入，包含所有数据类型的批量插入：
1. 股票基本信息
2. 股票日行情数据
3. 指数基本信息
4. 指数日行情数据
5. 基金信息
6. 债券信息
7. 股票分红信息
8. 机构交易信息

特性:
- 智能并发控制（自适应线程池）
- 共享数据库连接池
- 批量数据插入优化
- 完整的进度监控和错误恢复
- 数据验证和质量检查

环境变量配置:
export INFODATA_DB_PASSWORD="your_password"
export INFODATA_MAX_WORKERS="10"  # 并发工作线程数
export INFODATA_BATCH_SIZE="500"  # 批量插入大小
"""

import os
import sys
import logging
import time
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Any, Optional, Callable
import threading

# 添加src目录到路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

# 导入新架构模块
from data_collection.factory import get_akshare_client, get_tushare_client
from data_storage.manager import get_storage_manager
from data_storage.models.stock import StockInfo, StockDailyInfo, StockDividendInfo, InstitutionalTradingInfo
from data_storage.models.financial import IndexInfo, IndexDailyInfo, FundInfo, BondInfo
from config.manager import get_config_manager

# 设置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('insert_all_data.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class DataInsertionTask:
    """数据插入任务"""
    
    def __init__(self, name: str, func: Callable, description: str = "", priority: int = 0):
        self.name = name
        self.func = func
        self.description = description
        self.priority = priority  # 优先级：0=最低，10=最高
        self.result = None
        self.error = None
        self.start_time = None
        self.end_time = None
    
    def execute(self, *args, **kwargs):
        """执行任务"""
        self.start_time = datetime.now()
        try:
            self.result = self.func(*args, **kwargs)
            self.end_time = datetime.now()
            return self.result
        except Exception as e:
            self.error = str(e)
            self.end_time = datetime.now()
            raise
    
    def get_duration(self) -> float:
        """获取执行时长（秒）"""
        if self.start_time and self.end_time:
            return (self.end_time - self.start_time).total_seconds()
        return 0.0
    
    def __str__(self):
        status = "成功" if self.result else ("失败" if self.error else "未执行")
        duration = self.get_duration()
        return f"任务[{self.name}] - {status} - 耗时: {duration:.2f}s"


class ConcurrentDataInserter:
    """并发数据插入器"""
    
    def __init__(self):
        """初始化插入器"""
        self.config = None
        self.akshare_client = None
        self.tushare_client = None
        self.storage = None
        self.tasks = []
        self.results = {{}}
        self.lock = threading.Lock()
        self.progress = {{
            "total": 0,
            "completed": 0,
            "success": 0,
            "failed": 0,
            "start_time": None,
            "end_time": None
        }}
        
    def setup(self):
        """设置客户端和存储管理器"""
        try:
            # 获取配置
            env = os.getenv("INFODATA_APP_ENV", "production")
            self.config = get_config_manager(env=env)
            
            logger.info(f"使用配置环境: {{env}}")
            
            # 创建数据采集客户端
            self.akshare_client = get_akshare_client(
                client_id="full_insertion",
                max_retries=3,
                retry_delay=1.0
            )
            
            # 创建Tushare客户端（如果需要）
            tushare_token = os.getenv("INFODATA_TUSHARE_TOKEN")
            if tushare_token:
                self.tushare_client = get_tushare_client(
                    token=tushare_token,
                    client_id="full_insertion_tushare",
                    max_retries=3,
                    retry_delay=1.0
                )
            
            # 创建数据存储管理器
            self.storage = get_storage_manager(
                host=self.config.get("database.host", "localhost"),
                port=self.config.get("database.port", 3306),
                user=self.config.get("database.user", "root"),
                password=self.config.get("database.password", ""),
                database=self.config.get("database.name", "infodata"),
                pool_size=int(os.getenv("INFODATA_MAX_WORKERS", "10")) + 2  # 连接池大小
            )
            
            # 设置数据库
            self.storage.setup_database(create_tables=True)
            
            logger.info("客户端和存储管理器设置完成")
            return True
            
        except Exception as e:
            logger.error(f"设置失败: {{e}}")
            return False
    
    def create_tasks(self):
        """创建所有数据插入任务"""
        logger.info("创建数据插入任务...")
        
        # 清空任务列表
        self.tasks = []
        
        # 股票基本信息插入（高优先级）
        self.tasks.append(DataInsertionTask(
            name="stock_info",
            func=self.insert_stock_info,
            description="插入股票基本信息",
            priority=10
        ))
        
        # 股票日行情数据插入（高优先级）
        self.tasks.append(DataInsertionTask(
            name="stock_daily_info",
            func=self.insert_stock_daily_info,
            description="插入股票日行情数据",
            priority=9
        ))
        
        # 指数基本信息插入（中优先级）
        self.tasks.append(DataInsertionTask(
            name="index_info",
            func=self.insert_index_info,
            description="插入指数基本信息",
            priority=8
        ))
        
        # 指数日行情数据插入（中优先级）
        self.tasks.append(DataInsertionTask(
            name="index_daily_info",
            func=self.insert_index_daily_info,
            description="插入指数日行情数据",
            priority=7
        ))
        
        # 基金信息插入（中优先级）
        self.tasks.append(DataInsertionTask(
            name="fund_info",
            func=self.insert_fund_info,
            description="插入基金信息",
            priority=6
        ))
        
        # 债券信息插入（中优先级）
        self.tasks.append(DataInsertionTask(
            name="bond_info",
            func=self.insert_bond_info,
            description="插入债券信息",
            priority=5
        ))
        
        # 股票分红信息插入（低优先级）
        self.tasks.append(DataInsertionTask(
            name="stock_dividend_info",
            func=self.insert_stock_dividend_info,
            description="插入股票分红信息",
            priority=4
        ))
        
        # 机构交易信息插入（低优先级）
        self.tasks.append(DataInsertionTask(
            name="institutional_trading_info",
            func=self.insert_institutional_trading_info,
            description="插入机构交易信息",
            priority=3
        ))
        
        self.progress["total"] = len(self.tasks)
        logger.info(f"创建了 {{len(self.tasks)}} 个数据插入任务")
        
        return self.tasks
    
    def update_progress(self, task_name: str, success: bool):
        """更新进度"""
        with self.lock:
            self.progress["completed"] += 1
            if success:
                self.progress["success"] += 1
            else:
                self.progress["failed"] += 1
            
            # 计算进度百分比
            if self.progress["total"] > 0:
                percent = (self.progress["completed"] / self.progress["total"]) * 100
                logger.info(f"进度: {{percent:.1f}}% ({{self.progress['completed']}}/{{self.progress['total']}}) - 成功: {{self.progress['success']}}, 失败: {{self.progress['failed']}}")
    
    def execute_tasks_concurrently(self, max_workers: Optional[int] = None):
        """并发执行任务"""
        if max_workers is None:
            max_workers = int(os.getenv("INFODATA_MAX_WORKERS", "5"))
        
        logger.info(f"开始并发执行任务 (最大工作线程: {{max_workers}})")
        self.progress["start_time"] = datetime.now()
        
        # 按优先级排序
        sorted_tasks = sorted(self.tasks, key=lambda x: x.priority, reverse=True)
        
        try:
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                # 提交所有任务
                future_to_task = {{
                    executor.submit(task.execute, self): task 
                    for task in sorted_tasks
                }}
                
                # 处理完成的任务
                for future in as_completed(future_to_task):
                    task = future_to_task[future]
                    try:
                        result = future.result()
                        self.results[task.name] = {{
                            "success": True,
                            "result": result,
                            "duration": task.get_duration()
                        }}
                        logger.info(f"任务完成: {{task.name}} - 成功 - 耗时: {{task.get_duration():.2f}}s")
                        self.update_progress(task.name, True)
                    except Exception as e:
                        self.results[task.name] = {{
                            "success": False,
                            "error": str(e),
                            "duration": task.get_duration()
                        }}
                        logger.error(f"任务失败: {{task.name}} - {{e}} - 耗时: {{task.get_duration():.2f}}s")
                        self.update_progress(task.name, False)
        
        except Exception as e:
            logger.error(f"并发执行失败: {{e}}")
        
        self.progress["end_time"] = datetime.now()
        
        return self.results
    
    def insert_stock_info(self):
        """插入股票基本信息"""
        logger.info("开始插入股票基本信息")
        
        try:
            # 使用数据采集客户端获取数据
            df_stock_spot = self.akshare_client.get_stock_spot()
            
            if df_stock_spot.empty:
                logger.warning("未获取到股票实时数据")
                return {{"count": 0, "error": "空数据"}}
            
            logger.info(f"获取到 {{len(df_stock_spot)}} 条股票数据")
            
            # 转换为模型实例
            stock_instances = []
            today = datetime.now().date()
            
            for _, row in df_stock_spot.iterrows():
                try:
                    symbol = str(row.get("代码", "")).strip()
                    name = str(row.get("名称", "")).strip()
                    
                    if not symbol or not name:
                        continue
                    
                    stock = StockInfo(
                        symbol=symbol,
                        name=name,
                        update_date=today
                    )
                    
                    if stock.is_valid():
                        stock_instances.append(stock)
                        
                except Exception as e:
                    logger.debug(f"处理股票数据失败 {{symbol}}: {{e}}")
            
            # 批量插入
            if stock_instances:
                batch_size = int(os.getenv("INFODATA_BATCH_SIZE", "500"))
                inserted = self.storage.bulk_insert_data(
                    stock_instances,
                    on_duplicate_key_update=True,
                    chunk_size=batch_size
                )
                
                result = {{"