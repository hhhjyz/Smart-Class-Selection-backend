"""审计数据访问，实现 ports.AuditRepository。

audit_logs 仅允许 INSERT，UPDATE/DELETE 由 PG trigger 拦截（见 migrations）。
"""

from __future__ import annotations

import json

from psycopg import AsyncConnection

from app.core.logging import current_request_id
from app.domain.audit import AuditEntry

SQL_INSERT = """
INSERT INTO course_selection.audit_logs
    (actor_id, actor_role, action, target_type, target_id, before, after, request_id, occurred_at)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
"""


class PgAuditRepository:
    async def write(self, conn: AsyncConnection, entry: AuditEntry) -> None:
        await conn.execute(
            SQL_INSERT,
            (
                entry.actor_id,
                entry.actor_role,
                entry.action,
                entry.target_type,
                entry.target_id,
                json.dumps(entry.before) if entry.before is not None else None,
                json.dumps(entry.after) if entry.after is not None else None,
                entry.request_id or current_request_id(),
            ),
        )
