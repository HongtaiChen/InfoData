# InfoData - API接口规范

## 1. API设计原则

### 1.1 RESTful设计
- **资源导向**：所有API围绕资源设计，使用名词复数形式
- **HTTP方法语义化**：
  - `GET`：获取资源
  - `POST`：创建资源
  - `PUT`：更新整个资源
  - `PATCH`：部分更新资源
  - `DELETE`：删除资源
- **版本控制**：API版本通过URL路径(`/api/v1/`)或请求头控制
- **状态码标准化**：使用标准HTTP状态码

### 1.2 一致性和可预测性
- **URL命名规范**：小写字母，短横线分隔，语义清晰
- **参数命名**：蛇形命名法(snake_case)
- **响应格式**：统一JSON格式，包含状态码和消息
- **错误处理**：统一错误响应结构

### 1.3 安全性和性能
- **认证授权**：所有API需要认证(除公共接口)
- **速率限制**：防止滥用，保护后端服务
- **缓存策略**：适当使用HTTP缓存头
- **数据压缩**：支持gzip压缩

## 2. API端点设计

### 2.1 数据采集API
```
# 数据源管理
GET    /api/v1/data-sources           # 获取数据源列表
POST   /api/v1/data-sources           # 创建数据源配置
GET    /api/v1/data-sources/{id}      # 获取数据源详情
PUT    /api/v1/data-sources/{id}      # 更新数据源配置
DELETE /api/v1/data-sources/{id}      # 删除数据源

# 采集任务管理
GET    /api/v1/collection-tasks       # 获取采集任务列表
POST   /api/v1/collection-tasks       # 创建采集任务
GET    /api/v1/collection-tasks/{id}  # 获取任务详情
PUT    /api/v1/collection-tasks/{id}  # 更新任务配置
POST   /api/v1/collection-tasks/{id}/run  # 手动执行任务
DELETE /api/v1/collection-tasks/{id}  # 删除任务

# 采集状态监控
GET    /api/v1/collection-status      # 获取采集状态概览
GET    /api/v1/collection-logs        # 获取采集日志
GET    /api/v1/collection-metrics     # 获取采集性能指标
```

### 2.2 数据查询API
```
# 股票数据
GET    /api/v1/stocks                 # 获取股票列表
GET    /api/v1/stocks/{code}          # 获取单只股票信息
GET    /api/v1/stocks/{code}/history  # 获取股票历史数据
GET    /api/v1/stocks/{code}/realtime # 获取股票实时数据

# 基金数据
GET    /api/v1/funds                  # 获取基金列表
GET    /api/v1/funds/{code}           # 获取单只基金信息
GET    /api/v1/funds/{code}/nav       # 获取基金净值数据

# 债券数据
GET    /api/v1/bonds                  # 获取债券列表
GET    /api/v1/bonds/{code}           # 获取单只债券信息

# 通用数据查询
GET    /api/v1/data/search            # 搜索金融数据
GET    /api/v1/data/export            # 导出数据
```

### 2.3 数据分析API
```
# 分析任务管理
GET    /api/v1/analysis-tasks         # 获取分析任务列表
POST   /api/v1/analysis-tasks         # 创建分析任务
GET    /api/v1/analysis-tasks/{id}    # 获取任务详情
DELETE /api/v1/analysis-tasks/{id}    # 删除分析任务

# 分析结果查询
GET    /api/v1/analysis/results       # 获取分析结果列表
GET    /api/v1/analysis/results/{id}  # 获取分析结果详情
GET    /api/v1/analysis/reports/{id}  # 获取分析报告

# 指标计算
POST   /api/v1/analysis/indicators    # 计算金融指标
GET    /api/v1/analysis/trends        # 分析趋势数据
POST   /api/v1/analysis/compare       # 数据对比分析
```

### 2.4 可视化API
```
# 图表生成
POST   /api/v1/charts                 # 创建图表
GET    /api/v1/charts/{id}            # 获取图表详情
GET    /api/v1/charts/{id}/image      # 获取图表图片
DELETE /api/v1/charts/{id}            # 删除图表

# 报告生成
POST   /api/v1/reports                # 创建报告
GET    /api/v1/reports/{id}           # 获取报告详情
GET    /api/v1/reports/{id}/download  # 下载报告文件

# 模板管理
GET    /api/v1/chart-templates        # 获取图表模板
POST   /api/v1/chart-templates        # 创建图表模板
PUT    /api/v1/chart-templates/{id}   # 更新模板
DELETE /api/v1/chart-templates/{id}   # 删除模板
```

### 2.5 系统管理API
```
# 用户管理
GET    /api/v1/users                  # 获取用户列表
POST   /api/v1/users                  # 创建用户
GET    /api/v1/users/{id}             # 获取用户详情
PUT    /api/v1/users/{id}             # 更新用户信息
DELETE /api/v1/users/{id}             # 删除用户

# 权限管理
GET    /api/v1/roles                  # 获取角色列表
GET    /api/v1/permissions            # 获取权限列表

# 系统状态
GET    /api/v1/system/health          # 系统健康检查
GET    /api/v1/system/metrics         # 系统性能指标
GET    /api/v1/system/logs            # 系统日志查询
GET    /api/v1/system/config          # 系统配置查看
```

## 3. 请求和响应规范

### 3.1 通用请求头
```http
Accept: application/json
Content-Type: application/json
Authorization: Bearer {access_token}
X-Request-ID: {unique_request_id}
X-API-Version: v1
```

### 3.2 通用响应格式
**成功响应**：
```json
{
  "code": 200,
  "message": "成功",
  "data": {
    // 实际数据
  },
  "meta": {
    "total": 100,
    "page": 1,
    "per_page": 20,
    "total_pages": 5
  },
  "timestamp": "2026-03-17T21:30:00Z"
}
```

**错误响应**：
```json
{
  "code": 400,
  "message": "请求参数错误",
  "errors": [
    {
      "field": "username",
      "message": "用户名不能为空"
    }
  ],
  "timestamp": "2026-03-17T21:30:00Z"
}
```

### 3.3 分页参数
- `page`: 页码，从1开始
- `per_page`: 每页记录数，默认20，最大100
- `sort_by`: 排序字段
- `sort_order`: 排序方向，asc/desc

### 3.4 过滤参数
- `filter[key]=value`: 精确匹配过滤
- `filter[key__gt]=value`: 大于过滤
- `filter[key__lt]=value`: 小于过滤
- `filter[key__like]=value`: 模糊匹配
- `filter[key__in]=value1,value2`: 多值匹配

### 3.5 字段选择
- `fields=id,name,created_at`: 选择返回字段
- `exclude=password,token`: 排除敏感字段

## 4. 认证和授权

### 4.1 认证方式
**Bearer Token认证**：
```http
Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
```

**API Key认证**（仅限服务间调用）：
```http
X-API-Key: your_api_key_here
```

### 4.2 权限控制
- **公开接口**：无需认证，如健康检查、公开数据查询
- **用户接口**：需要用户认证，如个人数据分析
- **管理接口**：需要管理员权限，如系统配置

### 4.3 令牌管理
- **访问令牌(access_token)**：有效期2小时
- **刷新令牌(refresh_token)**：有效期7天
- **令牌刷新接口**：`POST /api/v1/auth/refresh`

## 5. 数据验证

### 5.1 输入验证规则
```python
# 示例验证规则
validation_rules = {
    "username": {
        "type": "string",
        "required": True,
        "min_length": 3,
        "max_length": 50,
        "pattern": r"^[a-zA-Z0-9_-]+$"
    },
    "email": {
        "type": "string",
        "required": True,
        "format": "email"
    },
    "age": {
        "type": "integer",
        "required": False,
        "min": 0,
        "max": 150
    }
}
```

### 5.2 数据清洗
- 去除前后空格
- 转换空字符串为None
- 数据类型转换
- HTML标签转义

## 6. 错误处理

### 6.1 标准HTTP状态码
- `200 OK`: 请求成功
- `201 Created`: 资源创建成功
- `204 No Content`: 请求成功，无返回内容
- `400 Bad Request`: 请求参数错误
- `401 Unauthorized`: 未认证
- `403 Forbidden`: 无权限
- `404 Not Found`: 资源不存在
- `429 Too Many Requests`: 请求过于频繁
- `500 Internal Server Error`: 服务器内部错误

### 6.2 自定义错误码
- `1001`: 数据验证失败
- `1002`: 数据不存在
- `1003`: 数据重复
- `2001`: 认证失败
- `2002`: 令牌过期
- `2003`: 权限不足
- `3001`: 外部服务错误
- `3002`: 数据库错误
- `4001`: 业务逻辑错误

## 7. 版本管理

### 7.1 版本控制策略
- URL路径版本控制：`/api/v1/resource`
- 请求头版本控制：`Accept: application/vnd.infodata.v1+json`
- 默认支持最新稳定版本
- 旧版本维护6个月后废弃

### 7.2 版本迁移指南
- 新功能在新版本中添加
- 不兼容变更需要创建新版本
- 提供版本迁移工具和文档
- 通知用户版本废弃时间表

## 8. 性能优化

### 8.1 缓存策略
- **响应缓存**：公共数据缓存5分钟
- **查询缓存**：复杂查询结果缓存
- **CDN缓存**：静态资源CDN加速
- **浏览器缓存**：适当设置缓存头

### 8.2 压缩和优化
- **响应压缩**：gzip压缩
- **图片优化**：WebP格式支持
- **数据分页**：避免大数据量传输
- **字段选择**：仅返回必要字段

### 8.3 监控和限流
- **API调用统计**：调用次数、响应时间
- **错误率监控**：不同API错误率
- **速率限制**：基于IP和用户限制
- **配额管理**：API使用配额控制

---
**版本**: 1.0.0  
**更新日期**: 2026-03-17  
**依据**: InfoData架构规范和需求规范