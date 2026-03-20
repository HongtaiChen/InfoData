# InfoData - 金融数据管理与分析系统

## 项目概述

InfoData 是一个综合性的金融数据处理与分析系统，主要面向中国A股市场数据。项目集成了数据采集、存储、分析和可视化功能，支持股票基础信息、历史行情、财务日历等金融数据的自动化管理。

## 项目结构

```
InfoData/
├── README.md                    # 项目说明（简单版）
├── monthly_update_stock_info.py # 月度股票信息更新脚本
├── draw/                        # 数据可视化模块
│   ├── draw_index_price.py      # 指数价格绘制
│   ├── draw_stock_price_example.py  # 股票价格示例绘制
│   ├── stock_price_comparison.html  # 股票价格对比HTML
│   └── test.py                  # 测试脚本
├── sql/                         # SQL脚本目录
│   ├── calcute_concept.sql      # 概念计算SQL
│   ├── calcute_stock.sql        # 股票计算SQL
│   ├── get_pre_trade_date.sql   # 获取前一交易日SQL
│   ├── stock_info_ex.sql        # 股票信息扩展SQL
│   └── test.sql                 # 测试SQL
├── aianalyze/                   # AI分析模块
│   └── doubaoai.py              # 豆包AI分析脚本
├── other/                       # 其他工具脚本
│   ├── create_mysql_tables.py   # 创建MySQL表脚本
│   ├── daily_update_config.ini  # 每日更新配置文件
│   ├── daily_update.py          # 每日数据更新脚本
│   └── insert_all_adata_to_mysql.py  # 批量数据插入脚本
├── history_init_py/             # 历史数据初始化
│   ├── create_mysql_tables.py   # 历史数据表创建
│   └── stock_market_daily_init.py    # 股票市场每日初始化
├── finance_calendar_app/        # 财务日历Web应用
│   ├── app.py                   # Flask应用主文件
│   └── templates/               # 网页模板目录
├── daily_update_config.ini      # 主配置文件
├── daily_update_stock_info_config.ini  # 股票信息更新配置
├── daily_update_fund_info_config.ini   # 基金信息更新配置
└── daily_update_bond_info_config.ini   # 债券信息更新配置
```

## 主要功能模块

### 1. 数据采集与更新
- **月度更新**：`monthly_update_stock_info.py` - 定期更新股票基础信息
- **每日更新**：`daily_update.py` - 定时更新每日行情数据
- **批量插入**：`insert_all_adata_to_mysql.py` - 批量导入数据到MySQL

### 2. 数据库管理
- **表结构创建**：
  - `other/create_mysql_tables.py` - 核心表结构
  - `history_init_py/create_mysql_tables.py` - 历史数据表结构
- **SQL查询**：`sql/`目录包含各类数据计算和查询脚本

### 3. 数据分析与可视化
- **价格绘制**：`draw/`目录提供股票和指数的价格图表生成
- **HTML展示**：`stock_price_comparison.html`提供交互式价格对比
- **AI分析**：`aianalyze/doubaoai.py`集成AI分析功能

### 4. Web应用
- **财务日历**：`finance_calendar_app/`提供基于Flask的财务事件日历应用

## 配置文件说明

项目包含多个配置文件，用于不同模块的数据更新设置：

### 主要配置文件：
1. **`daily_update_config.ini`** - 主更新配置
   - 更新时间设置（开盘、午间、收盘、晚间）
   - 数据更新限制和批处理大小
   - 调度器设置（非交易日跳过、启动时执行等）

2. **`daily_update_stock_info_config.ini`** - 股票信息更新配置
   - 包含主配置所有设置
   - 增加数据库连接配置
   - 数据采集批处理大小

3. **其他专项配置**：
   - `daily_update_fund_info_config.ini` - 基金信息更新
   - `daily_update_bond_info_config.ini` - 债券信息更新

### 配置项示例：
```ini
[daily_update]
enabled = true
market_open_time = 09:30
market_close_time = 15:10
daily_kline_limit = 200
request_delay = 0.1

[database]
host = localhost
port = 3306
user = root
password = root
database = adata
charset = utf8mb4
```

## 数据库设计

项目使用MySQL数据库（数据库名：`adata`），主要表包括：

### 核心表结构（根据SQL脚本推断）：
1. **`stock_info_ex`** - 股票扩展信息表
   - `stock_code` - 股票代码
   - `short_name` - 股票简称
   - `exchange` - 交易所
   - `list_date` - 上市日期
   - `update_time` - 更新时间

2. **`finance_calendar`** - 财务日历表
   - `event_date` - 事件日期
   - `title` - 事件标题
   - `content` - 事件内容

3. **相关数据表**：
   - `stock_history_dividend` - 历史股息数据
   - 其他行情数据表

## 使用说明

### 环境要求
- Python 3.7+
- MySQL 5.7+
- Flask（用于Web应用）
- 相关Python包：pymysql, akshare等

### 初始化步骤
1. **数据库设置**：
   ```bash
   # 创建数据库
   mysql -u root -p -e "CREATE DATABASE adata CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
   
   # 执行表创建脚本
   python other/create_mysql_tables.py
   ```

2. **数据初始化**：
   ```bash
   # 初始化历史数据
   python history_init_py/stock_market_daily_init.py
   
   # 批量导入数据
   python other/insert_all_adata_to_mysql.py
   ```

3. **启动数据更新**：
   ```bash
   # 启动每日更新服务
   python monthly_update_stock_info.py
   python other/daily_update.py
   ```

4. **启动Web应用**：
   ```bash
   cd finance_calendar_app
   python app.py
   # 访问 http://localhost:5000
   ```

### 数据更新调度
项目支持定时数据更新，根据配置文件自动执行：
- 开盘后更新（09:30）
- 午间更新（12:00）
- 收盘后更新（15:10）
- 晚间更新（18:00）

## SQL查询示例

### 股票信息查询
```sql
-- 获取股票基本信息及股息率数据
SELECT 
    a.*,
    b.cumulative_dividends,
    b.annual_average_dividend,
    b.dividend_cnt,
    TIMESTAMPDIFF(YEAR, a.list_date, NOW()) as list_years
FROM stock_info_ex a
LEFT JOIN stock_history_dividend b ON a.stock_code = b.stock_code
WHERE abs(TIMESTAMPDIFF(YEAR, a.list_date, NOW()) - IFNULL(b.dividend_cnt, 0)) <= 5
  AND IFNULL(b.annual_average_dividend, 0) > 1.0;
```

### 前一交易日获取
```sql
-- 获取前一交易日
SELECT * FROM get_pre_trade_date.sql;
```

## 可视化功能

### 价格图表生成
```python
# 使用draw_stock_price_example.py生成股票价格图表
python draw/draw_stock_price_example.py

# 生成指数价格图表
python draw/draw_index_price.py
```

### 交互式HTML
- 打开`draw/stock_price_comparison.html`在浏览器中查看交互式股票价格对比

## 注意事项

1. **数据源**：项目使用AKShare等开源数据源，需确保网络连接正常
2. **数据库安全**：生产环境请修改默认数据库密码
3. **更新时间**：中国A股交易时间为工作日9:30-15:00，请根据实际需求调整
4. **错误处理**：配置文件中的`error_threshold`可设置错误通知阈值

## 开发路线图

- [ ] 增加更多数据源支持
- [ ] 优化数据更新性能
- [ ] 添加API接口
- [ ] 开发移动端应用
- [ ] 集成更多AI分析模型

## 许可证

本项目为开源项目，具体许可证信息请查看LICENSE文件（如有）。

## 贡献指南

欢迎提交Issue和Pull Request来改进本项目。

---

*最后更新：2026年3月17日*