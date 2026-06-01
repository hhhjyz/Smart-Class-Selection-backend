"""对账任务包装：周期调用 Reconciler.run_once。"""

from __future__ import annotations

from app.core.redis import get_redis
from app.engine.capacity_lock import RedisStockStore
from app.repositories.audit_repo import PgAuditRepository
from app.repositories.capacity_repo import PgCapacityRepository
from app.services.reconciler import Reconciler


async def reconcile_once() -> int:
    reconciler = Reconciler(
        capacity_repo=PgCapacityRepository(),
        audit_repo=PgAuditRepository(),
        stock=RedisStockStore(get_redis()),
    )
    return await reconciler.run_once()
