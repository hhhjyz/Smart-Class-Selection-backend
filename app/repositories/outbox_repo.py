"""Outbox 数据访问，实现 ports.OutboxRepository。

业务事务内 emit 一条 pending 事件；投递器扫描 pending 推送 MQ 后置 published。
"""

from __future__ import annotations

import json
from collections.abc import Sequence

from psycopg import AsyncConnection

from app.domain.audit import OutboxEvent

SQL_EMIT = """
INSERT INTO course_selection.outbox_events
    (event_id, aggregate_type, aggregate_id, event_type, payload, status, retry_count, created_at)
VALUES (gen_random_uuid(), %s, %s, %s, %s, 'pending', 0, NOW())
"""

# 取一批待投递并用行锁防止多投递器重复处理
SQL_FETCH_PENDING = """
SELECT event_id, event_type, payload
  FROM course_selection.outbox_events
 WHERE status = 'pending'
 ORDER BY created_at
 FOR UPDATE SKIP LOCKED
 LIMIT %s
"""

SQL_MARK_PUBLISHED = """
UPDATE course_selection.outbox_events
   SET status = 'published', published_at = NOW()
 WHERE event_id = %s
"""

SQL_MARK_DEAD = """
UPDATE course_selection.outbox_events
   SET status = 'dead', retry_count = retry_count + 1
 WHERE event_id = %s
"""


class PgOutboxRepository:
    async def emit(self, conn: AsyncConnection, event: OutboxEvent) -> None:
        await conn.execute(
            SQL_EMIT,
            (event.aggregate_type, event.aggregate_id, event.event_type, json.dumps(event.payload)),
        )

    async def fetch_pending(self, conn: AsyncConnection, limit: int) -> Sequence[tuple[str, str, bytes]]:
        cur = await conn.execute(SQL_FETCH_PENDING, (limit,))
        out: list[tuple[str, str, bytes]] = []
        for event_id, event_type, payload in await cur.fetchall():
            body = json.dumps(payload).encode() if not isinstance(payload, (str, bytes)) else (
                payload.encode() if isinstance(payload, str) else payload
            )
            out.append((str(event_id), event_type, body))
        return out

    async def mark_published(self, conn: AsyncConnection, event_id: str) -> None:
        await conn.execute(SQL_MARK_PUBLISHED, (event_id,))

    async def mark_dead(self, conn: AsyncConnection, event_id: str) -> None:
        await conn.execute(SQL_MARK_DEAD, (event_id,))
