#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
InvestBuddy 后端 API 入口
启动：uvicorn app.main:app --host 0.0.0.0 --port 8000
"""
import logging
import sys
import threading
from contextlib import asynccontextmanager


def _setup_logging() -> None:
    """让调度器/采集器的 INFO 日志可见（幂等，重复调用无副作用）。

    为什么必须在这里配（2026-09-14 复盘）：uvicorn 只配置自己的 logger，root logger
    停留在默认 WARNING，导致 `scheduler.sync_from_db()` 的中文调度语义日志（cron_human）
    与启动补偿（catchup_missed）日志**根本不会输出**——「调度语义可视化」机制形同虚设。
    此前 `🧹 启动自愈` 能看到，是因为 WARNING 恰好够到 logging.lastResort 兜底 handler
    （无时间戳/无 logger 名），属于巧合而非设计。
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    # 第三方库降噪：默认 INFO 会把 akshare/requests 的噪音淹没调度日志
    for noisy in ("urllib3", "requests", "akshare", "matplotlib", "PIL", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


_setup_logging()

# =============================================================================
# 启动顺序（**不可调整**，2026-10-17 可移植性加固）
#
#   ① _setup_logging()      —— 先有日志，否则下面 .env 的加载结果无处输出
#   ② load_env_files()      —— 把 .env 写进 os.environ
#   ③ 再导入 .api / .scheduler —— 这些模块可能在 **import 期**就调 os.getenv
#                                （如 app/db.py 的 INFO_DATA_DB_POOL），
#                                晚一步加载 .env 它们读到的就是默认值。
#
# 之所以 .env 现在必须显式加载：后端从不自动读 .env（只读进程环境变量），
# 而 .env.example 却写着「复制为 .env」——曾经的静默错配见 app/env.py 文件头。
# =============================================================================
from .env import load_env_files  # noqa: E402  （有意放在配置之后，见上）

load_env_files()

from fastapi import FastAPI  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

from .api import market, concept, calendar, news, analysis, jobs, ai, db_browser, quality, sql_explorer, prefs, env  # noqa: E402
from .preflight import advisory  # noqa: E402
from .scheduler import manager as scheduler_manager  # noqa: E402

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """服务启停：先环境自检，再启动定时调度器（消费 task_config 的 cron），停止时优雅关闭"""
    # 环境自检（2026-10-17 可移植性加固）：把「换机器后的静默错配」提前喊出来。
    # 默认 advisory——只告警不阻断；INFO_DATA_PREFLIGHT=strict 时才拒绝启动。
    # 为什么不默认阻断：数据库正在重启这类瞬时故障，不该让后端永远起不来。
    if not advisory(logger):
        raise RuntimeError(
            "环境自检未通过，且已设 INFO_DATA_PREFLIGHT=strict —— 拒绝启动。"
            " 单独复跑看修法：python scripts/preflight.py"
        )
    scheduler_manager.start()
    # 后台预热总览缓存（国内指数 overview 全史现算 ~2.5s/窗口，预热后切换窗口即时返回）
    def _warm_overview_cache() -> None:
        try:
            from app.services.env_service import warm_overview_cache
            warm_overview_cache()
        except Exception as e:  # 预热失败不影响服务，首次请求再现算
            logger.warning(f"overview 缓存预热失败（不阻断）: {e}")
    threading.Thread(target=_warm_overview_cache, name="ov-cache-warm", daemon=True).start()
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
app.include_router(db_browser.router, prefix="/api/db", tags=["数据中心"])
app.include_router(quality.router, prefix="/api/dq", tags=["数据质量"])
app.include_router(sql_explorer.router, prefix="/api/sql", tags=["数据探查"])
app.include_router(prefs.router, prefix="/api/prefs", tags=["偏好设置"])
app.include_router(env.router, prefix="/api/environment", tags=["投资环境"])


@app.get("/api/health")
def health():
    """健康检查"""
    return {"status": "ok", "service": "InvestBuddy API"}
