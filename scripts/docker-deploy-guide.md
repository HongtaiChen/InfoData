# InvestBuddy 容器化部署指南（P5）

> 基于 docker-compose 编排，支持两种运行模式，覆盖开发（直连本机 MySQL）与生产（全容器化）场景。

## 前置条件

- Docker Desktop（Windows）/ Docker Engine 24+ / Docker Compose v2
- 本机已安装并运行 MySQL 8.x（模式 A 需要；模式 B 可选）

## 快速开始

### 模式 A：直连本机 MySQL（推荐开发调试）

后端容器直接连接你本机的 MySQL（`127.0.0.1:3306` 的 adata 库），**无需迁移数据**：

```bash
# 1.（可选）配置密钥：复制 .env.example 为 .env，填入 ARK_API_KEY 等
# 2. 构建并启动
docker compose up -d --build

# 3. 验证
curl http://127.0.0.1:18000/api/health     # {"status":"ok","service":"InvestBuddy API"}
# 浏览器访问 http://127.0.0.1:8080
```

> Windows 下容器访问宿主机 MySQL 使用 `host.docker.internal`（compose 已默认配置）。
> 若本机 MySQL 非 root/root，在 `.env` 中覆盖：
> ```
> INFO_DATA_DB_HOST=host.docker.internal
> INFO_DATA_DB_PORT=3306
> INFO_DATA_DB_USER=你的用户名
> INFO_DATA_DB_PASSWORD=你的密码
> INFO_DATA_DB_NAME=adata
> ```

### 模式 B：全容器化（含 MySQL 8.0，推荐生产）

```bash
# 1. 配置 .env（根目录）
# 复制 .env.example 并填写，至少：
#   INFO_DATA_DB_HOST=mysql          # 指向 compose 内的 mysql 服务
#   INFO_DATA_DB_USER=infodata
#   INFO_DATA_DB_PASSWORD=你的密码
#   MYSQL_ROOT_PASSWORD=你的强密码

# 2. 构建并启动全部三服务
docker compose --profile docker-mysql up -d --build

# 3. 首次使用需迁移本机数据到容器（重要！）
scripts\migrate-local-to-docker.bat

# 4. 验证
docker exec infodata-mysql mysql -uroot -p -e "SELECT COUNT(*) FROM adata.stock_market_daily;"   # 会提示输入 MYSQL_ROOT_PASSWORD
curl http://127.0.0.1:18000/api/health
# 浏览器访问 http://127.0.0.1:8080
```

## 数据库初始化（从零部署必做）

> ⚠️ 本指南的两种模式都**假定库里已经有表**（模式 B 的「首次使用需迁移本机数据」也是从
> **已建好库**的机器迁）。若目标机器是全新的，**必须先「导入表结构 + 灌配置」**，
> 否则后端容器起来就会退出：
> `app/scheduler.py::sync_from_db()` 查 `task_config` 且**该调用点无异常保护**
> → 异常冒到 `main.py::lifespan` → uvicorn `Application startup failed`。

### 模式 B（全容器化，compose 自带 MySQL）

`MYSQL_DATABASE` 只建**空库**、**不建表**。等 mysql 容器 healthy 后依次执行
（服务名以 `docker-compose.yml` 为准，此处按 `mysql` / `backend` 书写）：

```bat
:: 1) 导入建表语句（56 张表，幂等，可重复执行，不含 DROP）
docker compose exec -T mysql mysql -uroot -proot < scripts\schema.sql

:: 2) 灌入配置（任务 / DQ 规则 / 表元数据 / 索引）
docker compose exec -T backend python scripts\seed_task_config.py --apply
docker compose exec -T backend python scripts\seed_dq_rules.py
docker compose exec -T backend python scripts\seed_table_meta.py
docker compose exec -T backend python scripts\seed_indexes.py --apply
docker compose exec -T backend python scripts\seed_market_style.py
docker compose exec -T backend python scripts\seed_recovered_tasks.py
```

> 上面 `-uroot -proot` 请替换为 `.env` 里实际设置的 `MYSQL_ROOT_PASSWORD`（用户/库名同理）。
> `--apply` 不能少：`seed_task_config.py` / `seed_indexes.py` 默认只做**只读预览**、不写入。

### 模式 A（直连宿主机 MySQL）

在宿主机上执行，与根目录 `README.md`「第 4 步」完全一致：

```bat
mysql -u root -p < scripts\schema.sql
cd backend
python scripts\seed_task_config.py --apply
python scripts\seed_dq_rules.py
python scripts\seed_table_meta.py
python scripts\seed_indexes.py --apply
python scripts\seed_market_style.py
python scripts\seed_recovered_tasks.py
```

### 业务数据

库结构 + 配置就绪后，业务数据（约 3700 万行）**不随仓库分发**，由采集任务回补 ——
完整清单、表 ↔ 任务映射、可回补性与依赖顺序见 **[data-bootstrap.md](data-bootstrap.md)**。

> 库结构有变化后重新导出：`cd backend && python scripts\dump_schema.py`
> （逐表保留 `DEFAULT CHARSET / COLLATE`，**请勿手工删掉**，否则会触发 1267 排序规则错误）。

## 端口说明

| 服务 | 容器端口 | 宿主机端口 | 说明 |
|---|---|---|---|
| frontend | 80 | **8080** | 前端页面 + /api 反代 |
| backend | 8000 | **18000** | FastAPI（避开 dev 的 8000） |
| mysql | 3306 | **3307** | 容器版 MySQL（避开本机 3306） |

如需修改映射，编辑 `docker-compose.yml` 中对应 `ports`。

## 数据备份与恢复

```bash
# 备份容器 MySQL 到 scripts\backup\adata_时间戳.sql
scripts\backup-docker-mysql.bat

# 恢复（示例，会提示输入 MYSQL_ROOT_PASSWORD）
docker exec -i infodata-mysql mysql -uroot -p adata < scripts\backup\adata_20260902_0900.sql
```

## 常用运维命令

```bash
docker compose ps                     # 查看服务状态
docker compose logs -f backend        # 后端日志（采集任务输出）
docker compose logs -f frontend       # 前端日志
docker compose --profile docker-mysql down     # 停止（保留数据卷）
docker compose --profile docker-mysql down -v  # 停止并删除数据卷（慎用！）
docker compose build --no-cache backend       # 强制重建后端镜像
```

## 环境变量清单（.env）

| 变量 | 默认 | 说明 |
|---|---|---|
| INFO_DATA_DB_HOST | host.docker.internal | 模式 B 填 `mysql` |
| INFO_DATA_DB_PORT | 3306 | |
| INFO_DATA_DB_USER | root | 模式 B 填 `infodata` |
| INFO_DATA_DB_PASSWORD | root | 模式 B 填你在 .env 里设的值 |
| INFO_DATA_DB_NAME | adata | |
| MYSQL_ROOT_PASSWORD | （必填） | 仅模式 B 容器 MySQL 使用，必须自行设置强口令 |
| TUSHARE_TOKEN | （空） | Tushare 备源 token，可选 |
| ARK_API_KEY | （空） | 方舟豆包 AI Key，可选 |
| ARK_MODEL_ID | doubao-1-5-pro-32k-250115 | AI 模型，可选 |

## 注意事项

1. **采集任务在容器内运行**：容器内 Python 环境已含 akshare，作业监控界面可正常触发采集。首次全量采集较慢，建议先确认网络可直连数据源（东财/腾讯）。
2. **密钥安全**：所有密钥只通过 `.env` 注入，`.env` 已被 `.gitignore` 排除，不会进入镜像或 Git。
3. **时区**：容器统一 Asia/Shanghai，MySQL 容器默认 UTC+8，与本地数据一致。
4. **性能**：本机 3700 万行数据迁移约需 10-30 分钟（取决于磁盘）。迁移脚本使用 `--single-transaction` 不影响本机在线使用。
5. **开发模式不受影响**：`start-dev.bat`（本机 uvicorn + Vite）与 Docker 模式并行共存，端口互不冲突。
