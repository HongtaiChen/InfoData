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
       / detail=详情型（不上卡片墙，承载详情页元数据；2026-09-19 拆卡新增，见卡片墙契约 ⑦）
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
  ② 每个 KPI 可标 `card_rank`（升序，1 起）声明上卡片的优先级 —— 总览页据此挑，
     **不按数组顺序截断**。曾因前端 slice(0,3) 按声明顺序截断，把带分位的指标截掉、
     只留三个同类动量。展示条数由卡片宽度决定：半宽卡 3 个、`card_span='full'` 的整行卡 6 个。
     未标注的项仍完整出现在详情页，不丢信息。
  ③ `desc` 写「这个模块回答什么问题」，**不写功能清单、不写会漂移的计数**
     （计数改一次规则就要回来改一次文案，历史上已多次滞后）。
  ④ **卡片墙配色只表达「异常程度」，不表达方向**（2026-09-19 定稿）。KPI 的 `tone` 三态：
       updown  = 真正的行情涨跌数字（指数涨跌幅）→ 红涨绿跌
       diff    = **组间收益差 / 相对强弱**（风偏、剪刀差、超额、中位收益）
                 → 主色蓝 + 保留正负号。染红绿会让「防守占优」看起来像一条独立警报，
                   且卡片上一蓝一绿会被误读成两类指标；方向交给符号、status 文案与刻度条。
       neutral = 分位 / 占比 / 离散度等无量纲量 → 主色蓝、不带正号
     极端才变色（金 = 分位进极值区），**亮得少才算信号**。
  ⑤ KPI 可带 `scale = {pct, label}` 给数字配分位刻度条。⚠️ `label`（窗口口径）必须
     由后端下发，**前端不得写死「近一年」**：ERP 的分位窗口是「近 250 个月末」，
     写死会把月频分位说成日频 —— 这是口径错误，不是文案问题。没有历史序列的模块
     （如板块轮动）**宁可不下发 scale**，前端不渲染刻度条，也不拿别的量纲硬凑。
  ⑥ 每个 KPI 的 `hint`（口径说明）**必须在视图层有可见入口** ——「口径随响应下发」只有
     配上入口才成立，否则等于没下发（2026-09-19 实测：28/28 都下发了，但卡片墙一直没入口）。
     两页共用 `frontend/src/components/analysis/KpiHint.vue`：卡片墙入口 = ⓘ 圆标
     （悬停显形；因卡片本身是跳转按钮，图标必须 `@click.stop`），详情页入口 = 整卡悬浮。
     ⚠️ `hint` 用 `**强调**` 标重点 → 承载它的前端位置必须走 `RichText`，
     `{{ hint }}` 直出会把 `**` 原样显示（详见 `docs/design/分析研究模块交互设计规范.md` §14）。
  ⑦ **「回答三件事」拆卡**（2026-09-19 定稿）：每个分析模块的 desc 原本是「回答三件事：A、B、C」
     一句话，浮窗里挤成一团且断行随机。改为**三张独立卡各答一件事**，靠四个字段解耦：
       - `data`     —— 取数端点名（`/api/analysis/<data>`）。三张卡共用同一份模块响应
                      （后端 ttl_cache 命中 <1ms），前端按 URL 去重只发一次请求；
       - `question` —— 本卡回答第几件事（q1/q2/q3）。KPI 在模块响应里标 `questions` 数组
                      （一个 KPI 可服务多件事，如「行业中位」既是流向读数也是互证一腿），
                      卡片墙按 `kpi.questions ∋ card.question` 过滤后再套 card_rank；
       - `detail`   —— 点击卡片跳转的详情页路由（三卡同进一个聚合详情页，详情页不变）；
       - 响应新增 `verdicts = {q1, q2, q3}` 子判读，卡片取 `verdicts[question]`；
       原 `verdict`（四合一）保留给详情页/兼容。原模块注册为 `kind: 'detail'` 条目，
       承载 params / schedule / as_of_source，不上卡片墙。

展示约定：
  - updated_cron = 原始 cron（供运维/调试核对，勿直接面向用户展示）
  - schedule_text = 人话化的更新节奏（面向用户；缺省时前端回退显示 updated_cron）
"""
from __future__ import annotations

MODULE_GROUPS: list[str] = ["市场风向", "板块与概念", "个股基本面", "资金与情绪", "跟踪清单"]

REGISTRY: list[dict] = [
    # ================= 市场风向 · 三卡（data=market-wind，共用一份 TTL 缓存响应） =================
    {
        "module_id": "market-wind-position",
        "name": "市场风向 · 位置",
        "group": "市场风向",
        "kind": "track",
        "icon": "🧭",
        "desc": "回答①：**现在处在什么位置**——大势分位与情绪水位",
        "data": "market-wind",
        "question": "q1",
        "detail": "/analysis/market-wind",
    },
    {
        "module_id": "market-wind-valuation",
        "name": "市场风向 · 估值",
        "group": "市场风向",
        "kind": "track",
        "icon": "🧭",
        "desc": "回答②：**估值贵不贵**——股债性价比与风险补偿",
        "data": "market-wind",
        "question": "q2",
        "detail": "/analysis/market-wind",
    },
    {
        "module_id": "market-wind-structure",
        "name": "市场风向 · 结构",
        "group": "市场风向",
        "kind": "track",
        "icon": "🧭",
        "desc": "回答③：**内部结构有没有背离**——风格分化与交叉印证",
        "data": "market-wind",
        "question": "q3",
        "detail": "/analysis/market-wind",
    },
    {
        # 详情型：不上卡片墙；市场风向三张分卡都跳转到这里（聚合完整 6 KPI + 交叉印证清单）
        "module_id": "market-wind",
        "name": "市场风向",
        "group": "市场风向",
        "kind": "detail",
        "icon": "🧭",
        "desc": "回答三件事：**位置、估值、结构**——总览页三张分卡各答一件，本页聚合完整论据",
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
    # ================= 板块与概念 · 三卡（data=sector-rotation） =================
    {
        "module_id": "sector-rotation-flow",
        "name": "板块轮动 · 流向",
        "group": "板块与概念",
        "kind": "track",
        "icon": "🔀",
        "desc": "回答①：**钱在往哪些行业和概念走**——中位、广度与超基准",
        "data": "sector-rotation",
        "question": "q1",
        "detail": "/analysis/sector-rotation",
    },
    {
        "module_id": "sector-rotation-speed",
        "name": "板块轮动 · 速度",
        "group": "板块与概念",
        "kind": "track",
        "icon": "🔀",
        "desc": "回答②：**轮动快不快**——离散度与首尾差",
        "data": "sector-rotation",
        "question": "q2",
        "detail": "/analysis/sector-rotation",
    },
    {
        "module_id": "sector-rotation-compare",
        "name": "板块轮动 · 互证",
        "group": "板块与概念",
        "kind": "track",
        "icon": "🔀",
        "desc": "回答③：**两个口径是否互证**——行业 vs 概念",
        "data": "sector-rotation",
        "question": "q3",
        "detail": "/analysis/sector-rotation",
    },
    {
        "module_id": "sector-rotation",
        "name": "板块轮动",
        "group": "板块与概念",
        "kind": "detail",
        "icon": "🔀",
        "desc": "回答三件事：**流向、速度、互证**——总览页三张分卡各答一件，本页聚合完整排行",
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
    # ---------- Batch C（2026-09-19）：三张"只进不出"的表接上消费端 ----------
    # 背景：`interbank_rate_daily` / `overseas_index_daily` / `currency_boc_daily`
    # + `fund_new_issue` + `stock_repurchase` 五张表此前各有采集器与 DQ 规则，
    # 但**没有任何视图消费**（《未落地优化项盘点_2026-09-19》第三节）。
    # 采集任务排期的注释里本来就叫「蓝图A 钱贵不贵」「蓝图E 发行冰点/人民币中间价」
    # 「蓝图D 产业资本回购」—— 本轮就是把蓝图补齐。
    # ================= 资金与情绪 · 钱贵不贵三卡（data=money-cost） =================
    {
        "module_id": "money-cost-level",
        "name": "钱贵不贵 · 价格",
        "group": "资金与情绪",
        "kind": "track",
        "icon": "💧",
        "desc": "回答①：**钱现在贵不贵**——Shibor 3M 近一年分位",
        "data": "money-cost",
        "question": "q1",
        "detail": "/analysis/money-cost",
    },
    {
        "module_id": "money-cost-expectation",
        "name": "钱贵不贵 · 预期",
        "group": "资金与情绪",
        "kind": "track",
        "icon": "💧",
        "desc": "回答②：**资金预期是松是紧**——期限利差陡平与倒挂",
        "data": "money-cost",
        "question": "q2",
        "detail": "/analysis/money-cost",
    },
    {
        "module_id": "money-cost-policy",
        "name": "钱贵不贵 · 政策",
        "group": "资金与情绪",
        "kind": "track",
        "icon": "💧",
        "desc": "回答③：**政策利率动没动**——LPR 连续未动月数",
        "data": "money-cost",
        "question": "q3",
        "detail": "/analysis/money-cost",
    },
    {
        "module_id": "money-cost",
        "name": "钱贵不贵",
        "group": "资金与情绪",
        "kind": "detail",
        "icon": "💧",
        "desc": "回答三件事：**价格、预期、政策**——总览页三张分卡各答一件，本页聚合完整曲线",
        "as_of_source": "interbank_rate_daily.MAX(trade_date)",
        "updated_cron": "35 19 * * 0-4",
        "schedule_text": "每工作日 19:35（银行间市场收盘后）",
        "params": [
            {"key": "trend_days", "type": "select", "label": "时序窗口",
             "options": [250, 500, 1000], "default": 500},
        ],
        "drilldown": [
            {"label": "跨市场对照看外部环境", "target": "/analysis/cross-market"},
            {"label": "市场风向看股债性价比", "target": "/analysis/market-wind"},
        ],
    },
    # ================= 市场风向 · 跨市场对照三卡（data=cross-market） =================
    {
        "module_id": "cross-market-overseas",
        "name": "跨市场 · 涨跌",
        "group": "市场风向",
        "kind": "track",
        "icon": "🌍",
        "desc": "回答①：**外面在涨还是跌**——A股与海外市场 20 日收益",
        "data": "cross-market",
        "question": "q1",
        "detail": "/analysis/cross-market",
    },
    {
        "module_id": "cross-market-relative",
        "name": "跨市场 · 强弱",
        "group": "市场风向",
        "kind": "track",
        "icon": "🌍",
        "desc": "回答②：**我们相对外面强还是弱**——对美股/恒生超额",
        "data": "cross-market",
        "question": "q2",
        "detail": "/analysis/cross-market",
    },
    {
        "module_id": "cross-market-conduction",
        "name": "跨市场 · 传导",
        "group": "市场风向",
        "kind": "track",
        "icon": "🌍",
        "desc": "回答③：**外面的信息能不能传导进来**——隔夜同向率",
        "data": "cross-market",
        "question": "q3",
        "detail": "/analysis/cross-market",
    },
    {
        "module_id": "cross-market",
        "name": "跨市场对照",
        "group": "市场风向",
        "kind": "detail",
        "icon": "🌍",
        "desc": "回答三件事：**涨跌、强弱、传导**——总览页三张分卡各答一件，本页聚合完整对照",
        "as_of_source": "overseas_index_daily.MAX(trade_date)",
        "updated_cron": "50 19 * * *",
        "schedule_text": "每天 19:50（美股为中国香港/美国市场前一夜收盘）",
        "params": [
            {"key": "trend_days", "type": "select", "label": "对照窗口",
             "options": [120, 250, 500], "default": 500},
        ],
        "drilldown": [
            {"label": "市场风向看 A 股内部风格", "target": "/analysis/market-wind"},
            {"label": "资金温度看汇率与增量资金", "target": "/analysis/funding-temperature"},
        ],
    },
    # ================= 资金与情绪 · 资金温度三卡（data=funding-temperature） =================
    {
        "module_id": "funding-temperature-fx",
        "name": "资金温度 · 外部环境",
        "group": "资金与情绪",
        "kind": "track",
        "icon": "🌡️",
        "desc": "回答①：**外部资金环境松不松**——人民币汇率分位",
        "data": "funding-temperature",
        "question": "q1",
        "detail": "/analysis/funding-temperature",
    },
    {
        "module_id": "funding-temperature-fund",
        "name": "资金温度 · 增量资金",
        "group": "资金与情绪",
        "kind": "track",
        "icon": "🌡️",
        "desc": "回答②：**增量资金够不够**——新发基金份额与权益占比",
        "data": "funding-temperature",
        "question": "q2",
        "detail": "/analysis/funding-temperature",
    },
    {
        "module_id": "funding-temperature-rep",
        "name": "资金温度 · 产业资本",
        "group": "资金与情绪",
        "kind": "track",
        "icon": "🌡️",
        "desc": "回答③：**产业资本在不在场**——回购计划数与进度",
        "data": "funding-temperature",
        "question": "q3",
        "detail": "/analysis/funding-temperature",
    },
    {
        "module_id": "funding-temperature",
        "name": "资金温度",
        "group": "资金与情绪",
        "kind": "detail",
        "icon": "🌡️",
        "desc": "回答三件事：**外部环境、增量资金、产业资本**——总览页三张分卡各答一件，本页聚合三线索交叉",
        "as_of_source": "currency_boc_daily.MAX(trade_date)",
        "updated_cron": "35 20 * * *",
        "schedule_text": "每天 20:05–20:35 依次更新（汇率 → 基金发行 → 回购）",
        "params": [
            {"key": "months", "type": "select", "label": "月度窗口",
             "options": [24, 36, 60], "default": 36},
        ],
        "drilldown": [
            {"label": "钱贵不贵看资金成本", "target": "/analysis/money-cost"},
            {"label": "行情看板看大盘", "target": "/market"},
        ],
    },
]
