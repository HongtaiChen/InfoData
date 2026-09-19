#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
分析研究模块注册表（配置文件版，v0.2 决策：配置文件先行，模块多了再落库）

🔒 第一原则（2026-09-15 确立，新增模块前必读）——**数据用来比较才有意义**
   单个数字不是信息，只是噪声；只有放进一个比较关系里，它才成为信号。
   新增任何 KPI/指标，必须能回答「跟谁比」，否则不予接入视图层。

   五类参照系（至少落进一类）：
     ① 同类横比 —— 同层级不同对象之间比（谁强谁弱）        e.g. 六组指数 20 日收益
     ② 自身纵比 —— 跟自己的历时序列比（现在算不算极端）    e.g. pct 分位 / z-score / adj 风险调整
     ③ 基准超额 —— 跟恰当基准比（真强还是水涨船高）        e.g. 情绪温度 = 券商 − 中证全指
     ④ 交叉印证 —— 跟另一个独立维度比（有被证实吗）        e.g. 宽度 × 量能、股 × 债、股 × 商品
     ⑤ 结构分解 —— 看内部构成（普涨还是少数拉动）          e.g. 涨跌家数 / 新高新低 / 均线参与度

   比较的最高级用法是「背离」——两个本该同向的维度不同向，才是可判断的信息
     （指数 × 个股 / 量价 / 股债 / 股商）。

   三条硬性约束：无参照物不上线 · 口径随响应下发（前端只透传不手抄）· 优先落地「能产生背离信号」的指标对。
   详见 `docs/design/分析研究模块交互设计规范.md` §1.0 与 `docs/市场风向分析数据蓝图探索报告_2026-09-15.md`。

- 总览页 GET /api/analysis/registry 返回本清单（导航目录 + 卡片墙来源）
- 每个模块的取数 API 约定：GET /api/analysis/<module_id>
- kind: track=跟踪型（总览卡片墙，每日看状态）/ research=研究型（目录入口，按需查参数）
- group: 固定五类之一（市场风向/板块与概念/个股基本面/资金与情绪/跟踪清单）

新增模块标准动作：
  1) app/analysis/ 加计算/装配模块
  2) app/api/analysis.py 加路由
  3) 本文件注册一条元数据
  4) 前端 views/AnalysisModuleView.vue 的 moduleViews 里用 defineAsyncComponent 映射视图组件
     （⚠️ 必须 defineAsyncComponent 包装，裸 () => import() 会被 Vue 当函数式组件、渲染成 [object Promise]）

卡片墙契约（2026-09-19 确立，track 型模块必读）：
  总览页卡片曾出现「看不出想说什么」的问题，根因是三条约定缺失，新增模块必须遵守：

  ① `verdict = {headline, detail, tone}` —— 卡片顶部的一句话结论，**必须由后端生成**。
     这是「口径随响应下发，前端只透传不手抄」在本处的落点；前端拼结论句会立刻产生
     第二份口径，日后必然漂移。tone ∈ normal(蓝) / opportunity(金) / caution(琥珀)。
  ② 每个 KPI 可标 `card_rank`（1..3）声明是否上卡片 —— 总览页据此挑，**不按数组顺序截断**。
     曾因前端 slice(0,3) 按声明顺序截断，把带分位的指标截掉、只留三个同类动量。
     未标注的项仍完整出现在详情页，不丢信息。
  ③ `desc` 写「这个模块回答什么问题」，**不写功能清单、不写会漂移的计数**
     （计数改一次规则就要回来改一次文案，历史上已多次滞后）。

展示约定：
  - updated_cron = 原始 cron（供运维/调试核对，勿直接面向用户展示）
  - schedule_text = 人话化的更新节奏（面向用户；缺省时前端回退显示 updated_cron）
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
        "desc": "回答三件事：**现在处在什么位置、估值贵不贵、内部结构有没有背离**",
        "as_of_source": "market_style_daily.MAX(trade_date)",
        "updated_cron": "30 21 * * 0-4",
        "schedule_text": "日线完成后链式触发（兜底：每工作日 21:30）",
        "params": [
            {"key": "trend_days", "type": "select", "label": "时序窗口",
             "options": [120, 250, 500], "default": 250},
        ],
        "drilldown": [
            {"label": "行情看板看K线", "target": "/market"},
            {"label": "板块轮动看行业/概念", "target": "/analysis/sector-rotation"},
        ],
    },
    {
        "module_id": "sector-rotation",
        "name": "板块轮动",
        "group": "板块与概念",
        "kind": "track",
        "icon": "🔀",
        "desc": "回答三件事：**钱在往哪些行业和概念走、轮动快不快、两个口径是否互证**",
        "as_of_source": "stock_market_daily.MAX(trade_date)",
        "updated_cron": "20 19 * * 0-4",
        "schedule_text": "随个股日线（每工作日 19:20 后）+ 概念（21:00/22:00）自动可见",
        "params": [
            {"key": "level", "type": "select", "label": "行业层级",
             "options": ["一级", "二级"], "default": "一级"},
        ],
        "drilldown": [
            {"label": "概念中心看概念K线", "target": "/concept"},
            {"label": "行情看板看大盘", "target": "/market"},
        ],
    },
]
