#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
InvestBuddy 财经日历（投资大事）采集器（finance_calendar 表）
- 旧源 JY（聚源）失效，v1 换源：东财数据中心「财经日历」datacenter-web.eastmoney.com RPT_CPH_FECALENDAR
  内容与旧库一致（全球会议/会展/外事/经济数据发布等投资日历大事），已实测可用
- 策略：每日拉 [今天, 今天+days_ahead) 窗口；先删该窗口内本源旧行再插入，幂等无重复
- data_source = 'EM-CAL'（与历史 'JY' 区分）

写入侧容错（2026-10-08 立）—— 两个改动都源于**同一次真实故障**：
  09-28~10-07 服务停摆期间，本任务在 09-25 单日失败 10 次、10-08 又失败，报
  `(1406, "Data too long for column 'content' at row 1")`。根因不是"偶尔超长"，
  而是**字段宽度与消费口径不同源** + **整批提交把单行错误放大成整批回滚**：
    · 上游 CONTENT 是事件通知**全文**，长度天然无上界（实测 max 597，且只有 1/734 条超 400）；
    · 原实现 `executemany` 整批提交 ⇒ 1 条超长打回 734 条；
    · DELETE 与 INSERT 同在**一个事务**、失败一起回滚 ⇒ 旧窗口原样保留 ⇒
      前端日历照常有数据 ⇒ **整条链上没有任何一环会报错**（静默失败）。
  ⇒ 处置：落库前按消费口径截断（见 CONTENT_MAX），并把写入改成**逐行容错**。
"""
import logging
from datetime import date, timedelta

import pymysql
import requests

from ..db import get_db_config
from ._common import with_steps

logger = logging.getLogger(__name__)

# 运行步骤链模板（供前端「数据流·整链拓扑」展示运行逻辑）
RUN_STEPS = [
    {"no": 1, "name": "计算拉取窗口", "params": "[今天, 今天+days_ahead 天)，默认 60 天"},
    {"no": 2, "name": "分页拉取东财日历", "params": "datacenter-web RPT_CPH_FECALENDAR（每页 200）"},
    {"no": 3, "name": "字段清洗", "params": "START_DATE/FE_NAME/CONTENT → event_date/title/content（空白折叠后按上限截断）"},
    {"no": 4, "name": "幂等重建", "params": "DELETE 本窗口本源旧行（EM-CAL）→ **逐行** INSERT（单行失败不炸整批）"},
]

API = "https://datacenter-web.eastmoney.com/api/data/v1/get"
SOURCE_TAG = "EM-CAL"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Referer": "https://data.eastmoney.com/cjrl/",
}

# 列宽上限，与 DB 的 varchar(N) 一一对应（收窄前必须同时改 DB，见 _scratch/_fix_fincal_schema.py）
TITLE_MAX = 200
# 内容上限：与**消费端口径同源**（2026-10-08 定）
#   查遍全部消费方，没有任何一处需要全文，真实上限止于 600：
#     · 分析层 `concept_ai._call_doubao` 送 AI 时取 content[:600] ← 最大需求
#     · 分析结果落库取 [:400]、摘要取 [:120]
#     · 前端 `CalendarView .evt-content` 用 -webkit-line-clamp:3 展示（≈120 字符）
#   ⇒ 落库即截到 600：上游再怎么膨胀都不会越界，且**落库内容与 AI 读到的内容同源**。
#     此前列宽是 400 而 AI 读 600，是个"既存不下全文、又给不了 AI 要的量"的中间态；
#     单纯把列放宽只是把炸点往后推（上游长度无上界 ⇒ 任何 varchar(N) 都是赌）。
CONTENT_MAX = 600


def _clean_text(raw, limit: int) -> tuple[str | None, bool]:
    """归一化 + 截断，返回 (值, 是否发生截断)

    归一化的理由：这几列的下游**都把它当单行片段**用（三行截断的 div、AI 前缀切片、
    DB 前缀切片），而源数据带换行与缩进 ⇒ 不折叠就等于让空白字符白占上限预算、
    也会把展示搞成锯齿状。
    """
    if raw is None:
        return None, False
    s = " ".join(str(raw).split())      # strip + 折叠所有空白（含换行/制表）
    if not s:
        return None, False
    if len(s) > limit:
        return s[:limit], True
    return s, False


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
        truncated = 0
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
                title_s, cut_t = _clean_text(title, TITLE_MAX)
                content, cut_c = _clean_text(r.get("CONTENT"), CONTENT_MAX)
                if cut_t or cut_c:
                    truncated += 1
                items.append((d, title_s, content))
            page += 1
            if page > 1 and page > pages:
                break

        if not items:
            logger.warning(f"⚠️ 财经日历窗口 {start_s}~{end_s} 无数据（接口可能变更）")
            raise RuntimeError("财经日历接口返回空")

        conn = pymysql.connect(**get_db_config().to_dict())
        errors: list[str] = []
        try:
            with conn.cursor() as cur:
                # 幂等：删除本窗口内本源的旧行（含历史同窗口残留），避免重复
                cur.execute(
                    "DELETE FROM finance_calendar WHERE data_source=%s AND event_date>=%s",
                    (SOURCE_TAG, today),
                )
                # ⚠️ 逐行 INSERT（原为 executemany 整批）：MySQL 的 1406(值过长) / 1366(值非法)
                #    等**行级**错误在 executemany 下会让**整批**一起失败 —— 实测 2026-09-25
                #    单日因此失败 10 次，而表内窗口一直停在旧值（前端照常有数据 ⇒ 静默）。
                #    逐行 + try/except 后，异常只影响该行，其余照常落库。
                written = 0
                for d, title_s, content in items:
                    try:
                        cur.execute(
                            "INSERT INTO finance_calendar (event_date, title, content, update_time, data_source) "
                            "VALUES (%s, %s, %s, NOW(), %s)",
                            (d, title_s, content, SOURCE_TAG),
                        )
                        written += 1
                    except Exception as exc:  # noqa: BLE001 —— 行级容错：不让单行炸掉整批
                        errors.append(f"{d} {str(title_s)[:20]}: {type(exc).__name__}: {str(exc)[:100]}")
            # 全部行都失败 ⇒ 回滚，**保住旧窗口**（宁可留旧数据，也不要把窗口删空）
            if items and written == 0:
                conn.rollback()
                raise RuntimeError(
                    f"财经日历写入全部失败（{len(items)} 行），已回滚以保住旧窗口；首条错误: {errors[0]}"
                )
            conn.commit()
            suffix = (f"，截断 {truncated} 条" if truncated else "") + (f"，失败 {len(errors)} 条" if errors else "")
            logger.info(f"✅ 财经日历更新 {written}/{len(items)} 条（{start_s} 起 {self.days_ahead} 天，东财源）{suffix}")
            return with_steps(
                {
                    "records_written": written,
                    "error_count": len(errors),
                    "errors": errors[:20],
                    "note": (f"财经日历 {written}/{len(items)} 条（EM-CAL 源，窗口 {start_s}~{end_s}）"
                             + (f"；content/title 超列宽上限被截断 {truncated} 条" if truncated else "")
                             + (f"；{len(errors)} 行写入失败（已跳过，不影响其余行）" if errors else "")),
                },
                RUN_STEPS,
                {
                    1: f"{start_s} ~ {end_s}",
                    2: f"共 {pages} 页",
                    3: f"有效 {len(items)} 条" + (f"，截断 {truncated} 条" if truncated else ""),
                    4: f"删旧后写入 {written} 条" + (f"，失败 {len(errors)} 条" if errors else ""),
                },
            )
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
