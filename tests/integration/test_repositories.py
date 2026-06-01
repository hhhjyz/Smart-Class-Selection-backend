"""仓储层集成测试（真实 PostgreSQL）：覆盖各 repo 的读写与约束。"""

from __future__ import annotations

import uuid

import psycopg
import pytest

from app.domain.audit import AuditEntry, OutboxEvent
from app.domain.enrollment import Enrollment
from app.domain.enums import EnrollmentSource, EnrollmentStatus, ItemCategory, PlanStatus, Stage
from app.domain.offering import Offering, TimeSlot
from app.domain.study_plan import StudyPlan, StudyPlanItem
from app.repositories.audit_repo import PgAuditRepository
from app.repositories.enrollment_repo import PgEnrollmentRepository
from app.repositories.offering_cache_repo import PgOfferingCacheRepository
from app.repositories.outbox_repo import PgOutboxRepository
from app.repositories.study_plan_repo import PgStudyPlanRepository

pytestmark = pytest.mark.integration


def _enrollment(student="S-r1", offering="RP-CS1", key=None) -> Enrollment:  # type: ignore[no-untyped-def]
    return Enrollment(
        enrollment_id=str(uuid.uuid4()), student_id=student, offering_id=offering,
        semester="2026-1", status=EnrollmentStatus.ENROLLED, stage=Stage.ADD_DROP,
        source=EnrollmentSource.STUDENT_SELF, idempotency_key=key,
    )


@pytest.mark.asyncio
async def test_enrollment_repo_crud_and_idempotency(pg_pool) -> None:  # type: ignore[no-untyped-def]
    repo = PgEnrollmentRepository()
    e = _enrollment(key="idem-1")
    async with pg_pool.connection() as conn, conn.transaction():
        assert await repo.insert(conn, e) == e.enrollment_id
        # 唯一约束：重复 (student, offering, semester) → None
        assert await repo.insert(conn, _enrollment(key="idem-2")) is None
    async with pg_pool.connection() as conn:
        got = await repo.get(conn, e.enrollment_id)
        assert got is not None and isinstance(got.enrollment_id, str)  # UUID→str 生效
        assert (await repo.find_by_idempotency_key(conn, "idem-1")).enrollment_id == e.enrollment_id
        lst = await repo.list_by_student(conn, "S-r1", "2026-1", "enrolled")
        assert len(lst) == 1
        roster = await repo.list_roster(conn, "RP-CS1", include_dropped=False)
        assert roster[0][0] == "S-r1"
    async with pg_pool.connection() as conn, conn.transaction():
        canceled = await repo.soft_cancel(conn, e.enrollment_id, "student_drop")
        assert canceled is not None and canceled.status is EnrollmentStatus.CANCELED


@pytest.mark.asyncio
async def test_study_plan_repo_roundtrip(pg_pool) -> None:  # type: ignore[no-untyped-def]
    repo = PgStudyPlanRepository()
    plan = StudyPlan(
        plan_id=str(uuid.uuid4()), student_id="S-plan", major_code="CS", curriculum_version="2099",
        total_credit_required=10, status=PlanStatus.VALID,
        items=(StudyPlanItem(plan_item_id=str(uuid.uuid4()), course_code="CS101",
                             category=ItemCategory.MAJOR_REQUIRED, expected_semester="2026-1", credit=5),),
    )
    async with pg_pool.connection() as conn, conn.transaction():
        await repo.upsert(conn, plan)
    async with pg_pool.connection() as conn:
        got = await repo.get_by_student(conn, "S-plan")
        assert got is not None and isinstance(got.plan_id, str)
        assert len(got.items) == 1 and got.items[0].course_code == "CS101"


@pytest.mark.asyncio
async def test_offering_cache_repo_search(pg_pool) -> None:  # type: ignore[no-untyped-def]
    repo = PgOfferingCacheRepository()
    off = Offering(
        offering_id="RP-OFF-1", course_code="ML999", course_name="机器学习", teacher_id="T-1",
        teacher_name="王老师", semester="2099-1",
        time_slots=(TimeSlot(day=2, period=(3, 4), weeks="1-16"),), classroom="3-301", campus="紫金港",
    )
    async with pg_pool.connection() as conn, conn.transaction():
        assert await repo.upsert_many(conn, [off]) == 1
    async with pg_pool.connection() as conn:
        got = await repo.get(conn, "RP-OFF-1")
        assert got is not None and got.time_slots[0].day == 2
        items, total = await repo.search(
            conn, keyword="机器学习", teacher_name=None, semester="2099-1",
            category=None, limit=10, offset=0,
        )
        assert total >= 1 and any(o.course_code == "ML999" for o in items)


@pytest.mark.asyncio
async def test_outbox_repo_emit_and_publish(pg_pool) -> None:  # type: ignore[no-untyped-def]
    repo = PgOutboxRepository()
    async with pg_pool.connection() as conn, conn.transaction():
        await conn.execute("TRUNCATE course_selection.outbox_events")  # 隔离其它用例残留
        await repo.emit(conn, OutboxEvent(
            aggregate_type="enrollment", aggregate_id="x", event_type="enrollment.created",
            payload={"a": 1},
        ))
    async with pg_pool.connection() as conn, conn.transaction():
        pending = await repo.fetch_pending(conn, 10)
        assert pending
        event_id, routing_key, body = pending[0]
        assert routing_key == "enrollment.created" and isinstance(body, bytes)
        await repo.mark_published(conn, event_id)


@pytest.mark.asyncio
async def test_audit_log_is_immutable(pg_pool) -> None:  # type: ignore[no-untyped-def]
    repo = PgAuditRepository()
    async with pg_pool.connection() as conn, conn.transaction():
        await repo.write(conn, AuditEntry(
            actor_id="A", actor_role="admin", action="t.test", target_type="x", target_id="1",
        ))
    # UPDATE / DELETE 必须被 trigger 拦截
    async with pg_pool.connection() as conn:
        with pytest.raises(psycopg.errors.RaiseException):
            await conn.execute("UPDATE course_selection.audit_logs SET action='hack'")
        await conn.rollback()
    async with pg_pool.connection() as conn:
        with pytest.raises(psycopg.errors.RaiseException):
            await conn.execute("DELETE FROM course_selection.audit_logs")
        await conn.rollback()
