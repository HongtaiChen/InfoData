# 纯代码仓库重建方案（git 单点可移植）

> **本文回答一个问题**：本机彻底坏了、硬盘也没了、手里只有 GitHub 上的 `HongtaiChen/InfoData`，
> 在新机器上 clone 下来，**只靠代码 + 时间**，能把服务恢复到什么程度？要多久？
>
> 配套文档：
> - `scripts/data-restore-plan.md` —— 全库按可再生性五分类（**有备份时**的最优路径）
> - `scripts/data-bootstrap.md` —— 逐表回补操作手册
> - `backend/scripts/backup_assets.py` —— 冷备工具（**有备份文件时**用这个）
>
> 版本：2026-10-17 · 口径：所有行数均为 `SELECT COUNT(*)` 实测；标注「推算」的才是估算。

---

## 0. 结论先行

**这套方案能做到**：新机器上 **30 分钟内服务跑起来**（能登录、能看页面），
主线行情数据在 **1~2 天机器时间 + 约一周自然日** 内补齐。

恢复程度（分母 = 全库 **40,095,242 行**，2026-10-17 实测）：

| 情形 | 可恢复行数 | 覆盖率 |
|---|---|---|
| **本次加固前**（两张大表无写入方） | 20,693,191 | 53.8% |
| **现在**（已补 `ex` + `hold_by_fund` 采集器） | **38,075,532** | **94.96%** |
| 再买付费源补资金流 | 38,823,551 | 96.83% |

> ✅ **2026-10-17 已落地两项加固**：`stock_market_daily_ex`（1690 万行）与
> `stock_hold_by_fund`（11.3 万行）都补上了采集器并实际跑通。
> `table_meta` 里「无写入方的表」从 **11 张降到 9 张**，
> 克隆后重建的覆盖率因此从 **53.8% 提升到 94.96%**。

**剩下 5.04% 不是工程问题，而是源侧边界**（共 2,019,710 行）：

| 缺口 | 规模 | 为什么不可重建 |
|---|---|---|
| `stock_market_daily_ex` 的退市/长期停牌股 | 1,012,819 行 | 新浪对这类票**任何区间都返回空 JSON**（实测 002002/002013/000150/002118 全部如此） |
| `stock_market_daily_ex` 的老北交所 | 249,999 行 | 新浪不支持 `bj430/83x/87x` 前缀（实测 `JSONDecodeError`）；`920` 段支持 |
| `stock_capital_flow` 2025-09-19 之前 | 748,019 行 | 东财**服务端硬限最近 120 个交易日**，`lmt=0`/`lmt=1000000`/`beg=20230101` 三种写法返回**完全相同的 120 条**（独立出口复核） |
| 两张 DQ 审计明细 | 8,873 行 | 产出脚本已不在仓库 |

### ⚠️ 这个 94.96% 有个前提：**必须按清单传参**（2026-10-17 补测）

上面 94.96% 是「每张有写入方的表都按其全史参数补齐」的结果。
若**直接跑默认任务、不传参**，实测只有约 **93.75%** —— 差的那 1.21% 全部来自一张表：

| 表 | 总行数 | 默认参数能补 | 差额 | 根因 |
|---|---|---|---|---|
| `ths_concept_market` | 580,681 行（2018-04-12 ~ 2026-10-16） | 仅最近 **400 天** | **483,470 行 = 1.21%** | 采集器 `FALLBACK_DAYS=400`：空库时水位线为空 → 走 `now - 400 天` 兜底 |

**这不是无解**：`run_concept_market_sync` 支持 `params={"start_date": "20180101"}`
（`app/tasks/run.py:217` → `ConceptMarketSyncCollector(start_date=...)`），
且**已实测源侧确实提供 2018 年数据**（`stock_board_concept_index_ths(symbol='人工智能',
start_date='20180101', end_date='20181231')` 返回 243 行）。

> 由此得出一条**通用判据**，比「表有没有 writers」可靠得多：
> **「有没有采集器」≠「空库能不能补回全史」。** 凡是水位线续跑型采集器，
> 空库时水位线为空，必然落到 fallback 窗口 —— 必须逐表确认 fallback 是
> `list_date`（如 `stock_daily_incr`，2026-09-24 已修）还是「近 N 天」（如本表）。


---

## 1. 仓库里到底带了什么（实测清点）

| 项 | 实测值 | 用途 |
|---|---|---|
| 仓库体积 | **8.92 MiB**（234 个跟踪文件） | clone 秒级完成 |
| `scripts/schema.sql` | **56 张表** | 空库建表，零报错 |
| 生产库实际表数 | **60 张** | 差的 4 张全是 `*_bak_日期` / `*_fix_backup_日期` 归档表，**均属可丢弃**，功能无缺失 |
| `seed_*.py` | **6 个** | `table_meta` 59 行 / `task_config` 49 行 / `dq_rules` 209 行 / 索引 / 风格表 / 恢复任务 |
| 已启用的定时任务 | **51 个**（含 2026-10-17 新增 2 个采集任务） | 服务起来后自动滚动更新 |
| 有写入方的表 | **49 张**（`writers` 非空） | 这些能自己长回来 |
| 无写入方的表 | **9 张** | 见 §4 逐个交代（其中 3 张跑 seed 即得、3 张可丢弃） |

**好消息：默认情况下新机器「零配置」就能跑。** `backend/app/db.py` 的内置默认值就是
`127.0.0.1:3306 / root / root / adata`，只要新机器的 MySQL 密码也是 `root`，什么都不用配。

只有在**改了口令或端口**时才需要设环境变量（注意：是**环境变量**，不是 `.env` 文件，见 §S5）。
两个 token 是**可选**的，不填就自动跳过该备源：

| 变量 | 缺失后果 |
|---|---|
| `TUSHARE_TOKEN` | 少一个备源，主源 AKShare 照常工作 |
| `ARK_API_KEY` | AI 概念分析功能不可用，其余不受影响 |

> ⚠️ **`.env` 不在仓库里**（`.gitignore` 明确排除，只留 `.env.example` 作模板），
> 而 `.env.example` 里的 5 个数据库参数**在本地开发模式下不会被读取**（仅 Docker 场景生效）。
> 所以整条链路上**唯一需要你记住的信息只有：数据库口令**。

---

## 2. 新机器操作步骤（照抄即可）

> ⚡ **2026-10-17 起：结构 + 配置 + 数据已能用一条命令跑完**
>
> ```bash
> git clone https://github.com/HongtaiChen/InfoData.git && cd InfoData
> python -m venv backend/.venv && backend/.venv/Scripts/pip install -r backend/requirements.txt
> cd backend
> python scripts/preflight.py                  # 先体检：MySQL 版本/时区/表完整性
> python scripts/restore.py --list             # 看计划（不动任何数据）
> python scripts/restore.py --yes              # 全量恢复（建表→配置→数据→核对→报告）
> ```
>
> 只想要结构 + 配置、数据稍后再说：`python scripts/restore.py --yes --no-data`。
> 只重建某一段：`--data-only --stages core,daily`。
>
> 为什么要有这个脚本：下面 S3/S4/S6 的手工步骤里至少 3 处会**静默失败**
> （`--apply` 漏写、概念行情只补 400 天、增量采集器不传 `full_sweep`）。
> **手工步骤保留在下面** —— 它们是原理说明，也是脚本失灵时的兜底。

### S0 · 装环境（约 10 分钟，一次性）

| 依赖 | 版本 | 说明 |
|---|---|---|
| Python | 3.13（3.11+ 均可） | |
| Node.js | 22.x | 前端 |
| MySQL | **8.0+（硬要求）** | schema 用到 `utf8mb4_0900_ai_ci`，MariaDB / 5.7 建表即失败 |

### S1 · 拉代码（秒级）

```bash
git clone https://github.com/HongtaiChen/InfoData.git
cd InfoData
```

### S2 · 建虚拟环境装依赖（实测 44 秒）

> 虚拟环境固定建在 **`backend/.venv`** —— 启动脚本按「环境变量 → `.python-path` → `backend/.venv` → PATH」
> 四级回退查找解释器，放别处会导致一键启动找不到 Python。

```bash
cd backend
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
```

### S3 · 建库 + 建表（秒级）

```bash
# mysql 客户端可能不在 PATH，Windows 下用全路径，例如：
# "C:/Program Files/MySQL/MySQL Server 8.2/bin/mysql.exe"
mysql -uroot -proot -e "CREATE DATABASE IF NOT EXISTS adata DEFAULT CHARSET utf8mb4"
mysql -uroot -proot adata < ../scripts/schema.sql
```

### S4 · 灌配置（秒级，6 个脚本按序跑）

> ⚠️ **`seed_task_config.py` 和 `seed_indexes.py` 默认是「只读预览」，不加 `--apply` 一行都不写**，
> 而且它们照样打印成功信息 —— 这是最容易误判成「已经灌好了」的地方。
> 跑完用 §S5 的行数判据核对。

```bash
# 当前目录：backend
.venv/Scripts/python.exe scripts/seed_table_meta.py
.venv/Scripts/python.exe scripts/seed_task_config.py --apply   # ← --apply 不可省
.venv/Scripts/python.exe scripts/seed_dq_rules.py
.venv/Scripts/python.exe scripts/seed_indexes.py --apply       # ← --apply 不可省
.venv/Scripts/python.exe scripts/seed_market_style.py
.venv/Scripts/python.exe scripts/seed_recovered_tasks.py
```

> 顺序有讲究：后两个脚本也会向 `task_config` 补写任务（`market_style_sync` 等），
> 必须排在 `seed_task_config.py` **之后**，否则会被主清单覆盖掉。

### S5 · 配数据库连接并启动

> ⚠️ **一个容易白忙的坑**：后端读的是**进程环境变量**（`os.getenv`），
> **本地开发模式不读 `.env` 文件**（`.env` 只在 Docker Compose 场景被 `${VAR}` 插值读取）。
> 所以「复制 `.env.example` 改成 `.env`」在本地开发下**不生效**。

三条路，按情况选：

| 情况 | 做法 |
|---|---|
| **沿用默认 `root/root@127.0.0.1:3306/adata`** | **什么都不用配**，直接启动（`db.py` 内置默认值） |
| 口令/端口不同（永久生效） | `setx INFO_DATA_DB_PASSWORD "你的口令"`，然后**重开终端** |
| 只对本次启动生效 | `set INFO_DATA_DB_PASSWORD=你的口令` 后在同窗口启动 |
| 用 Docker 部署 | 这时 `.env` 才生效：`cp .env.example .env` 再 `docker compose up -d` |

```bat
:: 回到仓库根目录（seed 是在 backend 里跑的）
cd ..
start-dev.bat
```

**验证「能用」的判据**（不要只看进程有没有起来）：

| 检查项 | 期望 |
|---|---|
| `http://127.0.0.1:8000/api/health` | 返回 200 |
| `SELECT COUNT(*) FROM task_config` | **49** |
| `SELECT COUNT(*) FROM dq_rules` | **209** |
| `SELECT COUNT(*) FROM table_meta` | **59** |

到这里**服务已经可用了**，但库里业务数据几乎是空的。接下来才是花时间的部分。

### S6 · 让数据长回来（见下节）

---

## 3. 数据恢复的三个梯队

### 第一梯队 · 自动（不用管，等服务自己跑）

启动后调度器按 cron 自动执行 47 个任务；`startup_converge` 会做启动自愈。
**实测**：干净克隆启动后，`fund_info_sync` 自动补了 28,086 条基金档案。

这一梯队解决的是「从今天起的数据不再丢」，**不解决历史**。

### 第二梯队 · 手工触发一次全量（几小时）

按依赖顺序，**顺序不能乱**（后者依赖前者的票池）：

```bash
cd backend

# 1. 股票名册 —— 后面所有行情采集的票池，必须先跑
.venv/Scripts/python.exe -m app.tasks.run stock_info_sync

# 2. 交易日历
.venv/Scripts/python.exe -m app.tasks.run trade_calendar_sync

# 3. 日线全史（关键一步，见下方说明）
.venv/Scripts/python.exe -m app.tasks.run daily_backfill

# 4. 归档扩展行情（不复权原始价，约 45~55 分钟 / 1690 万行）
.venv/Scripts/python.exe -m app.tasks.run stock_market_daily_ex_sync

# 5. 基金持仓（58 个报告期，约 3 分钟）
.venv/Scripts/python.exe -m app.tasks.run fund_hold_sync
```

> `python -m app.tasks.run --list` 可列出全部已注册任务名。
> 注意 CLI **不能覆盖参数**：参数一律读 `task_config.params`，要改参数就改库里的那一行。
> 第 4、5 步在**空库**下会自动走「从上市日/最早报告期」的全量路径，无需额外参数。

**关于第 3 步的一个重要机制**（`app/tasks/run.py:628-631`）：

`daily_backfill` 默认 `source=dq_gap`（读缺口表补洞），但**空库时缺口表本身就是空的**
（缺口是靠 `LAG()` 在已有行之间算出来的，无行则无缺口），若不特殊处理会永远 `return 0`。
所以采集器内置了自动降级：**日线空表 + 无 open 缺口 ⇒ 自动改走 `source=all_stocks`**，
按每只股票的 `list_date` 生成「上市日 → 今天」的完整区间，用 `NOT EXISTS` 挑未覆盖的票，
**幂等、可分批、断了重跑会跳过已完成的**。

> 这一点已在 2026-10-17 用空库实测验证：5 只名册生成 4 个区间（B 股被正确排除）、
> 补 1 行后区间自动收缩到 3 个、日志出现自动切换记录。

**耗时**（推算，基于「日线增量单次实测 1,673s」外推）：

| 阶段 | 数据量 | 耗时 |
|---|---|---|
| 名册 + 日历 | 约 6,000 行 | 3~10 分钟 |
| 日线全史 | **1,858 万行** | **4~8 小时** |

**中途看进度**（不要看日志末行，看数据侧）：

```sql
SELECT COUNT(*) FROM stock_market_daily;              -- 目标 ≥ 18,500,000
SELECT COUNT(DISTINCT stock_code) FROM stock_market_daily;  -- 目标 ≥ 5,300
```

### 第三梯队 · 跨天轮转（约一周自然日）

| 任务 | 特征 | 耗时 |
|---|---|---|
| `financial_abstract_sync` | 800 只/轮，需 8 轮 | 实测 1,968s/轮 → **跨 6~8 天** |
| `stock_shares_sync` | 1,000 只/轮，需 6 轮 | 同上量级 |
| 其余参考类（债券 / 基金 / 指数 / 期货 / 宏观） | 各自 `*_sync` 全量 | 合计 3~5 小时 |

这些**不需要守着**，让调度器按 cron 自己跑即可，只是要等够天数。

---

## 4. 无写入方的表逐个交代（已从 11 张降到 9 张）

补完两个采集器后，`table_meta.writers = []` 只剩 **9 张、合计 1.1 万行**（原来 1,702 万行）。
按性质分：

### 4.1 跑 seed 脚本就能恢复（3 张，319 行）✅

| 表 | 行数 | 恢复方式 |
|---|---|---|
| `table_meta` | 59 | `seed_table_meta.py` |
| `task_config` | 51 | `seed_task_config.py --apply` |
| `dq_rules` | 209 | `seed_dq_rules.py` |

**这三张在 S4 已经恢复了**，不构成缺口。

### 4.2 可以直接丢弃（3 张，6,687 行）🗑️

| 表 | 行数 | 说明 |
|---|---|---|
| `task_runs` | 1,346 | 运行日志，自产 |
| `dq_pct_fix_backup_20260922` | 207 | 一次性修复前的备份 |
| `stock_company_profile_bak_20260905` | 5,134 | 备份表，原始档案在巨潮可重采 |

### 4.3 已补采集器、但仍有源侧硬缺口（2 张）⚠️

| 表 | 现有行数 | 状态 |
|---|---|---|
| `stock_market_daily_ex` | **18,465,512** | ✅ 采集器已落地并跑通（19 分钟补入 157 万行）；但退市股 101 万行 + 老北交所 25 万行**源侧无数据** |
| `stock_hold_by_fund` | **179,621** | ✅ 采集器已落地并跑通（3 分钟补入 6.6 万行 / 20 期）；已从 38 期扩到 **58 期（2012Q1 起）** |

### 4.4 真·脚本丢失（2 张，8,873 行）🔴

| 表 | 行数 | 补法 |
|---|---|---|
| `dq_close_nonpositive_detail` | 4,152 | 补脚本，或纳入冷备（合计仅 3.8 MB） |
| `dq_pct_anomaly_detail` | 4,721 | 同上 |

### 4.5 人工资产（1 张，15 行）✍️

| 表 | 行数 | 说明 |
|---|---|---|
| `user_prefs` | 15 | 表格排序偏好，无规则可推，但**价值极低**（重设一遍即可） |

> 另外两处人工资产不在这 9 张里，因为它挂在不带 `writers` 的列上而不是表上：
> `stock_info_ex.is_gxlstock`（722 只高股息标记）、`stock_code_mapping` 的 7 条人工映射。
> 这两处的处置见 `data-restore-plan.md` §3 —— 已确认「按阈值重算」可行，
> 高股息不再属于必须靠备份的研究结论。

---

## 5. 耗时总账

| 里程碑 | 累计耗时 | 说明 |
|---|---|---|
| **服务能访问** | **约 30 分钟** | clone + 装依赖 + 建表 + seed + 启动（含人工装 MySQL/Node） |
| **核心行情完整** | **+4~8 小时** | 日线 1,858 万行，占全库约 48% |
| **参考类完整** | **+3~5 小时** | 债券 / 基金 / 指数 / 期货 / 宏观 |
| **覆盖率 94.96%** | **+6~8 个自然日** | 财务摘要、股本轮转按天跑（这两项才是长尾） |
| **覆盖率 96.83%** | 需付费源 | 资金流 74.8 万行，Tushare `moneyflow_dc`（5000 积分） |
| **覆盖率 100%** | **不可能** | 退市股 + 老北交所 126 万行，源侧永久无数据 |

其中两张新表的实测耗时（2026-10-17 真机跑出，非推算）：

| 采集器 | 数据量 | 实测耗时 |
|---|---|---|
| `stock_market_daily_ex_sync`（增量补 13 个月） | 5611 只 / **157 万行** | **19 分钟**（4 线程） |
| `fund_hold_sync`（58 个报告期全量） | 58 期 / **6.6 万行** | **3 分钟** |

> 空库全史重建 `ex` 表按同一吞吐外推约 **45~55 分钟**（1690 万行），
> 不是原先估的 8~16 小时 —— 那是按「逐票单线程」的旧口径拍的，实测 4 线程快一个数量级。

对比另外两条路径（同一份数据）：

| 路径 | 耗时 | 保真度 | 前提 |
|---|---|---|---|
| 整库迁移（`migrate-machine.bat`） | **0.5~1 小时** | 100% | 原机还在 |
| 冷备恢复（`backup_assets.py`） | **1~2 小时** | core 覆盖的表 100% | 有 545.6 MB 备份文件 |
| **本文：纯 git 重建** | **30 分钟起 + 1~2 天** | **94.96%** | **只要有网和仓库** |

**所以三者的关系不是替代，是兜底**：git 重建是「什么都没了」时唯一还站着的那条路。

---

## 6. 什么时候用哪条路

```
原机还能开机？
├─ 能 → 整库迁移 migrate-machine.bat（0.5~1 小时，100%）
└─ 不能
   ├─ 有备份文件（移动硬盘/网盘）？
   │  ├─ 有 → backup_assets.py restore（1~2 小时，接近 100%）
   │  └─ 没有 → 【本文】git clone 重建（30 分钟起 + 1~2 天，94.96%）
```

---

## 7. 加固执行情况（2026-10-17 已落地）

| 优先级 | 动作 | 状态 | 实际结果 |
|---|---|---|---|
| **🔴 P0** | 补 `stock_market_daily_ex` 采集器 | ✅ **已完成** | 新增 `stock_market_daily_ex_sync`；已实跑补入 **1,574,199 行**，耗时 19 分钟。行数口径为 `COUNT(*)` 差分：**16,891,313 → 18,465,512 行**，`MAX(trade_date)` 从 2025-09-19 推进到 **2026-10-16**，覆盖 6,193 只。覆盖率 53.8% → **94.96%** |
| **🟠 P1** | 补 `stock_hold_by_fund` 采集器 | ✅ **已完成** | 新增 `fund_hold_sync`；已实跑 **58 期全量 0 失败**，表从 113,147 行/38 期扩到 **179,621 行/58 期**（覆盖 2012-03-31 ~ 2026-06-30，向前补 2012~2015 共 16 期，6,037 只） |
| **🟡 P2** | 写 `seed_manual_assets.py`（22 条人工记录） | ⏸️ **未做** | 价值已下降：按 R1「阈值重算」决策后只剩 2.4 KB，且不影响覆盖率 |
| ⚪ P3 | 两张 DQ 审计明细表（8,873 行） | ⏸️ 未做 | 产出脚本已丢失，建议直接纳入冷备（3.8 MB） |

**两项都已登记进调度**，此后不会再停更：

| 任务 | cron | params |
|---|---|---|
| `stock_market_daily_ex_sync` | `50 19 * * 0-4`（工作日 19:50，排在日线 19:20 之后） | `{"source":"auto","max_codes":0}` |
| `fund_hold_sync` | `10 20 5 * *`（每月 5 日 20:10） | `{"start_period":"20120331","overwrite":false}` |

> **为什么 `fund_hold_sync` 默认 `overwrite=false`**：基金季报会被持续修订 ——
> 同一个 2025-06-30，本地存的是披露早期快照（2,943 行 / 300750 基金 1,752 家），
> 接口现在给完整版（5,216 行 / 2,598 家）。若用 UPSERT，「重建历史」会把当年那版
> 静默改成今天的修订值，历史结论再也复原不了。故只补空缺。

### 重启生效验收（2026-10-17 14:56，实测）

采集器代码提交后必须**重启后端**才会进调度器。重启后按「端口 → 排期 → 心跳」三级验收：

| 验收项 | 实测证据 |
|---|---|
| 后端在跑 | 端口 `127.0.0.1:8000` LISTENING；`scheduler_running=True` |
| 排期总数 | `scheduled_count=47` |
| 51 与 47 的差额已逐条对账 | `task_config` 51 条 = 47 排期 + 2 禁用（`capital_flow_sync`、`ai_concept_analysis`）+ 2 纯手动（`daily_backfill`、`startup_converge`，cron=`手动`） |
| `stock_market_daily_ex_sync` | `enabled=True`、`scheduled=True`、`next_run=2026-10-19 19:50` |
| `fund_hold_sync` | `enabled=True`、`scheduled=True`、`next_run=2026-11-05 20:10` |
| `capital_flow_sync` | `enabled=False`、`scheduled=False`、`next_run=None`（**按计划保持禁用**） |
| 心跳正常 | `news_fetch` 每 30 分钟班次连续 success（重启前最后一次 14:30，写入 7 条） |

> **重启时的意外收获（顺带验证了幂等性）**：后端启动收敛流水线把这两个「从未跑过」的新任务
> 当成**遗漏班次**各补跑了一次 —— 日志原文
> `✅ 启动收敛执行 fund_hold_sync（写入 178307 条，依据 missed）`。
>
> 这恰好构成一次非刻意设计的真实验收：
> `fund_hold_sync` 58 期 **0 失败、175 秒**跑完，但表行数**一行未增**（前后都是 179,621 行）。
> 说明 `INSERT IGNORE` 真的生效 —— **重跑不会篡改已固化的历史快照**，这正是
> `overwrite=false` 想要的效果。若无此保护，这次补跑会把 58 期历史全部改写成今天的修订值。
>
> 同样的机制会让 `stock_market_daily_ex_sync` 也补跑一次；它走水位线续跑，无新交易日则写 0 行，
> 属正常。**补跑只发生在任务还没有成功记录之前**，一旦有过 success 就不会再触发。
>
> **空转成本的实测值（2026-10-17 15:15）**：`写入 0 行 / 5611 只，失败 37，无数据 5574，耗时 986 秒`。
> 即「表已是最新」时一轮仍要全扫 5,611 只、约 **16.4 分钟**（4 线程，约 5.8 只/秒）——
> 因为水位线续跑是**逐只判断**，没有「全市场最新日期」的快路径。37 只失败全部是源侧
> `JSONDecodeError: No value to decode`（退市/特殊代码无数据，非代码缺陷）。
> `无数据 5574` 不是异常，就是「该股区间内没有新交易日」。
> 好在这条 cron 是 `0-4`（APScheduler 下 0=周一），**只在周一到周五跑，周末不会白耗**。

### 仍未解决的一条（时间敏感）

> ⚠️ `capital_flow_sync` 已写好但 **`enabled=0`**，本次**刻意没有启用**。
> 原因不是懒，而是实测**本机对该域名完全不可达**：`push2his.eastmoney.com` 在 TLS 层
> 就被重置（HTTP/HTTPS × PC/移动 UA 四种组合全部 `http=000`）。
> 在这种情况下启用，只会得到「任务跑完、写入 0 行」的假象，**反而掩盖停更**。
>
> **换到能访问东财的网络后**，两步即可启用：
> ```sql
> UPDATE task_config SET enabled=1 WHERE task_name='capital_flow_sync';
> ```
> 然后手工触发一轮，确认 `stock_capital_flow` 行数真的增长，再交给调度器。

### 可移植性加固（2026-10-17 第二批，已落地）

上一批解决「数据能不能长回来」；这批解决「换个环境会不会**静默错配**」。

| # | 问题（换机时会怎样） | 已做的调整 |
|---|---|---|
| 1 | 云主机默认 UTC，cron 与 `CURDATE()` 错 8 小时 ⇒ 采集「成功」但少一天 | `preflight.py` 比对数据库 `NOW()` 与宿主机时间，偏差 >120s 直接报错并给三条修法 |
| 2 | 后端不读 `.env`，照模板建了 `.env` 却不生效 | 新增 `app/env.py`（零新依赖），启动时加载 `根/.env` → `backend/.env` → `backend/.env.local`；进程环境变量仍优先，被覆盖时打警告 |
| 3 | MariaDB / MySQL 5.7 建表即失败 | `preflight.py` 校验版本 ≥ 8.0；README 把 8.0+ 标为**硬要求** |
| 4 | 忘了 seed ⇒ `task_config` 空 ⇒ 调度器排 0 个任务，页面一片空白 | `preflight.py` 检查 `task_config`/`table_meta`/`dq_rules` 行数，为 0 直接判 fail |
| 5 | 配了通知渠道却漏 token ⇒ 永不推送且不报错 | `preflight.py` 按 provider 校验对应凭证 |
| 6 | 全新 `docker compose up` 得到**空库** | `docker-compose.yml` 挂载 `schema.sql` 到 `/docker-entrypoint-initdb.d/`（仅首次初始化生效，已注明重建需 `down -v`） |
| 7 | 仓库零个 `.sh`，Linux/macOS 无启动入口 | 新增 `scripts/start-dev.sh`，与 `.bat` 逻辑对齐（解释器解析 / 端口探测 / 只起后端或前端） |
| 8 | `schema.sql` 是快照，新增表后没人同步 ⇒ 新机器缺表 | `dump_schema.py --check`：比对表与**字段级**差异，有漂移退出码 1（可接 CI） |
| 9 | 6 步手工易漏 `--apply` / 参数 | 新增 `backend/scripts/restore.py`：一条命令跑完结构→配置→数据→核对，输出 JSON 报告 |

**实测过的负对照**（证明这些检查不是摆设）：

| 检查 | 负对照做法 | 结果 |
|---|---|---|
| 表完整性 | 故意要求一张不存在的表 | ✅ 判 fail |
| 时区 | 伪造「数据库慢 8 小时」的游标 | ✅ 判 fail |
| MySQL 版本 | 伪造 `5.7.44` | ✅ 判 fail |
| 通知渠道 | `provider=wecom` 但不设 webhook | ✅ 判 fail |
| schema 漂移 | 从 schema.sql 删一张表 / 加一个字段 | ✅ 两种都判 fail、退出码 1 |

> 🐞 加固过程中踩到并已修掉的一个真 bug：`restore.py` 最初用 `sql.split(";")` 切分
> schema.sql，被 `stock_info.data_source DEFAULT 'EM;BAOSTOCK;CNINFO'`
> （**字符串字面量内含分号**）劈成两半，报 `1064 ... near ''EM' at line 35` ——
> 报错位置与真因毫不相干。已改成引号/注释感知的切分器，实测 56/56 条建表语句正确切分。
>
> **时限**：窗口只有 120 个交易日（约 5.5 个月）。当前表停在 2025-09-19，
> 那段已经滑走、**永久补不回**；只有「从现在起」的窗口还能攒。
> 这也解释了「为什么当年能攒下数据」——不是网上有全史，而是**当年每天在跑，一天攒一格**。

---

## 8. 已知的元数据小瑕疵（不影响恢复，记录备查）

| 问题 | 现状 |
|---|---|
| `table_meta` 登记了已不存在的表 `stock_market_daily_bak_20250802` | 统计脚本需容错，不影响功能 |
| `stock_hold_by_fund` 历史 113,147 行的 `data_source` 是 **`'AKSAHRE'`（错拼）** | 新写入用正确拼写 `'AKSHARE'`，历史行**未擅自改写**。代价是按该字段分组会出现两组，如需统一要单独做一次带备份的订正 |
| `stock_market_daily_ex` 与 `stock_market_daily` 的 `volume` 注释都写「手」 | **实际单位不同**：`ex` 是手、`daily` 是股（同日 000001：918,090 vs 91,808,992）。跨表比对时必须先换算，否则会得出「差 100 倍」的假异常 |

---

## 附：本文的实测证据清单

| 结论 | 证据 |
|---|---|
| 干净克隆可独立部署 | 真实 clone 231 文件 → venv 44s → schema 56 表零错 → 6 seed 全成功 → `/api/health` 首探 200 → 13 个核心接口全 200 |
| 空库日线能自举全史 | 空库实测：5 只名册 → 4 个区间（B 股排除）→ 补 1 行后收缩到 3 个 → 日志出现自动切换 |
| `ex` 表可重建，且口径对齐 | 10 只 × 103 交易日 = 1,030 行逐字段对比：`open/high/low/close/volume/turnover_ratio/data_source` **1,030/1,030 完全一致**；`change_pct`/`change_amount` 零值差（新采还补上了历史为 NULL 的平盘行） |
| `ex` 采集器幂等 | 同一区间连跑两次均写 8,453 行，表内仍 8,453 行，不翻倍 |
| `ex` 采集器吞吐 | 抽样 60 只耗时 28.8s（4 线程）；生产库实跑 5,611 只 / 157 万行 / **19 分钟**，失败 37 只 |
| `hold_by_fund` 可逐期回溯 | `date=2015-12-31` 返 2,781 行、`2012-03-31` 返 1,540 行（比本地原最早更早） |
| `hold_by_fund` 市值单位是万元 | 接口 30,119,682,369.44 元 → 转换后 3,011,968.24 万元，**比值恰好 10000.0** |
| `hold_by_fund` 上游会修订历史 | 同一 2025-06-30：本地 2,943 行 / 300750 基金 1,752 家 vs 接口 5,216 行 / 2,598 家 |
| 东财资金流本机不可达 | `push2his` 四种请求组合（HTTP/HTTPS × PC/移动 UA）全部 `http=000`，curl exit 52 |
| 资金流 120 条是硬限 | `lmt=0` / `lmt=1000000` / `beg=20230101&end=20231231` 三写法返回完全相同的 120 条（2026-04-20~10-16），独立出口复核 |
| 新浪不支持老北交所 | `bj430047`/`bj830799`/`bj871981` 均 `JSONDecodeError`；`bj920819` 返 22 行（920 段可用） |
| 新浪不给退市股 | `sz002002`/`sz002013`/`sz000150`/`sz002118` 任何区间均 `JSONDecodeError` |
| 新浪资金流口径不可用 | 300750 同日：新浪 `r0_net` -7.60 亿 vs 本地特大单 -0.61 亿，差一个量级 |
| 资金流 120 条是硬限 | `lmt=0` / `lmt=1000000` / `beg=20230101&end=20231231` 三写法返回完全相同的 120 条（2026-04-20~10-16），独立出口复核 |
| 新浪资金流口径不可用 | 300750 同日：新浪 `r0_net` -7.60 亿 vs 本地特大单 -0.61 亿，差一个量级 |
