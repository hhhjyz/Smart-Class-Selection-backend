"""选课操作 handlers。"""

from __future__ import annotations

from fastapi import APIRouter, Query, Response

from app.api.deps import CurrentUser, EnrollmentServiceDep
from app.core.auth import Role
from app.schemas.common import Envelope
from app.schemas.enrollment import (
    EnrollmentListView,
    EnrollmentView,
    EnrollRequest,
    EnrollResult,
    QueuePosition,
    SwapRequest,
)

router = APIRouter(prefix="/api/course-selection/v1", tags=["enrollments"])


@router.post("/enrollments")
async def create_enrollment(
    body: EnrollRequest, principal: CurrentUser, service: EnrollmentServiceDep
) -> Envelope[EnrollResult]:
    principal.require_role(Role.STUDENT)
    outcome = await service.enroll(
        principal,
        student_id=principal.user_id,
        offering_id=body.offering_id,
        stage=body.stage,
        idempotency_key=body.idempotency_key,
    )
    return Envelope.ok(EnrollResult(enrollment_id=outcome.enrollment_id, status=outcome.status))


@router.delete("/enrollments/{enrollment_id}", status_code=204)
async def delete_enrollment(enrollment_id: str, principal: CurrentUser, service: EnrollmentServiceDep) -> Response:
    principal.require_role(Role.STUDENT, Role.ADMIN)
    await service.drop(principal, enrollment_id)
    return Response(status_code=204)


@router.post("/enrollments/swap")
async def swap_enrollment(
    body: SwapRequest, principal: CurrentUser, service: EnrollmentServiceDep
) -> Envelope[EnrollResult]:
    principal.require_role(Role.STUDENT)
    outcome = await service.swap(principal, drop_id=body.drop_id, add_offering_id=body.add_offering_id)
    return Envelope.ok(EnrollResult(enrollment_id=outcome.enrollment_id, status=outcome.status))


@router.get("/enrollments/me")
async def my_enrollments(
    principal: CurrentUser,
    service: EnrollmentServiceDep,
    semester: str = Query(...),
    status: str | None = Query(default=None),
) -> Envelope[EnrollmentListView]:
    principal.require_role(Role.STUDENT)
    rows = await service.list_my_enrollments(principal.user_id, semester, status)
    views = [
        EnrollmentView(
            enrollment_id=e.enrollment_id,
            offering_id=e.offering_id,
            course_code=off.course_code if off else "",
            course_name=off.course_name if off else "",
            teacher_id=off.teacher_id if off else "",
            teacher_name=off.teacher_name if off else "",
            status=e.status,
            stage=e.stage,
            enrolled_at=e.enrolled_at,
        )
        for e, off in rows
    ]
    return Envelope.ok(EnrollmentListView(list=views, total=len(views)))


@router.get("/enrollments/me/queue-position")
async def queue_position(principal: CurrentUser, offering_id: str = Query(...)) -> Envelope[QueuePosition]:
    principal.require_role(Role.STUDENT)
    from app.core.config import get_settings
    from app.core.redis import get_redis
    from app.engine.waiting_room import RedisWaitingRoom

    room = RedisWaitingRoom(get_redis())
    pos = await room.estimate_position(offering_id, principal.user_id)
    return Envelope.ok(QueuePosition(position=pos or 0, retry_after_ms=get_settings().waitroom_tick_ms))


@router.get("/enrollments/me/timetable")
async def my_timetable(principal: CurrentUser, semester: str = Query(...)) -> Envelope[dict[str, object]]:
    """本人周课表：由已选课程的开课时段拼装。"""
    principal.require_role(Role.STUDENT)
    from app.core import db
    from app.repositories.offering_cache_repo import PgOfferingCacheRepository

    repo = PgOfferingCacheRepository()
    async with db.connection() as conn:
        offerings = await repo.list_for_student_timetable(conn, principal.user_id, semester)
    slots = [
        {
            "offering_id": o.offering_id,
            "course_name": o.course_name,
            "day": ts.day,
            "period": list(ts.period),
            "weeks": ts.weeks,
            "classroom": o.classroom,
        }
        for o in offerings
        for ts in o.time_slots
    ]
    return Envelope.ok({"semester": semester, "slots": slots})
