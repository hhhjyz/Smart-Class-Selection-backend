"""测试夹具：解析 PG / Redis / RMQ 端点并初始化。

端点来源优先级：
1. 环境变量 TEST_PG_DSN / TEST_REDIS_URL / TEST_RMQ_URL（CI 用 services 注入）；
2. 缺失时用 testcontainers 现起（本地开发）。

集成/e2e 测试统一用本文件的夹具，不各自起容器。
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import pytest_asyncio

_MIGRATION = Path(__file__).resolve().parents[1] / "migrations" / "001_init.sql"
_SEED = Path(__file__).resolve().parent / "seed.sql"


@pytest.fixture(scope="session")
def pg_dsn() -> Iterator[str]:
    """PostgreSQL DSN。env 优先，否则起 testcontainers。"""
    env = os.getenv("TEST_PG_DSN")
    if env:
        yield env
        return
    pytest.importorskip("testcontainers")
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:16-alpine") as pg:
        yield pg.get_connection_url().replace("postgresql+psycopg2", "postgresql")


@pytest.fixture(scope="session")
def redis_url() -> Iterator[str]:
    env = os.getenv("TEST_REDIS_URL")
    if env:
        yield env
        return
    pytest.importorskip("testcontainers")
    from testcontainers.redis import RedisContainer

    with RedisContainer("redis:7-alpine") as rc:
        host = rc.get_container_host_ip()
        port = rc.get_exposed_port(6379)
        yield f"redis://{host}:{port}/0"


@pytest.fixture(scope="session")
def rmq_url() -> str | None:
    """RabbitMQ URL，仅 e2e 需要。缺失则相关测试 skip。"""
    return os.getenv("TEST_RMQ_URL")


def _apply_sql(dsn: str, sql_path: Path) -> None:
    import psycopg

    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(sql_path.read_text())


@pytest.fixture(scope="session")
def migrated_db(pg_dsn: str) -> str:
    """应用 DDL + 种子数据，返回 DSN。"""
    _apply_sql(pg_dsn, _MIGRATION)
    if _SEED.exists():
        _apply_sql(pg_dsn, _SEED)
    return pg_dsn


@pytest_asyncio.fixture
async def pg_pool(migrated_db: str) -> AsyncIterator[object]:
    """已开启的连接池（每个测试一个，确保干净状态）。"""
    from psycopg_pool import AsyncConnectionPool

    from app.core.db import configure_connection

    pool = AsyncConnectionPool(migrated_db, min_size=2, max_size=16, configure=configure_connection, open=False)
    await pool.open()
    try:
        yield pool
    finally:
        await pool.close()


@pytest_asyncio.fixture
async def app_pool(migrated_db: str) -> AsyncIterator[None]:
    """初始化 app 全局连接池（指向测试库），供使用 app.core.db 的代码（如 LotteryService）。"""
    import os

    os.environ["PG_DSN"] = migrated_db
    from app.core import db
    from app.core.config import get_settings

    get_settings.cache_clear()
    await db.open_pool()
    try:
        yield None
    finally:
        await db.close_pool()


@pytest_asyncio.fixture
async def redis_client(redis_url: str) -> AsyncIterator[object]:
    from redis.asyncio import Redis

    client = Redis.from_url(redis_url, decode_responses=False)
    await client.flushdb()
    try:
        yield client
    finally:
        await client.aclose()


@pytest_asyncio.fixture
async def clean_capacity(pg_pool) -> AsyncIterator[None]:  # type: ignore[no-untyped-def]
    """每个测试前重置 course_capacity，避免相互污染。"""
    async with pg_pool.connection() as conn:
        await conn.execute("TRUNCATE course_selection.enrollments")
        await conn.execute("UPDATE course_selection.course_capacity SET enrolled_count = 0, version = 0")
        await conn.commit()
    yield
