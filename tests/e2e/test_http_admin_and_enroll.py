"""e2e：管理员路径（真实服务即可，无需上游）+ 选课成功（override 上游为 fake）。

选课成功路径仍走真实 handler→service→真实 PG/Redis，仅把「等待室放行」与
「A 组成绩查询」换成 fake（这两者在 e2e 环境不具备），其余全链路真实。
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.e2e

_ADMIN = {"X-User-ID": "A-1", "X-User-Role": "admin", "X-Request-ID": "e2e-admin"}
_STUDENT = {"X-User-ID": "S-e2e", "X-User-Role": "student", "X-Request-ID": "e2e-stu"}
_OID = "B-CS101-2026-1-01"


# ---------------- 管理员（无需上游） ----------------
@pytest.mark.asyncio
async def test_admin_throttle_and_capacity(client) -> None:  # type: ignore[no-untyped-def]
    r1 = await client.post("/api/course-selection/v1/admin/throttle", headers=_ADMIN, json={"capacity_per_tick": 80})
    assert r1.status_code == 200 and r1.json()["data"]["waitroom_cap_per_tick"] == 80

    r2 = await client.post(
        f"/api/course-selection/v1/admin/capacity/{_OID}", headers=_ADMIN, json={"delta": 5, "reason": "加名额"}
    )
    assert r2.status_code == 200 and r2.json()["data"]["max_capacity"] >= 5


@pytest.mark.asyncio
async def test_admin_lottery_run_e2e(client) -> None:  # type: ignore[no-untyped-def]
    r = await client.post(
        "/api/course-selection/v1/admin/lottery/runs", headers=_ADMIN, json={"semester": "2099-E2E", "seed": 7}
    )
    assert r.status_code == 200
    run_id = r.json()["data"]["run_id"]
    got = await client.get(f"/api/course-selection/v1/admin/lottery/runs/{run_id}", headers=_ADMIN)
    assert got.status_code == 200 and got.json()["data"]["run_id"] == run_id


@pytest.mark.asyncio
async def test_admin_windows_upsert_and_list(client) -> None:  # type: ignore[no-untyped-def]
    body = {
        "semester": "2099-E2E",
        "stage": "add_drop",
        "start_at": "2099-01-01T00:00:00+08:00",
        "end_at": "2099-01-08T00:00:00+08:00",
    }
    w = await client.post("/api/course-selection/v1/admin/windows", headers=_ADMIN, json=body)
    assert w.status_code == 200 and w.json()["data"]["stage"] == "add_drop"
    lst = await client.get("/api/course-selection/v1/admin/windows", headers=_ADMIN, params={"semester": "2099-E2E"})
    assert lst.status_code == 200
    rows = lst.json()["data"]["list"]
    assert any(r["semester"] == "2099-E2E" and r["stage"] == "add_drop" for r in rows)


@pytest.mark.asyncio
async def test_admin_dashboard(client) -> None:  # type: ignore[no-untyped-def]
    r = await client.get("/api/course-selection/v1/admin/dashboard", headers=_ADMIN)
    assert r.status_code == 200
    data = r.json()["data"]
    assert set(data) >= {"online_count", "offerings_remaining", "rule_violations_dist"}
    assert isinstance(data["offerings_remaining"], list)


@pytest.mark.asyncio
async def test_teacher_offerings_and_conflicts(client) -> None:  # type: ignore[no-untyped-def]
    teacher = {"X-User-ID": "T-9001", "X-User-Role": "teacher", "X-Request-ID": "e2e-tch"}
    off = await client.get(
        "/api/course-selection/v1/teaching/offerings", headers=teacher, params={"semester": "2026-1"}
    )
    assert off.status_code == 200
    assert any(o["offering_id"] == _OID for o in off.json()["data"]["list"])

    # 时间冲突预检：无已选课程 → 不冲突
    conf = await client.get(f"/api/course-selection/v1/offerings/{_OID}/conflicts", headers=_STUDENT)
    assert conf.status_code == 200 and conf.json()["data"]["has_conflict"] is False


@pytest.mark.asyncio
async def test_admin_rbac_student_forbidden(client) -> None:  # type: ignore[no-untyped-def]
    r = await client.post("/api/course-selection/v1/admin/throttle", headers=_STUDENT, json={})
    assert r.status_code == 403


# ---------------- 选课成功（override 上游） ----------------
@pytest.mark.asyncio
async def test_enroll_success_via_http(app_client) -> None:  # type: ignore[no-untyped-def]
    app, client = app_client

    # 用「真实 repo + 真实 Redis 库存 + fake 等待室/上游」装配 EnrollmentService
    from app.api.deps import get_enrollment_service
    from app.core.redis import get_redis
    from app.engine.capacity_lock import RedisStockStore
    from app.engine.waiting_room import RedisWaitingRoom  # noqa: F401  (确保引擎可导入)
    from app.repositories.audit_repo import PgAuditRepository
    from app.repositories.capacity_repo import PgCapacityRepository
    from app.repositories.enrollment_repo import PgEnrollmentRepository
    from app.repositories.offering_cache_repo import PgOfferingCacheRepository
    from app.repositories.outbox_repo import PgOutboxRepository
    from app.repositories.study_plan_repo import PgStudyPlanRepository
    from app.services.enrollment_service import EnrollmentService
    from app.services.rule_engine import RuleEngine
    from tests import fakes

    redis = get_redis()
    stock = RedisStockStore(redis)
    await stock.reset(_OID, 100)  # 预置热路径库存

    svc = EnrollmentService(
        enrollment_repo=PgEnrollmentRepository(),
        capacity_repo=PgCapacityRepository(),
        offering_repo=PgOfferingCacheRepository(),
        study_plan_repo=PgStudyPlanRepository(),
        audit_repo=PgAuditRepository(),
        outbox_repo=PgOutboxRepository(),
        stock=stock,
        waiting_room=fakes.FakeWaitingRoom(admitted=True),
        info_client=fakes.FakeInfoServiceClient(),
        rule_engine=RuleEngine(),
    )
    app.dependency_overrides[get_enrollment_service] = lambda: svc

    student = f"S-{uuid.uuid4().hex[:6]}"
    headers = {"X-User-ID": student, "X-User-Role": "student", "X-Request-ID": "e2e-enr"}
    try:
        r = await client.post(
            "/api/course-selection/v1/enrollments", headers=headers, json={"offering_id": _OID, "stage": "add_drop"}
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["code"] == 0 and body["data"]["status"] == "enrolled"
        eid = body["data"]["enrollment_id"]

        # 列表能看到
        lst = await client.get(
            "/api/course-selection/v1/enrollments/me", headers=headers, params={"semester": "2026-1"}
        )
        assert any(e["enrollment_id"] == eid for e in lst.json()["data"]["list"])

        # 退课幂等
        d = await client.delete(f"/api/course-selection/v1/enrollments/{eid}", headers=headers)
        assert d.status_code == 204
    finally:
        app.dependency_overrides.clear()
