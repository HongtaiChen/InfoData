# InfoData 容器化部署指南（P5）

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
curl http://127.0.0.1:18000/api/health     # {"status":"ok","service":"InfoData API"}
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
#   INFO_DATA_DB_PASSWORD=infodata123
#   MYSQL_ROOT_PASSWORD=root123456

# 2. 构建并启动全部三服务
docker compose --profile docker-mysql up -d --build

# 3. 首次使用需迁移本机数据到容器（重要！）
scripts\migrate-local-to-docker.bat

# 4. 验证
docker exec infodata-mysql mysql -uroot -proot123456 -e "SELECT COUNT(*) FROM adata.stock_market_daily;"
curl http://127.0.0.1:18000/api/health
# 浏览器访问 http://127.0.0.1:8080
```

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

# 恢复（示例）
docker exec -i infodata-mysql mysql -uroot -proot123456 adata < scripts\backup\adata_20260902_0900.sql
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
| INFO_DATA_DB_PASSWORD | root | 模式 B 填 `infodata123` |
| INFO_DATA_DB_NAME | adata | |
| MYSQL_ROOT_PASSWORD | root123456 | 仅模式 B 容器 MySQL 使用 |
| TUSHARE_TOKEN | （空） | Tushare 备源 token，可选 |
| ARK_API_KEY | （空） | 方舟豆包 AI Key，可选 |
| ARK_MODEL_ID | doubao-1-5-pro-32k-250115 | AI 模型，可选 |

## 注意事项

1. **采集任务在容器内运行**：容器内 Python 环境已含 akshare，作业监控界面可正常触发采集。首次全量采集较慢，建议先确认网络可直连数据源（东财/腾讯）。
2. **密钥安全**：所有密钥只通过 `.env` 注入，`.env` 已被 `.gitignore` 排除，不会进入镜像或 Git。
3. **时区**：容器统一 Asia/Shanghai，MySQL 容器默认 UTC+8，与本地数据一致。
4. **性能**：本机 3700 万行数据迁移约需 10-30 分钟（取决于磁盘）。迁移脚本使用 `--single-transaction` 不影响本机在线使用。
5. **开发模式不受影响**：`start-dev.bat`（本机 uvicorn + Vite）与 Docker 模式并行共存，端口互不冲突。
