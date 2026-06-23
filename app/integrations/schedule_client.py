"""B 组排课服务（zjuse-schedule）客户端，实现 ports.ScheduleServiceClient。

已核对 B 组仓库真实契约（app/api/v1/schedule.py、classrooms.py、schemas/*）：
- 前缀 `/api/v1`；响应壳 `{code, msg, data}`（code=0 成功）；
- 鉴权通过 Gateway + service token；Gateway 验证后向 B 组注入 `X-User-Id` / `X-User-Role`；
- `GET /schedule/entries?semester=&teacher_id=&course_id=` → `ScheduleEntryOut[]`
  （B 组注释：下游智能选课组由此拉取课表数据）；
- `GET /classrooms?skip=&limit=` → `ClassroomOut[]`，用 classroom_id 解析教室名/校区。

一条 ScheduleEntry = 一门课的一个时段；同一 course_id 的多条聚合为一个本地 Offering，
其多个时段并入 time_slots。课名/教师名属 A 组目录，未接入前以 course_id 占位、教师名留空，
不依赖未核实的接口（与 RosterStudent.name 留空同一约定）。
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from typing import Any

import httpx

from app.core import errors
from app.core.config import get_settings
from app.core.http import CircuitBreaker, get_http
from app.domain.offering import Offering, TimeSlot

# B 组 WeekParity 枚举 → 周次展示后缀
_PARITY_SUFFIX = {"ALL": "周", "ODD": "周(单)", "EVEN": "周(双)"}


def _weeks_str(week_start: int, week_end: int, parity: str) -> str:
    """week_start/week_end + parity(ALL/ODD/EVEN) → 展示字符串，如 "1-16周"、"1-15周(单)"。"""
    return f"{week_start}-{week_end}{_PARITY_SUFFIX.get(parity, '周')}"


def _to_timeslot(entry: dict[str, Any]) -> TimeSlot:
    start, end = int(entry["slot_start"]), int(entry["slot_end"])
    return TimeSlot(
        day=int(entry["day_of_week"]),
        period=tuple(range(start, end + 1)),
        weeks=_weeks_str(int(entry["week_start"]), int(entry["week_end"]), str(entry.get("week_parity", "ALL"))),
    )


class HttpScheduleServiceClient:
    def __init__(self) -> None:
        self._settings = get_settings()
        self._breaker = CircuitBreaker()
        self._base = self._settings.schedule_service_base_url.rstrip("/")
        self._timeout = self._settings.schedule_service_timeout_ms / 1000
        self._service_token = ""
        self._service_token_expires_at = 0.0

    async def list_offerings(self, semester: str) -> Sequence[Offering]:
        entries = await self._get_list(self._settings.schedule_entries_path, params={"semester": semester})
        rooms = await self._load_classrooms()
        # 按 offering_id 聚合多条排课条目为一个开课；旧数据缺 offering_id 时才退回 course_id。
        by_offering: dict[str, list[dict[str, Any]]] = {}
        for e in entries:
            key = str(e.get("offering_id") or e["course_id"])
            by_offering.setdefault(key, []).append(e)
        offerings: list[Offering] = []
        for offering_id, group in by_offering.items():
            first = group[0]
            course_id = str(first["course_id"])
            teacher_ids = [str(t) for t in first.get("teacher_ids", [])]
            room = rooms.get(int(first["classroom_id"]), {})
            offerings.append(
                Offering(
                    offering_id=offering_id,
                    course_code=course_id,  # A 组课程目录待接入，暂以 course_id 占位
                    course_name=course_id,
                    teacher_id=teacher_ids[0] if teacher_ids else "",
                    teacher_name="",  # A 组身份目录待接入
                    semester=semester,
                    time_slots=tuple(_to_timeslot(e) for e in group),
                    classroom=room.get("name"),
                    campus=room.get("campus"),
                )
            )
        return offerings

    async def _load_classrooms(self) -> dict[int, dict[str, Any]]:
        rows = await self._get_list(self._settings.schedule_classrooms_path, params={"skip": 0, "limit": 1000})
        return {int(r["id"]): r for r in rows}

    async def _headers(self) -> dict[str, str]:
        token = await self._get_service_token()
        return {"Authorization": f"Bearer {token}"} if token else {}

    async def _get_service_token(self) -> str:
        now = time.monotonic()
        if self._service_token and now < self._service_token_expires_at:
            return self._service_token
        if not self._settings.course_selection_service_client_secret:
            return ""

        client = get_http()
        auth_base = self._settings.auth_service_base_url.rstrip("/")
        resp = await client.post(
            f"{auth_base}/api/v1/auth/sys/login",
            json={
                "client_id": self._settings.course_selection_service_client_id,
                "client_secret": self._settings.course_selection_service_client_secret,
            },
            timeout=self._timeout,
        )
        resp.raise_for_status()
        body = resp.json()
        if body.get("code", 0) != 0:
            raise errors.UpstreamDown(f"Auth 服务签发 service token 失败 code={body.get('code')}")
        data = body.get("data") or {}
        token = str(data.get("service_token") or data.get("access_token") or "")
        if not token:
            raise errors.UpstreamDown("Auth 服务未返回 service token")
        expires_in = int(data.get("expires_in") or data.get("expires") or 3600)
        self._service_token = token
        self._service_token_expires_at = now + max(expires_in - 60, 60)
        return token

    async def _get_list(self, path: str, params: dict[str, str | int] | None = None) -> list[dict[str, Any]]:
        data = await self._get(path, params=params)
        return data if isinstance(data, list) else []

    async def _get(self, path: str, params: dict[str, str | int] | None = None) -> Any:
        if self._breaker.is_open:
            raise errors.UpstreamDown("B 排课服务熔断中")
        client = get_http()
        last_exc: Exception | None = None
        for _ in range(self._settings.upstream_max_retries + 1):
            try:
                resp = await client.get(
                    f"{self._base}{path}", params=params, headers=await self._headers(), timeout=self._timeout
                )
                resp.raise_for_status()
                self._breaker.record_success()
                body = resp.json()
                if body.get("code", 0) != 0:
                    raise errors.UpstreamDown(f"B 排课服务业务错误 code={body.get('code')}")
                return body.get("data")
            except (httpx.HTTPStatusError, httpx.TransportError) as exc:
                last_exc = exc
                if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code < 500:
                    raise errors.UpstreamDown("B 排课服务返回错误") from exc
        self._breaker.record_failure()
        raise errors.UpstreamDown("B 排课服务不可用") from last_exc
