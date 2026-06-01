"""Redis 客户端单例（redis.asyncio）。

承载热路径库存（stock:*）与 Virtual Waiting Room（Stream）。
全部用单命令原语，不写 Lua 脚本。对应《04 高并发引擎设计》。
"""

from __future__ import annotations

from redis.asyncio import Redis

from app.core.config import get_settings

_client: Redis | None = None


async def open_redis() -> None:
    """启动时建立连接。幂等。"""
    global _client
    if _client is not None:
        return
    settings = get_settings()
    _client = Redis.from_url(settings.redis_url, decode_responses=False)
    await _client.ping()


async def close_redis() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def get_redis() -> Redis:
    """取已初始化的 Redis 客户端。"""
    if _client is None:
        raise RuntimeError("Redis 未初始化，请先 await open_redis()")
    return _client
