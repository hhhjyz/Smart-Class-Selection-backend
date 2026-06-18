"""A 组基础信息服务客户端，实现 ports.InfoServiceClient。

对齐 A 组真实契约：
- 前缀 `/api/v1`；响应壳 `{code:0, message, data}`；
- 鉴权 `Authorization: Bearer <token>`，服务间 token 由 A 组 `POST /api/v1/auth/sys/login`
  （client_id/secret）签发；
- 身份 `GET /api/v1/users/{id}` → `data:{id, name, ...}`。

开课时段/教室不在 A 组：那是 B 组排课服务（见 integrations/schedule_client.py）。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import httpx

from app.core import errors
from app.core.config import get_settings
from app.core.http import CircuitBreaker, get_http
from app.domain.offering import CourseInfo, OfferingCatalogEntry, StudentProfile
from app.domain.study_plan import TrainingProgram


class HttpInfoServiceClient:
    def __init__(self) -> None:
        self._settings = get_settings()
        self._breaker = CircuitBreaker()
        self._base = self._settings.info_service_base_url.rstrip("/")
        self._timeout = self._settings.info_service_timeout_ms / 1000

    async def get_student(self, student_id: str) -> StudentProfile:
        # A 组 UserResponse: {id, user_no, username, profile:{full_name,...}}
        data = await self._get(self._settings.info_user_path.format(id=student_id))
        profile = data.get("profile") or {}
        return StudentProfile(
            student_id=str(data.get("user_no") or data.get("id", student_id)),
            name=str(profile.get("full_name", "") if isinstance(profile, dict) else ""),
        )

    async def get_course(self, course_id: int) -> CourseInfo | None:
        try:
            data = await self._get(self._settings.info_course_path.format(id=course_id))
        except errors.NotFound:
            return None
        if not data:
            return None
        return CourseInfo(
            course_id=int(data.get("id", course_id)),
            course_code=str(data.get("course_code", "")),
            course_name=str(data.get("course_name", "")),
            credit=float(data.get("credit", 0) or 0),
        )

    async def list_offerings(self, term_code: str) -> Sequence[OfferingCatalogEntry]:
        # A 组列表壳：data:{items, pagination}；OfferingResponse 见其 offering_schema
        data = await self._get(self._settings.info_offerings_path, params={"term_code": term_code})
        items = data.get("items", []) if isinstance(data, dict) else []
        return [
            OfferingCatalogEntry(
                offering_id=str(o.get("id", "")),
                course_id=int(o.get("course_id", 0)),
                course_code=str(o.get("course_code") or ""),
                course_name=str(o.get("course_name") or ""),
                term_code=str(o.get("term_code", term_code)),
                class_no=str(o.get("class_no", "")),
                capacity=int(o.get("capacity", 0) or 0),
            )
            for o in items
            if str(o.get("status", "ACTIVE")) == "ACTIVE"
        ]

    async def list_training_programs(
        self, major_code: str, grade: str | None = None, version: str | None = None
    ) -> Sequence[TrainingProgram]:
        # A 组 data-provision 列表壳：data:{items, pagination, snapshot_time}
        params: dict[str, str | int] = {"major_code": major_code}
        if grade:
            params["grade"] = grade
        if version:
            params["version"] = version
        data = await self._get(self._settings.info_training_programs_path, params=params)
        items = data.get("items", []) if isinstance(data, dict) else []
        return [
            TrainingProgram(
                program_code=str(p.get("program_code", "")),
                major_code=str(p.get("major_code", major_code)),
                grade=str(p.get("grade", "")),
                version=str(p.get("version", "1.0")),
                required_course_ids=tuple(int(c) for c in p.get("required_course_ids", [])),
            )
            for p in items
        ]

    def _headers(self) -> dict[str, str]:
        # A 组要求 Authorization: Bearer（必填）。token 由 /api/v1/auth/sys/login 签发。
        token = self._settings.info_service_token
        return {"Authorization": f"Bearer {token}"} if token else {}

    async def _get(self, path: str, params: dict[str, str | int] | None = None) -> dict[str, Any]:
        if self._breaker.is_open:
            raise errors.UpstreamDown("A 服务熔断中")
        client = get_http()
        last_exc: Exception | None = None
        for _ in range(self._settings.upstream_max_retries + 1):
            try:
                resp = await client.get(
                    f"{self._base}{path}", params=params, headers=self._headers(), timeout=self._timeout
                )
                if resp.status_code == 404:
                    raise errors.NotFound("A 服务资源不存在")
                resp.raise_for_status()
                self._breaker.record_success()
                body = resp.json()
                if body.get("code", 0) != 0:
                    raise errors.UpstreamDown(f"A 服务业务错误 code={body.get('code')}")
                data: dict[str, Any] = body.get("data", {})
                return data
            except errors.NotFound:
                raise
            except (httpx.HTTPStatusError, httpx.TransportError) as exc:
                last_exc = exc
                if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code < 500:
                    raise errors.UpstreamDown("A 服务返回错误") from exc
        self._breaker.record_failure()
        raise errors.UpstreamDown("A 服务不可用") from last_exc
