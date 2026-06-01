"""e2e 补充：覆盖 study-plan validate/delete、AI 会话/推荐/采纳、teaching 路径。"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.e2e

_STU = {"X-User-ID": "S-more", "X-User-Role": "student", "X-Request-ID": "e2e-more"}
_TEACHER = {"X-User-ID": "T-9001", "X-User-Role": "teacher", "X-Request-ID": "e2e-t"}
_OID = "B-CS101-2026-1-01"


@pytest.mark.asyncio
async def test_study_plan_validate_and_delete_item(client) -> None:  # type: ignore[no-untyped-def]
    # dry-run 校验
    r = await client.post("/api/course-selection/v1/study-plans/me/validate", headers=_STU, json={
        "major_code": "CS", "curriculum_version": "2023",
        "items": [{"course_code": "CS101", "category": "major_required",
                   "expected_semester": "2026-1", "credit": 9}],
    })
    assert r.status_code == 200 and "valid" in r.json()["data"]

    # 删除一项（幂等：不存在的合法 id 也返回 204）
    d = await client.delete(
        f"/api/course-selection/v1/study-plans/me/items/{uuid.uuid4()}", headers=_STU
    )
    assert d.status_code == 204


@pytest.mark.asyncio
async def test_ai_conversation_and_recommendation(client) -> None:  # type: ignore[no-untyped-def]
    c = await client.post("/api/course-selection/v1/ai/conversations", headers=_STU)
    assert c.status_code == 200 and c.json()["data"]["conversation_id"]

    rec = await client.post("/api/course-selection/v1/ai/recommendations", headers=_STU,
                            json={"goal": "补满专业选修", "semester": "2026-1"})
    assert rec.status_code == 200 and "rec_id" in rec.json()["data"]


@pytest.mark.asyncio
async def test_cross_team_student_enrollments_self_and_forbidden(client) -> None:  # type: ignore[no-untyped-def]
    # 本人可查（空列表也 200）
    ok = await client.get("/api/course-selection/v1/students/S-more/enrollments",
                          headers=_STU, params={"semester": "2026-1"})
    assert ok.status_code == 200
    # 学生查他人 → 403
    forbid = await client.get("/api/course-selection/v1/students/OTHER/enrollments",
                              headers=_STU, params={"semester": "2026-1"})
    assert forbid.status_code == 403


@pytest.mark.asyncio
async def test_teaching_roster_by_teacher(client) -> None:  # type: ignore[no-untyped-def]
    r = await client.get(f"/api/course-selection/v1/teaching/offerings/{_OID}/roster", headers=_TEACHER)
    assert r.status_code == 200
    assert r.json()["data"]["offering_id"] == _OID


@pytest.mark.asyncio
async def test_ai_accept_reuses_enroll(app_client) -> None:  # type: ignore[no-untyped-def]
    """AI 一键采纳：seed 一条推荐日志，override 选课服务为可成功，验证逐笔采纳结果。"""
    app, client = app_client

    from app.api.deps import get_ai_advisor, get_enrollment_service
    from app.core import db
    from app.core.redis import get_redis
    from app.engine.capacity_lock import RedisStockStore
    from app.repositories.audit_repo import PgAuditRepository
    from app.repositories.capacity_repo import PgCapacityRepository
    from app.repositories.enrollment_repo import PgEnrollmentRepository
    from app.repositories.offering_cache_repo import PgOfferingCacheRepository
    from app.repositories.outbox_repo import PgOutboxRepository
    from app.repositories.study_plan_repo import PgStudyPlanRepository
    from app.services.ai_advisor import AIAdvisor
    from app.services.enrollment_service import EnrollmentService
    from app.services.rule_engine import RuleEngine
    from tests import fakes

    stock = RedisStockStore(get_redis())
    await stock.reset(_OID, 100)
    enroll_svc = EnrollmentService(
        enrollment_repo=PgEnrollmentRepository(), capacity_repo=PgCapacityRepository(),
        offering_repo=PgOfferingCacheRepository(), study_plan_repo=PgStudyPlanRepository(),
        audit_repo=PgAuditRepository(), outbox_repo=PgOutboxRepository(),
        stock=stock, waiting_room=fakes.FakeWaitingRoom(admitted=True),
        info_client=fakes.FakeInfoServiceClient(), rule_engine=RuleEngine(),
    )
    advisor = AIAdvisor(
        llm_client=fakes.FakeLLMClient(), offering_repo=PgOfferingCacheRepository(),
        audit_repo=PgAuditRepository(), enrollment_service=enroll_svc,
    )
    app.dependency_overrides[get_ai_advisor] = lambda: advisor
    app.dependency_overrides[get_enrollment_service] = lambda: enroll_svc

    rec_id = str(uuid.uuid4())
    student = f"S-{uuid.uuid4().hex[:6]}"
    async with db.connection() as conn, conn.transaction():
        await conn.execute(
            "INSERT INTO course_selection.ai_recommendation_logs (rec_id, student_id, offering_ids)"
            " VALUES (%s, %s, %s)",
            (rec_id, student, [_OID]),
        )
    headers = {"X-User-ID": student, "X-User-Role": "student", "X-Request-ID": "e2e-ai"}
    try:
        r = await client.post(f"/api/course-selection/v1/ai/recommendations/{rec_id}/accept", headers=headers)
        assert r.status_code == 200, r.text
        results = r.json()["data"]["results"]
        assert results and results[0]["offering_id"] == _OID and results[0]["status"] == "enrolled"
    finally:
        app.dependency_overrides.clear()
