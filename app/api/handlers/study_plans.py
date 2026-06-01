"""培养方案 handlers。"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Response

from app.api.deps import CurrentUser, StudyPlanServiceDep
from app.core.auth import Role
from app.domain.study_plan import StudyPlanItem
from app.schemas.common import Envelope
from app.schemas.study_plan import PlanItemInput, PlanValidationResult, SavePlanRequest, ViolationView

router = APIRouter(prefix="/api/course-selection/v1", tags=["study-plans"])


def _to_items(items: list[PlanItemInput]) -> list[StudyPlanItem]:
    return [
        StudyPlanItem(
            plan_item_id=str(uuid.uuid4()), course_code=i.course_code, category=i.category,
            expected_semester=i.expected_semester, credit=i.credit,
        )
        for i in items
    ]


@router.get("/study-plans/me")
async def get_my_plan(principal: CurrentUser, service: StudyPlanServiceDep) -> Envelope[object]:
    principal.require_role(Role.STUDENT)
    plan = await service.get(principal.user_id)
    return Envelope.ok(plan.model_dump(mode="json") if plan else None)


@router.put("/study-plans/me")
async def save_my_plan(
    body: SavePlanRequest, principal: CurrentUser, service: StudyPlanServiceDep
) -> Envelope[PlanValidationResult]:
    principal.require_role(Role.STUDENT)
    plan, violations = await service.save(
        principal, major_code=body.major_code,
        curriculum_version=body.curriculum_version, items=_to_items(body.items),
    )
    return Envelope.ok(PlanValidationResult(
        plan_id=plan.plan_id, status=plan.status, valid=True,
        violations=[ViolationView(**v.model_dump()) for v in violations],
    ))


@router.post("/study-plans/me/validate")
async def validate_my_plan(
    body: SavePlanRequest, principal: CurrentUser, service: StudyPlanServiceDep
) -> Envelope[PlanValidationResult]:
    principal.require_role(Role.STUDENT)
    violations = await service.validate_dry_run(
        principal.user_id, major_code=body.major_code,
        curriculum_version=body.curriculum_version, items=_to_items(body.items),
    )
    from app.domain.enums import PlanStatus, Severity

    valid = not any(v.severity is Severity.HARD for v in violations)
    return Envelope.ok(PlanValidationResult(
        status=PlanStatus.VALID if valid else PlanStatus.INVALID, valid=valid,
        violations=[ViolationView(**v.model_dump()) for v in violations],
    ))


@router.delete("/study-plans/me/items/{plan_item_id}", status_code=204)
async def delete_plan_item(
    plan_item_id: str, principal: CurrentUser, service: StudyPlanServiceDep
) -> Response:
    principal.require_role(Role.STUDENT)
    await service.delete_item(principal, plan_item_id)
    return Response(status_code=204)
