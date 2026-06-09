"""教师 handlers 与跨组对外契约（enrollments / roster）。

跨组接口与教师花名册共用 roster 读路径，权限分别校验。
对应《10 跨组接口契约》。
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from app.api.deps import CurrentUser, EnrollmentServiceDep
from app.core.auth import Role
from app.schemas.common import Envelope
from app.schemas.enrollment import (
    EnrollmentListView,
    EnrollmentView,
    RosterStudent,
    RosterView,
)

router = APIRouter(prefix="/api/course-selection/v1", tags=["teaching", "cross-team"])


# --- 跨组：D 组查询学生选课列表 ---
@router.get("/students/{student_id}/enrollments")
async def student_enrollments(
    student_id: str,
    principal: CurrentUser,
    service: EnrollmentServiceDep,
    semester: str = Query(...),
    status: str | None = Query(default="enrolled"),
) -> Envelope[EnrollmentListView]:
    # 学生仅可访问自身；teacher/admin 全量
    principal.require_self_or_privileged(student_id)
    rows = await service.list_my_enrollments(student_id, semester, status)
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


# --- 跨组：F 组查询花名册 ---
@router.get("/offerings/{offering_id}/roster")
async def offering_roster(
    offering_id: str,
    principal: CurrentUser,
    service: EnrollmentServiceDep,
    include_dropped: bool = Query(default=False),
) -> Envelope[RosterView]:
    # 本课教师或管理员（教师身份的本课校验在生产中结合 teaching 关系，这里限定角色）
    principal.require_role(Role.TEACHER, Role.ADMIN)
    return await _build_roster(service, offering_id, include_dropped)


# --- 教师：本人本学期任课列表 ---
_SQL_TEACHING_OFFERINGS = """
SELECT co.offering_id, co.course_name, cc.max_capacity, cc.enrolled_count
  FROM course_selection.cached_offerings co
  LEFT JOIN course_selection.course_capacity cc ON cc.offering_id = co.offering_id
 WHERE co.teacher_id = %s AND (%s::text IS NULL OR co.semester = %s)
 ORDER BY co.course_code
"""


@router.get("/teaching/offerings")
async def teaching_offerings(
    principal: CurrentUser, semester: str | None = Query(default=None)
) -> Envelope[dict[str, object]]:
    principal.require_role(Role.TEACHER, Role.ADMIN)
    from app.core import db

    async with db.connection() as conn:
        cur = await conn.execute(_SQL_TEACHING_OFFERINGS, (principal.user_id, semester, semester))
        rows = await cur.fetchall()
    return Envelope.ok(
        {
            "list": [
                {"offering_id": r[0], "course_name": r[1], "max_capacity": r[2] or 0, "enrolled_count": r[3] or 0}
                for r in rows
            ]
        }
    )


# --- 教师：本人任课花名册 ---
@router.get("/teaching/offerings/{offering_id}/roster")
async def teaching_roster(
    offering_id: str,
    principal: CurrentUser,
    service: EnrollmentServiceDep,
    include_dropped: bool = Query(default=False),
) -> Envelope[RosterView]:
    principal.require_role(Role.TEACHER, Role.ADMIN)
    return await _build_roster(service, offering_id, include_dropped)


async def _build_roster(service: EnrollmentServiceDep, offering_id: str, include_dropped: bool) -> Envelope[RosterView]:
    from app.core import errors

    offering, students = await service.get_roster(offering_id, include_dropped)
    if offering is None:
        raise errors.NotFound("开课实例不存在")
    view = RosterView(
        offering_id=offering.offering_id,
        course_code=offering.course_code,
        semester=offering.semester,
        students=[RosterStudent(student_id=s, name=n, enrolled_at=t) for s, n, t in students],
        total=len(students),
    )
    return Envelope.ok(view)
