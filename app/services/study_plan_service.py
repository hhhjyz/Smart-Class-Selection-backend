"""培养方案编排：保存、校验、增删项。

校验委托规则引擎的硬规则子集（min_credit_total 等结构性规则）。
"""

from __future__ import annotations

import uuid

from app.core import db, errors
from app.core.auth import Principal
from app.domain.enums import ItemCategory, PlanStatus, RuleType, Severity
from app.domain.ports import InfoServiceClient, StudyPlanRepository
from app.domain.study_plan import StudyPlan, StudyPlanItem, Violation


class StudyPlanService:
    def __init__(self, *, study_plan_repo: StudyPlanRepository, info_client: InfoServiceClient) -> None:
        self._plans = study_plan_repo
        self._info = info_client

    async def get(self, student_id: str) -> StudyPlan | None:
        async with db.connection() as conn:
            return await self._plans.get_by_student(conn, student_id)

    async def get_program(
        self, *, major_code: str, grade: str | None = None, version: str | None = None
    ) -> list[StudyPlanItem]:
        """从 A 组拉取本专业培养方案要求的课程，逐个解析课程目录，产出必修项列表。"""
        programs = await self._info.list_training_programs(major_code, grade, version)
        if not programs:
            return []
        prog = programs[0]
        items: list[StudyPlanItem] = []
        for course_id in prog.required_course_ids:
            course = await self._info.get_course(course_id)
            if course is None:
                continue
            items.append(
                StudyPlanItem(
                    plan_item_id=str(course_id),
                    course_code=course.course_code,
                    category=ItemCategory.MAJOR_REQUIRED,
                    expected_semester="",
                    credit=course.credit,
                )
            )
        return items

    async def save(
        self, principal: Principal, *, major_code: str, curriculum_version: str, items: list[StudyPlanItem]
    ) -> tuple[StudyPlan, list[Violation]]:
        """全量保存并校验。校验不通过仍落库为 invalid，返回 violations。"""
        violations = await self._validate(principal.user_id, major_code, curriculum_version, items)
        status = PlanStatus.VALID if not _has_hard(violations) else PlanStatus.INVALID
        plan = StudyPlan(
            plan_id=str(uuid.uuid4()),
            student_id=principal.user_id,
            major_code=major_code,
            curriculum_version=curriculum_version,
            total_credit_required=sum(i.credit for i in items),
            status=status,
            items=tuple(items),
        )
        async with db.transaction() as conn:
            saved = await self._plans.upsert(conn, plan)
        if status is PlanStatus.INVALID:
            raise errors.DomainError(
                errors.ERR_PLAN_RULE_FAILED,
                data={
                    "status": status.value,
                    "valid": False,
                    "violations": [v.model_dump(mode="json") for v in violations],
                },
            )
        return saved, violations

    async def validate_dry_run(
        self, student_id: str, *, major_code: str, curriculum_version: str, items: list[StudyPlanItem]
    ) -> list[Violation]:
        return await self._validate(student_id, major_code, curriculum_version, items)

    async def delete_item(self, principal: Principal, plan_item_id: str) -> bool:
        async with db.transaction() as conn:
            return await self._plans.delete_item(conn, principal.user_id, plan_item_id)

    async def _validate(
        self, student_id: str, major_code: str, curriculum_version: str, items: list[StudyPlanItem]
    ) -> list[Violation]:
        async with db.connection() as conn:
            rules = await self._plans.get_curriculum_rules(conn, major_code, curriculum_version)
        out: list[Violation] = []
        total = sum(i.credit for i in items)
        for rule in rules:
            if rule.rule_type is RuleType.MIN_CREDIT_TOTAL:
                required = _as_float(rule.payload.get("min", 0))
                if total < required:
                    out.append(
                        Violation(
                            code=errors.ERR_PLAN_RULE_FAILED,
                            rule_type=rule.rule_type,
                            message=f"总学分 {total} < 要求 {required}",
                            severity=Severity.HARD,
                        )
                    )
            elif rule.rule_type is RuleType.MIN_CREDIT_CATEGORY:
                cat = rule.payload.get("category")
                required = _as_float(rule.payload.get("min", 0))
                got = sum(i.credit for i in items if i.category.value == cat)
                if got < required:
                    out.append(
                        Violation(
                            code=errors.ERR_PLAN_RULE_FAILED,
                            rule_type=rule.rule_type,
                            message=f"{cat} 学分 {got} < 要求 {required}",
                            severity=Severity.HARD,
                        )
                    )
        return out


def _has_hard(violations: list[Violation]) -> bool:
    return any(v.severity is Severity.HARD for v in violations)


def _as_float(v: object) -> float:
    """把规则 payload（dict[str, object]）里的数值安全转 float。"""
    if isinstance(v, (int, float, str)):
        return float(v)
    return 0.0
