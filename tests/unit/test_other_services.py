"""StudyPlanService / AIAdvisor / Reconciler 单测（fakes + patched db）。"""

from __future__ import annotations

import pytest

from app.core import errors
from app.core.auth import Principal, Role
from app.domain.enums import ItemCategory, RuleType
from app.domain.study_plan import CurriculumRule, StudyPlanItem
from app.services.ai_advisor import AIAdvisor
from app.services.enrollment_service import EnrollmentService
from app.services.reconciler import Reconciler
from app.services.rule_engine import RuleEngine
from app.services.study_plan_service import StudyPlanService
from tests import fakes

_STUDENT = Principal(user_id="S-1", role=Role.STUDENT)
_OID = "B-CS101-2026-1-01"


@pytest.fixture(autouse=True)
def _patch_db(monkeypatch):  # type: ignore[no-untyped-def]
    fakes.patch_db(monkeypatch)


def _items(*credits: float) -> list[StudyPlanItem]:
    return [
        StudyPlanItem(plan_item_id=f"i{i}", course_code=f"C{i}", category=ItemCategory.MAJOR_REQUIRED,
                      expected_semester="2026-1", credit=c)
        for i, c in enumerate(credits)
    ]


# ----------------------- StudyPlanService -----------------------
@pytest.mark.asyncio
async def test_study_plan_save_valid() -> None:
    rule = CurriculumRule(rule_id="r1", major_code="CS", curriculum_version="2023",
                          rule_type=RuleType.MIN_CREDIT_TOTAL, payload={"min": 8})
    svc = StudyPlanService(study_plan_repo=fakes.FakeStudyPlanRepository(rules=[rule]))
    plan, violations = await svc.save(_STUDENT, major_code="CS", curriculum_version="2023", items=_items(5, 5))
    assert plan.status.value == "valid"
    assert violations == []


@pytest.mark.asyncio
async def test_study_plan_save_invalid_raises_30101() -> None:
    rule = CurriculumRule(rule_id="r1", major_code="CS", curriculum_version="2023",
                          rule_type=RuleType.MIN_CREDIT_TOTAL, payload={"min": 20})
    svc = StudyPlanService(study_plan_repo=fakes.FakeStudyPlanRepository(rules=[rule]))
    with pytest.raises(errors.DomainError) as ei:
        await svc.save(_STUDENT, major_code="CS", curriculum_version="2023", items=_items(3))
    assert ei.value.code == errors.ERR_PLAN_RULE_FAILED


@pytest.mark.asyncio
async def test_study_plan_validate_category_rule() -> None:
    rule = CurriculumRule(rule_id="r1", major_code="CS", curriculum_version="2023",
                          rule_type=RuleType.MIN_CREDIT_CATEGORY,
                          payload={"category": "major_elective", "min": 4})
    svc = StudyPlanService(study_plan_repo=fakes.FakeStudyPlanRepository(rules=[rule]))
    v = await svc.validate_dry_run("S-1", major_code="CS", curriculum_version="2023", items=_items(5, 5))
    assert v and v[0].rule_type is RuleType.MIN_CREDIT_CATEGORY


@pytest.mark.asyncio
async def test_study_plan_get_and_delete() -> None:
    repo = fakes.FakeStudyPlanRepository()
    svc = StudyPlanService(study_plan_repo=repo)
    assert await svc.get("S-1") is None
    assert await svc.delete_item(_STUDENT, "x") is False


# ----------------------- AIAdvisor -----------------------
def _enroll_service(stock=2):  # type: ignore[no-untyped-def]
    return EnrollmentService(
        enrollment_repo=fakes.FakeEnrollmentRepository(),
        capacity_repo=fakes.FakeCapacityRepository({_OID: fakes.make_capacity(_OID, 50, 0)}),
        offering_repo=fakes.FakeOfferingCacheRepository({_OID: fakes.make_offering(_OID)}),
        study_plan_repo=fakes.FakeStudyPlanRepository(),
        audit_repo=fakes.FakeAuditRepository(), outbox_repo=fakes.FakeOutboxRepository(),
        stock=fakes.FakeStockStore({_OID: stock}), waiting_room=fakes.FakeWaitingRoom(admitted=True),
        info_client=fakes.FakeInfoServiceClient(), rule_engine=RuleEngine(),
    )


@pytest.mark.asyncio
async def test_ai_stream_passthrough_and_guardrail() -> None:
    # 正常 delta + done
    llm_ok = fakes.FakeLLMClient([{"content": "推荐"}, {"done": True}])
    advisor = AIAdvisor(llm_client=llm_ok, offering_repo=fakes.FakeOfferingCacheRepository(),
                        audit_repo=fakes.FakeAuditRepository(), enrollment_service=_enroll_service())
    chunks = [c async for c in advisor.stream_message(_STUDENT, "hi")]
    assert {"content": "推荐"} in chunks

    # 越界工具调用 → 502 + audit
    audit = fakes.FakeAuditRepository()
    llm_bad = fakes.FakeLLMClient([{"tool_call": {"name": "rm_rf", "arguments": "{}"}}])
    advisor2 = AIAdvisor(llm_client=llm_bad, offering_repo=fakes.FakeOfferingCacheRepository(),
                         audit_repo=audit, enrollment_service=_enroll_service())
    with pytest.raises(errors.UpstreamDown):
        _ = [c async for c in advisor2.stream_message(_STUDENT, "hi")]
    assert audit.entries[0].action == "ai.guardrail.violated"


@pytest.mark.asyncio
async def test_ai_allowed_tool_passes() -> None:
    llm = fakes.FakeLLMClient([{"tool_call": {"name": "search_courses", "arguments": "{}"}}, {"done": True}])
    advisor = AIAdvisor(llm_client=llm, offering_repo=fakes.FakeOfferingCacheRepository(),
                        audit_repo=fakes.FakeAuditRepository(), enrollment_service=_enroll_service())
    chunks = [c async for c in advisor.stream_message(_STUDENT, "hi")]
    assert any("tool_call" in c for c in chunks)


# ----------------------- Reconciler -----------------------
@pytest.mark.asyncio
async def test_reconciler_fixes_drift() -> None:
    cap = fakes.FakeCapacityRepository({_OID: fakes.make_capacity(_OID, 50, 10)})  # expected remaining=40
    stock = fakes.FakeStockStore({_OID: 5})  # drift
    rec = Reconciler(capacity_repo=cap, audit_repo=fakes.FakeAuditRepository(), stock=stock)
    fixes = await rec.run_once()
    assert fixes == 1
    assert stock.stock[_OID] == 40  # 校齐到权威余量


@pytest.mark.asyncio
async def test_reconciler_no_drift() -> None:
    cap = fakes.FakeCapacityRepository({_OID: fakes.make_capacity(_OID, 50, 10)})
    stock = fakes.FakeStockStore({_OID: 40})  # 一致
    rec = Reconciler(capacity_repo=cap, audit_repo=fakes.FakeAuditRepository(), stock=stock)
    assert await rec.run_once() == 0
