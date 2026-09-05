#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
InvestBuddy 财经日历（投资大事）采集器（finance_calendar 表）
- 旧源 JY（聚源）失效，v1 换源：东财数据中心「财经日历」datacenter-web.eastmoney.com RPT_CPH_FECALENDAR
  内容与旧库一致（全球会议/会展/外事/经济数据发布等投资日历大事），已实测可用
- 策略：每日拉 [今天, 今天+days_ahead) 窗口；先删该窗口内本源旧行再插入，幂等无重复
- data_source = 'EM-CAL'（与历史 'JY' 区分）
"""
import logging
from datetime import date, timedelta

import pymysql
import requests

from ..db import get_db_config

logger = logging.getLogger(__name__)

API = "https://datacenter-web.eastmoney.com/api/data/v1/get"
SOURCE_TAG = "EM-CAL"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Referer": "https://data.eastmoney.com/cjrl/",
}


def _page(page_number: int, start: str, end_after: str, page_size: int = 200) -> dict:
    resp = requests.get(
        API,
        params={
            "reportName": "RPT_CPH_FECALENDAR",
            "columns": "ALL",
            "pageNumber": page_number,
            "pageSize": page_size,
            "sortColumns": "START_DATE",
            "sortTypes": "1",
            "filter": f"(END_DATE>='{start}')(START_DATE<'{end_after}')",
            "source": "WEB",
            "client": "WEB",
        },
        headers=HEADERS,
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json()


class FinanceCalendarSyncCollector:
    """财经日历同步（换源 EM-CAL）"""

    def __init__(self, days_ahead: int = 60):
        self.days_ahead = days_ahead

    def run(self) -> dict:
        today = date.today()
        start_s = today.isoformat()
        end_s = (today + timedelta(days=self.days_ahead)).isoformat()

        items: list[tuple] = []
        page = 1
        pages = 1
        while page <= pages:
            data = _page(page, start_s, end_s)
            result = data.get("result") or {}
            if not data.get("success") or not result:
                raise RuntimeError(f"东财财经日历接口异常: {data.get('message') or data.get('code')}")
            pages = int(result.get("pages") or 1)
            for r in result.get("data") or []:
                d = str(r.get("START_DATE") or "")[:10]
                title = r.get("FE_NAME")
                if not d or not title:
                    continue
                content = r.get("CONTENT") or ""
                items.append((d, str(title).strip(), str(content).strip() if content else None))
            page += 1
            if page > 1 and page > pages:
                break

        if not items:
            logger.warning(f"⚠️ 财经日历窗口 {start_s}~{end_s} 无数据（接口可能变更）")
            raise RuntimeError("财经日历接口返回空")

        conn = pymysql.connect(**get_db_config().to_dict())
        try:
            with conn.cursor() as cur:
                # 幂等：删除本窗口内本源的旧行（含历史同窗口残留），避免重复
                cur.execute(
                    "DELETE FROM finance_calendar WHERE data_source=%s AND event_date>=%s",
                    (SOURCE_TAG, today),
                )
                cur.executemany(
                    "INSERT INTO finance_calendar (event_date, title, content, update_time, data_source) "
                    "VALUES (%s, %s, %s, NOW(), %s)",
                    [(d, t, c, SOURCE_TAG) for d, t, c in items],
                )
            conn.commit()
            logger.info(f"✅ 财经日历更新 {len(items)} 条（{start_s} 起 {self.days_ahead} 天，东财源）")
            return {
                "records_written": len(items),
                "error_count": 0,
                "errors": [],
                "note": f"财经日历 {len(items)} 条（EM-CAL 源，窗口 {start_s}~{end_s}）",
            }
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
