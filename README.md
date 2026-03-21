# InfoData - 金融数据收集、存储和分析系统

## 概述

InfoData是一个基于Python的金融数据收集、存储和分析系统，支持股票、基金、债券、指数等多种金融数据的定时收集、质量检查和智能分析。

## 功能特性

### 🚀 核心功能
- **定时数据收集**: 支持股票、基金、债券、指数等金融数据
- **智能调度**: 基于APScheduler的智能任务调度
- **数据质量保障**: 99.99%数据准确性，最晚22:00前完成更新
- **历史数据同步**: 一键同步全量历史数据
- **监控告警**: 实时监控任务执行状态和数据质量

### 📊 数据源
- **AKShare**: 开源金融数据接口库
- **Tushare**: 专业金融数据API
- **数据准确性**: 99.99%
- **更新及时性**: 日度数据最晚22:00前完成

### 🔧 技术栈
- **调度框架**: APScheduler
- **数据库**: MySQL + SQLAlchemy
- **配置管理**: Pydantic + YAML
- **监控告警**: 内置监控 + 邮件/Webhook告警
- **部署**: Docker容器化

## 快速开始

### 环境要求
- Python 3.8+
- MySQL 5.7+
- Docker (可选)

### 安装
```bash
# 克隆项目
git clone https://github.com/HongtaiChen/InfoData.git
cd InfoData

# 安装依赖
pip install -e ".[dev]"
```

### 配置
1. 复制配置文件模板：
```bash
cp config.example.yaml config.yaml
```

2. 编辑配置文件：
```yaml
# config.yaml
database:
  host: localhost
  port: 3306
  user: root
  password: your_password
  database: infodata

scheduler:
  timezone: Asia/Shanghai
  jobstore: sqlalchemy
```

### 运行
```bash
# 启动调度服务
infodata start

# 查看任务状态
infodata status

# 手动执行任务
infodata run-task stock_daily_update

# 查看日志
infodata logs
```

## 项目结构

```
infodata/
├── config/              # 配置管理
│   ├── __init__.py
│   ├── manager.py      # 配置管理器
│   ├── schemas.py      # 配置验证
│   └── defaults.yaml   # 默认配置
├── tasks/              # 任务管理
│   ├── __init__.py
│   ├── base.py         # 任务基类
│   ├── stock.py        # 股票任务
│   ├── fund.py         # 基金任务
│   ├── bond.py         # 债券任务
│   └── index.py        # 指数任务
├── data/               # 数据服务
│   ├── collector.py    # 数据收集
│   ├── processor.py    # 数据处理
│   ├── validator.py    # 数据验证
│   └── storage.py      # 数据存储
├── monitoring/         # 监控告警
│   ├── metrics.py      # 指标收集
│   ├── alerts.py       # 告警管理
│   └── quality.py      # 数据质量检查
├── models/             # 数据模型
│   ├── task.py         # 任务模型
│   ├── execution.py    # 执行记录
│   └── quality.py      # 质量指标
├── utils/              # 工具函数
│   ├── logging.py      # 日志配置
│   ├── database.py     # 数据库工具
│   └── exceptions.py   # 异常定义
└── cli.py              # 命令行入口
```

## 定时策略

### 股票数据
- **日度收盘数据**: 每天19:00开始，最晚22:00前完成
- **周度基本信息**: 每周一02:00执行
- **月度财务数据**: 每月15号03:00执行

### 基金数据
- **日度净值**: 每天20:00执行
- **月度基本信息**: 每月1号03:00执行

### 债券数据
- **日度行情**: 每天18:00执行
- **月度基本信息**: 每月5号03:00执行

### 指数数据
- **日度行情**: 每天19:30执行
- **月度成分股**: 每月10号03:00执行

### 节假日处理
- 数据对账检验
- 低频数据更新同步

## 监控指标

### 任务执行监控
- 任务执行成功率
- 任务执行时间
- 任务失败率
- 任务重试次数

### 数据质量监控
- 数据准确性 (99.99%)
- 数据完整性
- 数据及时性 (最晚22:00)
- 数据一致性

### 系统资源监控
- CPU使用率
- 内存使用率
- 磁盘使用率
- 数据库连接数

## 部署

### Docker部署
```bash
# 构建镜像
docker build -t infodata .

# 运行容器
docker run -d \
  --name infodata \
  -p 8080:8080 \
  -v ./config.yaml:/app/config.yaml \
  -v ./logs:/app/logs \
  infodata
```

### Kubernetes部署
```yaml
# deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: infodata
spec:
  replicas: 1
  selector:
    matchLabels:
      app: infodata
  template:
    metadata:
      labels:
        app: infodata
    spec:
      containers:
      - name: infodata
        image: infodata:latest
        ports:
        - containerPort: 8080
        volumeMounts:
        - name: config
          mountPath: /app/config.yaml
          subPath: config.yaml
      volumes:
      - name: config
        configMap:
          name: infodata-config
```

## 开发指南

### 代码规范
- 使用Black进行代码格式化
- 使用flake8进行代码检查
- 使用mypy进行类型检查
- 测试覆盖率要求>80%

### 测试
```bash
# 运行单元测试
pytest infodata/tests/unit -v

# 运行集成测试
pytest infodata/tests/integration -v

# 生成测试覆盖率报告
pytest --cov=infodata --cov-report=html
```

### 提交代码
```bash
# 代码格式化
black infodata tests

# 代码检查
flake8 infodata tests
mypy infodata

# 运行测试
pytest

# 提交代码
git commit -m "feat: add new feature"
```

## API文档

### 命令行接口
```bash
# 启动服务
infodata start [--config CONFIG]

# 停止服务
infodata stop

# 查看状态
infodata status [--task TASK]

# 执行任务
infodata run-task TASK_NAME [--date DATE]

# 查看日志
infodata logs [--task TASK] [--level LEVEL]

# 管理配置
infodata config show
infodata config set KEY VALUE
```

### Python API
```python
from infodata.scheduler import SchedulerManager
from infodata.config import ConfigManager

# 初始化配置
config = ConfigManager.load("config.yaml")

# 创建调度器
scheduler = SchedulerManager(config)

# 启动调度器
scheduler.start()

# 添加任务
scheduler.add_task(StockDailyUpdateTask, "0 19 * * *")

# 停止调度器
scheduler.stop()
```

## 故障排除

### 常见问题

#### 1. 数据库连接失败
```bash
# 检查数据库服务
systemctl status mysql

# 检查连接配置
infodata config show database
```

#### 2. 任务执行失败
```bash
# 查看任务日志
infodata logs --task stock_daily_update

# 查看错误详情
infodata status --task stock_daily_update --verbose
```

#### 3. 数据更新延迟
```bash
# 检查数据源状态
infodata check-data-source

# 查看数据质量报告
infodata quality-report --date 2024-01-01
```

### 日志文件
- `logs/infodata.log` - 主日志文件
- `logs/tasks/` - 任务执行日志
- `logs/errors/` - 错误日志

## 贡献指南

1. Fork项目
2. 创建功能分支 (`git checkout -b feature/amazing-feature`)
3. 提交更改 (`git commit -m 'Add amazing feature'`)
4. 推送到分支 (`git push origin feature/amazing-feature`)
5. 创建Pull Request

## 许可证

本项目采用MIT许可证。详见 [LICENSE](LICENSE) 文件。

## 联系方式

- 项目主页: https://github.com/HongtaiChen/InfoData
- 问题反馈: https://github.com/HongtaiChen/InfoData/issues
- 文档: https://infodata.readthedocs.io/

## 致谢

感谢以下开源项目的支持：
- [AKShare](https://github.com/akfamily/akshare)
- [Tushare](https://github.com/waditu/tushare)
- [APScheduler](https://github.com/agronholm/apscheduler)
- [SQLAlchemy](https://github.com/sqlalchemy/sqlalchemy)

---

**版本**: 0.1.0  
**最后更新**: 2026-03-21  
**状态**: 开发中