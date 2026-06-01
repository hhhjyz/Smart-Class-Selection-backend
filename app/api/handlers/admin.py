"""教务管理员 handlers。"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import CurrentUser, EnrollmentServiceDep, LotteryServiceDep
from app.core import db, errors
from app.core.auth import Role
from app.domain.enums import EnrollmentSource, Stage
from app.repositories.capacity_repo import PgCapacityRepository
from app.schemas.common import Envelope
from app.schemas.enrollment import (
    CapacityAdjustRequest,
    EnrollResult,
    ProxyEnrollRequest,
    ThrottleRequest,
)

router = APIRouter(prefix="/api/course-selection/v1/admin", tags=["admin"])


@router.post("/lottery/runs")
async def trigger_lottery(
    principal: CurrentUser, service: LotteryServiceDep, semester: str, seed: int | None = None
) -> Envelope[dict[str, object]]:
    principal.require_role(Role.ADMIN)
    run_id = await service.trigger(principal, semester=semester, seed=seed)
    return Envelope.ok({"run_id": run_id, "status": "running"})


@router.get("/lottery/runs/{run_id}")
async def get_lottery_run(
    run_id: str, principal: CurrentUser, service: LotteryServiceDep
) -> Envelope[dict[str, object]]:
    principal.require_role(Role.ADMIN)
    run = await service.get_run(run_id)
    if run is None:
        raise errors.NotFound("抽签批次不存在")
    return Envelope.ok(run)


@router.post("/proxy-enroll")
async def proxy_enroll(
    body: ProxyEnrollRequest, principal: CurrentUser, service: EnrollmentServiceDep
) -> Envelope[EnrollResult]:
    principal.require_role(Role.ADMIN)
    # 复用统一选课路径，标记来源为 admin_proxy，写 audit；允许越过软规则
    outcome = await service.enroll(
        principal, student_id=body.student_id, offering_id=body.offering_id,
        stage=Stage.ADD_DROP, source=EnrollmentSource.ADMIN_PROXY, allow_override=True,
    )
    return Envelope.ok(EnrollResult(enrollment_id=outcome.enrollment_id, status=outcome.status))


@router.post("/capacity/{offering_id}")
async def adjust_capacity(
    offering_id: str, body: CapacityAdjustRequest, principal: CurrentUser
) -> Envelope[dict[str, object]]:
    principal.require_role(Role.ADMIN)
    repo = PgCapacityRepository()
    async with db.transaction() as conn:
        updated = await repo.adjust_max(conn, offering_id, body.delta)
    if updated is None:
        raise errors.BadRequest("容量调整非法（低于已选人数或开课不存在）")
    # 同步 Redis 余量（事务外）
    from app.core.redis import get_redis
    from app.engine.capacity_lock import RedisStockStore

    await RedisStockStore(get_redis()).reset(offering_id, updated.max_capacity - updated.enrolled_count)
    return Envelope.ok({"offering_id": offering_id, "max_capacity": updated.max_capacity})


@router.post("/throttle")
async def update_throttle(body: ThrottleRequest, principal: CurrentUser) -> Envelope[dict[str, object]]:
    principal.require_role(Role.ADMIN)
    from app.core.config import get_settings

    settings = get_settings()
    if body.tick_interval_ms is not None:
        settings.waitroom_tick_ms = body.tick_interval_ms
    if body.capacity_per_tick is not None:
        settings.waitroom_cap_per_tick = body.capacity_per_tick
    if body.per_user_rps is not None:
        settings.per_user_rps = body.per_user_rps
    return Envelope.ok({
        "waitroom_tick_ms": settings.waitroom_tick_ms,
        "waitroom_cap_per_tick": settings.waitroom_cap_per_tick,
        "per_user_rps": settings.per_user_rps,
    })
