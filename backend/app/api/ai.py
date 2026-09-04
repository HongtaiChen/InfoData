#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""AI 分析 API：日历事件 → 概念分析（豆包）

- POST /api/ai/analyze-event/{event_id}：触发单个事件 AI 分析
- POST /api/ai/analyze-batch       ：批量分析最近 N 个事件
- GET  /api/ai/analysis/{event_id}：查询事件 AI 分析结果
- GET  /api/ai/status             ：AI 服务可用性（是否有 ARK_API_KEY）
"""
from fastapi import APIRouter, Query

from ..analysis import concept_ai

router = APIRouter()


@router.get("/status")
def status():
    """AI 服务状态（前端用来决定是否启用按钮/显示占位提示）"""
    return {
        "available": concept_ai.is_available(),
        "model": concept_ai._model_id(),
        "hint": "OK" if concept_ai.is_available() else "未配置 ARK_API_KEY 环境变量，当前返回占位结果",
    }


@router.post("/analyze-event/{event_id}")
def analyze_event(event_id: int):
    """触发单个事件的 AI 概念分析（同步执行，几秒内返回）"""
    return concept_ai.analyze_event(event_id)


@router.post("/analyze-batch")
def analyze_batch(
    limit: int = Query(20, ge=1, le=100),
    days_back: int = Query(30, ge=1, le=180),
):
    """批量分析最近 N 天事件（供后台任务调用）"""
    return concept_ai.batch_analyze(limit=limit, days_back=days_back)


@router.get("/analysis/{event_id}")
def get_analysis(event_id: int):
    """查询指定事件的 AI 分析结果（按 relation_degree 降序）"""
    rows = concept_ai.get_analysis(event_id)
    return {"event_id": event_id, "total": len(rows), "items": rows}