#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""采集器外部 HTTP 工具（「货币流动性·数量维度」新增源共用）

模块名带下划线前缀，避免与任务模块混淆。

⚠️⚠️ 本模块存在的**唯一理由**（2026-09-25 实测，勿删改）：

  本机环境变量里配了 HTTP 代理（`http_proxy` / `https_proxy` / `all_proxy` / 大写同名），
  该代理**会阻断**本域新增的几个国际数据源 —— 实测必须 `unset` 掉全部 proxy 变量才能直连成功：

    · DBnomics        api.db.nomics.world              （免 Key，美国/欧元区货币总量）
    · 美联储官网       www.federalreserve.gov           （H.4.1 / H.6 交叉校验）
    · 纽约联储         markets.newyorkfed.org           （SOFR / EFFR / ON RRP）
    · 美财政部         api.fiscaldata.treasury.gov       （TGA / 国债总额）
    · BIS             stats.bis.org                     （全球流动性指标 WS_GLI）

  而采集器跑在调度器进程里，**不能依赖调用方先 unset**（探针脚本可以手工 unset，
  生产任务不行）。故必须在会话层显式 `trust_env = False` + `proxies = {}` 绕开。

  对比：项目既有的新浪 / 东财 / akshare 源走境内网络，本来就不受影响，
  所以既有采集器没有这层处理 —— 这是本域新增源特有的工程约束。
"""
import json
import logging
import time

import requests

logger = logging.getLogger(__name__)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


def make_session() -> requests.Session:
    """建一个**绕过环境代理**的会话（见模块头说明）"""
    s = requests.Session()
    s.trust_env = False          # 忽略 HTTP_PROXY / NO_PROXY / REQUESTS_CA_BUNDLE 等环境变量
    s.proxies = {}
    s.headers.update({"User-Agent": UA, "Accept": "*/*", "Accept-Language": "en-US,en;q=0.9"})
    return s


def get_text(url: str, *, timeout: float = 60, attempts: int = 3, base_delay: float = 1.5,
             headers: dict | None = None, encoding: str | None = None,
             session: requests.Session | None = None) -> str:
    """GET → 文本，带指数退避重试。全部失败时抛出最后一次异常。

    headers 会**合并**进会话默认头（不覆盖 UA，除非显式传）。encoding 非空时强制解码。
    """
    s = session or make_session()
    last: Exception | None = None
    for i in range(max(1, attempts)):
        try:
            r = s.get(url, timeout=timeout, headers=headers or {})
            r.raise_for_status()
            if encoding:
                r.encoding = encoding
            return r.text
        except Exception as e:                      # noqa: BLE001 - 重试后原样抛出
            last = e
            if i < max(1, attempts) - 1:
                time.sleep(base_delay * (2 ** i))
    assert last is not None
    raise last


def get_json(url: str, *, timeout: float = 60, attempts: int = 3, base_delay: float = 1.5,
             headers: dict | None = None, session: requests.Session | None = None):
    """GET → 解析后的 JSON。**不截断响应体**（截断会制造「空数组」假象，本项目踩过）。"""
    s = session or make_session()
    last: Exception | None = None
    for i in range(max(1, attempts)):
        try:
            r = s.get(url, timeout=timeout, headers=headers or {})
            r.raise_for_status()
            return json.loads(r.text)
        except Exception as e:                      # noqa: BLE001
            last = e
            if i < max(1, attempts) - 1:
                time.sleep(base_delay * (2 ** i))
    assert last is not None
    raise last
