"""Redis ↔ Postgres 库存对账。

每分钟一次，将 Redis 库存校齐到 PG 权威余量；单分钟修正 > 阈值触发告警。
Redis 失效时可由本任务从 course_capacity 完整重建 stock:*。
对应《04 高并发引擎设计》「一致性对账」。
"""

from __future__ import annotations

import logging

from app.core import db
from app.domain.audit import AuditEntry
from app.domain.ports import AuditRepository, CapacityRepository, StockStore

logger = logging.getLogger(__name__)

_ALERT_THRESHOLD = 5


class Reconciler:
    def __init__(self, *, capacity_repo: CapacityRepository, audit_repo: AuditRepository, stock: StockStore) -> None:
        self._capacity = capacity_repo
        self._audit = audit_repo
        self._stock = stock

    async def run_once(self, older_than_seconds: int = 60) -> int:
        """对账一轮，返回修正条数。"""
        async with db.connection() as conn:
            stale = list(await self._capacity.list_stale(conn, older_than_seconds))

        fixes = 0
        for cap in stale:
            expected = cap.max_capacity - cap.enrolled_count
            redis_remaining = await self._stock.get_remaining(cap.offering_id)
            if redis_remaining != expected:
                await self._stock.reset(cap.offering_id, expected)
                async with db.transaction() as conn:
                    await self._audit.write(
                        conn,
                        AuditEntry(
                            actor_id="system",
                            actor_role="system",
                            action="reconcile.fix",
                            target_type="offering",
                            target_id=cap.offering_id,
                            before={"redis": redis_remaining},
                            after={"db": expected},
                        ),
                    )
                    await self._capacity.mark_reconciled(conn, cap.offering_id)
                fixes += 1
            else:
                async with db.transaction() as conn:
                    await self._capacity.mark_reconciled(conn, cap.offering_id)

        if fixes > _ALERT_THRESHOLD:
            logger.warning("对账修正条数 %d 超阈值，触发告警", fixes, extra={"event": "reconcile.alert"})
        return fixes
