"""容量数据访问，实现 ports.CapacityRepository。

冷路径权威库存，乐观锁（version 列）保护并发更新。
对应《04 高并发引擎设计》「库存原子扣减」。
"""

from __future__ import annotations

from collections.abc import Sequence

from psycopg import AsyncConnection
from psycopg.rows import class_row

from app.domain.enrollment import Capacity

_COLS = "offering_id, semester, max_capacity, enrolled_count, waitlist_count, version"

SQL_GET = f"SELECT {_COLS} FROM course_selection.course_capacity WHERE offering_id = %s"

# 乐观锁 +1：版本匹配且不越界才更新
SQL_INCR = f"""
UPDATE course_selection.course_capacity
   SET enrolled_count = enrolled_count + 1,
       version = version + 1
 WHERE offering_id = %s AND version = %s
   AND enrolled_count + 1 <= max_capacity
RETURNING {_COLS}
"""

SQL_DECR = f"""
UPDATE course_selection.course_capacity
   SET enrolled_count = GREATEST(enrolled_count - 1, 0),
       version = version + 1
 WHERE offering_id = %s
RETURNING {_COLS}
"""

SQL_ADJUST_MAX = f"""
UPDATE course_selection.course_capacity
   SET max_capacity = max_capacity + %s,
       version = version + 1
 WHERE offering_id = %s AND max_capacity + %s >= enrolled_count
RETURNING {_COLS}
"""

SQL_LIST_STALE = f"""
SELECT {_COLS} FROM course_selection.course_capacity
 WHERE last_reconciled_at < NOW() - (%s || ' seconds')::interval
"""

SQL_MARK_RECONCILED = """
UPDATE course_selection.course_capacity SET last_reconciled_at = NOW() WHERE offering_id = %s
"""


class PgCapacityRepository:
    async def get(self, conn: AsyncConnection, offering_id: str) -> Capacity | None:
        cur = conn.cursor(row_factory=class_row(Capacity))
        await cur.execute(SQL_GET, (offering_id,))
        return await cur.fetchone()

    async def increment_enrolled(self, conn: AsyncConnection, offering_id: str, version: int) -> Capacity | None:
        cur = conn.cursor(row_factory=class_row(Capacity))
        await cur.execute(SQL_INCR, (offering_id, version))
        return await cur.fetchone()

    async def decrement_enrolled(self, conn: AsyncConnection, offering_id: str) -> Capacity | None:
        cur = conn.cursor(row_factory=class_row(Capacity))
        await cur.execute(SQL_DECR, (offering_id,))
        return await cur.fetchone()

    async def adjust_max(self, conn: AsyncConnection, offering_id: str, delta: int) -> Capacity | None:
        cur = conn.cursor(row_factory=class_row(Capacity))
        await cur.execute(SQL_ADJUST_MAX, (delta, offering_id, delta))
        return await cur.fetchone()

    async def list_stale(self, conn: AsyncConnection, older_than_seconds: int) -> Sequence[Capacity]:
        cur = conn.cursor(row_factory=class_row(Capacity))
        await cur.execute(SQL_LIST_STALE, (older_than_seconds,))
        return await cur.fetchall()

    async def mark_reconciled(self, conn: AsyncConnection, offering_id: str) -> None:
        await conn.execute(SQL_MARK_RECONCILED, (offering_id,))
