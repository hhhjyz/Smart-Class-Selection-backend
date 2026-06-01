"""A 组基础信息服务客户端，实现 ports.InfoServiceClient。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import httpx

from app.core import errors
from app.core.config import get_settings
from app.core.http import CircuitBreaker, get_http
from app.domain.enums import RuleType
from app.domain.offering import GradeRecord, StudentProfile
from app.domain.study_plan import CurriculumRule


class HttpInfoServiceClient:
    def __init__(self) -> None:
        self._settings = get_settings()
        self._breaker = CircuitBreaker()
        self._base = self._settings.info_service_base_url.rstrip("/")
        self._timeout = self._settings.info_service_timeout_ms / 1000

    async def get_student(self, student_id: str) -> StudentProfile:
        data = await self._get(f"/api/base-info/v1/students/{student_id}")
        return StudentProfile(
            student_id=data["student_id"], name=data["name"],
            major_code=data["major_code"], curriculum_version=data["curriculum_version"],
        )

    async def get_curriculum_rules(self, plan_id: str) -> Sequence[CurriculumRule]:
        data = await self._get(f"/api/base-info/v1/curriculum/{plan_id}")
        return [
            CurriculumRule(
                rule_id=r["rule_id"], major_code=r["major_code"],
                curriculum_version=r["curriculum_version"], rule_type=RuleType(r["rule_type"]),
                payload=r.get("payload", {}), priority=r.get("priority", 0),
            )
            for r in data.get("rules", [])
        ]

    async def get_grades(self, student_id: str) -> Sequence[GradeRecord]:
        data = await self._get(f"/api/base-info/v1/students/{student_id}/grades")
        return [
            GradeRecord(course_code=g["course_code"], credit=g["credit"], passed=g["passed"])
            for g in data.get("grades", [])
        ]

    async def _get(self, path: str) -> dict[str, Any]:  # 外部 JSON 边界，Any 收敛于此
        """带重试 + 熔断的 GET，返回响应壳的 data。失败抛 30301。"""
        if self._breaker.is_open:
            raise errors.UpstreamDown("A 服务熔断中")
        client = get_http()
        last_exc: Exception | None = None
        for _ in range(self._settings.upstream_max_retries + 1):
            try:
                resp = await client.get(f"{self._base}{path}", timeout=self._timeout)
                resp.raise_for_status()
                self._breaker.record_success()
                data: dict[str, Any] = resp.json().get("data", {})
                return data
            except (httpx.HTTPStatusError, httpx.TransportError) as exc:
                last_exc = exc
                if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code < 500:
                    raise errors.UpstreamDown("A 服务返回错误") from exc
        self._breaker.record_failure()
        raise errors.UpstreamDown("A 服务不可用") from last_exc
