#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""分析模块共用的进程内 TTL 缓存

**为什么需要**：分析模块里有几处取数要扫大表，而结果在**同一交易日内是确定不变的**——
最典型的是「板块轮动」：个股日线表 `stock_market_daily` 按 stock_code 顺序扫 1,622 万条索引项
才能取到两市快照（单次约 8 秒），而分析页每次切窗口/切参数都会重发请求。
没有缓存时，用户每点一次就要等 8 秒。

**边界（刻意写清楚，避免被误当通用缓存）**：
- 进程内缓存：多 worker 部署时每个进程各存一份。结果确定性一致，只是各自算一次，无一致性问题。
- 数据更新后最长 TTL 秒内仍返回旧值。目标模块全是**日频、盘后更新**的分析视图，完全可接受。
- 调用参数（含 as_of 历史回放锚点）参与 key，不同参数天然隔离、互不污染。

用法：
    @ttl_cache(600)
    def heavy_query(as_of: str | None = None) -> dict: ...
"""
from __future__ import annotations

import threading
import time
from functools import wraps


def ttl_cache(ttl: float = 300.0, max_entries: int = 64):
    """进程内 TTL 缓存装饰器（线程安全）

    ⚠️ 被装饰函数的参数必须可哈希（本项目实际传入 str / int / None，均满足）。
    """
    def deco(fn):
        store: dict = {}
        lock = threading.Lock()

        @wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                key = (args, tuple(sorted(kwargs.items())))
                hash(key)
            except TypeError:                 # 参数不可哈希 → 直接透传，不缓存
                return fn(*args, **kwargs)
            now = time.time()
            with lock:
                hit = store.get(key)
                if hit and now - hit[0] < ttl:
                    return hit[1]
            val = fn(*args, **kwargs)
            with lock:
                if len(store) >= max_entries:
                    store.clear()             # 简单策略：满则整体清空（条目极少，无需 LRU）
                store[key] = (now, val)
            return val

        wrapper.cache_clear = lambda: store.clear()   # type: ignore[attr-defined]
        return wrapper

    return deco
