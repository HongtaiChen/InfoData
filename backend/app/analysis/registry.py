#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
分析研究模块注册表（配置文件版，v0.2 决策：配置文件先行，模块多了再落库）

- 总览页 GET /api/analysis/registry 返回本清单（导航目录 + 卡片墙来源）
- 每个模块的取数 API 约定：GET /api/analysis/<module_id>
- kind: track=跟踪型（总览卡片墙，每日看状态）/ research=研究型（目录入口，按需查参数）
- group: 固定五类之一（市场风向/板块与概念/个股基本面/资金与情绪/跟踪清单）

新增模块标准动作：
  1) app/analysis/ 加计算/装配模块
  2) app/api/analysis.py 加路由
  3) 本文件注册一条元数据
  4) 前端 src/analysis/modules.ts 把 module_id 映射到视图组件（复用内置视图库）
"""
from __future__ import annotations

MODULE_GROUPS: list[str] = ["市场风向", "板块与概念", "个股基本面", "资金与情绪", "跟踪清单"]

REGISTRY: list[dict] = [
    {
        "module_id": "market-wind",
        "name": "市场风向",
        "group": "市场风向",
        "kind": "track",
        "icon": "🧭",
        "desc": "六组指数等权收益热力 + 大小盘剪刀差 + 风偏分数 + 情绪温度",
        "as_of_source": "market_style_daily.MAX(trade_date)",
        "updated_cron": "45 18 * * 0-4",
        "params": [
            {"key": "trend_days", "type": "select", "label": "时序窗口",
             "options": [120, 250, 500], "default": 250},
        ],
        "drilldown": [
            {"label": "行情看板看K线", "target": "/market"},
        ],
    },
]
