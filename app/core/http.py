"""共享 httpx.AsyncClient 与轻量熔断器。

上游（A / B）客户端复用同一连接池。熔断器在连续失败超阈值时快速失败，
避免连接耗尽。对应《03 API 设计》「调用规范」与《08 构件》Circuit Breaker。
"""

from __future__ import annotations

import time

import httpx

from app.core.config import get_settings

_client: httpx.AsyncClient | None = None


async def open_http() -> None:
    global _client
    if _client is not None:
        return
    _client = httpx.AsyncClient(timeout=httpx.Timeout(2.0))


async def close_http() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def get_http() -> httpx.AsyncClient:
    if _client is None:
        raise RuntimeError("HTTP 客户端未初始化，请先 await open_http()")
    return _client


class CircuitBreaker:
    """熔断器：连续失败达阈值后进入 open 状态，冷却期内直接拒绝。

    使用单调时钟（time.monotonic），不依赖墙钟，避免时间回拨影响。
    """

    def __init__(self, threshold: int | None = None, cooldown_s: int | None = None) -> None:
        settings = get_settings()
        self._threshold = threshold or settings.circuit_break_threshold
        self._cooldown_s = cooldown_s or settings.circuit_break_cooldown_s
        self._failures = 0
        self._opened_at: float | None = None

    @property
    def is_open(self) -> bool:
        if self._opened_at is None:
            return False
        if time.monotonic() - self._opened_at >= self._cooldown_s:
            # 冷却结束，半开：清零等待下一次尝试结果
            self._opened_at = None
            self._failures = 0
            return False
        return True

    def record_success(self) -> None:
        self._failures = 0
        self._opened_at = None

    def record_failure(self) -> None:
        self._failures += 1
        if self._failures >= self._threshold:
            self._opened_at = time.monotonic()
