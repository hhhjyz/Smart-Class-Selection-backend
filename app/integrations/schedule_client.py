"""B 组排课服务客户端，实现 ports.ScheduleServiceClient。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import httpx

from app.core import errors
from app.core.config import get_settings
from app.core.http import CircuitBreaker, get_http
from app.domain.offering import Offering, TimeSlot


def _to_offering(d: dict[str, Any]) -> Offering:  # 外部 JSON 边界，Any 收敛于此
    slots = tuple(
        TimeSlot(day=s["day"], period=tuple(s["period"]), weeks=s["weeks"])
        for s in d.get("time_slots", [])
    )
    return Offering(
        offering_id=d["offering_id"], course_code=d["course_code"], course_name=d["course_name"],
        teacher_id=d["teacher_id"], teacher_name=d["teacher_name"], semester=d["semester"],
        time_slots=slots, classroom=d.get("classroom"), campus=d.get("campus"),
    )


class HttpScheduleServiceClient:
    def __init__(self) -> None:
        self._settings = get_settings()
        self._breaker = CircuitBreaker()
        self._base = self._settings.schedule_service_base_url.rstrip("/")
        self._timeout = self._settings.schedule_service_timeout_ms / 1000

    async def list_offerings(self, semester: str, page: int, page_size: int) -> Sequence[Offering]:
        data = await self._get(
            "/api/schedule/v1/offerings", params={"semester": semester, "page": page, "page_size": page_size}
        )
        return [_to_offering(o) for o in data.get("list", [])]

    async def get_offering(self, offering_id: str) -> Offering | None:
        try:
            data = await self._get(f"/api/schedule/v1/offerings/{offering_id}")
        except errors.NotFound:
            return None
        return _to_offering(data) if data else None

    async def _get(self, path: str, params: dict[str, str | int] | None = None) -> dict[str, Any]:
        if self._breaker.is_open:
            raise errors.UpstreamDown("B 服务熔断中")
        client = get_http()
        last_exc: Exception | None = None
        for _ in range(self._settings.upstream_max_retries + 1):
            try:
                resp = await client.get(f"{self._base}{path}", params=params, timeout=self._timeout)
                if resp.status_code == 404:
                    raise errors.NotFound("B 服务资源不存在")
                resp.raise_for_status()
                self._breaker.record_success()
                data: dict[str, Any] = resp.json().get("data", {})
                return data
            except errors.NotFound:
                raise
            except (httpx.HTTPStatusError, httpx.TransportError) as exc:
                last_exc = exc
                if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code < 500:
                    raise errors.UpstreamDown("B 服务返回错误") from exc
        self._breaker.record_failure()
        raise errors.UpstreamDown("B 服务不可用") from last_exc
