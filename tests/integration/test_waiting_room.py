"""Waiting Room 引擎集成测试（真实 Redis）。"""

from __future__ import annotations

import pytest

from app.engine.waiting_room import RedisWaitingRoom, admit_worker_tick

pytestmark = pytest.mark.integration

_OID = "WR-CS101"


@pytest.mark.asyncio
async def test_enqueue_admit_consume_flow(redis_client) -> None:  # type: ignore[no-untyped-def]
    room = RedisWaitingRoom(redis_client)
    # 入队前未放行
    assert await room.is_admitted(_OID, "u1") is False
    pos = await room.enqueue(_OID, "u1")
    assert pos >= 0
    # 位置可估算
    assert await room.estimate_position(_OID, "u1") is not None

    # 放行 worker 一拍 → u1 进入 admitted
    admitted = await admit_worker_tick(redis_client, _OID, worker_idx=0)
    assert admitted >= 1
    assert await room.is_admitted(_OID, "u1") is True

    # 消费一次性令牌后不再放行
    await room.consume_admission(_OID, "u1")
    assert await room.is_admitted(_OID, "u1") is False


@pytest.mark.asyncio
async def test_remove_admission(redis_client) -> None:  # type: ignore[no-untyped-def]
    room = RedisWaitingRoom(redis_client)
    await room.enqueue(_OID, "u2")
    await admit_worker_tick(redis_client, _OID, worker_idx=0)
    await room.remove_admission(_OID, "u2")
    assert await room.is_admitted(_OID, "u2") is False


@pytest.mark.asyncio
async def test_estimate_position_unknown_user(redis_client) -> None:  # type: ignore[no-untyped-def]
    room = RedisWaitingRoom(redis_client)
    assert await room.estimate_position("WR-NONE", "ghost") is None
