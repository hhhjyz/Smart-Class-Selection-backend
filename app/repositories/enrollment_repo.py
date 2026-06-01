"""选课记录数据访问，实现 ports.EnrollmentRepository。"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from psycopg import AsyncConnection
from psycopg.rows import class_row, tuple_row

from app.domain.enrollment import Enrollment

SQL_INSERT = """
INSERT INTO course_selection.enrollments
    (enrollment_id, student_id, offering_id, semester, status, stage,
     enrolled_at, source, idempotency_key)
VALUES (%s, %s, %s, %s, %s, %s, NOW(), %s, %s)
ON CONFLICT (student_id, offering_id, semester) DO NOTHING
RETURNING enrollment_id
"""

SQL_SOFT_CANCEL = """
UPDATE course_selection.enrollments
   SET status = 'canceled', canceled_at = NOW()
 WHERE enrollment_id = %s AND status = 'enrolled'
RETURNING enrollment_id, student_id, offering_id, semester, status, stage,
          source, idempotency_key, enrolled_at, canceled_at
"""

SQL_GET = """
SELECT enrollment_id, student_id, offering_id, semester, status, stage,
       source, idempotency_key, enrolled_at, canceled_at
  FROM course_selection.enrollments
 WHERE enrollment_id = %s
"""

SQL_LIST_BY_STUDENT = """
SELECT enrollment_id, student_id, offering_id, semester, status, stage,
       source, idempotency_key, enrolled_at, canceled_at
  FROM course_selection.enrollments
 WHERE student_id = %s AND semester = %s
   AND (%s::text IS NULL OR status = %s)
 ORDER BY enrolled_at DESC NULLS LAST
"""

SQL_FIND_BY_IDEMPOTENCY = """
SELECT enrollment_id, student_id, offering_id, semester, status, stage,
       source, idempotency_key, enrolled_at, canceled_at
  FROM course_selection.enrollments
 WHERE idempotency_key = %s
"""

SQL_LIST_ROSTER = """
SELECT student_id, enrolled_at
  FROM course_selection.enrollments
 WHERE offering_id = %s
   AND (%s OR status = 'enrolled')
 ORDER BY enrolled_at
"""


class PgEnrollmentRepository:
    """psycopg3 实现。"""

    async def insert(self, conn: AsyncConnection, e: Enrollment) -> str | None:
        cur = await conn.execute(
            SQL_INSERT,
            (e.enrollment_id, e.student_id, e.offering_id, e.semester,
             e.status.value, e.stage.value, e.source.value, e.idempotency_key),
        )
        row = await cur.fetchone()
        return row[0] if row else None

    async def soft_cancel(self, conn: AsyncConnection, enrollment_id: str, reason: str) -> Enrollment | None:
        cur = conn.cursor(row_factory=class_row(Enrollment))
        await cur.execute(SQL_SOFT_CANCEL, (enrollment_id,))
        return await cur.fetchone()

    async def get(self, conn: AsyncConnection, enrollment_id: str) -> Enrollment | None:
        cur = conn.cursor(row_factory=class_row(Enrollment))
        await cur.execute(SQL_GET, (enrollment_id,))
        return await cur.fetchone()

    async def list_by_student(
        self, conn: AsyncConnection, student_id: str, semester: str, status: str | None
    ) -> Sequence[Enrollment]:
        cur = conn.cursor(row_factory=class_row(Enrollment))
        await cur.execute(SQL_LIST_BY_STUDENT, (student_id, semester, status, status))
        return await cur.fetchall()

    async def find_by_idempotency_key(self, conn: AsyncConnection, key: str) -> Enrollment | None:
        cur = conn.cursor(row_factory=class_row(Enrollment))
        await cur.execute(SQL_FIND_BY_IDEMPOTENCY, (key,))
        return await cur.fetchone()

    async def list_roster(
        self, conn: AsyncConnection, offering_id: str, include_dropped: bool
    ) -> Sequence[tuple[str, datetime | None]]:
        cur = conn.cursor(row_factory=tuple_row)
        await cur.execute(SQL_LIST_ROSTER, (offering_id, include_dropped))
        return await cur.fetchall()
