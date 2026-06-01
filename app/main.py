"""应用入口。MODE=api 启 FastAPI；MODE=worker 启 APScheduler 后台任务。

api 与 worker 共用同一镜像，通过环境变量 MODE 区分入口。
对应《07 部署与运维》容器拓扑。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from prometheus_client import make_asgi_app

from app.api.errors_handler import register_exception_handlers
from app.api.handlers import admin, ai, courses, enrollments, study_plans, teaching
from app.core import db, mq, redis
from app.core.config import get_settings
from app.core.http import close_http, open_http
from app.core.logging import RequestContextMiddleware, setup_logging

logger = logging.getLogger(__name__)


async def _startup() -> None:
    setup_logging()
    await db.open_pool()
    await redis.open_redis()
    await open_http()
    await mq.open_mq()


async def _shutdown() -> None:
    await mq.close_mq()
    await close_http()
    await redis.close_redis()
    await db.close_pool()


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    await _startup()
    try:
        yield
    finally:
        await _shutdown()


def create_app() -> FastAPI:
    """构造 FastAPI 应用（api 模式入口）。"""
    app = FastAPI(title="Smart Course Selection", version="1.0.0", lifespan=lifespan)
    app.add_middleware(RequestContextMiddleware)
    register_exception_handlers(app)
    for module in (enrollments, study_plans, courses, teaching, admin, ai):
        app.include_router(module.router)
    # Prometheus /metrics
    app.mount("/metrics", make_asgi_app())
    return app


app = create_app()


async def run_worker() -> None:
    """worker 模式：调度 Outbox 投递、对账、缓存刷新等后台任务。"""
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    from app.tasks.publish_outbox import publish_pending
    from app.tasks.reconcile import reconcile_once

    setup_logging()
    await db.open_pool()
    await redis.open_redis()
    await open_http()
    await mq.open_mq()

    scheduler = AsyncIOScheduler()
    scheduler.add_job(publish_pending, "interval", seconds=2, id="outbox")
    scheduler.add_job(reconcile_once, "interval", seconds=60, id="reconcile")
    scheduler.start()
    logger.info("worker 已启动", extra={"event": "worker.start"})

    try:
        await asyncio.Event().wait()  # 常驻
    finally:
        scheduler.shutdown()
        await _shutdown()


def main() -> None:
    settings = get_settings()
    if settings.mode == "worker":
        asyncio.run(run_worker())
    else:
        import uvicorn

        uvicorn.run("app.main:app", host="0.0.0.0", port=settings.service_port)  # noqa: S104


if __name__ == "__main__":
    main()
