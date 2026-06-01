"""培养方案相关 HTTP DTO。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import ItemCategory, PlanStatus, RuleType, Severity


class PlanItemInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    course_code: str
    category: ItemCategory
    expected_semester: str
    credit: float = Field(ge=0)


class SavePlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    major_code: str
    curriculum_version: str
    items: list[PlanItemInput]


class ViolationView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: int
    rule_type: RuleType | None = None
    message: str
    severity: Severity = Severity.HARD


class PlanValidationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_id: str | None = None
    status: PlanStatus
    valid: bool
    violations: list[ViolationView] = []
