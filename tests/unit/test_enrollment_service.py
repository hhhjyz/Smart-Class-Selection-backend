"""EnrollmentService 单测：用内存 fakes 覆盖主路径与全部 corner case。

依赖倒置让这一切无需 DB/Redis：注入 fake ports，patch db 事务上下文。
"""

from __future__ import annotations

import pytest

from app.core import errors
from app.core.auth import Principal, Role
from app.domain.enums import EnrollmentSource, EnrollmentStatus, PlanStatus, RuleType, Stage
from app.domain.study_plan import CurriculumRule, StudyPlan
from app.services.enrollment_service import EnrollmentService
from app.services.rule_engine import RuleEngine
from tests import fakes

_STUDENT = Principal(user_id="S-1", role=Role.STUDENT)
_ADMIN = Principal(user_id="A-1", role=Role.ADMIN)
_OID = "B-CS101-2026-1-01"


def _build(*, admitted=True, stock=1, max_capacity=50, enrolled=0, rules=(), timetable=()):  # type: ignore[no-untyped-def]
    enr = fakes.FakeEnrollmentRepository()
    cap = fakes.FakeCapacityRepository({_OID: fakes.make_capacity(_OID, max_capacity, enrolled)})
    off = fakes.FakeOfferingCacheRepository({_OID: fakes.make_offering(_OID)}, timetable=timetable)
    # 学生须有培养方案，service 才会加载并应用 curriculum_rules
    student_plan = StudyPlan(
        plan_id="P-1",
        student_id="S-1",
        major_code="CS",
        curriculum_version="2023",
        total_credit_required=160,
        status=PlanStatus.VALID,
    )
    plan = fakes.FakeStudyPlanRepository(plans={"S-1": student_plan}, rules=rules)
    audit = fakes.FakeAuditRepository()
    outbox = fakes.FakeOutboxRepository()
    stock_store = fakes.FakeStockStore({_OID: stock})
    room = fakes.FakeWaitingRoom(admitted=admitted)
    svc = EnrollmentService(
        enrollment_repo=enr,
        capacity_repo=cap,
        offering_repo=off,
        study_plan_repo=plan,
        audit_repo=audit,
        outbox_repo=outbox,
        stock=stock_store,
        waiting_room=room,
        info_client=fakes.FakeInfoServiceClient(),
        rule_engine=RuleEngine(),
    )
    deps = {"enr": enr, "cap": cap, "off": off, "audit": audit, "outbox": outbox, "stock": stock_store, "room": room}
    return svc, deps


@pytest.fixture(autouse=True)
def _patch_db(monkeypatch):  # type: ignore[no-untyped-def]
    fakes.patch_db(monkeypatch)


@pytest.mark.asyncio
async def test_enroll_success() -> None:
    svc, dep = _build(admitted=True, stock=1)
    out = await svc.enroll(_STUDENT, student_id="S-1", offering_id=_OID, stage=Stage.ADD_DROP)
    assert out.status is EnrollmentStatus.ENROLLED
    assert dep["cap"].caps[_OID].enrolled_count == 1
    assert [e.event_type for e in dep["outbox"].events] == ["enrollment.created"]
    assert dep["audit"].entries[0].action == "enroll.create"
    assert dep["room"].consumed == [(_OID, "S-1")]


@pytest.mark.asyncio
async def test_enroll_queued_when_not_admitted() -> None:
    svc, dep = _build(admitted=False)
    with pytest.raises(errors.DomainError) as ei:
        await svc.enroll(_STUDENT, student_id="S-1", offering_id=_OID, stage=Stage.ADD_DROP)
    assert ei.value.code == errors.ERR_QUEUED
    assert ei.value.data["position"] == 7  # type: ignore[index]
    assert dep["enr"].rows == {}  # 未落库


@pytest.mark.asyncio
async def test_enroll_capacity_full_stock_zero() -> None:
    svc, dep = _build(admitted=True, stock=0)
    with pytest.raises(errors.CapacityFull):
        await svc.enroll(_STUDENT, student_id="S-1", offering_id=_OID, stage=Stage.ADD_DROP)
    assert dep["enr"].rows == {}


@pytest.mark.asyncio
async def test_enroll_rule_rejected_hard_violation() -> None:
    # 互斥规则命中 → 硬违例（不依赖成绩数据源）
    rule = CurriculumRule(
        rule_id="r1",
        major_code="CS",
        curriculum_version="2023",
        rule_type=RuleType.EXCLUSIVE,
        payload={"group": ["CS101", "CS101H"]},
    )
    # 已选互斥课 CS101H
    timetable = [fakes.make_offering("B-CS101H", "CS101H")]
    svc, dep = _build(admitted=True, stock=1, rules=[rule], timetable=timetable)
    with pytest.raises(errors.RuleRejected) as ei:
        await svc.enroll(_STUDENT, student_id="S-1", offering_id=_OID, stage=Stage.ADD_DROP)
    assert ei.value.data["violations"]  # type: ignore[index]
    # 规则在扣库存前拒绝，库存不动
    assert dep["stock"].stock[_OID] == 1


@pytest.mark.asyncio
async def test_enroll_duplicate_releases_stock() -> None:
    svc, dep = _build(admitted=True, stock=2)
    await svc.enroll(_STUDENT, student_id="S-1", offering_id=_OID, stage=Stage.ADD_DROP)
    with pytest.raises(errors.DuplicateEnrollment):
        await svc.enroll(_STUDENT, student_id="S-1", offering_id=_OID, stage=Stage.ADD_DROP)
    assert dep["stock"].releases == 1  # 第二次扣了又补偿


@pytest.mark.asyncio
async def test_enroll_capacity_version_fail_releases_and_raises() -> None:
    # max=enrolled → increment 越界返回 None → CapacityFull + 补偿
    svc, dep = _build(admitted=True, stock=1, max_capacity=1, enrolled=1)
    with pytest.raises(errors.CapacityFull):
        await svc.enroll(_STUDENT, student_id="S-1", offering_id=_OID, stage=Stage.ADD_DROP)
    assert dep["stock"].releases == 1


@pytest.mark.asyncio
async def test_enroll_idempotency_short_circuit() -> None:
    svc, dep = _build(admitted=True, stock=2)
    first = await svc.enroll(_STUDENT, student_id="S-1", offering_id=_OID, stage=Stage.ADD_DROP, idempotency_key="k1")
    # 同 key 再次提交 → 直接返回首次结果，不再扣库存
    stock_before = dep["stock"].stock[_OID]
    second = await svc.enroll(_STUDENT, student_id="S-1", offering_id=_OID, stage=Stage.ADD_DROP, idempotency_key="k1")
    assert second.enrollment_id == first.enrollment_id
    assert dep["stock"].stock[_OID] == stock_before


@pytest.mark.asyncio
async def test_admin_proxy_skips_waiting_room() -> None:
    svc, dep = _build(admitted=False, stock=1)  # 未放行也应通过（代选不过等待室）
    out = await svc.enroll(
        _ADMIN,
        student_id="S-1",
        offering_id=_OID,
        stage=Stage.ADD_DROP,
        source=EnrollmentSource.ADMIN_PROXY,
        allow_override=True,
    )
    assert out.status is EnrollmentStatus.ENROLLED
    assert dep["room"].enqueued == []


@pytest.mark.asyncio
async def test_enroll_offering_not_found() -> None:
    svc, _ = _build(admitted=True, stock=1)
    with pytest.raises(errors.NotFound):
        await svc.enroll(_STUDENT, student_id="S-1", offering_id="NOPE", stage=Stage.ADD_DROP)


@pytest.mark.asyncio
async def test_drop_success_and_idempotent() -> None:
    svc, dep = _build(admitted=True, stock=2)
    out = await svc.enroll(_STUDENT, student_id="S-1", offering_id=_OID, stage=Stage.ADD_DROP)
    ok = await svc.drop(_STUDENT, out.enrollment_id)
    assert ok is True
    assert dep["cap"].caps[_OID].enrolled_count == 0
    assert dep["outbox"].events[-1].event_type == "enrollment.canceled"
    # 再次退课 → 幂等，返回 False
    assert await svc.drop(_STUDENT, out.enrollment_id) is False


@pytest.mark.asyncio
async def test_drop_nonexistent_returns_false() -> None:
    svc, _ = _build()
    assert await svc.drop(_STUDENT, "missing") is False


@pytest.mark.asyncio
async def test_swap_drops_then_enrolls() -> None:
    svc, _ = _build(admitted=True, stock=3)
    first = await svc.enroll(_STUDENT, student_id="S-1", offering_id=_OID, stage=Stage.ADD_DROP)
    # swap：退掉 first，再选同一门（fake 已退课，可重新选）
    out = await svc.swap(_STUDENT, drop_id=first.enrollment_id, add_offering_id=_OID)
    assert out.status is EnrollmentStatus.ENROLLED


@pytest.mark.asyncio
async def test_swap_drop_missing_raises() -> None:
    svc, _ = _build()
    with pytest.raises(errors.NotFound):
        await svc.swap(_STUDENT, drop_id="missing", add_offering_id=_OID)


@pytest.mark.asyncio
async def test_list_my_enrollments() -> None:
    svc, _ = _build(admitted=True, stock=1)
    await svc.enroll(_STUDENT, student_id="S-1", offering_id=_OID, stage=Stage.ADD_DROP)
    rows = await svc.list_my_enrollments("S-1", "2026-1", "enrolled")
    assert len(rows) == 1
    enrollment, offering = rows[0]
    assert offering is not None and offering.course_code == "CS101"


@pytest.mark.asyncio
async def test_get_roster_enriches_names_from_a_team() -> None:
    svc, _ = _build(admitted=True, stock=1)
    await svc.enroll(_STUDENT, student_id="S-1", offering_id=_OID, stage=Stage.ADD_DROP)
    offering, students = await svc.get_roster(_OID, include_dropped=False)
    assert offering is not None
    # 姓名经 A 组 GET /api/v1/users/{id} 补全（fake 返回固定姓名）
    assert students[0][0] == "S-1" and students[0][1] == "测试同学"
