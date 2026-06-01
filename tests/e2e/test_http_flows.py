"""e2e：真实 HTTP → handler → service → repo → PG/Redis → 响应壳。

覆盖鉴权头、响应壳结构、错误码映射、Waiting Room 排队语义等全链路行为。
上游 A/B 服务不在 e2e 范围内（需独立部署），故只覆盖不依赖上游的路径。
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.e2e

_STUDENT = {"X-User-ID": "S-3210123", "X-User-Role": "student", "X-Request-ID": "e2e-1"}
_TEACHER = {"X-User-ID": "T-9001", "X-User-Role": "teacher", "X-Request-ID": "e2e-2"}


@pytest.mark.asyncio
async def test_missing_auth_headers_returns_30002(client) -> None:  # type: ignore[no-untyped-def]
    resp = await client.get("/api/course-selection/v1/study-plans/me")
    assert resp.status_code == 401
    body = resp.json()
    assert body["code"] == 30002
    assert "trace_id" in body


@pytest.mark.asyncio
async def test_envelope_and_trace_id_echo(client) -> None:  # type: ignore[no-untyped-def]
    resp = await client.get(
        "/api/course-selection/v1/courses/search",
        params={"semester": "2026-1"},
        headers=_STUDENT,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    assert body["trace_id"] == "e2e-1"
    # 种子里有 CS101
    codes = [o["course_code"] for o in body["data"]["list"]]
    assert "CS101" in codes


@pytest.mark.asyncio
async def test_offering_detail_and_404(client) -> None:  # type: ignore[no-untyped-def]
    ok = await client.get("/api/course-selection/v1/offerings/B-CS101-2026-1-01", headers=_STUDENT)
    assert ok.status_code == 200
    assert ok.json()["data"]["course_name"] == "软件工程"

    missing = await client.get("/api/course-selection/v1/offerings/NOPE", headers=_STUDENT)
    assert missing.status_code == 404
    assert missing.json()["code"] == 30004


@pytest.mark.asyncio
async def test_study_plan_save_validate_and_get(client) -> None:  # type: ignore[no-untyped-def]
    # 种子规则要求总学分 >= 8；先存一个不足的 → 30101
    too_few = await client.put(
        "/api/course-selection/v1/study-plans/me",
        headers=_STUDENT,
        json={
            "major_code": "CS", "curriculum_version": "2023",
            "items": [{"course_code": "CS101", "category": "major_required",
                       "expected_semester": "2026-1", "credit": 3}],
        },
    )
    assert too_few.status_code == 422
    assert too_few.json()["code"] == 30101

    # 满足学分 → valid
    ok = await client.put(
        "/api/course-selection/v1/study-plans/me",
        headers=_STUDENT,
        json={
            "major_code": "CS", "curriculum_version": "2023",
            "items": [
                {"course_code": "CS101", "category": "major_required", "expected_semester": "2026-1", "credit": 5},
                {"course_code": "CS102", "category": "major_required", "expected_semester": "2026-1", "credit": 5},
            ],
        },
    )
    assert ok.status_code == 200
    assert ok.json()["data"]["valid"] is True

    got = await client.get("/api/course-selection/v1/study-plans/me", headers=_STUDENT)
    assert got.status_code == 200
    assert got.json()["data"]["major_code"] == "CS"


@pytest.mark.asyncio
async def test_enroll_enters_waiting_room(client) -> None:  # type: ignore[no-untyped-def]
    """未放行的学生提交选课 → 30201 进入排队（仅依赖 Redis，不触上游）。"""
    resp = await client.post(
        "/api/course-selection/v1/enrollments",
        headers=_STUDENT,
        json={"offering_id": "B-CS101-2026-1-01", "stage": "add_drop"},
    )
    assert resp.status_code == 202
    body = resp.json()
    assert body["code"] == 30201
    assert "position" in body["data"]
    assert "retry_after_ms" in body["data"]


@pytest.mark.asyncio
async def test_rbac_student_cannot_read_roster(client) -> None:  # type: ignore[no-untyped-def]
    resp = await client.get(
        "/api/course-selection/v1/offerings/B-CS101-2026-1-01/roster", headers=_STUDENT
    )
    assert resp.status_code == 403
    assert resp.json()["code"] == 30003


@pytest.mark.asyncio
async def test_metrics_exposed(client) -> None:  # type: ignore[no-untyped-def]
    resp = await client.get("/metrics")
    assert resp.status_code == 200
