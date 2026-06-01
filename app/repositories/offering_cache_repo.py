"""开课缓存数据访问，实现 ports.OfferingCacheRepository。

cached_offerings 为 B 组课表的本地缓存，规则引擎做时间冲突只读本表。
"""

from __future__ import annotations

import json
from collections.abc import Sequence

from psycopg import AsyncConnection

from app.domain.offering import Offering, TimeSlot

# 字面量常量（保持 LiteralString，满足 psycopg execute 的类型约束、杜绝动态 SQL）
_COLS = "offering_id, course_code, course_name, teacher_id, teacher_name, semester, time_slots, classroom, campus"
# JOIN 查询需限定表别名，避免 offering_id 等同名列歧义
_COLS_O = "o.offering_id, o.course_code, o.course_name, o.teacher_id, o.teacher_name, o.semester, o.time_slots, o.classroom, o.campus"  # noqa: E501

SQL_GET = f"SELECT {_COLS} FROM course_selection.cached_offerings WHERE offering_id = %s"

SQL_SEARCH = f"""
SELECT {_COLS} FROM course_selection.cached_offerings
 WHERE (%s::text IS NULL OR course_name ILIKE '%%' || %s || '%%' OR course_code ILIKE '%%' || %s || '%%')
   AND (%s::text IS NULL OR teacher_name ILIKE '%%' || %s || '%%')
   AND (%s::text IS NULL OR semester = %s)
 ORDER BY course_code
 LIMIT %s OFFSET %s
"""

SQL_SEARCH_COUNT = """
SELECT COUNT(*) FROM course_selection.cached_offerings
 WHERE (%s::text IS NULL OR course_name ILIKE '%%' || %s || '%%' OR course_code ILIKE '%%' || %s || '%%')
   AND (%s::text IS NULL OR teacher_name ILIKE '%%' || %s || '%%')
   AND (%s::text IS NULL OR semester = %s)
"""

SQL_LIST_TIMETABLE = f"""
SELECT {_COLS_O} FROM course_selection.cached_offerings o
 JOIN course_selection.enrollments e ON e.offering_id = o.offering_id
 WHERE e.student_id = %s AND e.semester = %s AND e.status = 'enrolled'
"""

SQL_UPSERT = """
INSERT INTO course_selection.cached_offerings
    (offering_id, course_code, course_name, teacher_id, teacher_name,
     semester, time_slots, classroom, campus, fetched_at)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
ON CONFLICT (offering_id) DO UPDATE SET
    course_name = EXCLUDED.course_name,
    teacher_name = EXCLUDED.teacher_name,
    time_slots = EXCLUDED.time_slots,
    classroom = EXCLUDED.classroom,
    campus = EXCLUDED.campus,
    fetched_at = NOW()
"""


def _row_to_offering(r: tuple) -> Offering:
    slots_raw = r[6] if isinstance(r[6], list) else json.loads(r[6] or "[]")
    slots = tuple(
        TimeSlot(day=s["day"], period=tuple(s["period"]), weeks=s["weeks"]) for s in slots_raw
    )
    return Offering(
        offering_id=r[0], course_code=r[1], course_name=r[2], teacher_id=r[3],
        teacher_name=r[4], semester=r[5], time_slots=slots, classroom=r[7], campus=r[8],
    )


class PgOfferingCacheRepository:
    async def get(self, conn: AsyncConnection, offering_id: str) -> Offering | None:
        cur = await conn.execute(SQL_GET, (offering_id,))
        row = await cur.fetchone()
        return _row_to_offering(row) if row else None

    async def search(
        self, conn: AsyncConnection, *, keyword: str | None, teacher_name: str | None,
        semester: str | None, category: str | None, limit: int, offset: int,
    ) -> tuple[Sequence[Offering], int]:
        params = (keyword, keyword, keyword, teacher_name, teacher_name, semester, semester)
        cur = await conn.execute(SQL_SEARCH, (*params, limit, offset))
        offerings = [_row_to_offering(r) for r in await cur.fetchall()]
        count_cur = await conn.execute(SQL_SEARCH_COUNT, params)
        total_row = await count_cur.fetchone()
        return offerings, (total_row[0] if total_row else 0)

    async def list_for_student_timetable(
        self, conn: AsyncConnection, student_id: str, semester: str
    ) -> Sequence[Offering]:
        cur = await conn.execute(SQL_LIST_TIMETABLE, (student_id, semester))
        return [_row_to_offering(r) for r in await cur.fetchall()]

    async def upsert_many(self, conn: AsyncConnection, offerings: Sequence[Offering]) -> int:
        n = 0
        for o in offerings:
            slots = json.dumps([{"day": s.day, "period": list(s.period), "weeks": s.weeks} for s in o.time_slots])
            await conn.execute(
                SQL_UPSERT,
                (o.offering_id, o.course_code, o.course_name, o.teacher_id, o.teacher_name,
                 o.semester, slots, o.classroom, o.campus),
            )
            n += 1
        return n
