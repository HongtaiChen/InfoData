# InvestBuddy — 个人 A 股金融数据平台

> 本地部署的 A 股投研数据平台：自动采集行情 / 概念 / 投资日历 / 资讯数据到本地 MySQL，提供浏览器端查看与作业监控，支持 AI 事件-概念分析。

## 功能总览（7 个栏目）

| 栏目 | 路由 | 说明 |
|---|---|---|
| 📈 行情看板 | `/market` | 股票搜索切换 + 同花顺风格 K 线（MA/VOL/MACD）+ 最新行情表（排序/分页/红涨绿跌） |
| 🧩 概念板块 | `/concept` | 概念列表 + 概念 K 线 + 成分股联动 |
| 📅 投资日历 | `/calendar` | 日历表格视图（休市标记/事件徽章）+ 当日事件详情 + **AI 概念分析** |
| 📰 资讯浏览 | `/news` | 财联社 / 东财双源新闻列表 + 详情 |
| 📊 分析研究 | `/analysis` | 股息率 / 概念排名 / 年初至今涨幅 TOP 模板 + ECharts 柱状图 |
| ⚙️ 作业监控 | `/jobs` | 任务配置 + 运行记录（10 秒自动轮询）+ 统计卡片 |
| 🗄️ 数据浏览 | `/data` | 库表浏览器（只读）：左侧 27 张业务表 + 搜索，右侧网格分页/排序/过滤（点击表头排序） |
| 🛠 系统设置 | `/settings` | 系统状态 + 数据源配置 + 任务规划 |

## 技术栈

- **前端**：Vue 3.5 + Vite 8 + TypeScript + Naive UI + KLineChart（K 线）+ ECharts（统计图）+ Vue Router 4 + Pinia + Axios
- **后端**：Python FastAPI + uvicorn + APScheduler
- **数据**：MySQL 8（复用既有 `adata` 库，24 张表 / 约 3700 万行）
- **采集**：AKShare（东财 → 腾讯 → 新浪 → Tushare 四级降级，硬超时兜底）
- **AI**：火山引擎方舟（豆包），`ARK_API_KEY` 环境变量注入，无 Key 自动占位降级
- **部署**：Docker Compose（两种模式：直连宿主机 MySQL / 全容器化）

## 快速启动

### 方式一：开发模式（本机 Python + Node）

```bat
start-dev.bat
```

- 前端 http://127.0.0.1:5173 （Vite dev server）
- 后端 http://127.0.0.1:8000 （FastAPI）

### 方式二：Docker 模式

见 [scripts/docker-deploy-guide.md](scripts/docker-deploy-guide.md)

```bat
docker-start.bat             :: 模式 A：backend+frontend，MySQL 直连宿主机
docker-start.bat full        :: 模式 B：mysql+backend+frontend 全容器化
```

- 前端 http://127.0.0.1:8080
- 后端 http://127.0.0.1:18000/api/health

## 环境变量（.env）

复制 `.env.example` 为 `.env` 并填写（`.env` 已被 gitignore，绝不上库）：

| 变量 | 必填 | 说明 |
|---|---|---|
| `INFO_DATA_DB_HOST/PORT/USER/PASSWORD/NAME` | ✅ | 数据库连接（开发默认 root/root@127.0.0.1:3306/adata） |
| `ARK_API_KEY` | 可选 | 方舟豆包 AI Key；不配则 AI 分析返回占位结果 |
| `ARK_MODEL_ID` | 可选 | 默认 `doubao-1-5-pro-32k-250115` |
| `TUSHARE_TOKEN` | 可选 | Tushare 备源 token（北交所数据） |
| `NO_PROXY` | 可选 | 建议 `*` 避免代理干扰采集 |

## 数据采集

数据通过作业监控触发（`task_config` 表配置，APScheduler 调度）：

| 任务 | 说明 | 数据源 |
|---|---|---|
| 股票日线增量采集 | 每日收盘后增量更新 K 线 | 东财 → 腾讯 → 新浪 → Tushare |
| 概念板块 | 概念列表 + 成分股 | 同花顺 |
| 投资日历 | 财经事件日历 | 巨潮/东财 |
| 资讯采集 | 财联社 + 东财快讯 | 双源 |
| AI 概念分析 | 事件 → 相关概念（评分 1-10） | 豆包（方舟） |

## 目录结构

```
InvestBuddy/
├── backend/                 # FastAPI 后端
│   ├── app/
│   │   ├── api/             # 7 个 API 模块（market/concept/calendar/news/analysis/jobs/ai）
│   │   ├── collectors/      # 数据采集器（增量 K 线/概念/日历/资讯）
│   │   ├── analysis/        # 分析服务（股息率/概念排名/AI 概念分析）
│   │   ├── tasks/           # 任务注册与调度（APScheduler）
│   │   ├── db.py            # 数据库配置（环境变量驱动）
│   │   └── main.py          # 应用入口
│   ├── Dockerfile
│   └── requirements.txt
├── frontend/                # Vue3 前端
│   ├── src/views/           # 7 栏目视图
│   ├── src/components/      # KLineChart 等组件
│   ├── Dockerfile
│   └── nginx.conf           # 生产环境静态托管 + /api 反代
├── scripts/                 # 部署脚本（迁移/备份/指南）
├── docker-compose.yml       # 容器编排
├── docker-start.bat         # Docker 一键启动
├── start-dev.bat            # 开发模式一键启动
└── .env.example             # 环境变量模板
```

## 数据源与风控策略

- **四级降级**：东财被风控时自动降级腾讯 → 新浪 → Tushare，首源只试 1 次快速切换
- **硬超时兜底**：每只股票请求线程化包装，最多 25 秒，杜绝挂死
- **北交所默认排除**：4/8/92 开头（腾讯/新浪不支持、Tushare 需 token），可 `include_bj` 参数开启
- **退市股过滤**：名称含退/PT 的 151 只 SQL 排除 + 数据超 2 年软跳过
- **随机延时**：0.3-1.2s 错峰，降低风控概率

## 安全说明

- 所有密钥（DB 密码 / API Key）只从环境变量读取，`.env` 不入 Git、不进镜像
- 曾发现 `doubaoai.py` 硬编码 Key 泄露 → 已重构为 `ARK_API_KEY` 环境变量方案
- 数据库密码等敏感信息请勿写入代码或提交历史

## License

个人学习项目，仅供研究使用。数据版权归各数据源所有，请勿用于商业用途。
