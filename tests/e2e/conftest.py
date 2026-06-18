"""e2e 夹具：真实 ASGI 应用（含 lifespan）+ 真实 PG/Redis/RMQ 跑黑盒 HTTP。

需要三件依赖齐备（CI 用 services 提供），否则整组 e2e skip——不静默假装通过。
`client` 用于纯黑盒；`app_client` 额外暴露 app 以便用 dependency_overrides
覆盖需要上游（A/B）的路径。
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio


@contextlib.asynccontextmanager
async def _build(migrated_db: str, redis_url: str, rmq_url: str) -> AsyncIterator[tuple[object, object]]:
    os.environ["PG_DSN"] = migrated_db
    os.environ["REDIS_URL"] = redis_url
    os.environ["RMQ_URL"] = rmq_url
    from app.core.config import get_settings

    get_settings.cache_clear()

    # deps 的 service 工厂用 lru_cache 且构建时绑定 redis/连接句柄；每个用例重启应用，
    # 故先清缓存，确保 service 重建后绑定到当前 lifespan 打开的句柄。
    from app.api import deps

    for builder in (
        deps._build_enrollment_service,
        deps._build_study_plan_service,
        deps._build_lottery_service,
        deps._build_ai_advisor,
    ):
        builder.cache_clear()

    import httpx

    from app.main import create_app

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://testserver", follow_redirects=True) as ac,
    ):
        yield app, ac


@pytest_asyncio.fixture
async def client(migrated_db: str, redis_url: str, rmq_url: str | None) -> AsyncIterator[object]:
    if rmq_url is None:
        pytest.skip("e2e 需要 TEST_RMQ_URL（应用启动会连 RabbitMQ）")
    async with _build(migrated_db, redis_url, rmq_url) as (_app, ac):
        yield ac


@pytest_asyncio.fixture
async def app_client(migrated_db: str, redis_url: str, rmq_url: str | None) -> AsyncIterator[tuple[object, object]]:
    if rmq_url is None:
        pytest.skip("e2e 需要 TEST_RMQ_URL（应用启动会连 RabbitMQ）")
    async with _build(migrated_db, redis_url, rmq_url) as pair:
        yield pair
