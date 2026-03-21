#!/usr/bin/env python3
"""
仅测试配置管理模块
"""

import sys
import os
import logging

# 添加路径
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

logging.basicConfig(level=logging.INFO)

try:
    from config.manager import ConfigManager
    
    print("=== 测试配置管理模块 ===")
    
    # 创建配置管理器
    config = ConfigManager(env="development")
    print(f"创建配置管理器，环境: {config.env}")
    
    # 获取配置值
    app_name = config.get("app.name")
    log_level = config.get("app.log_level")
    db_host = config.get("database.host")
    
    print(f"应用名称: {app_name}")
    print(f"日志级别: {log_level}")
    print(f"数据库主机: {db_host}")
    
    # 验证配置
    is_valid = config.validate()
    print(f"配置验证: {'通过' if is_valid else '失败'}")
    
    # 获取所有配置（敏感值已掩码）
    all_config = config.get_all()
    print(f"配置节数: {len(all_config)}")
    for section, values in all_config.items():
        if isinstance(values, dict):
            print(f"  [{section}]")
            for key, value in values.items():
                print(f"    {key}: {value}")
        else:
            print(f"  {section}: {values}")
    
    print("配置管理模块测试完成")
    
except Exception as e:
    print(f"错误: {e}")
    import traceback
    traceback.print_exc()