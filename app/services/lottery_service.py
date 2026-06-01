"""抽签编排：触发批次、驱动分片 worker、发布完成事件。"""

from __future__ import annotations

import uuid

from app.core import db
from app.core.auth import Principal
from app.domain.audit import AuditEntry, OutboxEvent
from app.domain.ports import AuditRepository, OutboxRepository
from app.engine.lottery_runner import run_shard

SQL_CREATE_RUN = """
INSERT INTO course_selection.lottery_runs
    (run_id, semester, triggered_at, triggered_by, offering_count, enrolled_count, seed, status)
VALUES (%s, %s, NOW(), %s, 0, 0, %s, 'running')
"""

SQL_FINISH_RUN = """
UPDATE course_selection.lottery_runs
   SET status = 'completed', enrolled_count = %s, report = %s
 WHERE run_id = %s
"""

SQL_GET_RUN = """
SELECT run_id, semester, status, offering_count, enrolled_count, seed
  FROM course_selection.lottery_runs WHERE run_id = %s
"""


class LotteryService:
    def __init__(self, *, audit_repo: AuditRepository, outbox_repo: OutboxRepository, total_shards: int = 8) -> None:
        self._audit = audit_repo
        self._outbox = outbox_repo
        self._total_shards = total_shards

    async def trigger(self, principal: Principal, *, semester: str, seed: int | None) -> str:
        """触发抽签：建批次记录，逐分片处理（各自短事务），发完成事件。"""
        run_id = str(uuid.uuid4())
        actual_seed = seed if seed is not None else uuid.uuid4().int % (2**31)
        async with db.transaction() as conn:
            await conn.execute(SQL_CREATE_RUN, (run_id, semester, principal.user_id, actual_seed))

        succeeded = failed = 0
        for shard in range(self._total_shards):
            # 每个分片一个短事务，避免单个大事务长时间持锁
            async with db.transaction() as conn:
                res = await run_shard(
                    conn, semester=semester, shard=shard,
                    total_shards=self._total_shards, seed=actual_seed,
                )
            succeeded += res.succeeded
            failed += res.failed

        import json

        async with db.transaction() as conn:
            await conn.execute(
                SQL_FINISH_RUN,
                (succeeded, json.dumps({"succeeded": succeeded, "failed": failed}), run_id),
            )
            await self._audit.write(
                conn,
                AuditEntry(
                    actor_id=principal.user_id, actor_role=principal.role.value,
                    action="lottery.run", target_type="lottery_run", target_id=run_id,
                    after={"seed": actual_seed, "succeeded": succeeded, "failed": failed},
                ),
            )
            await self._outbox.emit(
                conn,
                OutboxEvent(
                    aggregate_type="lottery_run", aggregate_id=run_id,
                    event_type="lottery.completed",
                    payload={
                        "run_id": run_id, "semester": semester,
                        "succeeded_count": succeeded, "failed_count": failed,
                    },
                ),
            )
        return run_id

    async def get_run(self, run_id: str) -> dict[str, object] | None:
        async with db.connection() as conn:
            cur = await conn.execute(SQL_GET_RUN, (run_id,))
            row = await cur.fetchone()
        if row is None:
            return None
        return {
            "run_id": row[0], "semester": row[1], "status": row[2],
            "offering_count": row[3], "enrolled_count": row[4], "seed": row[5],
        }
