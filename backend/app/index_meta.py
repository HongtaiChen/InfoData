#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""指数元数据常量 —— API 层与采集器共用的「单一来源」

为什么单独放一个模块：同一个指数清单有三个消费点 ——
  ① 成分采集（collectors/index_cons_sync.py 的 CSINDEX_CONS / CNINDEX_CONS / CNI_ONLY）
  ② 详情接口的「口径不适用」说明（api/market.py）
  ③ 释义档案 seed（setup_index_tables.py 的 SEED）
历史教训：**写死的清单 vs 动态的行情表（dc_index_market 采集决定）已漂移两次**
（2026-09-19 释义 seed 少 8 个、2026-09-20 成分清单少 9 个）。
常量集中在无重依赖的地方（不放 collectors —— 那里 `import akshare` 会让 API 冷启动
多背几秒与几十 MB 内存），两边导入同一份，改一处即生效。

新增指数上市场页时的三处同步义务，见 collectors/index_cons_sync.py 文件头「维护约定」。
"""

# 非股票指数：成分口径**不适用**（不是「数据暂缺」——那是我们没采到，这是概念本身没有股票成分）
# 两者必须区分：把设计上正确的结果显示成故障，会误导使用者去「修一个不存在的问题」。
NON_EQUITY_INDEXES: dict[str, str] = {
    "000832": "中证转债为债券指数，成分是可转换公司债券而非股票，无股票成分与行业分布",
}

# 全市场派生指数：成分按交易所全体上市股实时派生，不落成分快照表
DERIVED_INDEXES: dict[str, str] = {
    "000001": "上证指数为全市场指数，成分股按「沪市全部上市股」实时派生（不落快照表）",
}
