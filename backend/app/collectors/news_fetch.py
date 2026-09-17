#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
InvestBuddy 资讯采集器（财联社 cls + 东财 em 双源，news 表）
- 东财快讯：np-listapi.eastmoney.com/comm/web/getFastNewsList（已验证连通）
- 财联社：走 akshare stock_info_global_cls（内部签名），失败仅告警不中断
- 去重：按 (title, published_at) 判重（news 表无唯一键，不改表结构）

⚠️ 回看补采（2026-09-17 加）：资讯是**唯一离线即永久丢失**的数据通道——
日线可逐股补 500 天、指数回看 7 天、物化表可全史重算，而本采集器原本只翻
最新 max_pages×page_size = 150 条滚动窗口，一旦离线超过该窗口（实测日产量
240~655 条，即 >18 小时）中间段落就再也取不回来。

改造：以 `news.MAX(published_at)` 为**水位线**，离线时长超过
BACKFILL_TRIGGER_HOURS 时把翻页上限提到 MAX_PAGES_BACKFILL，
并翻到「批次最早一条 <= 水位线」为止（不靠估算，按实际数据停）。
"""
import logging
import os
import random
import time
from datetime import datetime

import pymysql
import requests

from ..db import get_db_config
from ._common import with_steps

os.environ["NO_PROXY"] = "*"
os.environ["no_proxy"] = "*"

logger = logging.getLogger(__name__)

# 运行步骤链模板（供前端「数据流·整链拓扑」展示运行逻辑）
RUN_STEPS = [
    {"no": 1, "name": "读取水位线并抓取东财快讯", "params": "MAX(published_at) 判离线时长 → 动态决定翻页深度（sortEnd 游标，离线则加深回看）"},
    {"no": 2, "name": "抓取财联社电报", "params": "akshare stock_info_global_cls（签名通道，仅最新 20 条，失败仅告警不中断）"},
    {"no": 3, "name": "双源合并判重", "params": "标题+发布时间 MD5 判重；INSERT IGNORE 兜底 (source,url)"},
    {"no": 4, "name": "逐条入库", "params": "news 表（title/source/published_at/content/url）"},
]

EM_API = "https://np-listapi.eastmoney.com/comm/web/getFastNewsList"
EM_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Referer": "https://finance.eastmoney.com/",
}

# 回看补采参数：水位线（库内最新资讯）距现在超过 BACKFILL_TRIGGER_HOURS 即判定
# 「离线过」，把翻页上限提到 MAX_PAGES_BACKFILL 页（20 × 50 = 1000 条，覆盖 1~2 天缺口）。
# 只有东财源支持回看（sortEnd 游标可一直往前翻）；财联社通道只返回最新 20 条。
BACKFILL_TRIGGER_HOURS = 2
MAX_PAGES_BACKFILL = 20


def _parse_dt(s) -> datetime | None:
    """解析东财 showTime（如 '2026-09-17 19:30:00'）→ datetime；失败返回 None"""
    if not s:
        return None
    try:
        return datetime.strptime(str(s)[:19], "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return None


class NewsFetchCollector:
    """资讯采集器"""

    def __init__(self, sources: list[str] | None = None, max_pages: int = 3, page_size: int = 50,
                 max_pages_backfill: int = MAX_PAGES_BACKFILL):
        self.sources = [s.lower() for s in (sources or ["em"])]
        self.max_pages = max_pages
        self.page_size = page_size
        self.max_pages_backfill = max_pages_backfill   # 回看补采时的翻页上限

    # ---------- 数据源 ----------
    def _fetch_em(self, max_pages: int | None = None, stop_before: datetime | None = None) -> list[dict]:
        """东财快讯：翻页拉取，sortEnd 游标

        max_pages:   本次翻页上限（默认 self.max_pages；回看补采时用更大值）
        stop_before: 水位线。当某批返回的最早一条时间已 <= 水位线，说明缺口已补齐，
                     立即停止翻页——不靠"离线时长估算页数"，按实际数据停，避免多翻。
        """
        items: list[dict] = []
        sort_end = ""
        pages = max_pages or self.max_pages
        for page in range(pages):
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
                # 回看补采：本批已翻到水位线之前 → 缺口补齐，停止翻页
                if stop_before is not None:
                    stamps = [t for t in (_parse_dt(it.get("showTime")) for it in batch) if t]
                    if stamps and min(stamps) <= stop_before:
                        logger.info(
                            f"📚 回看补采：第 {page + 1} 页最早 {min(stamps)} 已到水位线 "
                            f"{stop_before}，停止翻页"
                        )
                        break
                sort_end = batch[-1].get("realSort") or ""
                if not sort_end:
                    break
                time.sleep(random.uniform(0.3, 0.8))
            except Exception as e:  # 单页失败不中断
                logger.warning(f"⚠️ 东财资讯第 {page + 1} 页失败: {e}")
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

    @staticmethod
    def _read_watermark():
        """库内最新资讯发布时间（回看补采的水位线）；表为空或无数据返回 None"""
        conn = pymysql.connect(**get_db_config().to_dict())
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT MAX(published_at) FROM news")
                row = cur.fetchone()
                return row[0] if row else None
        finally:
            conn.close()

    def run(self) -> dict:
        # 回看补采：资讯是唯一「离线即永久丢失」的通道，故按离线时长加深翻页。
        # 水位线 = 库内最新发布时间。离线超过 BACKFILL_TRIGGER_HOURS 才启用，
        # 正常在线时行为与改动前完全一致（仍是最多 self.max_pages 页）。
        watermark = self._read_watermark()
        gap_hours = None
        stop_before = None
        pages = self.max_pages
        if watermark is not None:
            gap_hours = (datetime.now() - watermark).total_seconds() / 3600
            if gap_hours > BACKFILL_TRIGGER_HOURS:
                pages = max(self.max_pages, self.max_pages_backfill)
                stop_before = watermark
                logger.info(
                    f"📚 回看补采：库内最新资讯 {watermark}（{gap_hours:.1f} 小时前，"
                    f"超阈值 {BACKFILL_TRIGGER_HOURS}h）→ 翻页上限提到 {pages} 页，翻到水位线为止"
                )

        fetched: list[dict] = []
        source_errors: list[str] = []
        src_stat: dict[str, int] = {}
        for src in self.sources:
            before = len(fetched)
            if src == "em":
                fetched.extend(self._fetch_em(pages, stop_before))
            elif src == "cls":
                items = self._fetch_cls()
                fetched.extend(items)
                if not items:
                    source_errors.append("财联社接口不可用（akshare 通道）")
            else:
                source_errors.append(f"未知资讯源 {src}")
            src_stat[src] = len(fetched) - before

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
            if stop_before is not None:
                note += (
                    f"；📚 回看补采（离线 {gap_hours:.1f}h，水位线 {stop_before}，翻页 {pages} 页）"
                )
            if source_errors:
                note += "；" + "；".join(source_errors)
            return with_steps(
                {"records_written": inserted, "error_count": 0, "errors": [], "note": note},
                RUN_STEPS,
                {
                    1: (f"{src_stat.get('em', 0)} 条（翻页 {pages} 页"
                        + (f"，回看至 {stop_before}" if stop_before else "") + "）")
                       if "em" in self.sources else "未启用",
                    2: f"{src_stat.get('cls', 0)} 条" if "cls" in self.sources else "未启用",
                    3: f"合并 {len(fetched)} 条",
                    4: f"新增 {inserted} · 去重 {dup}",
                },
            )
        finally:
            conn.close()
