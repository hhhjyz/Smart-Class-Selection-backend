"""Redis 热路径库存，实现 ports.StockStore。

DECR 单命令原子扣减；余量为负时 INCR 补偿。两命令均单命令串行执行，
并发 caller 拿到各不相同的返回值，补偿不会错位。对应《04 高并发引擎设计》。
"""

from __future__ import annotations

from redis.asyncio import Redis


def _key(offering_id: str) -> str:
    return f"stock:{offering_id}"


class RedisStockStore:
    def __init__(self, redis: Redis) -> None:
        self._r = redis

    async def try_consume(self, offering_id: str) -> bool:
        key = _key(offering_id)
        new_stock = await self._r.decr(key)
        if new_stock < 0:
            await self._r.incr(key)  # 补偿
            return False
        return True

    async def release(self, offering_id: str) -> None:
        await self._r.incr(_key(offering_id))

    async def reset(self, offering_id: str, remaining: int) -> None:
        await self._r.set(_key(offering_id), remaining)

    async def get_remaining(self, offering_id: str) -> int | None:
        val = await self._r.get(_key(offering_id))
        return int(val) if val is not None else None
