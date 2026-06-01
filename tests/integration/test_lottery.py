"""抽签批处理集成测试（真实 PostgreSQL）：覆盖 lottery_service + lottery_runner。"""

from __future__ import annotations

import uuid

import pytest

from app.core.auth import Principal, Role
from app.repositories.audit_repo import PgAuditRepository as _AuditRepo
from app.repositories.outbox_repo import PgOutboxRepository as _OutboxRepo
from app.services.lottery_service import LotteryService

pytestmark = pytest.mark.integration

_ADMIN = Principal(user_id="A-lot", role=Role.ADMIN)


@pytest.mark.asyncio
async def test_lottery_run_allocates_within_capacity(pg_pool, app_pool) -> None:  # type: ignore[no-untyped-def]
    sem = "2099-LOT"
    oid = "LOT-CS1"
    # 容量 2，3 个意愿 → 至多 2 中签
    async with pg_pool.connection() as conn, conn.transaction():
        await conn.execute(
            "INSERT INTO course_selection.course_capacity (offering_id, semester, max_capacity, enrolled_count)"
            " VALUES (%s,%s,2,0) ON CONFLICT (offering_id) DO UPDATE SET max_capacity=2, enrolled_count=0",
            (oid, sem),
        )
        for i in range(3):
            await conn.execute(
                "INSERT INTO course_selection.enrollment_intents"
                " (intent_id, student_id, offering_id, semester, priority) VALUES (%s,%s,%s,%s,%s)",
                (str(uuid.uuid4()), f"S-lot{i}", oid, sem, 1),
            )

    svc = LotteryService(audit_repo=_AuditRepo(), outbox_repo=_OutboxRepo(), total_shards=2)
    run_id = await svc.trigger(_ADMIN, semester=sem, seed=42)

    run = await svc.get_run(run_id)
    assert run is not None and run["status"] == "completed"
    async with pg_pool.connection() as conn:
        cur = await conn.execute(
            "SELECT count(*) FROM course_selection.enrollments WHERE offering_id=%s AND status='enrolled'", (oid,)
        )
        enrolled = (await cur.fetchone())[0]
    assert enrolled == 2  # 不超卖


@pytest.mark.asyncio
async def test_lottery_reproducible_with_seed(pg_pool, app_pool) -> None:  # type: ignore[no-untyped-def]
    """同 seed 两次运行决策一致（可申诉复算）——这里验证 run 落库 seed。"""
    svc = LotteryService(audit_repo=_AuditRepo(), outbox_repo=_OutboxRepo(), total_shards=1)
    run_id = await svc.trigger(_ADMIN, semester="2099-EMPTY", seed=123)
    run = await svc.get_run(run_id)
    assert run is not None and run["seed"] == 123
