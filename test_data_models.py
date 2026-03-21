#!/usr/bin/env python3
"""
数据模型测试脚本

测试数据模型定义、数据库迁移和基本操作。
"""

import os
import sys
import tempfile
from pathlib import Path
from datetime import datetime, timedelta

# 添加项目根目录到Python路径
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from infodata.config import ConfigManager, AppConfig
from infodata.utils.logging import setup_logging
from infodata.data.migration import (
    DatabaseMigrator, create_database_tables, 
    check_database_status, reset_database_tables
)


def setup_test_config():
    """设置测试配置"""
    # 创建临时配置文件
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        import yaml
        config_data = {
            "app_name": "TestApp",
            "environment": "testing",
            "log_level": "INFO",
            "database": {
                "sqlite": {
                    "database": ":memory:",
                    "echo": False,
                }
            }
        }
        yaml.dump(config_data, f)
        config_path = f.name
    
    # 加载配置
    config = ConfigManager.load(config_path)
    
    # 清理临时文件
    os.unlink(config_path)
    
    return config


def test_model_imports():
    """测试模型导入"""
    print("测试模型导入...")
    
    try:
        # 测试导入所有模型
        from infodata.models.base import BaseModel, TimeSeriesModel, FinancialInstrumentModel
        from infodata.models.stock import StockDaily, StockInfo
        from infodata.models.fund import FundDaily, FundInfo
        from infodata.models.bond import BondDaily, BondInfo
        from infodata.models.index import IndexDaily, IndexInfo
        from infodata.models.task import TaskExecution, TaskMetric
        from infodata.models.quality import DataQualityMetric, DataValidationRule
        
        print("✓ 所有模型导入成功")
        
        # 测试模型属性
        assert hasattr(StockDaily, '__tablename__')
        assert hasattr(FundInfo, '__tablename__')
        assert hasattr(BondDaily, '__tablename__')
        assert hasattr(IndexInfo, '__tablename__')
        
        print("✓ 模型属性检查通过")
        
        return True
        
    except Exception as e:
        print(f"✗ 模型导入测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_database_migration():
    """测试数据库迁移"""
    print("\n测试数据库迁移...")
    
    try:
        # 设置测试配置
        config = setup_test_config()
        
        # 创建迁移器
        migrator = DatabaseMigrator()
        
        # 测试表创建
        create_result = migrator.create_tables()
        assert create_result['success']
        assert create_result['tables_created'] > 0
        print(f"✓ 数据库表创建成功: {create_result['tables_created']} 个表")
        
        # 测试表状态检查
        check_result = migrator.check_tables()
        assert check_result['success']
        assert check_result['database_connected']
        assert check_result['all_tables_match']
        print("✓ 数据库表状态检查通过")
        
        # 测试表删除和重建
        reset_result = migrator.reset_database()
        assert reset_result['success']
        assert reset_result['drop_result']['success']
        assert reset_result['create_result']['success']
        print("✓ 数据库重置测试通过")
        
        # 测试便捷函数
        status_result = check_database_status()
        assert status_result['success']
        print("✓ 便捷函数测试通过")
        
        return True
        
    except Exception as e:
        print(f"✗ 数据库迁移测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_model_operations():
    """测试模型操作"""
    print("\n测试模型操作...")
    
    try:
        # 设置测试配置
        config = setup_test_config()
        
        # 创建数据库表
        from infodata.data.migration import create_database_tables
        create_result = create_database_tables()
        if not create_result['success']:
            raise RuntimeError(f"创建数据库表失败: {create_result}")
        
        # 获取数据库会话
        from infodata.utils.database import get_db_manager, session_scope
        db_manager = get_db_manager(config.database)
        
        # 测试股票信息模型
        with session_scope(db_manager) as session:
            from infodata.models.stock import StockInfo
            
            # 创建测试数据
            stock = StockInfo(
                source="test",
                source_id="600000.SH",
                symbol="600000.SH",
                name="浦发银行",
                market="上海",
                exchange="SSE",
                listing_date=datetime(1999, 11, 10),
                is_active=True,
            )
            
            # 保存到数据库
            session.add(stock)
            session.commit()
            
            # 从数据库查询
            retrieved = StockInfo.get_by_symbol(session, "600000.SH", "上海")
            assert retrieved is not None
            assert retrieved.symbol == "600000.SH"
            assert retrieved.name == "浦发银行"
            print("✓ 股票信息模型CRUD操作通过")
            
            # 测试搜索
            results = StockInfo.search_by_name(session, "浦发", limit=5)
            assert len(results) >= 1
            print("✓ 股票信息搜索测试通过")
        
        # 测试股票日度模型
        with session_scope(db_manager) as session:
            from infodata.models.stock import StockDaily
            
            # 创建测试数据
            trade_date = datetime.now()
            stock_daily = StockDaily(
                source="test",
                source_id="600000.SH_20250321",
                symbol="600000.SH",
                trade_date=trade_date,
                timestamp=datetime.now(),
                open_price=10.50,
                high_price=10.80,
                low_price=10.40,
                close_price=10.60,
                volume=1000000,
                amount=10600000,
                pct_change=0.0192,
            )
            
            # 保存到数据库
            session.add(stock_daily)
            session.commit()
            
            # 查询最新数据
            latest = StockDaily.get_latest_by_source_id(session, "test", "600000.SH")
            assert latest is not None
            assert latest.close_price == 10.60
            print("✓ 股票日度模型CRUD操作通过")
            
            # 测试日期范围查询
            start_date = trade_date - timedelta(days=1)
            end_date = trade_date + timedelta(days=1)
            records = StockDaily.get_by_date_range(session, "test", "600000.SH", start_date, end_date)
            assert len(records) >= 1
            print("✓ 日期范围查询测试通过")
        
        # 测试批量操作
        with session_scope(db_manager) as session:
            from infodata.models.stock import StockInfo
            
            # 准备批量数据
            batch_data = []
            for i in range(5):
                stock = StockInfo(
                    source="test",
                    source_id=f"60000{i}.SH",
                    symbol=f"60000{i}.SH",
                    name=f"测试股票{i}",
                    market="上海",
                    exchange="SSE",
                    listing_date=datetime(2000, 1, 1),
                    is_active=True,
                )
                batch_data.append(stock.to_dict(exclude=['id', 'created_at', 'updated_at']))
            
            # 批量插入
            result = StockInfo.bulk_upsert(session, batch_data)
            assert result['inserted'] > 0
            print(f"✓ 批量插入测试通过: 插入 {result['inserted']} 条记录")
        
        return True
        
    except Exception as e:
        print(f"✗ 模型操作测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_data_quality_models():
    """测试数据质量模型"""
    print("\n测试数据质量模型...")
    
    try:
        # 设置测试配置
        config = setup_test_config()
        
        # 创建数据库表
        from infodata.data.migration import create_database_tables
        create_result = create_database_tables()
        if not create_result['success']:
            raise RuntimeError(f"创建数据库表失败: {create_result}")
        
        # 获取数据库会话
        from infodata.utils.database import get_db_manager, session_scope
        db_manager = get_db_manager(config.database)
        
        # 测试数据质量指标
        with session_scope(db_manager) as session:
            from infodata.models.quality import DataQualityMetric
            
            # 创建测试数据
            metric = DataQualityMetric(
                source="test",
                source_id="stock_daily_20250321",
                metric_name="数据完整性",
                metric_value=0.985,
                metric_target=0.99,
                data_source="akshare",
                data_type="stock_daily",
                measurement_date=datetime.now(),
                accuracy_score=0.99,
                completeness_score=0.985,
                timeliness_score=0.995,
                overall_score=0.99,
                quality_level="良好",
            )
            
            session.add(metric)
            session.commit()
            
            # 测试质量摘要
            summary = DataQualityMetric.get_quality_summary(session, "akshare")
            assert summary['success'] is True
            assert summary['summary']['total_metrics'] >= 1
            print("✓ 数据质量指标测试通过")
        
        # 测试数据验证规则
        with session_scope(db_manager) as session:
            from infodata.models.quality import DataValidationRule
            
            # 创建测试规则
            rule = DataValidationRule(
                source="test",
                source_id="rule_price_positive",
                rule_name="价格正数检查",
                rule_description="检查股票价格是否为正数",
                rule_type="range_check",
                data_source="akshare",
                data_type="stock_daily",
                table_name="stock_daily",
                column_name="close_price",
                rule_expression="value > 0",
                severity_level="error",
                error_code="PRICE_NEGATIVE",
                error_message="股票价格不能为负数",
                is_active=True,
            )
            
            session.add(rule)
            session.commit()
            
            # 测试数据验证
            test_data = {
                "close_price": 10.5,
                "volume": 1000000,
            }
            
            validation_result = DataValidationRule.validate_data(
                session, "akshare", "stock_daily", test_data
            )
            
            assert validation_result['data_source'] == "akshare"
            assert validation_result['data_type'] == "stock_daily"
            print("✓ 数据验证规则测试通过")
        
        return True
        
    except Exception as e:
        print(f"✗ 数据质量模型测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_task_models():
    """测试任务模型"""
    print("\n测试任务模型...")
    
    try:
        # 设置测试配置
        config = setup_test_config()
        
        # 创建数据库表
        from infodata.data.migration import create_database_tables
        create_result = create_database_tables()
        if not create_result['success']:
            raise RuntimeError(f"创建数据库表失败: {create_result}")
        
        # 获取数据库会话
        from infodata.utils.database import get_db_manager, session_scope
        db_manager = get_db_manager(config.database)
        
        # 测试任务执行记录
        with session_scope(db_manager) as session:
            from infodata.models.task import TaskExecution
            
            # 创建测试数据
            task = TaskExecution(
                source="test",
                source_id="task_001",
                task_name="stock_daily_update",
                task_type="data_collection",
                execution_start=datetime.now(),
                execution_end=datetime.now(),
                execution_duration=30.5,
                execution_status="success",
                exit_code=0,
                input_parameters={"date": "2025-03-21", "symbols": ["600000.SH"]},
                output_result={"records_processed": 100, "records_succeeded": 100},
                records_processed=100,
                records_succeeded=100,
                execution_host="test-server",
            )
            
            session.add(task)
            session.commit()
            
            # 测试任务统计
            stats = TaskExecution.get_task_statistics(session, "stock_daily_update")
            assert stats['task_name'] == "stock_daily_update"
            assert stats['statistics']['total_executions'] >= 1
            print("✓ 任务执行模型测试通过")
        
        # 测试任务指标
        with session_scope(db_manager) as session:
            from infodata.models.task import TaskMetric
            
            # 创建测试数据
            metric = TaskMetric(
                source="test",
                source_id="metric_001",
                metric_name="execution_time",
                metric_type="performance",
                metric_value=30.5,
                metric_unit="seconds",
                metric_timestamp=datetime.now(),
                task_execution_id=1,
            )
            
            session.add(metric)
            session.commit()
            
            # 测试指标趋势
            start_time = datetime.now() - timedelta(days=7)
            end_time = datetime.now()
            trend = TaskMetric.get_metric_trend(
                session, "execution_time", start_time, end_time, "avg"
            )
            
            assert trend['metric_name'] == "execution_time"
            print("✓ 任务指标模型测试通过")
        
        return True
        
    except Exception as e:
        print(f"✗ 任务模型测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """主测试函数"""
    print("=" * 60)
    print("InfoData 数据模型测试")
    print("=" * 60)
    
    # 设置日志
    setup_logging(
        log_level="INFO",
        log_file=None,
        enable_console=False,
    )
    
    tests = [
        ("模型导入", test_model_imports),
        ("数据库迁移", test_database_migration),
        ("模型操作", test_model_operations),
        ("数据质量模型", test_data_quality_models),
        ("任务模型", test_task_models),
    ]
    
    results = []
    for test_name, test_func in tests:
        try:
            success = test_func()
            results.append((test_name, success))
        except Exception as e:
            print(f"✗ {test_name} 测试异常: {e}")
            import traceback
            traceback.print_exc()
            results.append((test_name, False))
    
    # 输出测试结果
    print("\n" + "=" * 60)
    print("测试结果汇总:")
    print("=" * 60)
    
    passed = 0
    total = len(results)
    
    for test_name, success in results:
        status = "✓ 通过" if success else "✗ 失败"
        print(f"{test_name:20} {status}")
        if success:
            passed += 1
    
    print("-" * 60)
    print(f"总计: {passed}/{total} 通过")
    
    if passed == total:
        print("\n🎉 所有测试通过！数据模型功能正常。")
        return 0
    else:
        print(f"\n⚠️  {total - passed} 个测试失败，需要检查。")
        return 1


if __name__ == "__main__":
    sys.exit(main())