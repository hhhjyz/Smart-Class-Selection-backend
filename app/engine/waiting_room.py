"""Virtual Waiting Room，实现 ports.WaitingRoom。

每门课一个 Redis Stream + 消费者组 admit。放行 worker（见 main.py worker 模式）
按 capacity_per_tick 把队首用户加入 admitted 集合（TTL 30s）；业务 handler
入队时校验是否已被放行。对应《04 高并发引擎设计》。
"""

from __future__ import annotations

import contextlib
from typing import cast

from redis import exceptions as redis_exceptions
from redis.asyncio import Redis

from app.core.config import get_settings

# redis-py 异步响应类型较宽松；在边界用 cast/helper 收敛
_StreamEntries = list[tuple[bytes, dict[bytes, bytes]]]
_ReadGroupBatch = list[tuple[bytes, _StreamEntries]]


def _decode(v: str | bytes) -> str:
    return v.decode() if isinstance(v, bytes) else v


def _stream(offering_id: str) -> str:
    return f"waitroom:{offering_id}"


def _admitted(offering_id: str) -> str:
    return f"waitroom:{offering_id}:admitted"


def _enq_marker(offering_id: str, user_id: str) -> str:
    return f"waitroom:{offering_id}:enq:{user_id}"


class RedisWaitingRoom:
    def __init__(self, redis: Redis) -> None:
        self._r = redis

    async def enqueue(self, offering_id: str, user_id: str) -> int:
        stream = _stream(offering_id)
        entry_id = _decode(await self._r.xadd(stream, {"user_id": user_id}))
        await self._r.set(_enq_marker(offering_id, user_id), entry_id, ex=300)
        return await self._estimate(stream, entry_id)

    async def is_admitted(self, offering_id: str, user_id: str) -> bool:
        return bool(await self._r.sismember(_admitted(offering_id), user_id))

    async def consume_admission(self, offering_id: str, user_id: str) -> None:
        # 一次性令牌：放行后立即移除，防止重复使用
        await self._r.srem(_admitted(offering_id), user_id)

    async def estimate_position(self, offering_id: str, user_id: str) -> int | None:
        entry_id = await self._r.get(_enq_marker(offering_id, user_id))
        if entry_id is None:
            return None
        eid = entry_id.decode() if isinstance(entry_id, bytes) else entry_id
        return await self._estimate(_stream(offering_id), eid)

    async def remove_admission(self, offering_id: str, user_id: str) -> None:
        await self._r.srem(_admitted(offering_id), user_id)

    async def _estimate(self, stream: str, entry_id: str) -> int:
        """用 entry_id 之前的条目数粗估位置；仅供前端展示，不作业务凭据。"""
        items = await self._r.xrange(stream, min="-", max=entry_id)
        return max(len(items or []) - 1, 0)


async def admit_worker_tick(redis: Redis, offering_id: str, worker_idx: int) -> int:
    """放行 worker 单次 tick：从 Stream 读一批加入 admitted 集合。

    供 worker 进程循环调用。返回本次放行人数。
    """
    settings = get_settings()
    stream = _stream(offering_id)
    group = "admit"
    # 组已存在会抛 BUSYGROUP，忽略即可（幂等创建）
    with contextlib.suppress(redis_exceptions.ResponseError):
        await redis.xgroup_create(stream, group, id="0", mkstream=True)

    raw = await redis.xreadgroup(
        groupname=group,
        consumername=f"admit-{worker_idx}",
        streams={stream: ">"},
        count=settings.waitroom_cap_per_tick,
        block=settings.waitroom_tick_ms,
    )
    if not raw:
        return 0
    # redis-py 响应类型宽松，在边界 cast 成已知结构
    batch = cast("_ReadGroupBatch", raw)
    admitted_key = _admitted(offering_id)
    count = 0
    for _stream_name, entries in batch:
        for entry_id, fields in entries:
            user_id = _decode(fields[b"user_id"])
            await redis.sadd(admitted_key, user_id)
            await redis.expire(admitted_key, 30)
            await redis.xack(stream, group, entry_id)
            count += 1
    return count
