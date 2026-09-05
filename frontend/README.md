# InvestBuddy 前端

Vue 3 + Vite + Naive UI + TypeScript 构建的本地 A 股投研平台前端。

## 技术栈

- **Vue 3.5** + **Vite 8** + **TypeScript 6** —— 主框架
- **Naive UI** —— UI 组件库（原生 TS 支持，开箱即用）
- **KLineChart** —— K 线图（同花顺风格，含 MA/MACD/KDJ 指标）
- **ECharts** + **vue-echarts** —— 统计图表
- **Vue Router 4**（Hash 模式） + **Pinia** —— 路由与状态
- **Axios** —— HTTP 客户端（代理到后端 /api）

## 启动

```bash
cd D:\Project\InfoData\frontend
npm install              # 首次需安装依赖
npm run dev              # 启动开发服务器（端口 5173）
npm run build            # 生产构建
npm run preview          # 预览生产包
```

打开浏览器访问 **http://127.0.0.1:5173/** ，会自动跳转到「行情看板」栏目。

## 目录结构

```
src/
├── api/              # axios 封装
├── layouts/          # 主布局（侧边栏 + 顶栏）
├── router/           # 路由（七栏目）
├── views/            # 八个栏目页面
│   ├── MarketView.vue      # 行情看板
│   ├── ConceptView.vue     # 概念板块
│   ├── CalendarView.vue    # 投资日历
│   ├── NewsView.vue        # 资讯浏览
│   ├── AnalysisView.vue    # 分析研究
│   ├── JobsView.vue        # 作业监控
│   ├── DataView.vue        # 数据浏览（DBeaver 式只读表浏览器）
│   └── SettingsView.vue    # 系统设置
├── App.vue           # 根组件（NConfigProvider + 路由出口）
├── main.ts           # 入口
└── style.css         # 全局样式（A 股涨红跌绿约定）
```

## 后端代理

`vite.config.ts` 配置了 `/api` → `http://127.0.0.1:8000` 的开发代理。
确保后端服务在 8000 端口运行，否则前端调用接口会失败。

## 后续规划

- P3-2: 行情看板接入（最新行情表 + K 线 + 股票搜索）
- P3-3: 概念板块接入（概念列表 + 成分股 + 概念 K 线）
- P3-4: 投资日历接入（日历表格视图）
- P3-5: 资讯浏览接入（双源新闻列表）
- P3-6: 分析研究接入（三个核心模板）
- P3-7: 作业监控接入（轮询 + 状态统计）