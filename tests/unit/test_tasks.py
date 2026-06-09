"""后台任务单测：publish_outbox / prefetch_offerings / reconcile。"""

from __future__ import annotations

import pytest

import app.tasks.prefetch_offerings as prefetch_mod
import app.tasks.publish_outbox as outbox_mod
import app.tasks.reconcile as reconcile_mod
from app.domain.audit import OutboxEvent
from tests import fakes


@pytest.mark.asyncio
async def test_publish_pending_success(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    fakes.patch_db(monkeypatch)
    repo = fakes.FakeOutboxRepository()
    repo.events = [
        OutboxEvent(aggregate_type="enrollment", aggregate_id="1", event_type="enrollment.created", payload={})
    ]
    monkeypatch.setattr(outbox_mod, "_repo", repo)
    published: list[str] = []

    async def fake_publish(rk: str, body: bytes) -> None:
        published.append(rk)

    monkeypatch.setattr(outbox_mod.mq, "publish", fake_publish)
    n = await outbox_mod.publish_pending(10)
    assert n == 1 and published == ["enrollment.created"]


@pytest.mark.asyncio
async def test_publish_pending_failure_marks_dead(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    fakes.patch_db(monkeypatch)
    repo = fakes.FakeOutboxRepository()
    repo.events = [OutboxEvent(aggregate_type="e", aggregate_id="1", event_type="x", payload={})]
    dead: list[str] = []

    async def mark_dead(conn, event_id):  # type: ignore[no-untyped-def]
        dead.append(event_id)

    repo.mark_dead = mark_dead  # type: ignore[method-assign]
    monkeypatch.setattr(outbox_mod, "_repo", repo)

    async def boom(rk: str, body: bytes) -> None:
        raise RuntimeError("mq down")

    monkeypatch.setattr(outbox_mod.mq, "publish", boom)
    n = await outbox_mod.publish_pending(10)
    assert n == 0 and dead  # 投递失败 → 标记 dead


@pytest.mark.asyncio
async def test_refresh_offerings_composes_a_and_b(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # A 组开课为主干（课名/容量/班），B 组排课补时段/教室，A 组补教师名，容量播种
    from app.domain.offering import OfferingCatalogEntry

    fakes.patch_db(monkeypatch)
    cache = fakes.FakeOfferingCacheRepository()
    caps = fakes.FakeCapacityRepository()
    catalog = OfferingCatalogEntry(
        offering_id="OFF-1",
        course_id=7,
        course_code="CS101",
        course_name="软件工程",
        term_code="2026-1",
        class_no="01",
        capacity=100,
    )
    # B 组排课：schedule_client 把 course_id 放在 course_code 上，键需与 str(course_id) 一致
    b_sched = fakes.make_offering(offering_id="ignored", course_code="7")
    info = fakes.FakeInfoServiceClient(
        profile=fakes.StudentProfile(student_id="T-9001", name="张老师"), offerings=[catalog]
    )
    monkeypatch.setattr(prefetch_mod, "HttpInfoServiceClient", lambda: info)
    monkeypatch.setattr(
        prefetch_mod, "HttpScheduleServiceClient", lambda: fakes.FakeScheduleServiceClient(offerings=[b_sched])
    )
    monkeypatch.setattr(prefetch_mod, "PgOfferingCacheRepository", lambda: cache)
    monkeypatch.setattr(prefetch_mod, "PgCapacityRepository", lambda: caps)

    n = await prefetch_mod.refresh_offerings("2026-1")
    assert n == 1
    o = cache.offerings["OFF-1"]
    assert o.course_name == "软件工程" and o.teacher_name == "张老师"
    assert o.time_slots == b_sched.time_slots  # B 组时段已并入
    assert caps.caps["OFF-1"].max_capacity == 100  # 容量已从 A 组播种


@pytest.mark.asyncio
async def test_reconcile_once_wrapper(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(reconcile_mod, "get_redis", lambda: None)

    class FakeReconciler:
        def __init__(self, **kw):  # type: ignore[no-untyped-def]
            pass

        async def run_once(self) -> int:
            return 3

    monkeypatch.setattr(reconcile_mod, "Reconciler", FakeReconciler)
    assert await reconcile_mod.reconcile_once() == 3
