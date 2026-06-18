"""培养方案域实体。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import ItemCategory, PlanStatus, RuleType, Severity


class StudyPlanItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    plan_item_id: str
    course_code: str
    category: ItemCategory
    expected_semester: str
    credit: float = Field(ge=0)


class StudyPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    plan_id: str
    student_id: str
    major_code: str
    curriculum_version: str
    total_credit_required: float = Field(ge=0)
    status: PlanStatus
    validated_at: datetime | None = None
    items: tuple[StudyPlanItem, ...] = ()


class TrainingProgram(BaseModel):
    """培养方案（来源 A 组 `GET /api/v1/info/data-provision/training-programs`）。

    A 组 TrainingProgramDataResponse：program_code / major_code / grade / version /
    required_course_ids(list[int])，即该专业要求修读的课程 id 列表。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    program_code: str
    major_code: str
    grade: str
    version: str = "1.0"
    required_course_ids: tuple[int, ...] = ()


class CurriculumRule(BaseModel):
    """培养方案规则缓存项，对应 curriculum_rules 表。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    rule_id: str
    major_code: str
    curriculum_version: str
    rule_type: RuleType
    payload: dict[str, object]
    priority: int = 0


class Violation(BaseModel):
    """规则校验违例。规则引擎产出，跨层传递的契约对象。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: int
    rule_type: RuleType | None = None
    message: str
    severity: Severity = Severity.HARD
