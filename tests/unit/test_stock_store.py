"""库存原子扣减单元测试（用内存 fake Redis，验证 DECR/INCR 补偿逻辑）。"""

from __future__ import annotations

import asyncio

import pytest

from app.engine.capacity_lock import RedisStockStore


class FakeRedis:
    """单线程 asyncio 下足以验证 DECR/INCR 串行语义的内存假实现。"""

    def __init__(self) -> None:
        self._d: dict[str, int] = {}

    async def decr(self, key: str) -> int:
        self._d[key] = self._d.get(key, 0) - 1
        return self._d[key]

    async def incr(self, key: str) -> int:
        self._d[key] = self._d.get(key, 0) + 1
        return self._d[key]

    async def set(self, key: str, val: int) -> None:
        self._d[key] = int(val)

    async def get(self, key: str):
        return self._d.get(key)


@pytest.mark.asyncio
async def test_consume_until_empty_then_reject() -> None:
    r = FakeRedis()
    store = RedisStockStore(r)  # type: ignore[arg-type]
    await store.reset("o1", 3)
    assert await store.try_consume("o1") is True
    assert await store.try_consume("o1") is True
    assert await store.try_consume("o1") is True
    # 第 4 次应失败且自补偿，余量保持 0
    assert await store.try_consume("o1") is False
    assert await store.get_remaining("o1") == 0


@pytest.mark.asyncio
async def test_no_oversell_under_concurrency() -> None:
    """100 库存被 1000 并发争抢，恰好 100 次成功（无超卖、无少卖）。"""
    r = FakeRedis()
    store = RedisStockStore(r)  # type: ignore[arg-type]
    await store.reset("o1", 100)

    results = await asyncio.gather(*(store.try_consume("o1") for _ in range(1000)))
    assert sum(results) == 100
    assert await store.get_remaining("o1") == 0
