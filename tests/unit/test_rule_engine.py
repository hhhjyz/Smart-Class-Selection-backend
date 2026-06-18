"""规则引擎单元测试。规则是纯计算，无需任何 I/O。"""

from __future__ import annotations

import random

import pytest

from app.core import errors
from app.domain.enums import RuleType, Severity
from app.domain.offering import Offering, TimeSlot
from app.domain.study_plan import CurriculumRule
from app.services.rule_engine import (
    CreditCapRule,
    PrerequisiteRule,
    RuleContext,
    RuleEngine,
    TimeConflictRule,
)


def _offering(oid: str, code: str, day: int, periods: tuple[int, ...], campus: str = "紫金港") -> Offering:
    return Offering(
        offering_id=oid,
        course_code=code,
        course_name=code,
        teacher_id="T",
        teacher_name="师",
        semester="2026-1",
        time_slots=(TimeSlot(day=day, period=periods, weeks="1-16"),),
        campus=campus,
    )


def test_time_conflict_detected() -> None:
    target = _offering("o1", "CS101", day=1, periods=(1, 2))
    existing = _offering("o2", "MA102", day=1, periods=(2, 3))
    ctx = RuleContext(target=target, existing_offerings=[existing], passed_courses=frozenset())
    v = TimeConflictRule().check(ctx)
    assert v is not None
    assert v.code == errors.ERR_TIME_CONFLICT
    assert v.severity is Severity.HARD


def test_no_time_conflict_different_day() -> None:
    target = _offering("o1", "CS101", day=1, periods=(1, 2))
    existing = _offering("o2", "MA102", day=2, periods=(1, 2))
    ctx = RuleContext(target=target, existing_offerings=[existing], passed_courses=frozenset())
    assert TimeConflictRule().check(ctx) is None


def test_prerequisite_missing() -> None:
    target = _offering("o1", "OS301", day=3, periods=(5, 6))
    rule = CurriculumRule(
        rule_id="r1",
        major_code="CS",
        curriculum_version="2023",
        rule_type=RuleType.PREREQUISITE,
        payload={"subject_key": "OS301", "requires": ["DS201"]},
    )
    ctx = RuleContext(target=target, existing_offerings=[], passed_courses=frozenset(), curriculum_rules=[rule])
    v = PrerequisiteRule().check(ctx)
    assert v is not None and v.code == errors.ERR_PREREQUISITE


def test_prerequisite_satisfied() -> None:
    target = _offering("o1", "OS301", day=3, periods=(5, 6))
    rule = CurriculumRule(
        rule_id="r1",
        major_code="CS",
        curriculum_version="2023",
        rule_type=RuleType.PREREQUISITE,
        payload={"subject_key": "OS301", "requires": ["DS201"]},
    )
    ctx = RuleContext(
        target=target, existing_offerings=[], passed_courses=frozenset({"DS201"}), curriculum_rules=[rule]
    )
    assert PrerequisiteRule().check(ctx) is None


def test_credit_cap_soft_and_override() -> None:
    target = _offering("o1", "CS101", day=1, periods=(1, 2))
    ctx = RuleContext(
        target=target,
        existing_offerings=[],
        passed_courses=frozenset(),
        current_total_credit=29,
        target_credit=3,
        credit_cap=30,
    )
    v = CreditCapRule().check(ctx)
    assert v is not None and v.severity is Severity.SOFT
    # 教务特批可强选
    ctx_override = RuleContext(
        target=target,
        existing_offerings=[],
        passed_courses=frozenset(),
        current_total_credit=29,
        target_credit=3,
        credit_cap=30,
        allow_override=True,
    )
    assert CreditCapRule().check(ctx_override) is None


def test_engine_aggregates_and_hard_flag() -> None:
    target = _offering("o1", "CS101", day=1, periods=(1, 2))
    existing = _offering("o2", "MA102", day=1, periods=(2, 3))
    ctx = RuleContext(target=target, existing_offerings=[existing], passed_courses=frozenset())
    engine = RuleEngine()
    violations = engine.validate(ctx)
    assert engine.has_hard_violation(violations)


def test_campus_commute_soft_warning() -> None:
    from app.domain.enums import Severity as Sev
    from app.services.rule_engine import CampusCommuteRule

    target = _offering("o1", "CS101", day=1, periods=(3,), campus="紫金港")
    existing = _offering("o2", "MA1", day=1, periods=(2,), campus="玉泉")  # 相邻且跨校区
    ctx = RuleContext(target=target, existing_offerings=[existing], passed_courses=frozenset())
    v = CampusCommuteRule().check(ctx)
    assert v is not None and v.severity is Sev.SOFT
    # 强选可豁免
    ctx2 = RuleContext(target=target, existing_offerings=[existing], passed_courses=frozenset(), allow_override=True)
    assert CampusCommuteRule().check(ctx2) is None


def test_exclusive_rule() -> None:
    from app.services.rule_engine import ExclusiveRule

    rule = CurriculumRule(
        rule_id="r",
        major_code="CS",
        curriculum_version="2023",
        rule_type=RuleType.EXCLUSIVE,
        payload={"group": ["CS101", "CS101H"]},
    )
    target = _offering("o1", "CS101", day=1, periods=(1,))
    existing = _offering("o2", "CS101H", day=2, periods=(1,))
    ctx = RuleContext(target=target, existing_offerings=[existing], passed_courses=frozenset(), curriculum_rules=[rule])
    v = ExclusiveRule().check(ctx)
    assert v is not None and v.rule_type is RuleType.EXCLUSIVE


def test_credit_cap_within_limit_ok() -> None:
    from app.services.rule_engine import CreditCapRule

    target = _offering("o1", "CS101", day=1, periods=(1,))
    ctx = RuleContext(
        target=target,
        existing_offerings=[],
        passed_courses=frozenset(),
        current_total_credit=10,
        target_credit=3,
        credit_cap=30,
    )
    assert CreditCapRule().check(ctx) is None


@pytest.mark.parametrize("seed", range(20))
def test_fuzz_engine_never_crashes(seed: int) -> None:
    """fuzz：随机规则 × 随机学生，引擎绝不抛异常（健壮性）。"""
    rng = random.Random(seed)
    target = _offering("o1", "CS101", day=rng.randint(1, 5), periods=(rng.randint(1, 4),))
    existing = [
        _offering(
            f"e{i}", f"C{i}", day=rng.randint(1, 5), periods=(rng.randint(1, 4),), campus=rng.choice(["紫金港", "玉泉"])
        )
        for i in range(rng.randint(0, 8))
    ]
    ctx = RuleContext(
        target=target,
        existing_offerings=existing,
        passed_courses=frozenset(rng.sample(["DS201", "MA101", "EE101"], rng.randint(0, 3))),
        current_total_credit=rng.randint(0, 35),
        target_credit=rng.randint(1, 5),
    )
    # 不应抛出
    RuleEngine().validate(ctx)
