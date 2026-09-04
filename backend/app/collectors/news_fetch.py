#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
InfoData 资讯采集器（财联社 cls + 东财 em 双源，news 表）
- 东财快讯：np-listapi.eastmoney.com/comm/web/getFastNewsList（已验证连通）
- 财联社：走 akshare stock_info_global_cls（内部签名），失败仅告警不中断
- 去重：按 (title, published_at) 判重（news 表无唯一键，不改表结构）
"""
import logging
import os
import random
import time
from datetime import datetime

import pymysql
import requests

from ..db import get_db_config

os.environ["NO_PROXY"] = "*"
os.environ["no_proxy"] = "*"

logger = logging.getLogger(__name__)

EM_API = "https://np-listapi.eastmoney.com/comm/web/getFastNewsList"
EM_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Referer": "https://finance.eastmoney.com/",
}


class NewsFetchCollector:
    """资讯采集器"""

    def __init__(self, sources: list[str] | None = None, max_pages: int = 3, page_size: int = 50):
        self.sources = [s.lower() for s in (sources or ["em"])]
        self.max_pages = max_pages
        self.page_size = page_size

    # ---------- 数据源 ----------
    def _fetch_em(self) -> list[dict]:
        """东财快讯：翻页拉取，sortEnd 游标"""
        items: list[dict] = []
        sort_end = ""
        for _ in range(self.max_pages):
            try:
                resp = requests.get(
                    EM_API,
                    params={
                        "client": "web", "biz": "web_724", "fastColumn": "102",
                        "sortEnd": sort_end, "pageSize": self.page_size, "req_trace": "1",
                    },
                    headers=EM_HEADERS,
                    timeout=15,
                )
                data = (resp.json() or {}).get("data") or {}
                batch = data.get("fastNewsList") or []
                if not batch:
                    break
                for it in batch:
                    summary = (it.get("summary") or "").strip()
                    title = (it.get("title") or "").strip()
                    if not summary and not title:
                        continue
                    code = str(it.get("code") or "")
                    items.append({
                        "title": title or summary[:80],
                        "content": summary,
                        "source": "em",
                        "published_at": it.get("showTime"),
                        # 东财文章 ID 拼唯一 URL（news 表 uk_source_url 唯一键依赖 url 非空）
                        "url": f"https://finance.eastmoney.com/a/{code}.html" if code else "",
                    })
                sort_end = batch[-1].get("realSort") or ""
                if not sort_end:
                    break
                time.sleep(random.uniform(0.3, 0.8))
            except Exception as e:  # 单页失败不中断
                logger.warning(f"⚠️ 东财资讯第 {len(items) // self.page_size + 1} 页失败: {e}")
                break
        logger.info(f"东财快讯拉到 {len(items)} 条")
        return items

    def _fetch_cls(self) -> list[dict]:
        """财联社电报：akshare stock_info_global_cls（已实测可用，返回最新 20 条）
        财联社电报无独立文章页，url 用内容指纹拼伪链接（news 表 uk_source_url 要求非空）"""
        try:
            import hashlib

            import akshare as ak

            df = ak.stock_info_global_cls()
            if df is None or df.empty:
                return []
            out = []
            for _, r in df.iterrows():
                content = str(r.get("内容", "")).strip()
                title = str(r.get("标题", "")).strip() or content[:80]
                if not title and not content:
                    continue
                # 财联社发布日期与时间分列：如 2026-09-03 + 19:21:15
                pub_date = str(r.get("发布日期", "")).strip()
                pub_time = str(r.get("发布时间", "")).strip().replace("/", "-")
                published = (pub_date + " " + pub_time).strip() if pub_time else pub_date
                fp = hashlib.md5(f"{title}|{published}".encode("utf-8")).hexdigest()[:16]
                out.append({
                    "title": title,
                    "content": content,
                    "source": "cls",
                    "published_at": published,
                    "url": f"https://www.cls.cn/telegraph/{fp}",
                })
            logger.info(f"财联社拉到 {len(out)} 条")
            return out
        except Exception as e:
            logger.warning(f"⚠️ 财联社拉取失败（跳过，仅东财源）：{e}")
            return []

    # ---------- 入库 ----------
    def _insert_if_new(self, cur, item: dict) -> bool:
        try:
            published_at = (
                datetime.strptime(item["published_at"][:19], "%Y-%m-%d %H:%M:%S")
                if item.get("published_at")
                else datetime.now()
            )
        except (ValueError, TypeError):
            published_at = datetime.now()
        # 判重一：标题+时间（同源同条重复）
        cur.execute(
            "SELECT 1 FROM news WHERE title=%s AND published_at=%s LIMIT 1",
            (item["title"][:500], published_at),
        )
        if cur.fetchone():
            return False
        # 判重二：INSERT IGNORE 兜底唯一键 (source,url)——并发/调度器同时触发时防撞键
        cur.execute(
            "INSERT IGNORE INTO news (title, source, published_at, content, url) VALUES (%s,%s,%s,%s,%s)",
            (item["title"][:500], item["source"], published_at, item.get("content") or "", item.get("url") or ""),
        )
        return cur.rowcount > 0

    def run(self) -> dict:
        fetched: list[dict] = []
        source_errors: list[str] = []
        for src in self.sources:
            if src == "em":
                fetched.extend(self._fetch_em())
            elif src == "cls":
                items = self._fetch_cls()
                fetched.extend(items)
                if not items:
                    source_errors.append("财联社接口不可用（akshare 通道）")
            else:
                source_errors.append(f"未知资讯源 {src}")

        if not fetched:
            raise RuntimeError("资讯拉取为空：" + ("；".join(source_errors) or "所有源均无数据"))

        conn = pymysql.connect(**get_db_config().to_dict())
        inserted = dup = 0
        try:
            with conn.cursor() as cur:
                for item in fetched:
                    if self._insert_if_new(cur, item):
                        inserted += 1
                    else:
                        dup += 1
                conn.commit()
            logger.info(f"✅ 资讯入库：新增 {inserted} 条，去重 {dup} 条")
            note = f"来源 {self.sources}：新增 {inserted} / 去重 {dup}"
            if source_errors:
                note += "；" + "；".join(source_errors)
            return {"records_written": inserted, "error_count": 0, "errors": [], "note": note}
        finally:
            conn.close()
