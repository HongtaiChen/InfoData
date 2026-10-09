# 业务数据回补指南（data bootstrap）

> 适用：**全新环境**首次部署，或**换机迁移**后，把业务数据准备起来。
> 配套：`scripts/schema.sql`（建表）→ 本文档（准备数据）→ 启动服务。
> **换机请先看 §1 路径 0（整库迁移）**；全新环境 / 干净副本看路径 A/B/C。
> 最后更新：2026-10-10

---

## 0. 为什么数据不随仓库分发

`adata` 库实测有 **60 张表 / 约 3700 万行**（其中 `stock_market_daily` 单表就 1700 万行）。
数据来自 AKShare / 同花顺 / 央行 / BIS 等**外部数据源**，既带版权约束，体积也远超仓库承载能力。

⇒ **仓库只带「结构 + 配置」，不带「数据」。**
`scripts/schema.sql` 建出 56 张空表，`seed_*.py` 灌入任务/DQ/元数据配置，
**业务数据必须用本文档的方式从上游重新采集**。

> ⚠️ **但「换机」≠「新环境」**（2026-10-10 补充）：
> 如果**原来的机器还在**，就不要重新采集 —— 直接整库迁移，见 §1 **路径 0**。
> 原因是本库有一张 **1600 万行 / 3.2 GB 的 `stock_market_daily_ex` 冻结归档表**，
> 它**没有任何采集器**（`app/selfheal.py` 明确把它列为「没有任务可重跑」），
> 重新采集永远拿不回；`news` / `finance_calendar` / `ths_concept_market` /
> `finance_concept_analysis`(AI) 这类 B 类表的历史同理。
> **「从上游重新采集」只适用于**：给对方一个干净副本、或原机已经毁掉、或只想重建部分数据。

---

## 1. 四条回补路径（从省力到彻底）

### 路径 0：整库迁移（**换机首选**，也是唯一能保住历史数据的办法）

**适用**：原来的机器还在（换机、数据搬家）。
**不适用**：给对方干净副本 / 原机已毁 ⇒ 走路径 A/B/C。

工具：`scripts/migrate-machine.bat`（dump 在源机跑，load 在目标机跑）。

**源机 — 导出**

```bat
scripts\migrate-machine.bat dump
:: 默认输出 scripts\backup\adata_migrate_YYYYMMDD_HHMMSS.sql
:: 默认自动排除 *_bak_YYYYMMDD / *_backup_YYYYMMDD 归档表（省 1.6 GB）
:: 也可指定路径：migrate-machine.bat dump D:\adata_migrate.sql
```

**目标机 — 导入**

```bat
scripts\migrate-machine.bat load "D:\path\to\adata_migrate_20261010_074352.sql"
:: 会先提示确认（dump 自带 DROP TABLE）；确认或直接加 -y 跳过
```

**⭐ 关键：迁移之后不要再跑 `schema.sql` 和 `seed_*` 脚本。**
dump 里已经包含 `CREATE DATABASE` + 全部表 + `task_config` / `dq_rules` / `table_meta`
配置，导入完直接 `start-dev.bat` 就行。

**实测体积与耗时**（2026-10-10）

| 项 | 实测值 |
|---|---|
| 现网库逻辑大小 | **11,396.9 MB**（数据 8,154.9 + 索引 3,242.0） |
| 排除 4 张归档表后 | **9,730.4 MB**（归档表合计 1,666.5 MB，`stock_market_daily_bak_20260920` 独占 1.65 GB） |
| 最大两张表 | `stock_market_daily` 5.8 GB · `stock_market_daily_ex` 3.2 GB |
| dump 文本体积 | `--no-data` 全结构实测 104 KB；带数据推算 **≈5.3 GB** |
| gzip 后（可选） | 单表实测压缩比 ÷4.8 ⇒ 全库推算 **≈1.1 GB** |
| 导入耗时 | **10~30 分钟**（估算，主要耗在重建索引） |

**四条注意事项**

1. **dump 文件几 GB，绝不要提交进 Git**。默认输出目录 `scripts/backup/` 已在 `.gitignore` 里。
   （可选）先压缩再传：`gzip` 后约 1.1 GB，U 盘 / 网盘都可行。
2. **目标机必须 MySQL 8.0+** —— dump 里带 `utf8mb4_0900_ai_ci`，5.7 会直接导入失败。
3. **导入后新库是 56 张表，不是 60** —— 那 4 张 `*_bak_*` 归档表是被**有意排除**的，
   不是丢数据。核对命令见 §5。
4. **口令不落命令行**：脚本按 `INFO_DATA_DB_PASSWORD` 环境变量 → 仓库根 `.env` 的顺序取，
   再通过 `MYSQL_PWD` 传给客户端，所以不会出现在控制台历史里。

### 路径 A：什么都不做，让启动收敛自动补（零操作）

**实测有效**：只要表已建好、`task_config` 已灌入，启动后端时「启动即收敛」会巡检 DQ 结果，
把「数据缺失型」问题自动排进修复队列并执行。

> 2026-10-09 实测：在一个「只有结构 + seed、零业务数据」的库上启动后端，
> 收敛流水线自动执行了 `index_cons_sync`，**写入 10,228 行**（`index_constituents`）。

- ✅ 优点：零操作，服务一起来就在补
- ⚠️ 局限：**只覆盖「DQ 规则能发现缺失」的表**，且单轮有修复数量上限（默认 `max_repairs=10`）
- 想看进展：数据中心 →「数据质量」页顶部「启动自愈」摘要条

### 路径 B：手动触发全部采集器（推荐，一次性把数据拉齐）

依次触发（顺序见 §3 的依赖关系）。两种触发方式：

**方式 1 · 前端界面**（最直观）
> 作业监控（`/jobs`）→ 在任务行上点「触发」→ 看运行记录

**方式 2 · HTTP 接口**（便于脚本化）

```bash
curl -X POST http://127.0.0.1:8000/api/jobs/tasks/<任务名>/trigger
# 例：
curl -X POST http://127.0.0.1:8000/api/jobs/tasks/trade_calendar_sync/trigger
```

> ⚠️ 触发是**异步**的，接口立刻返回。用 `GET /api/jobs/runs` 或前端作业监控页看结果。
> ⚠️ 同一任务并发保护：2 小时内已有 `running` 记录时会跳过。

### 路径 C：定向回补历史区间（`daily_backfill`）

日线是唯一提供「指定区间回补」入口的采集器，`source=ranges` 可直接补任意股票任意区间：

```json
{
  "source": "ranges",
  "ranges": [["600519", "20010827", "20261231"], ["000001", "19910403", "20261231"]],
  "adjust": "qfq",
  "max_codes": 0
}
```

调用（把 params 更新到任务上再触发）：

```bash
# 1) 写入参数
curl -X PUT http://127.0.0.1:8000/api/jobs/tasks/daily_backfill \
     -H "Content-Type: application/json" \
     -d '{"params": {"source":"ranges","adjust":"qfq","max_codes":0,
                     "ranges":[["600519","20010827","20261231"]]}}'
# 2) 触发
curl -X POST http://127.0.0.1:8000/api/jobs/tasks/daily_backfill/trigger
```

`source=dq_gap`（默认）则不用给区间 —— 它读 `dq_gap_detail` 里 `status='open'` 的疑似缺口自动补。

---

## 2. 各表可回补性一览

依据 `table_meta.writers`（权威映射）+ 各采集器的 `first_lookback_days` / `max_days` 等参数实测。

### ✅ A 类 —— 能拉到历史（新环境可补齐）

| 表 | 采集任务 | 说明 |
|---|---|---|
| `trade_calendar` | `trade_calendar_sync` | 交易日历，全量 |
| `stock_info` / `stock_info_ex` | `stock_info_sync` / `stock_status_sync` / `bj_stock_sync` / `stock_company_sync` | 股票列表与档案，全量快照 |
| `stock_market_daily` | `stock_daily_incr` / `daily_backfill` | **历史可补**，见路径 C；单表最大 |
| `bond_profit_daily` | `bond_profit_sync` | 中美国债收益率日线，全量 |
| `dc_index_market` | `index_market_sync` | 指数行情 |
| `index_constituents` / `index_profile` | `index_cons_sync` | 指数成分，月度快照 |
| `ths_concept_info` / `ths_stock_concepts` | `concept_sync` | 概念列表与成分，全量 |
| `securities_margin` | `margin_sync` | 融资融券（`max_days=0` 不限） |
| `currency_boc_daily` | `currency_boc_sync` | 首次回看 **1825 天** |
| `global_usd_index_daily` | `usd_index_sync` | 首次回看 **3650 天** |
| `overseas_index_daily` | `overseas_index_sync` | 海外指数 |
| `futures_spot_price` | `futures_sync` | 期货现货与基差 |
| `stock_jgdy_detail` | `jgdy_sync` | 机构调研（首次回看 365 天） |
| `sw_industry_sync` → `stock_industry_sw` | `sw_industry_sync` | 申万行业分类 |
| `stock_shares` | `stock_shares_sync` | 股本 |
| `stock_repurchase` | `stock_repurchase_sync` | 回购 |
| `fund_info` / `fund_new_issue` | `fund_info_sync` / `fund_new_issue_sync` | 基金 |
| `ths_stock_dividend` | `ths_dividend_sync` | 分红送配 |
| `stock_financial_abstract_ths` | `financial_abstract_sync` | 财务摘要 |
| 宏观 10 张<br>`cn_liquidity_monthly` `cn_cb_balance_monthly` `cn_omo_daily` `cn_reserve_monthly` `cb_policy_rate` `us_fed_balance_weekly` `us_money_market_daily` `us_money_supply_monthly` `eu_money_supply_monthly` `global_liquidity_bis` | 各自 `*_sync` | 央行/美联储/BIS 官方源，历史齐全 |

### ⚠️ B 类 —— 只能拿到「最近一段」（历史补不回来）

| 表 | 采集任务 | 为什么 |
|---|---|---|
| `news` | `news_fetch` | 快讯接口只吐最新列表（`max_pages=3`），**历史新闻无法回溯** |
| `ths_concept_market` | `concept_market_sync` | 窗口 `days_back=15`，概念行情只保留近 15 天 |
| `finance_calendar` | `finance_calendar_sync` | 滚动窗口（未来 60 天 + 近期），早期事件需持续积累 |
| `stock_market_current` | `market_current_sync` | 当日快照，天然不回溯 |
| `stock_capital_flow` | `capital_flow_sync` | `refresh_days=7` |
| `index_valuation_daily` | `index_valuation_sync` | `lookback_days=0`（只当日） |
| `ai_concept_analysis` → `finance_concept_analysis` | `ai_concept_analysis` | 依赖当日事件 + AI 调用 |

### 🔗 C 类 —— 必须先有前置数据

| 表 | 采集任务 | 前置 |
|---|---|---|
| `market_style_daily` | `market_style_sync` | **`stock_market_daily`**（它是对个股日线的库内聚合） |
| `market_xcheck_daily` | `xcheck_sync` | `stock_market_daily`（交叉校验） |
| `dq_report` / `dq_gap_detail` / `dq_recon_detail` | `data_quality_check` / `data_quality_check_weekly` / `daily_recon_window` | 业务数据本身 |
| `finance_concept_analysis` | `ai_concept_analysis` | `finance_calendar` + 概念数据 + `ARK_API_KEY` |

### 🚫 D 类 —— 无需回补（配置/元数据，已由 seed 灌好）

`task_config` · `task_runs` · `dq_rules` · `table_meta` · `user_prefs` · 各类 `*_bak_*` 备份表

---

## 3. 推荐执行顺序（依赖关系，别颠倒）

```
① trade_calendar_sync              交易日历先行（多处计算依赖）
② stock_info_sync                  股票列表
   stock_status_sync               上市/退市状态
   bj_stock_sync                   北交所代码映射
③ stock_daily_incr                 日线增量（首次会自动向前拉一段）
   └ 需要更深历史 → daily_backfill（路径 C）
④ concept_sync                     概念列表 + 成分
   concept_market_sync             概念行情
⑤ 其余采集器（宏观 / 债券 / 基金 / 期货 / 调研 / 分红 / 财务 …）
⑥ market_style_sync                依赖 ③，日线齐了再跑
   xcheck_sync                      依赖 ③
⑦ data_quality_check               最后跑，让体检基于真实数据
```

> 顺序依据：③ 依赖 ② 的股票列表；⑥ 依赖 ③ 的日线；⑦ 依赖前面全部。

---

## 4. 耗时与风险提示

| 项 | 预期 | 说明 |
|---|---|---|
| 建表（schema.sql） | 秒级 | 56 张空表 |
| seed 配置 | 秒级 | 49 任务 / 209 DQ 规则 / 59 表元数据 |
| 首次日线全量（3700 万行量级） | **数小时**（数量级估算，未实测） | 受上游风控与限速影响最大 |
| 单表小采集（如指数成分） | 数秒~数十秒 | 2026-10-09 实测 10228 行约 10 秒 |
| DQ 体检单轮 | 数十秒 | 199 条 daily 规则 |

**风险与注意**
- 🔴 **上游风控**：东财/同花顺高频访问会被限流。采集器已内置四级降级（东财→腾讯→新浪→Tushare）与随机延时，
  但**不要并发触发同一采集器**。
- 🟠 **首次跑会比日常慢很多**：多数采集器有 `first_lookback_days`（首次回看 N 天），一次性拉的量远大于增量。
- 🟠 **`news` / `finance_calendar` / `ths_concept_market` 的历史补不齐**（见 §2 的 B 类），
  这类表的价值随时间积累，新环境初期内容会明显偏少 —— 这是上游接口限制，不是故障。
- 🟡 **`tushare` 备源需 `TUSHARE_TOKEN`**（可选）。不配会少一级降级，但仍能跑。

---

## 5. 回补完成后怎么校验

**方法 1 · 服务端自检**（推荐）

```bash
# 0) 先核对表数量
#    迁移（路径 0）：应为 56（4 张 *_bak_* 归档表被有意排除，不是丢数据）
#    从零初始化（路径 A/B/C）：也应为 56
mysql -u root -p -e "
SELECT COUNT(*) AS n_tables FROM information_schema.TABLES WHERE TABLE_SCHEMA='adata';"

# 看哪些表有数据、多少行
mysql -u root -p -e "
SELECT TABLE_NAME, TABLE_ROWS FROM information_schema.TABLES
WHERE TABLE_SCHEMA='adata' AND TABLE_ROWS > 0 ORDER BY TABLE_ROWS DESC;"
```

**方法 2 · 走 DQ 体检**

```bash
curl -X POST http://127.0.0.1:8000/api/jobs/tasks/data_quality_check/trigger
# 然后看 /dq 页面，或：
curl http://127.0.0.1:8000/api/dq/report
```

初次回补后**仍会有一批 warning/fail**（因为 B 类表天然补不齐），这是正常的。
判读标准：**看「每天在新增」而不是「一次到齐」** —— 连续几个交易日后，`stock_market_daily`、
`market_style_daily`、`news` 的日期水位线应逐日推进。

**方法 3 · 看页面**

- 行情看板 `/market`：能搜到股票、K 线有数据
- 数据中心 `/data`：点表名看行数
- 数据质量 `/dq`：看各表的 freshness / row_count 结论
