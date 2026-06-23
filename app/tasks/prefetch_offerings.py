"""开课缓存预热与增量刷新：组合 A 组开课 + B 组排课写入本地缓存。

数据流（均为已核对的真实上游契约）：
- A 组 `GET /api/v1/info/offerings`：开课实体（课/学期/班/容量）——主干；
- B 组 `GET /api/v1/schedule/entries`：时段 + 教室——按 (course_id, term) 关联补充；
- A 组 `GET /api/v1/info/data-provision/users/{id}`：教师姓名（B 组只给 teacher_id）。
容量从 A 组开课的 capacity 播种到本地权威库存（已存在则不覆盖）。
"""

from __future__ import annotations

import logging

from app.core import db, errors
from app.domain.offering import Offering
from app.integrations.info_client import HttpInfoServiceClient
from app.integrations.schedule_client import HttpScheduleServiceClient
from app.repositories.capacity_repo import PgCapacityRepository
from app.repositories.offering_cache_repo import PgOfferingCacheRepository

logger = logging.getLogger(__name__)


async def refresh_offerings(semester: str) -> int:
    """A 组开课为主干，B 组排课补时段/教室，A 组补教师名，并播种容量。返回开课条数。"""
    info = HttpInfoServiceClient()
    sched = HttpScheduleServiceClient()
    cache = PgOfferingCacheRepository()
    capacity = PgCapacityRepository()

    catalog = await info.list_offerings(semester)  # A 组开课主干
    if not catalog:
        return 0
    schedules = await sched.list_offerings(semester)
    sched_by_offering = {o.offering_id: o for o in schedules}
    sched_by_course = {o.course_code: o for o in schedules}

    teacher_names: dict[str, str] = {}
    composed: list[Offering] = []
    for entry in catalog:
        sch = sched_by_offering.get(entry.offering_id) or sched_by_course.get(str(entry.course_id))
        teacher_id = sch.teacher_id if sch else ""
        if teacher_id and teacher_id not in teacher_names:
            try:
                teacher_names[teacher_id] = (await info.get_student(teacher_id)).name
            except errors.UpstreamDown:
                teacher_names[teacher_id] = ""
        composed.append(
            Offering(
                offering_id=entry.offering_id,
                course_code=entry.course_code,
                course_name=entry.course_name,
                teacher_id=teacher_id,
                teacher_name=teacher_names.get(teacher_id, ""),
                semester=entry.term_code,
                time_slots=sch.time_slots if sch else (),
                classroom=sch.classroom if sch else None,
                campus=sch.campus if sch else None,
            )
        )

    async with db.transaction() as conn:
        total = await cache.upsert_many(conn, composed)
        for entry in catalog:
            await capacity.seed_capacity(conn, entry.offering_id, entry.term_code, entry.capacity)
    logger.info("开课缓存刷新 %d 条 semester=%s", total, semester, extra={"event": "offerings.refresh"})
    return total
