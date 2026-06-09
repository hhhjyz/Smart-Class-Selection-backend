"""课程检索与开课详情 handlers。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import CurrentUser, get_offering_repo
from app.core import db, errors
from app.domain.offering import Offering
from app.repositories.capacity_repo import PgCapacityRepository
from app.repositories.offering_cache_repo import PgOfferingCacheRepository
from app.schemas.common import Envelope, Pagination

router = APIRouter(prefix="/api/course-selection/v1", tags=["courses"])

OfferingRepoDep = Annotated[PgOfferingCacheRepository, Depends(get_offering_repo)]
_capacity = PgCapacityRepository()


async def _with_capacity(conn, offering: Offering) -> dict[str, object]:  # type: ignore[no-untyped-def]
    """开课基本信息 + 权威容量（remaining/max_capacity/enrolled_count）合并成前端视图。"""
    d = offering.model_dump(mode="json")
    cap = await _capacity.get(conn, offering.offering_id)
    if cap is not None:
        d["max_capacity"] = cap.max_capacity
        d["enrolled_count"] = cap.enrolled_count
        d["remaining"] = max(cap.max_capacity - cap.enrolled_count, 0)
    else:
        d["max_capacity"] = 0
        d["enrolled_count"] = 0
        d["remaining"] = 0
    return d


@router.get("/courses/search")
async def search_courses(
    principal: CurrentUser,
    repo: OfferingRepoDep,
    keyword: str | None = Query(default=None),
    teacher_name: str | None = Query(default=None),
    semester: str | None = Query(default=None),
    category: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> Envelope[dict[str, object]]:
    pg = Pagination(page=page, page_size=page_size)
    async with db.connection() as conn:
        offerings, total = await repo.search(
            conn,
            keyword=keyword,
            teacher_name=teacher_name,
            semester=semester,
            category=category,
            limit=pg.page_size,
            offset=pg.offset,
        )
        items = [await _with_capacity(conn, o) for o in offerings]
    return Envelope.ok({"list": items, "total": total})


@router.get("/offerings/{offering_id}")
async def get_offering(offering_id: str, principal: CurrentUser, repo: OfferingRepoDep) -> Envelope[dict[str, object]]:
    async with db.connection() as conn:
        offering = await repo.get(conn, offering_id)
        if offering is None:
            raise errors.NotFound("开课实例不存在")
        return Envelope.ok(await _with_capacity(conn, offering))


@router.get("/offerings/{offering_id}/conflicts")
async def offering_conflicts(
    offering_id: str,
    principal: CurrentUser,
    repo: OfferingRepoDep,
    student_id: str = Query(default="me"),
) -> Envelope[dict[str, object]]:
    """选课前时间冲突预检：目标开课时段 vs 本人已选课程时段。"""
    sid = principal.user_id if student_id == "me" else student_id
    async with db.connection() as conn:
        target = await repo.get(conn, offering_id)
        if target is None:
            raise errors.NotFound("开课实例不存在")
        existing = await repo.list_for_student_timetable(conn, sid, target.semester)
    conflicts: list[dict[str, object]] = []
    for o in existing:
        if o.offering_id == target.offering_id:
            continue
        for a in target.time_slots:
            for b in o.time_slots:
                if a.day == b.day and set(a.period) & set(b.period):
                    conflicts.append(
                        {
                            "type": "time",
                            "with_offering_id": o.offering_id,
                            "message": f"周{a.day} 第{'-'.join(map(str, a.period))}节与《{o.course_name}》冲突",
                            "code": errors.ERR_TIME_CONFLICT,
                        }
                    )
    return Envelope.ok({"has_conflict": bool(conflicts), "conflicts": conflicts})
