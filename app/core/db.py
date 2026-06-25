"""PostgreSQL 连接池与事务上下文。

事务边界统一在此提供：service 层用 ``db.transaction()`` 拿带事务的 conn，
``db.connection()`` 拿只读/自动提交连接。**事务块内禁止做任何外部 I/O**
（HTTP / Redis / LLM），以杜绝长事务占用连接池。对应《06 代码规范》。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from anyio import Path
from psycopg import AsyncConnection
from psycopg.adapt import Loader
from psycopg_pool import AsyncConnectionPool

from app.core.config import get_settings

_pool: AsyncConnectionPool | None = None


class _UuidStrLoader(Loader):
    """把 PostgreSQL uuid 列加载为 str。

    领域实体统一用 str 作为标识符（见 domain.*），故在连接边界把 uuid
    解析成字符串，避免 repo / 实体层处理 uuid.UUID 类型。
    """

    def load(self, data: bytes | bytearray | memoryview) -> str:
        return bytes(data).decode()


async def configure_connection(conn: AsyncConnection) -> None:
    """连接初始化钩子：注册 uuid→str 加载器。供应用连接池与测试连接池共用。"""
    conn.adapters.register_loader("uuid", _UuidStrLoader)


async def open_pool() -> None:
    """应用启动时打开连接池。幂等。"""
    global _pool
    if _pool is not None:
        return
    settings = get_settings()
    _pool = AsyncConnectionPool(
        conninfo=settings.pg_dsn,
        min_size=settings.pg_pool_min,
        max_size=settings.pg_pool_max,
        configure=configure_connection,
        open=False,
    )
    await _pool.open()
    if settings.auto_migrate:
        await _run_initial_migration_if_needed()


async def close_pool() -> None:
    """应用关闭时释放连接池。"""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def _require_pool() -> AsyncConnectionPool:
    if _pool is None:
        raise RuntimeError("连接池未初始化，请先 await open_pool()")
    return _pool


async def _run_initial_migration_if_needed() -> None:
    async with _require_pool().connection() as conn:
        cur = await conn.execute("SELECT to_regclass('course_selection.study_plans')")
        row = await cur.fetchone()
        if row and row[0] is not None:
            return
        app_dir = await Path(__file__).resolve()
        migration = app_dir.parents[2] / "migrations" / "001_init.sql"
        await conn.execute(await migration.read_bytes(), prepare=False)
        await conn.commit()


@asynccontextmanager
async def transaction() -> AsyncIterator[AsyncConnection]:
    """带事务的连接：退出时自动 commit，异常自动 rollback。

    仅在此块内做 PG 读写，固定步骤（insert→capacity→audit→outbox），
    不在块内 await 任何外部依赖。
    """
    async with _require_pool().connection() as conn, conn.transaction():
        yield conn


@asynccontextmanager
async def connection() -> AsyncIterator[AsyncConnection]:
    """自动提交连接，用于单条只读查询或不需要事务的写。"""
    async with _require_pool().connection() as conn:
        yield conn
