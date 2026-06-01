"""Outbox 投递器：扫描 pending 事件推送 RabbitMQ。

每条事件在自己的短事务内取出（FOR UPDATE SKIP LOCKED）、投递、标记 published。
投递失败标记 dead 由后续重试。对应《08 构件》Outbox Publisher。
"""

from __future__ import annotations

import logging

from app.core import db, mq
from app.repositories.outbox_repo import PgOutboxRepository

logger = logging.getLogger(__name__)

_repo = PgOutboxRepository()


async def publish_pending(batch: int = 100) -> int:
    """投递一批待发事件，返回成功投递数。"""
    async with db.connection() as conn:
        pending = await _repo.fetch_pending(conn, batch)

    published = 0
    for event_id, routing_key, body in pending:
        try:
            await mq.publish(routing_key, body)
            async with db.transaction() as conn:
                await _repo.mark_published(conn, event_id)
            published += 1
        except Exception:  # noqa: BLE001 - 投递失败不应中断整批
            logger.exception("Outbox 投递失败 event_id=%s", event_id, extra={"event": "outbox.publish.fail"})
            async with db.transaction() as conn:
                await _repo.mark_dead(conn, event_id)
    return published
