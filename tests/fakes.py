"""端口（domain.ports）的内存假实现，用于无 I/O 的 service 单测。

因为业务层依赖倒置（只依赖抽象接口），这里把每个 port 用纯内存实现替换，
即可在不起 DB/Redis/HTTP 的情况下完整跑通 service 逻辑与各类 corner case。

repo 方法签名接受 conn，但内存实现忽略它（service 单测里 conn 是哑对象）。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from app.domain.audit import AuditEntry, OutboxEvent
from app.domain.enrollment import Capacity, Enrollment
from app.domain.enums import EnrollmentStatus
from app.domain.offering import GradeRecord, Offering, StudentProfile
from app.domain.study_plan import CurriculumRule, StudyPlan


# --------------------------------------------------------------------------- #
# 哑事务上下文：让 service 里的 db.transaction()/db.connection() 不碰真实 DB     #
# --------------------------------------------------------------------------- #
@asynccontextmanager
async def fake_cm() -> AsyncIterator[None]:
    yield None


def patch_db(monkeypatch: Any) -> None:
    """把 app.core.db 的事务/连接上下文替换为哑实现。"""
    from app.core import db

    monkeypatch.setattr(db, "transaction", fake_cm)
    monkeypatch.setattr(db, "connection", fake_cm)


# --------------------------------------------------------------------------- #
# Repository fakes                                                            #
# --------------------------------------------------------------------------- #
class FakeEnrollmentRepository:
    def __init__(self) -> None:
        self.rows: dict[str, Enrollment] = {}

    async def insert(self, conn: Any, e: Enrollment) -> str | None:
        # 唯一约束：(student, offering, semester) 与 idempotency_key
        for r in self.rows.values():
            if (r.student_id, r.offering_id, r.semester) == (e.student_id, e.offering_id, e.semester) \
                    and r.status != EnrollmentStatus.CANCELED:
                return None
        self.rows[e.enrollment_id] = e
        return e.enrollment_id

    async def soft_cancel(self, conn: Any, enrollment_id: str, reason: str) -> Enrollment | None:
        e = self.rows.get(enrollment_id)
        if e is None or e.status != EnrollmentStatus.ENROLLED:
            return None
        canceled = e.model_copy(update={"status": EnrollmentStatus.CANCELED, "canceled_at": datetime.now(UTC)})
        self.rows[enrollment_id] = canceled
        return canceled

    async def get(self, conn: Any, enrollment_id: str) -> Enrollment | None:
        return self.rows.get(enrollment_id)

    async def list_by_student(
        self, conn: Any, student_id: str, semester: str, status: str | None
    ) -> Sequence[Enrollment]:
        return [
            r for r in self.rows.values()
            if r.student_id == student_id and r.semester == semester
            and (status is None or r.status.value == status)
        ]

    async def find_by_idempotency_key(self, conn: Any, key: str) -> Enrollment | None:
        return next((r for r in self.rows.values() if r.idempotency_key == key), None)

    async def list_roster(self, conn: Any, offering_id: str, include_dropped: bool) -> Sequence[tuple[str, Any]]:
        return [
            (r.student_id, r.enrolled_at)
            for r in self.rows.values()
            if r.offering_id == offering_id and (include_dropped or r.status == EnrollmentStatus.ENROLLED)
        ]


class FakeCapacityRepository:
    def __init__(self, caps: dict[str, Capacity] | None = None) -> None:
        self.caps = caps or {}

    async def get(self, conn: Any, offering_id: str) -> Capacity | None:
        return self.caps.get(offering_id)

    async def increment_enrolled(self, conn: Any, offering_id: str, version: int) -> Capacity | None:
        c = self.caps.get(offering_id)
        if c is None or c.version != version or c.enrolled_count + 1 > c.max_capacity:
            return None
        c = c.model_copy(update={"enrolled_count": c.enrolled_count + 1, "version": c.version + 1})
        self.caps[offering_id] = c
        return c

    async def decrement_enrolled(self, conn: Any, offering_id: str) -> Capacity | None:
        c = self.caps.get(offering_id)
        if c is None:
            return None
        c = c.model_copy(update={"enrolled_count": max(c.enrolled_count - 1, 0), "version": c.version + 1})
        self.caps[offering_id] = c
        return c

    async def adjust_max(self, conn: Any, offering_id: str, delta: int) -> Capacity | None:
        c = self.caps.get(offering_id)
        if c is None or c.max_capacity + delta < c.enrolled_count:
            return None
        c = c.model_copy(update={"max_capacity": c.max_capacity + delta, "version": c.version + 1})
        self.caps[offering_id] = c
        return c

    async def list_stale(self, conn: Any, older_than_seconds: int) -> Sequence[Capacity]:
        return list(self.caps.values())

    async def mark_reconciled(self, conn: Any, offering_id: str) -> None:
        return None


class FakeStudyPlanRepository:
    def __init__(self, plans: dict[str, StudyPlan] | None = None, rules: Sequence[CurriculumRule] = ()) -> None:
        self.plans = plans or {}
        self.rules = list(rules)

    async def get_by_student(self, conn: Any, student_id: str) -> StudyPlan | None:
        return self.plans.get(student_id)

    async def upsert(self, conn: Any, plan: StudyPlan) -> StudyPlan:
        self.plans[plan.student_id] = plan
        return plan

    async def delete_item(self, conn: Any, student_id: str, plan_item_id: str) -> bool:
        return student_id in self.plans

    async def get_curriculum_rules(
        self, conn: Any, major_code: str, curriculum_version: str
    ) -> Sequence[CurriculumRule]:
        return self.rules


class FakeOfferingCacheRepository:
    def __init__(self, offerings: dict[str, Offering] | None = None, timetable: Sequence[Offering] = ()) -> None:
        self.offerings = offerings or {}
        self.timetable = list(timetable)

    async def get(self, conn: Any, offering_id: str) -> Offering | None:
        return self.offerings.get(offering_id)

    async def search(self, conn: Any, *, keyword, teacher_name, semester, category, limit, offset):  # type: ignore[no-untyped-def]
        items = list(self.offerings.values())
        return items[offset:offset + limit], len(items)

    async def list_for_student_timetable(self, conn: Any, student_id: str, semester: str) -> Sequence[Offering]:
        return self.timetable

    async def upsert_many(self, conn: Any, offerings: Sequence[Offering]) -> int:
        for o in offerings:
            self.offerings[o.offering_id] = o
        return len(offerings)


class FakeAuditRepository:
    def __init__(self) -> None:
        self.entries: list[AuditEntry] = []

    async def write(self, conn: Any, entry: AuditEntry) -> None:
        self.entries.append(entry)


class FakeOutboxRepository:
    def __init__(self) -> None:
        self.events: list[OutboxEvent] = []

    async def emit(self, conn: Any, event: OutboxEvent) -> None:
        self.events.append(event)

    async def fetch_pending(self, conn: Any, limit: int) -> Sequence[tuple[str, str, bytes]]:
        return [(str(uuid.uuid4()), e.event_type, b"{}") for e in self.events[:limit]]

    async def mark_published(self, conn: Any, event_id: str) -> None:
        return None

    async def mark_dead(self, conn: Any, event_id: str) -> None:
        return None


# --------------------------------------------------------------------------- #
# Engine fakes                                                                #
# --------------------------------------------------------------------------- #
class FakeStockStore:
    def __init__(self, stock: dict[str, int] | None = None) -> None:
        self.stock = stock or {}
        self.releases = 0

    async def try_consume(self, offering_id: str) -> bool:
        n = self.stock.get(offering_id, 0)
        if n <= 0:
            return False
        self.stock[offering_id] = n - 1
        return True

    async def release(self, offering_id: str) -> None:
        self.releases += 1
        self.stock[offering_id] = self.stock.get(offering_id, 0) + 1

    async def reset(self, offering_id: str, remaining: int) -> None:
        self.stock[offering_id] = remaining

    async def get_remaining(self, offering_id: str) -> int | None:
        return self.stock.get(offering_id)


class FakeWaitingRoom:
    def __init__(self, admitted: bool = True) -> None:
        self.admitted = admitted
        self.enqueued: list[tuple[str, str]] = []
        self.consumed: list[tuple[str, str]] = []
        self.removed: list[tuple[str, str]] = []

    async def enqueue(self, offering_id: str, user_id: str) -> int:
        self.enqueued.append((offering_id, user_id))
        return 7

    async def is_admitted(self, offering_id: str, user_id: str) -> bool:
        return self.admitted

    async def consume_admission(self, offering_id: str, user_id: str) -> None:
        self.consumed.append((offering_id, user_id))

    async def estimate_position(self, offering_id: str, user_id: str) -> int | None:
        return 3

    async def remove_admission(self, offering_id: str, user_id: str) -> None:
        self.removed.append((offering_id, user_id))


# --------------------------------------------------------------------------- #
# Integration client fakes                                                    #
# --------------------------------------------------------------------------- #
class FakeInfoServiceClient:
    def __init__(self, grades: Sequence[GradeRecord] = (), profile: StudentProfile | None = None) -> None:
        self._grades = list(grades)
        self._profile = profile or StudentProfile(
            student_id="S-1", name="测试同学", major_code="CS", curriculum_version="2023"
        )

    async def get_student(self, student_id: str) -> StudentProfile:
        return self._profile

    async def get_curriculum_rules(self, plan_id: str) -> Sequence[CurriculumRule]:
        return []

    async def get_grades(self, student_id: str) -> Sequence[GradeRecord]:
        return self._grades


class FakeLLMClient:
    def __init__(self, chunks: Sequence[dict[str, object]] = ()) -> None:
        self._chunks = list(chunks)

    async def stream_chat(self, messages, tools):  # type: ignore[no-untyped-def]
        for c in self._chunks:
            yield c

    async def embed(self, texts):  # type: ignore[no-untyped-def]
        return [[0.0] * 4 for _ in texts]


# --------------------------------------------------------------------------- #
# 便捷构造器                                                                   #
# --------------------------------------------------------------------------- #
def make_offering(offering_id: str = "B-CS101-2026-1-01", course_code: str = "CS101", **kw: Any) -> Offering:
    base = {
        "offering_id": offering_id, "course_code": course_code, "course_name": "软件工程",
        "teacher_id": "T-9001", "teacher_name": "张老师", "semester": "2026-1",
        "time_slots": (), "classroom": "201", "campus": "紫金港",
    }
    base.update(kw)
    return Offering(**base)  # type: ignore[arg-type]


def make_capacity(offering_id: str = "B-CS101-2026-1-01", max_capacity: int = 50, enrolled: int = 0) -> Capacity:
    return Capacity(
        offering_id=offering_id, semester="2026-1",
        max_capacity=max_capacity, enrolled_count=enrolled, waitlist_count=0, version=0,
    )
