"""课程检索与开课详情 handlers。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import CurrentUser, get_offering_repo
from app.core import db, errors
from app.repositories.offering_cache_repo import PgOfferingCacheRepository
from app.schemas.common import Envelope, Pagination

router = APIRouter(prefix="/api/course-selection/v1", tags=["courses"])

OfferingRepoDep = Annotated[PgOfferingCacheRepository, Depends(get_offering_repo)]


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
            conn, keyword=keyword, teacher_name=teacher_name, semester=semester,
            category=category, limit=pg.page_size, offset=pg.offset,
        )
    return Envelope.ok({
        "list": [o.model_dump(mode="json") for o in offerings],
        "total": total,
    })


@router.get("/offerings/{offering_id}")
async def get_offering(
    offering_id: str, principal: CurrentUser, repo: OfferingRepoDep
) -> Envelope[dict[str, object]]:
    async with db.connection() as conn:
        offering = await repo.get(conn, offering_id)
    if offering is None:
        raise errors.NotFound("开课实例不存在")
    return Envelope.ok(offering.model_dump(mode="json"))
