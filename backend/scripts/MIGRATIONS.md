# 迁移台账 MIGRATIONS

> 本文件记录所有「改库」类脚本的登记，对应 `AGENTS.md` 第 2 条「登记」要求。
> 每条含：编号 / 日期 / 目的 / 幂等性 / 是否必须重跑 / 依赖顺序 / 回滚办法。
> 新增脚本一律在此登记后再提交。

---

## M-001 · index_profile 新增两列 + seed 迁入恢复链路

- **日期**：2026-10-18
- **脚本**：
  1. `backend/scripts/migrate_index_profile_two_columns.py`（加 `core_note` / `limit_note` 两列）
  2. `backend/scripts/seed_index_profile.py`（21 只档案 + 核心说明/不足说明全量播种，替代 `setup_index_tables.py` 里的 index_profile 部分）
- **目的**：落地「指数两字段」方案（`docs/design/指数两字段落地方案_设计规范_v4.0_2026-10-18.md`）——在 `index_profile` 存用户的「核心说明 / 不足说明」原话；并把 index_profile 的 seed 从非标准位置迁进 restore.py 的 SEEDS，修掉「换机即失」的可移植性缺陷。
- **幂等性**：两个脚本均可重复执行。迁移脚本 `SHOW COLUMNS` 检测已存在则跳过；seed 用 `ON DUPLICATE KEY UPDATE`，连跑两遍行数不变。
- **是否必须重跑**：换机恢复时由 `restore.py` 的 `SEEDS` 自动执行 `seed_index_profile.py`（已注册）；迁移脚本也已在换机场景下由 DDL 覆盖（`setup_index_tables.py` 建表不含这两列，需注意）。
- **依赖顺序**：先 `migrate_...`（加列）→ 再 `seed_...`（写值）。`seed_...` 内置护栏：缺列时报错退出码 2，不静默写坏。
- **回滚**：`ALTER TABLE index_profile DROP COLUMN core_note, DROP COLUMN limit_note;` 改前已备份 `index_profile_bak_20261018`（21 行）。
- **数据来源**：用户 2026-10-18 对 14 只指数的归纳（40 句核心 + 14 段不足），其余 7 只两列留空。
- **验收证据**：
  1. 实库独立连接复核 `_scratch/p22_verify_land.py` 16 项全绿（两列类型/注释、填充 14/14、全空 7 只=用户未提、长度 13~81/11~87 无越界、LIKE 认知度 5 只、上证逐字、原 description 未被改动）。
  2. **空库可移植性实测**：`INFO_DATA_DB_NAME=adata_port_test python scripts/restore.py --no-data --yes --no-verify` → S1 建 56 表（含两列）+ S2 灌 7 个 seed（含 seed_index_profile）全绿；另起连接读该空库，两列在、填充 14/14/7、上证 core_note 逐字正确。测试库已删除。

### 连带修正（本次一并登记）

- `table_meta.index_profile` 的 `writers` 由 `["index_cons_sync"]`（误标：该采集器只做覆盖率巡检、从不写本表）改为 `[]`；`source_desc` 改为 `seed_index_profile.py（人工整理，非采集器）`；`rebuild` 保持 `full`（现在 seed 进了 SEEDS，`full` 才名副其实）。定义在 `backend/scripts/seed_table_meta.py`。
- `backend/scripts/restore.py` 的 `SEEDS` 新增 `("seed_index_profile.py", ["--apply"], "指数档案")`。

---

## 待办约定

- `setup_index_tables.py` 里仍保留旧的 `index_profile` 建表 DDL 与 SEED（`app/` 目录，不在 scripts/，且无任何程序入口调用它，仅两处 docstring 提及）。已在脚本头部加「废弃声明」指向 `seed_index_profile.py`。它的 `CREATE TABLE IF NOT EXISTS` 不会覆盖已存在表、`INSERT` 列清单不含两列也不会冲值，但**勿在干净机器上先于 schema.sql 手动跑它**。其 index_constituents 建表能力保留，后续清理时一并处理。

---

## M-002 · index_profile 新增「代表指数的重要程度」列

- **日期**：2026-10-18
- **脚本**：
  1. `backend/scripts/migrate_index_profile_importance_rank.py`（加 `importance_rank` 一列）
  2. `backend/scripts/seed_index_profile.py`（已扩展，每只档案追加 importance_rank 值；21 行 8 元组）
- **目的**：落地用户 2026-10-18 决策——「新增一列代表指数的重要程度，按之前给的文本中指数出现的先后顺序」。14 只按用户清单顺序填 1~14；其余 7 只用户未提的填 **99 哨兵**（默认最小值；D16 初版定为 NULL，09:52 用户改判"为空的统一给最小值"后修订）。完整设计在 `docs/design/指数重要程度列_增量补丁_v5.0_2026-10-18.md`。
- **DDL**：`ALTER TABLE index_profile ADD COLUMN importance_rank TINYINT UNSIGNED NULL COMMENT '代表指数的重要程度：1=上证 ... 14=中证全指房地产（按用户清单出现先后）；99=用户未确认排序的默认最小值'`（列已存在时迁移脚本会比对 COMMENT，旧口径自动 MODIFY 更新）
- **幂等性**：迁移脚本 `SHOW FULL COLUMNS` 检测——列不存在则 ADD，COMMENT 为旧口径则 MODIFY 更新，均满足则跳过；seed 用 `ON DUPLICATE KEY UPDATE`，连跑两遍行数不变。已在脚本内追加 `EXPECT_RANK` 校验：21 只的 importance_rank 必须等于 `{1..14 一一映射 + 7 只 99}` 否则启动失败。
- **是否必须重跑**：换机恢复时由 `restore.py` 的 `SEEDS` 自动执行 `seed_index_profile.py`（已在册）；schema.sql 已重新 dump（56 表含本列）。
- **依赖顺序**：先 `migrate_...`（加列）→ 再 `seed_...`（写值）。`seed_...` 内置护栏：缺列时报错退出码 2，不静默写坏。
- **回滚**：`ALTER TABLE index_profile DROP COLUMN importance_rank;` 改前已有 `index_profile_bak_20261018` 备份（10 列，无本列），可作为还原锚点。
- **数据来源**：用户 2026-10-18 文本中 14 只指数的出现先后顺序（与 `backend/app/api/market.py` 的 `INDEX_ORDER` 常量口径一致，下一轮可改用 `ORDER BY importance_rank` 取代常量）。
- **验收证据**：
  1. 实库独立连接复核 `_scratch/p23_verify_importance.py` 18 项全绿（列类型/注释含 99 口径、21 行全有值 NULL=0、14 真实值 1~14 + 7 哨兵 99、1~14 唯一无重、与 EXPECT_RANK 21 行逐项一致、v4.0 两列未被改动、上证=1 与 931775=14 边界定位正确、7 只 99 集正确、两字段全空集与 99 哨兵集同源）。
  2. **空库可移植性实测**：`adata_port_test3` 空库 → `restore.py --no-data --yes --no-verify` → S1 建 56 表（含 99 口径 COMMENT）+ S2 灌 7 seed 全绿；另起连接读该空库，21 行 = 14 真实值 + 7 哨兵 99。测试库已删除。
  3. **负对照**：迁移脚本幂等重跑显示「已存在且 COMMENT 为最新口径，跳过」；seed 幂等重跑 7 只显示「一致」。

### 连带修正（本次一并登记）

- `backend/scripts/seed_index_profile.py` 的 `EXPECT_CODES` 旁新增 `EXPECT_RANK` 21 项映射，作为 SEED 内置自检（错一项即拒绝写入）。
