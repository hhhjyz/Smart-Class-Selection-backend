"""审计与 Outbox 域实体。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class AuditEntry(BaseModel):
    """不可变审计记录，对应 audit_logs 表（仅 INSERT）。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    actor_id: str
    actor_role: str
    action: str
    target_type: str
    target_id: str
    before: dict[str, object] | None = None
    after: dict[str, object] | None = None
    request_id: str | None = None


class OutboxEvent(BaseModel):
    """Outbox 待投递事件，对应 outbox_events 表。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    aggregate_type: str
    aggregate_id: str
    event_type: str
    payload: dict[str, object]
