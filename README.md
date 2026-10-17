# InvestBuddy — 个人 A 股金融数据平台

> 本地部署的 A 股投研数据平台：自动采集行情 / 概念 / 投资日历 / 资讯数据到本地 MySQL，提供浏览器端查看与作业监控，支持 AI 事件-概念分析。

## 功能总览（9 个栏目）

| 栏目 | 路由 | 说明 |
|---|---|---|
| 📈 行情看板 | `/market` | 股票搜索切换 + 同花顺风格 K 线（MA/VOL/MACD）+ 最新行情表（排序/分页/红涨绿跌） |
| 🧩 概念板块 | `/concept` | 概念列表 + 概念 K 线 + 成分股联动 |
| 📅 投资日历 | `/calendar` | 日历表格视图（休市标记/事件徽章）+ 当日事件详情 + **AI 概念分析** |
| 📰 资讯浏览 | `/news` | 财联社 / 东财双源新闻列表 + 详情 |
| 🔬 分析研究 | `/analysis` | 分析模块卡片墙（货币流动性 / 市场风向 …）+ 结论区 + 下钻详情页 |
| ⚙️ 作业监控 | `/jobs` | 任务配置 + 运行记录（10 秒自动轮询）+ 统计卡片 |
| 🗄️ 数据中心 | `/data` | 库表浏览器（只读）：左侧业务表 + 搜索，右侧网格分页/排序/过滤 |
| 🩺 数据质量 | `/quality` | DQ 体检报告 + 缺口明细 + **启动自愈**摘要条与详情 |
| 🔧 系统设置 | `/settings` | 系统状态 + 数据源配置 + 任务规划 |

## 技术栈

- **前端**：Vue 3.5 + Vite 8 + TypeScript + Naive UI + KLineChart（K 线）+ ECharts（统计图）+ Vue Router 4 + Pinia + Axios
- **后端**：Python FastAPI + uvicorn + APScheduler
- **数据**：MySQL 8（本机 `adata` 库：现网 **60 张表 / 约 3700 万行**；其中 **56 张**已固化进 `scripts/schema.sql`）
- **采集**：AKShare（东财 → 腾讯 → 新浪 → Tushare 四级降级，硬超时兜底）
- **AI**：火山引擎方舟（豆包），`ARK_API_KEY` 环境变量注入，无 Key 自动占位降级
- **部署**：Docker Compose（两种模式：直连宿主机 MySQL / 全容器化）

## 从零部署（换一台机器 / 别人的电脑）

> 本仓库**不含任何本机绝对路径**，可直接 clone 到任意目录运行。
> Python 解释器通过「环境变量 → `.python-path` → `backend/.venv` → PATH」四级回退解析。

### 前置条件

| 依赖 | 版本 | 说明 |
|---|---|---|
| Python | 3.12+ | 后端 |
| Node.js | 18+ | 前端构建 |
| MySQL | 8.0 | 本机服务或 Docker 均可 |

### 第 1 步 · 获取代码

```bat
git clone <本仓库地址> InfoData
cd InfoData
```

### 第 2 步 · 准备 Python 解释器

任选一种（启动脚本按此优先级自动查找，四种都没有会给出提示）：

```bat
:: 方式 a（推荐）—— 项目内虚拟环境
cd backend
python -m venv .venv
cd ..

:: 方式 b —— 设环境变量
set INFO_DATA_PYTHON=D:\path\to\python.exe

:: 方式 c —— 仓库根建 .python-path 文件，内容为解释器完整路径（一行）
:: 该文件已 gitignore，是「本机私有配置」，不会跟着仓库走
```

### 第 3 步 · 安装后端依赖

```bat
:: 用第 2 步选定的解释器，例如 venv：
backend\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
```

### 第 4 步 · 初始化数据库（**关键，漏了会启动失败**）

```bat
:: 4.1 建库 + 建全部 56 张表（幂等，可重复执行，不含 DROP）
::
::     ⚠️ Windows 版 MySQL 的安装程序**默认不把 bin 加进 PATH**，
::        所以「MySQL 已装好、服务也在跑」时，直接敲 mysql 仍会报
::        『mysql 不是内部或外部命令』。此时用全路径（版本号按你的实际改）：
::        "C:\Program Files\MySQL\MySQL Server 8.2\bin\mysql.exe" -u root -p < scripts\schema.sql
::
::     ⚠️ 本文件把库名写死为 adata（第 31 行 CREATE DATABASE + 第 35 行 USE）。
::        要用别的库名，先改这两行，再同步设 INFO_DATA_DB_NAME，否则后端会连到空库。
mysql -u root -p < scripts\schema.sql

:: 4.2 灌入配置（任务 / DQ 规则 / 表元数据 / 索引）
::
::     ⚠️ 必须用第 3 步装过依赖的**同一个解释器**。不要用 PATH 上的裸 python：
::        它没有 pymysql，会直接 ModuleNotFoundError 中断（干净克隆实测必踩）。
::        用方式 b/c 指定的解释器时，把 PY 换成那个全路径即可。
cd backend
set "PY=.venv\Scripts\python.exe"
"%PY%" scripts\seed_task_config.py --apply
"%PY%" scripts\seed_dq_rules.py
"%PY%" scripts\seed_table_meta.py
"%PY%" scripts\seed_indexes.py --apply
"%PY%" scripts\seed_market_style.py
"%PY%" scripts\seed_recovered_tasks.py
cd ..
```

> ⚠️ **为什么这步不能省**：后端起不来最常见的原因就是库里没有表。
> `app/scheduler.py::sync_from_db()` 会查 `task_config`，该调用点**没有异常保护**，
> 表不存在时异常直接冒到 `main.py::lifespan`，uvicorn 报
> `Application startup failed` 并退出。
>
> ⚠️ `--apply` 不能少：`seed_task_config.py` / `seed_indexes.py` / `setup_v3_tables.py`
> 默认只做**只读预览**，不加 `--apply` 不会写入任何东西。
>
> 🔎 **2026-10-17 干净克隆实测**：把仓库 clone 到一个空目录后按本节逐条执行，
> 56 张表零报错建成、6 个 `seed_*` 全绿（49 任务 / 209 条 DQ 规则 / 59 张表元数据），
> 后端起来后 `/api/health` **首次探测即 200**，启动自愈随即补数据（`fund_info_sync` 写入 28086 条）。
> 上面 4.1 / 4.2 的两条 ⚠️ 正是这次实测中**真的卡住过**的位置，不是预防性提醒。

### 第 5 步 · 准备业务数据

数据（现网实测 **60 表 / 11.4 GB**，排除归档表后 **56 表 / 9.7 GB**）**不随仓库分发**。
按你的场景二选一：

**情况 1 · 原来的机器还在（换机 / 数据搬家）→ 整库迁移（推荐）**

```bat
:: 源机：导出（默认排除 4 张 *_bak_* 归档表，省 1.6 GB）
scripts\migrate-machine.bat dump

:: 目标机：把上一步的 .sql 拷过来，然后导入（会先要求确认）
scripts\migrate-machine.bat load "D:\path\to\adata_migrate_20261010_074352.sql"
```

> ⭐ 迁移**不需要**再跑 `schema.sql` / `seed_*` —— dump 里已经含全部表结构与配置，导入完直接启动。
> 为什么必须迁移而不能重新采集：本库有几张大表**没有采集器**（`table_meta.writers=[]`），
> 其中最重的 `stock_market_daily_ex`（1690 万行）重新采集要自己补采集器、并长跑数小时以上。
> （**2026-10-17 勘误**：原文「重新采集永远拿不回来」已被实测推翻 —— 该表是标准不复权日K，
> 数据可重建，仅缺落表采集器，见 [git-only-restore.md](scripts/git-only-restore.md) §4.3）
> 实测体积 dump ≈5.3 GB（gzip 后 ≈1.1 GB）、导入 10~30 分钟。
> 详见 **[scripts/data-bootstrap.md](scripts/data-bootstrap.md)「路径 0」**。

> 💾 **别让「这台机器」成为唯一副本**：`backend/scripts/backup_assets.py` 只备
> **高成本 / 不可复原**的约 3.4 GB（实测 gz 后 **545.6 MB**，其中人工资产+配置仅 ≈4 MB）。冻结表
> **备一次 + 每周 `verify`** 即可，不必每天全备。落点纪律（同一块盘不算异地）与
> GitHub 的硬限制见 **[scripts/data-bootstrap.md](scripts/data-bootstrap.md) §7**。
>
> 🧭 **「原机没了到底能恢复到什么程度」**——全库按可再生性分五类、每类配哪种手段、
> 耗时总账、人工资产（仅 **9.4 KB**）的 seed 方案，见
> **[scripts/data-restore-plan.md](scripts/data-restore-plan.md)**。
>
> 🆘 **「什么都没有了，只有 GitHub 上的仓库」**——本机损毁、备份也没了时的兜底路径：
> 新机器 clone → 30 分钟服务可用 → 1~2 天数据补到约 99.99%，逐条命令与耗时总账见
> **[scripts/git-only-restore.md](scripts/git-only-restore.md)**。

**情况 2 · 全新环境 / 给对方干净副本 → 从上游重新采集**

完整清单、依赖顺序、可回补性分类见 **[scripts/data-bootstrap.md](scripts/data-bootstrap.md)**。
最省事的开端：直接把服务起起来 —— 启动收敛会自动补一部分（见该文档「路径 A」）。

### 第 6 步 · 安装前端依赖

```bat
cd frontend
npm install
cd ..
```

### 第 7 步 · 启动

```bat
start-dev.bat
```

- 前端 http://127.0.0.1:5173 （Vite dev server）
- 后端 http://127.0.0.1:8000 （FastAPI，接口文档 `/docs`）

> 🔧 **端口被占时不用改代码**，设环境变量即可（不设仍是默认的 8000 / 5173）：
>
> ```bat
> set INFO_DATA_API_PORT=8010
> set INFO_DATA_WEB_PORT=5180
> start-dev.bat
> ```
>
> - 一键启动（`start-dev.bat`）只认**系统环境变量**——它是 cmd 脚本，**不读** `frontend/.env.local`。
> - 直接 `npm run dev` 时才会读 `frontend/.env.local`（模板见 `frontend/.env.example`，该文件不入库）。
> - 两边用的是**同一组变量名**，不会各自漂移。前端设了 `strictPort`：端口被占会直接报错，
>   而不是悄悄换到 5174 却仍提示 5173。

---

## 常见问题（部署排障速查）

> 这几条是**实测踩过的**，按「报什么错 → 为什么 → 怎么修」列出。

| 现象 | 根因 | 处置 |
|---|---|---|
| `mysql: command not found` / 『mysql 不是内部或外部命令』 | MySQL 已安装但 **bin 目录不在 PATH**（Windows 安装程序默认不写） | 用全路径 `"C:\Program Files\MySQL\MySQL Server 8.2\bin\mysql.exe" -u root -p < scripts\schema.sql`，或把 `...\MySQL Server 8.2\bin` 加进 PATH |
| 跑 `seed_*.py` 报 `ModuleNotFoundError: No module named 'pymysql'` | 用了 **PATH 上的裸 python**（没装项目依赖），多见于「建了 venv 但没激活」 | 用第 3 步那个解释器：`cd backend` 后 `.venv\Scripts\python.exe scripts\seed_task_config.py --apply` |
| `Application startup failed` + `ModuleNotFoundError: No module named 'fastapi'` | 依赖没装，或启动用了**没有项目依赖的解释器** | 用第 2 步选定的解释器重装 `backend\requirements.txt`；确认脚本打印的 `[OK] Python = ...` 正是你要的那个 |
| `Application startup failed` + `Table 'adata.task_config' doesn't exist` | **漏做第 4 步**（数据库未初始化） | 导入 `scripts\schema.sql` 并跑那 6 个 `seed_*` 脚本 |
| `Table '<某张表>' doesn't exist`（只缺个别表） | `schema.sql` 是较早版本导出的 | 重新 `python scripts\dump_schema.py` 导出后再导入（幂等，可重复） |
| 配了 `.env` 但后端读不到变量 | **开发模式不读 `.env` 文件** | 用 `setx` / `set` 设系统环境变量，见「环境变量」一节 |
| 一键启动提示找不到 Python | 四级回退（env → `.python-path` → `.venv` → PATH）全未命中 | 建 `backend\.venv`，或设 `INFO_DATA_PYTHON`，或建 `.python-path` 文件 |
| `1267 Illegal mix of collations` | 自建表继承了库默认排序规则 | 建临时表时显式 `COLLATE=utf8mb4_0900_ai_ci`（`schema.sql` 已逐表声明，**勿手删**） |
| 采集反复失败 / 拉不到数据 | 上游风控，或本地代理干扰 | 设 `NO_PROXY=*`；**不要并发触发同一采集器**；见 [data-bootstrap.md](scripts/data-bootstrap.md) §4 |
| 数据量明显偏少 | 属「历史补不齐」的表 | 上游接口限制，非故障，见 [data-bootstrap.md](scripts/data-bootstrap.md) §2 的 B 类 |
| 页面能打开但表格是空的 | 连到了空库 / 别的库 | 核对 `INFO_DATA_DB_NAME`；看 `GET /api/health` 与数据中心页实际库名 |

> 🔎 自愈能力：服务起来后「启动即收敛」会自动巡检 DQ 结果并补「数据缺失型」问题。
> 详情见 [docs/design/自愈能力设计_2026-10-08.md](docs/design/自愈能力设计_2026-10-08.md)。

---

## 日常启动（环境已就绪）

```bat
start-dev.bat          :: 启动后端 :8000 + 前端 :5173（已在跑的会自动跳过；端口可用 INFO_DATA_API_PORT / INFO_DATA_WEB_PORT 覆盖）
```

单独启动后端（开机自启也用这个）：

```bat
backend\scripts\start_backend.bat
:: 配 Windows 开机自启：把 backend\scripts\start_backend_hidden.vbs
:: 复制到 shell:startup 目录（或放它的快捷方式）
```

### Docker 模式

见 [scripts/docker-deploy-guide.md](scripts/docker-deploy-guide.md)

```bat
docker-start.bat             :: 模式 A：backend+frontend，MySQL 直连宿主机
docker-start.bat full        :: 模式 B：mysql+backend+frontend 全容器化
```

- 前端 http://127.0.0.1:8080
- 后端 http://127.0.0.1:18000/api/health

> Docker 模式是**读 `.env` 文件**的（compose 的 `${VAR}` 插值），
> 所以这条路才需要「复制 `.env.example` 为 `.env`」。见下节。

---

## 环境变量

### ⚠️ 先弄清机制：开发模式**不读 `.env` 文件**

这是一个容易踩的坑。全仓库代码**没有 `load_dotenv`**，后端只读**进程环境变量**。
真正会用到 `.env` 文件的只有两处：

| 场景 | 是否读 `.env` | 说明 |
|---|---|---|
| 开发模式（`start-dev.bat` / `uvicorn`） | ❌ | 只读**系统环境变量**。照抄 `.env` 是**无效**的 |
| Docker 模式（`docker compose`） | ✅ | compose 自动读根目录 `.env` 做 `${VAR}` 插值 |
| 告警通知（`notifier.py`） | ✅ | 额外读 `backend\.env.local` |

开发模式下设置变量的正确姿势：

```bat
:: 临时（当前窗口）
set INFO_DATA_DB_PASSWORD=你的密码
start-dev.bat

:: 永久（推荐）
setx INFO_DATA_DB_PASSWORD "你的密码"
```

### 变量清单

| 变量 | 默认值 | 必填 | 说明 |
|---|---|---|---|
| `INFO_DATA_DB_HOST` | `127.0.0.1` | | 数据库地址 |
| `INFO_DATA_DB_PORT` | `3306` | | 端口 |
| `INFO_DATA_DB_USER` | `root` | | 用户 |
| `INFO_DATA_DB_PASSWORD` | `root` | | 口令（**不要写进任何入库文件**） |
| `INFO_DATA_DB_NAME` | `adata` | | 库名 |
| `INFO_DATA_DB_CHARSET` | `utf8mb4` | | 字符集 |
| `INFO_DATA_DB_POOL` | `1` | | 设 `0` 退回「每次新建连接」旧行为（排查用） |
| `ARK_API_KEY` | 空 | 可选 | 火山方舟（豆包）Key；不配则 AI 分析返回占位结果 |
| `ARK_MODEL_ID` | `doubao-1-5-pro-32k-250115` | 可选 | 模型 ID |
| `TUSHARE_TOKEN` | 空 | 可选 | Tushare 备源 token（不装 `tushare` 包也能跑，它是函数内惰性导入） |
| `NO_PROXY` | 空 | 可选 | 建议 `*` 避免本地代理干扰采集 |
| `INFO_DATA_PYTHON` | 空 | 可选 | 启动脚本用的 Python 解释器路径 |
| `INFO_DATA_API_PORT` | `8000` | 可选 | 后端端口：`start-dev.bat` 用它起后端并检测占用，`vite` 代理目标也由它拼出 |
| `INFO_DATA_WEB_PORT` | `5173` | 可选 | 前端 dev server 端口（被占时改这里，不用改 `vite.config.ts`） |
| `INFO_DATA_WEB_HOST` | `127.0.0.1` | 可选 | 前端监听地址；需要局域网内其他设备访问时设 `0.0.0.0` |
| `INFO_DATA_API_TARGET` | 空 | 可选 | 完整后端地址（如 `http://192.168.1.9:8000`），**优先于** `API_PORT`；指向远程 / 容器后端时用 |
| `INFO_DATA_NOTIFY_PROVIDER` | 空 | 可选 | 告警通道（见下） |

### 告警通知（可选）

支持 4 个通道，凭证放 `backend\.env.local`（已 gitignore，改完不用重启进程）：

| 通道 | 变量 |
|---|---|
| 企业微信机器人 | `INFO_DATA_WECOM_WEBHOOK` |
| Server 酱 | `INFO_DATA_SERVERCHAN_KEY` |
| PushPlus | `INFO_DATA_PUSHPLUS_TOKEN` |
| WxPusher | `INFO_DATA_WXPUSHER_TOKEN` + `INFO_DATA_WXPUSHER_UID` |

配合 `INFO_DATA_NOTIFY_PROVIDER=<通道名>` 启用。告警是**旁路**：不配不影响启动，
配置错误也不会进重试死循环。

| 调节项 | 默认 | 说明 |
|---|---|---|
| `INFO_DATA_NOTIFY_ON` | `problems` | `problems`（仅异常时推）/ `always`（每轮都推）/ `never`（关闭） |
| `INFO_DATA_NOTIFY_TIMEOUT` | `10` | 单次推送超时（秒） |

## 数据采集

数据通过作业监控触发（`task_config` 表配置，APScheduler 调度）：

| 任务 | 说明 | 数据源 |
|---|---|---|
| 股票日线增量采集 | 每日收盘后增量更新 K 线 | 东财 → 腾讯 → 新浪 → Tushare |
| 概念板块 | 概念列表 + 成分股 | 同花顺 |
| 投资日历 | 财经事件日历 | 巨潮/东财 |
| 资讯采集 | 财联社 + 东财快讯 | 双源 |
| AI 概念分析 | 事件 → 相关概念（评分 1-10） | 豆包（方舟） |

> 📖 **新环境首次拉数据**请看 [scripts/data-bootstrap.md](scripts/data-bootstrap.md)：
> 内含「表 ↔ 采集任务」完整映射、可回补性分类（哪些能补历史、哪些只能增量）、
> 执行依赖顺序、触发方式（界面 / HTTP 接口）与校验方法。
>
> 触发单个任务：
> `POST /api/jobs/tasks/<任务名>/trigger`（异步，结果在作业监控页看）

## 目录结构

```
InvestBuddy/
├── backend/                 # FastAPI 后端
│   ├── app/
│   │   ├── api/             # API 模块（market/concept/calendar/news/analysis/jobs/ai/db/dq/sql/prefs）
│   │   ├── collectors/      # 数据采集器（行情/概念/日历/资讯/宏观/债券/基金…）
│   │   ├── analysis/        # 分析服务（股息率/概念排名/货币流动性/AI 概念分析）
│   │   ├── tasks/           # 任务注册与调度（APScheduler）
│   │   ├── selfheal.py      # 启动自愈「启动即收敛」
│   │   ├── notifier.py      # 告警通道（4 通道，可选）
│   │   ├── db.py            # 数据库配置（环境变量驱动 + 连接复用）
│   │   └── main.py          # 应用入口
│   ├── scripts/             # 建表 / 播种 / 维护脚本（init_* / seed_* / dump_schema）
│   ├── tests/
│   ├── Dockerfile
│   └── requirements.txt
├── frontend/                # Vue3 前端
│   ├── src/views/           # 栏目视图
│   ├── src/components/      # KLineChart 等组件
│   ├── Dockerfile
│   └── nginx.conf           # 生产环境静态托管 + /api 反代
├── scripts/
│   ├── schema.sql           # ⭐ 全库建表语句（56 张表，幂等，不含数据）
│   ├── data-bootstrap.md    # ⭐ 业务数据准备指南（新环境 / 换机必读）
│   ├── data-restore-plan.md # ⭐ 整体还原方案（五类可再生性 + 手段 + 耗时总账）
│   ├── git-only-restore.md  # ⭐ 纯代码仓库重建（只剩 git 时的兜底路径，含逐条命令）
│   ├── migrate-machine.bat  # ⭐ 换机整库迁移（dump 源机 / load 目标机）
│   ├── docker-deploy-guide.md
│   ├── migrate-local-to-docker.bat
│   └── backup-docker-mysql.bat
├── docs/                    # 设计与复盘文档
│   ├── README.md            # ⭐ 文档索引（规范 / 机制 / 专项 / 复盘 分类）
│   └── design/              # 设计规范与各专项方案
├── docker-compose.yml       # 容器编排
├── docker-start.bat         # Docker 一键启动
├── start-dev.bat            # 开发模式一键启动
├── .gitattributes           # 行尾规范（*.bat/*.vbs 强制 CRLF）
├── .python-path             # 本机私有：Python 解释器路径（gitignore，不入库）
└── .env.example             # 环境变量模板
```

> 📚 **要通读设计文档，从 [docs/README.md](docs/README.md) 进**（含版本与状态标注）。

### 数据库结构如何维护

`schema.sql` 是**从现网库导出**的，不是手写的。改了表结构后重新导出即可：

```bat
cd backend
python scripts\dump_schema.py          :: 默认输出到 ../scripts/schema.sql
```

> 该脚本会逐表保留 `DEFAULT CHARSET / COLLATE`。**请勿手工删掉这些声明** ——
> 本库默认排序规则是 `utf8mb4_unicode_ci`，而多数业务表是 `utf8mb4_0900_ai_ci`，
> 省略声明会让新表继承库默认，与业务表 JOIN 时报 `1267 Illegal mix of collations`。

## 数据源与风控策略

- **四级降级**：东财被风控时自动降级腾讯 → 新浪 → Tushare，首源只试 1 次快速切换
- **硬超时兜底**：每只股票请求线程化包装，最多 25 秒，杜绝挂死
- **北交所默认排除**：4/8/92 开头（腾讯/新浪不支持、Tushare 需 token），可 `include_bj` 参数开启
- **退市股过滤**：名称含退/PT 的 151 只 SQL 排除 + 数据超 2 年软跳过
- **随机延时**：0.3-1.2s 错峰，降低风控概率

## 安全说明

- 所有密钥（DB 密码 / API Key）只从环境变量读取；`.env`、`.env.*`、`.env.local`、`.python-path` 均**不入 Git**
- 曾发现 `doubaoai.py` 硬编码 Key 泄露 → 已重构为 `ARK_API_KEY` 环境变量方案
- **2026-10-09 又发现一处**：`scripts/migrate-local-to-docker.bat` 里硬编码了 Docker MySQL 明文口令
  → 已改为「环境变量 → `.env`」读取，脚本内不再出现任何口令
- 🔴 **注意历史**：上面那处口令**已随提交进入 Git 历史**。若它与你在用的容器口令相同，
  建议改掉容器口令（`docker compose down -v` 后重设 `MYSQL_ROOT_PASSWORD` 再起）
- `.gitignore` 已覆盖 `_scratch/`（本地验收脚本与截图）与 `.workbuddy/`（会话数据），二者不入库

## License

个人学习项目，仅供研究使用。数据版权归各数据源所有，请勿用于商业用途。
