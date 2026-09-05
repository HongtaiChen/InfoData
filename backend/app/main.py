#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
InvestBuddy 后端 API 入口
启动：uvicorn app.main:app --host 0.0.0.0 --port 8000
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import market, concept, calendar, news, analysis, jobs, ai, db_browser
from .scheduler import manager as scheduler_manager


@asynccontextmanager
async def lifespan(app: FastAPI):
    """服务启停：启动定时调度器（消费 task_config 的 cron），停止时优雅关闭"""
    scheduler_manager.start()
    yield
    scheduler_manager.shutdown()


app = FastAPI(
    title="InvestBuddy API",
    description="个人金融数据平台后端：行情 / 概念 / 投资日历 / 资讯 / 分析研究 / 作业监控",
    version="0.1.0",
    lifespan=lifespan,
)

# 开发期允许所有来源跨域（前端 Vite dev server 默认 5173 端口）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(market.router, prefix="/api/market", tags=["行情"])
app.include_router(concept.router, prefix="/api/concept", tags=["概念板块"])
app.include_router(calendar.router, prefix="/api/calendar", tags=["投资日历"])
app.include_router(news.router, prefix="/api/news", tags=["资讯"])
app.include_router(analysis.router, prefix="/api/analysis", tags=["分析研究"])
app.include_router(jobs.router, prefix="/api/jobs", tags=["作业监控"])
app.include_router(ai.router, prefix="/api/ai", tags=["AI 分析"])
app.include_router(db_browser.router, prefix="/api/db", tags=["数据浏览"])


@app.get("/api/health")
def health():
    """健康检查"""
    return {"status": "ok", "service": "InvestBuddy API"}
