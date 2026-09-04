#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""AI 概念分析服务（豆包 / 方舟）
- 从环境变量 ARK_API_KEY 读取密钥（绝不硬编码）
- 有 Key → 调用豆包 API；无 Key / 失败 → 返回占位结果
- 写入表：finance_concept_analysis（既有表，不新增）
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime
from typing import Any

import pymysql

from ..db import get_db_config, query_all

logger = logging.getLogger(__name__)

# 方舟（豆包）OpenAI 兼容端点
ARK_API_URL = "https://ark.cn-beijing.volces.com/api/v3/chat/completions"
# 兼容多种模型 id（用户配置 ARK_MODEL_ID 后可切换；默认使用常用轻量模型）
DEFAULT_MODEL = "doubao-1-5-pro-32k-250115"
# 一次分析最多返回概念数（与 relation_degree TOP N 配合）
MAX_CONCEPTS = 5


def is_available() -> bool:
    """是否配置了方舟 API Key"""
    return bool(os.environ.get("ARK_API_KEY", "").strip())


def _model_id() -> str:
    """优先 ARK_MODEL_ID 环境变量；缺省使用 DEFAULT_MODEL"""
    return os.environ.get("ARK_MODEL_ID", DEFAULT_MODEL).strip() or DEFAULT_MODEL


def _connect() -> pymysql.Connection:
    """创建数据库连接"""
    return pymysql.connect(**get_db_config().to_dict())


# ---------- 主入口：分析单个事件 ----------
def analyze_event(event_id: int) -> dict:
    """分析指定日历事件，返回 {event_id, status, concepts: [...], error?}

    status: 'ai' 真实豆包调用成功 / 'placeholder' 占位结果
    """
    event = _load_event(event_id)
    if not event:
        return {"event_id": event_id, "status": "error", "error": "事件不存在", "concepts": []}

    # 先清掉该事件已有的分析结果（每次分析重新生成）
    _clear_existing(event_id)

    if is_available():
        try:
            concepts = _call_doubao(event["title"], event["content"], event["event_date"])
            if concepts:
                _save_concepts(event_id, event, concepts, source="ai")
                return {
                    "event_id": event_id,
                    "status": "ai",
                    "concepts": concepts,
                    "event": event,
                }
        except Exception as e:
            logger.warning(f"豆包调用失败 event={event_id}: {e}")
            # 失败回退到占位，error 信息写入 analysis 文本

    # 占位实现
    placeholder = _placeholder_concepts(event)
    _save_concepts(event_id, event, placeholder, source="placeholder")
    return {
        "event_id": event_id,
        "status": "placeholder",
        "concepts": placeholder,
        "event": event,
        "hint": "占位结果：未配置 ARK_API_KEY 或调用失败。配置环境变量 ARK_API_KEY 后启用真实 AI 分析。",
    }


# ---------- 查询已有分析 ----------
def get_analysis(event_id: int) -> list[dict]:
    """查询指定事件的 AI 分析结果（按 relation_degree 降序）
    通过 finance_calendar 关联 event_id -> title + event_date，再查 finance_concept_analysis
    """
    evt = _load_event(event_id)
    if not evt:
        return []
    rows = query_all(
        """
        SELECT id, event_date, title, content, concept_code, concept_name,
               relation_type, relation_degree, analysis, update_time
        FROM finance_concept_analysis
        WHERE event_date = %s AND title = %s
        ORDER BY relation_degree DESC, id ASC
        """,
        [evt["event_date"], evt["title"]],
    )
    return rows


def get_analysis_by_date(event_date: str) -> list[dict]:
    """按日期查询分析结果（YYYY-MM-DD 或 YYYYMMDD 都可）"""
    s = str(event_date).replace("-", "")
    rows = query_all(
        """
        SELECT id, event_date, title, content, concept_code, concept_name,
               relation_type, relation_degree, analysis, update_time
        FROM finance_concept_analysis
        WHERE event_date = %s
        ORDER BY relation_degree DESC
        """,
        [s],
    )
    return rows


# ---------- 批量分析 ----------
def batch_analyze(limit: int = 20, days_back: int = 30) -> dict:
    """批量分析最近 N 天事件"""
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT event_date, title FROM finance_calendar
                WHERE event_date >= DATE_SUB(CURDATE(), INTERVAL %s DAY)
                GROUP BY event_date, title
                ORDER BY event_date DESC
                LIMIT %s
                """,
                [days_back, limit],
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    results = {"total": len(rows), "ai": 0, "placeholder": 0, "errors": []}
    for r in rows:
        try:
            conn = _connect()
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM finance_calendar WHERE event_date=%s AND title=%s LIMIT 1",
                    (r[0], r[1]),
                )
                evt = cur.fetchone()
            conn.close()
            if not evt:
                continue
            res = analyze_event(evt[0])
            results[res["status"]] = results.get(res["status"], 0) + 1
        except Exception as e:
            results["errors"].append(f"{r[0]} {r[1]}: {e}")
    return results


# ---------- 内部：事件加载 / 落库 ----------
def _load_event(event_id: int) -> dict | None:
    rows = query_all(
        "SELECT id, event_date, title, content FROM finance_calendar WHERE id = %s",
        [event_id],
    )
    if not rows:
        return None
    e = rows[0]
    return {
        "id": e["id"],
        "event_date": e["event_date"],
        "title": e["title"],
        "content": e["content"],
    }


def _clear_existing(event_id: int) -> None:
    """清理该事件已有的分析（按 title + event_date 匹配）"""
    event = _load_event(event_id)
    if not event:
        return
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM finance_concept_analysis WHERE event_date=%s AND title=%s",
                (event["event_date"], event["title"]),
            )
        conn.commit()
    finally:
        conn.close()


def _save_concepts(
    event_id: int, event: dict, concepts: list[dict], source: str
) -> None:
    conn = _connect()
    try:
        with conn.cursor() as cur:
            for c in concepts:
                cur.execute(
                    """
                    INSERT INTO finance_concept_analysis
                        (event_date, title, content, concept_code, concept_name,
                         relation_type, relation_degree, analysis, update_time)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        event["event_date"],
                        event["title"],
                        (event.get("content") or "")[:400],
                        c.get("concept_code") or "",
                        c.get("concept_name") or "",
                        c.get("relation_type") or "中性",
                        int(c.get("relation_degree") or 0),
                        (c.get("analysis") or "") + (f" [source={source}]" if source == "placeholder" else ""),
                        datetime.now(),
                    ),
                )
        conn.commit()
    finally:
        conn.close()


# ---------- 内部：豆包调用 ----------
SYSTEM_PROMPT = """你是 A 股投资分析师，专注财经事件对相关概念板块的影响分析。
针对用户给出的财经事件，请输出 3-5 个最相关的同花顺概念板块（按相关度排序），每个概念给出：
- concept_name：概念名称（用同花顺常用名，如"光刻机""新能源汽车""半导体"）
- concept_code：同花顺概念指数代码（可留空，由后端映射补全）
- relation_type：利好 / 利空 / 中性
- relation_degree：相关度评分（1-10，越高越强）
- analysis：50-80 字的影响分析

严格以 JSON 数组格式返回，不要任何其他文字或代码块标记。例如：
[{"concept_name":"光刻机","concept_code":"886054","relation_type":"利好","relation_degree":8,"analysis":"..."}]"""


def _call_doubao(title: str, content: str, event_date: Any) -> list[dict]:
    """调用豆包分析事件，返回概念列表"""
    import requests

    api_key = os.environ["ARK_API_KEY"]
    model = _model_id()

    user_msg = f"事件日期：{event_date}\n标题：{title}\n内容：{(content or '')[:600]}"

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        "temperature": 0.5,
        "max_tokens": 1500,
    }
    resp = requests.post(ARK_API_URL, json=payload, headers=headers, timeout=30)
    resp.raise_for_status()
    body = resp.json()
    text = body["choices"][0]["message"]["content"].strip()

    # 解析模型返回的 JSON（可能含 ```json 包裹）
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    concepts = json.loads(text)
    if not isinstance(concepts, list):
        raise ValueError("模型返回不是 JSON 数组")

    # 限制条数 + 字段裁剪
    out: list[dict] = []
    for c in concepts[:MAX_CONCEPTS]:
        if not isinstance(c, dict):
            continue
        out.append(
            {
                "concept_name": str(c.get("concept_name", "")).strip(),
                "concept_code": str(c.get("concept_code", "")).strip(),
                "relation_type": str(c.get("relation_type", "中性")).strip(),
                "relation_degree": max(1, min(10, int(c.get("relation_degree", 5)))),
                "analysis": str(c.get("analysis", "")).strip()[:300],
            }
        )
    if not out:
        raise ValueError("模型返回为空")
    return out


# ---------- 内部：占位实现 ----------
def _placeholder_concepts(event: dict) -> list[dict]:
    """占位实现：无 Key / 调用失败时返回"""
    title = event.get("title") or ""
    content = event.get("content") or ""
    return [
        {
            "concept_name": "（待 AI 分析）",
            "concept_code": "",
            "relation_type": "中性",
            "relation_degree": 5,
            "analysis": f"事件「{title[:50]}」的 AI 概念分析尚未启用。"
            f"配置环境变量 ARK_API_KEY 后重新点击 AI 分析即可获得真实分析结果。"
            f"事件摘要：{content[:120]}",
        }
    ]