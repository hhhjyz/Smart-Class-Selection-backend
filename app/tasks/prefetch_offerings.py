"""开课缓存预热与增量刷新：从 B 组拉课表写入 cached_offerings。"""

from __future__ import annotations

import logging

from app.core import db
from app.integrations.schedule_client import HttpScheduleServiceClient
from app.repositories.offering_cache_repo import PgOfferingCacheRepository

logger = logging.getLogger(__name__)


async def refresh_offerings(semester: str, page_size: int = 200) -> int:
    """分页拉取 B 组开课列表并 upsert 本地缓存，返回刷新条数。"""
    client = HttpScheduleServiceClient()
    repo = PgOfferingCacheRepository()
    total = 0
    page = 1
    while True:
        offerings = await client.list_offerings(semester, page=page, page_size=page_size)
        if not offerings:
            break
        async with db.transaction() as conn:
            total += await repo.upsert_many(conn, offerings)
        if len(offerings) < page_size:
            break
        page += 1
    logger.info("开课缓存刷新 %d 条 semester=%s", total, semester, extra={"event": "offerings.refresh"})
    return total
