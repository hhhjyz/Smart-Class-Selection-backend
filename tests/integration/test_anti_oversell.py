"""防超卖集成测试：真实 PostgreSQL + Redis，1000 并发抢 100 库存。

选课正确性的核心验证项（《04 高并发引擎设计》压测目标）。
用共享夹具（env 提供或 testcontainers），验证 Redis 热路径 + PG 乐观锁
配合下恰好 100 成功、DB 计数也恰好 100。
"""

from __future__ import annotations

import asyncio

import pytest

from app.engine.capacity_lock import RedisStockStore
from app.repositories.capacity_repo import PgCapacityRepository

pytestmark = pytest.mark.integration

_OID = "B-CS101-2026-1-01"


@pytest.mark.asyncio
async def test_1000_concurrent_grab_100(pg_pool, redis_client, clean_capacity) -> None:  # type: ignore[no-untyped-def]
    store = RedisStockStore(redis_client)
    capacity = PgCapacityRepository()
    await store.reset(_OID, 100)

    # 限制在途并发，避免压垮 Redis/PG 连接池（生产有个人级限流 + 限定连接池）。
    # 1000 个请求仍全部执行，只是并发窗口受限——足以验证强争抢下的正确性。
    sem = asyncio.Semaphore(50)

    async def attempt() -> bool:
        async with sem:
            # 热路径先扣 Redis；持有令牌即代表有一个名额，再走 DB 乐观锁落库。
            if not await store.try_consume(_OID):
                return False
            # 乐观锁在单行高并发下会有版本冲突，重读版本重试（与引擎设计「重试」一致）。
            for _ in range(200):
                async with pg_pool.connection() as conn, conn.transaction():
                    cap = await capacity.get(conn, _OID)
                    assert cap is not None
                    if cap.enrolled_count >= cap.max_capacity:
                        await store.release(_OID)  # 真满员
                        return False
                    if await capacity.increment_enrolled(conn, _OID, cap.version) is not None:
                        return True
            await store.release(_OID)
            return False

    results = await asyncio.gather(*(attempt() for _ in range(1000)))
    success = sum(results)

    async with pg_pool.connection() as conn:
        cap = await capacity.get(conn, _OID)
        assert cap is not None
        db_count = cap.enrolled_count

    assert success == 100, f"恰好应 100 成功，实际 {success}"
    assert db_count == 100, f"DB enrolled_count 应为 100，实际 {db_count}"
    assert await store.get_remaining(_OID) == 0


@pytest.mark.asyncio
async def test_optimistic_lock_rejects_overflow(pg_pool, redis_client, clean_capacity) -> None:  # type: ignore[no-untyped-def]
    """乐观锁兜底：即便绕过 Redis 直接打 DB，也不会超过 max_capacity。"""
    capacity = PgCapacityRepository()
    # 把容量设为 2，连续 +1 三次，第三次应被 CHECK / 越界条件拦下
    reset_sql = (
        "UPDATE course_selection.course_capacity "
        "SET max_capacity = 2, enrolled_count = 0, version = 0 WHERE offering_id = %s"
    )
    async with pg_pool.connection() as conn, conn.transaction():
        await conn.execute(reset_sql, (_OID,))
    ok = 0
    for _ in range(3):
        async with pg_pool.connection() as conn, conn.transaction():
            cap = await capacity.get(conn, _OID)
            assert cap is not None
            if await capacity.increment_enrolled(conn, _OID, cap.version) is not None:
                ok += 1
    assert ok == 2
