"""后台任务单测：publish_outbox / prefetch_offerings / reconcile / refresh_curriculum。"""

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
    repo.events = [OutboxEvent(aggregate_type="enrollment", aggregate_id="1",
                               event_type="enrollment.created", payload={})]
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
async def test_refresh_offerings(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    fakes.patch_db(monkeypatch)

    class FakeSched:
        async def list_offerings(self, semester, page, page_size):  # type: ignore[no-untyped-def]
            return [fakes.make_offering()] if page == 1 else []

    repo = fakes.FakeOfferingCacheRepository()
    monkeypatch.setattr(prefetch_mod, "HttpScheduleServiceClient", lambda: FakeSched())
    monkeypatch.setattr(prefetch_mod, "PgOfferingCacheRepository", lambda: repo)
    n = await prefetch_mod.refresh_offerings("2026-1", page_size=200)
    assert n == 1 and "B-CS101-2026-1-01" in repo.offerings


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


@pytest.mark.asyncio
async def test_refresh_curriculum_skeleton() -> None:
    from app.tasks.refresh_curriculum import refresh_curriculum_rules

    assert await refresh_curriculum_rules() == 0
