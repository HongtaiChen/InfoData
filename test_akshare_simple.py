#!/usr/bin/env python3
"""
简单测试AKShare模块
"""

import sys
import os

# 添加路径
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

try:
    print("=== 测试AKShare基础功能 ===")
    
    # 首先测试直接导入
    import akshare as ak
    print("AKShare导入成功")
    
    # 测试一个简单的函数
    try:
        df = ak.stock_zh_a_spot_em()
        print(f"获取到 {len(df)} 条股票数据")
        if not df.empty:
            print(f"数据列: {', '.join(df.columns[:5])}...")
            print(f"示例股票: {df.iloc[0]['代码']} - {df.iloc[0]['名称']}")
    except Exception as e:
        print(f"AKShare API调用异常: {e}")
    
    print("\n=== 测试自定义AKShare客户端 ===")
    
    from data_collection.akshare_client import AKShareClient
    from data_collection.base import RateLimitConfig
    
    # 创建客户端
    rate_limit = RateLimitConfig(
        max_requests_per_minute=5,
        max_requests_per_hour=50,
        delay_between_requests=0.2
    )
    
    client = AKShareClient(rate_limit_config=rate_limit, max_retries=1)
    print(f"创建客户端: {client.name}")
    
    # 测试连接
    try:
        connected = client.test_connection()
        print(f"连接测试: {'成功' if connected else '失败'}")
    except Exception as e:
        print(f"连接测试异常: {e}")
    
    # 获取统计数据
    stats = client.get_stats()
    print("客户端统计:")
    for key, value in stats.items():
        print(f"  {key}: {value}")
    
    print("测试完成")
    
except ImportError as e:
    print(f"导入错误: {e}")
    print("请确保已安装: akshare")
except Exception as e:
    print(f"错误: {e}")
    import traceback
    traceback.print_exc()