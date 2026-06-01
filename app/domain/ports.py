"""抽象端口（DIP 核心）。

高层模块（services / engine）只依赖本文件定义的接口，不依赖
repositories / integrations 的具体实现。具体实现由 api/deps.py 注入。

约定：
- Repository 方法都接受一个已开启的连接 ``conn``（由 service 在事务边界内传入），
  自身不管理事务、不暴露 cursor。
- Store / Client 端口封装 Redis / HTTP / LLM 等外部依赖。

import-linter 强制 services 层只能 import 本模块，不得 import repositories / integrations。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from datetime import datetime
from typing import Protocol, runtime_checkable

from psycopg import AsyncConnection

from app.domain.audit import AuditEntry, OutboxEvent
from app.domain.enrollment import Capacity, Enrollment
from app.domain.offering import GradeRecord, Offering, StudentProfile
from app.domain.study_plan import CurriculumRule, StudyPlan


# --------------------------------------------------------------------------- #
# Repository ports（数据访问抽象）                                            #
# --------------------------------------------------------------------------- #
@runtime_checkable
class EnrollmentRepository(Protocol):
    async def insert(self, conn: AsyncConnection, e: Enrollment) -> str | None:
        """插入选课记录；命中唯一约束（重复）返回 None。"""
        ...

    async def soft_cancel(self, conn: AsyncConnection, enrollment_id: str, reason: str) -> Enrollment | None:
        """软删退课，返回被退课记录；不存在或已退返回 None。"""
        ...

    async def get(self, conn: AsyncConnection, enrollment_id: str) -> Enrollment | None: ...

    async def list_by_student(
        self, conn: AsyncConnection, student_id: str, semester: str, status: str | None
    ) -> Sequence[Enrollment]: ...

    async def list_roster(
        self, conn: AsyncConnection, offering_id: str, include_dropped: bool
    ) -> Sequence[tuple[str, datetime | None]]:
        """花名册：返回 [(student_id, enrolled_at)]。"""
        ...

    async def find_by_idempotency_key(self, conn: AsyncConnection, key: str) -> Enrollment | None: ...


@runtime_checkable
class CapacityRepository(Protocol):
    async def get(self, conn: AsyncConnection, offering_id: str) -> Capacity | None: ...

    async def increment_enrolled(self, conn: AsyncConnection, offering_id: str, version: int) -> Capacity | None:
        """乐观锁 +1：版本不匹配或越界返回 None（调用方据此回滚/重试）。"""
        ...

    async def decrement_enrolled(self, conn: AsyncConnection, offering_id: str) -> Capacity | None:
        """退课 -1。"""
        ...

    async def adjust_max(self, conn: AsyncConnection, offering_id: str, delta: int) -> Capacity | None: ...

    async def list_stale(self, conn: AsyncConnection, older_than_seconds: int) -> Sequence[Capacity]:
        """对账用：返回上次对账早于阈值的容量行。"""
        ...

    async def mark_reconciled(self, conn: AsyncConnection, offering_id: str) -> None: ...


@runtime_checkable
class StudyPlanRepository(Protocol):
    async def get_by_student(self, conn: AsyncConnection, student_id: str) -> StudyPlan | None: ...

    async def upsert(self, conn: AsyncConnection, plan: StudyPlan) -> StudyPlan: ...

    async def delete_item(self, conn: AsyncConnection, student_id: str, plan_item_id: str) -> bool: ...

    async def get_curriculum_rules(
        self, conn: AsyncConnection, major_code: str, curriculum_version: str
    ) -> Sequence[CurriculumRule]: ...


@runtime_checkable
class OfferingCacheRepository(Protocol):
    async def get(self, conn: AsyncConnection, offering_id: str) -> Offering | None: ...

    async def search(
        self, conn: AsyncConnection, *, keyword: str | None, teacher_name: str | None,
        semester: str | None, category: str | None, limit: int, offset: int,
    ) -> tuple[Sequence[Offering], int]: ...

    async def list_for_student_timetable(
        self, conn: AsyncConnection, student_id: str, semester: str
    ) -> Sequence[Offering]: ...

    async def upsert_many(self, conn: AsyncConnection, offerings: Sequence[Offering]) -> int: ...


@runtime_checkable
class AuditRepository(Protocol):
    async def write(self, conn: AsyncConnection, entry: AuditEntry) -> None: ...


@runtime_checkable
class OutboxRepository(Protocol):
    async def emit(self, conn: AsyncConnection, event: OutboxEvent) -> None:
        """在业务事务内写入一条 outbox 事件（与业务变更同事务提交）。"""
        ...

    async def fetch_pending(self, conn: AsyncConnection, limit: int) -> Sequence[tuple[str, str, bytes]]:
        """投递器用：取 (event_id, routing_key, body) 待投递批次。"""
        ...

    async def mark_published(self, conn: AsyncConnection, event_id: str) -> None: ...

    async def mark_dead(self, conn: AsyncConnection, event_id: str) -> None: ...


# --------------------------------------------------------------------------- #
# Engine ports（高并发原语抽象）                                              #
# --------------------------------------------------------------------------- #
@runtime_checkable
class StockStore(Protocol):
    """Redis 热路径库存。DECR/INCR 原子扣减与补偿。"""

    async def try_consume(self, offering_id: str) -> bool:
        """尝试扣 1，余量不足返回 False（并已自补偿）。"""
        ...

    async def release(self, offering_id: str) -> None:
        """补偿 +1（退课或事务回滚时调用）。"""
        ...

    async def reset(self, offering_id: str, remaining: int) -> None:
        """对账：将库存重置为权威余量。"""
        ...

    async def get_remaining(self, offering_id: str) -> int | None: ...


@runtime_checkable
class WaitingRoom(Protocol):
    """Virtual Waiting Room：入队、放行校验、位置估算。"""

    async def enqueue(self, offering_id: str, user_id: str) -> int:
        """入队，返回粗估位置。"""
        ...

    async def is_admitted(self, offering_id: str, user_id: str) -> bool: ...

    async def consume_admission(self, offering_id: str, user_id: str) -> None:
        """消费一次性放行令牌。"""
        ...

    async def estimate_position(self, offering_id: str, user_id: str) -> int | None: ...

    async def remove_admission(self, offering_id: str, user_id: str) -> None:
        """超时/断线回收名额。"""
        ...


# --------------------------------------------------------------------------- #
# Integration client ports（外部依赖抽象）                                    #
# --------------------------------------------------------------------------- #
@runtime_checkable
class InfoServiceClient(Protocol):
    """A 组基础信息服务。"""

    async def get_student(self, student_id: str) -> StudentProfile: ...

    async def get_curriculum_rules(self, plan_id: str) -> Sequence[CurriculumRule]: ...

    async def get_grades(self, student_id: str) -> Sequence[GradeRecord]: ...


@runtime_checkable
class ScheduleServiceClient(Protocol):
    """B 组排课服务。"""

    async def list_offerings(self, semester: str, page: int, page_size: int) -> Sequence[Offering]: ...

    async def get_offering(self, offering_id: str) -> Offering | None: ...


@runtime_checkable
class LLMClient(Protocol):
    """LLM 调用抽象（流式 chat + 嵌入）。"""

    def stream_chat(
        self, messages: Sequence[dict[str, object]], tools: Sequence[dict[str, object]]
    ) -> AsyncIterator[dict[str, object]]:
        """流式返回 delta / tool_call / done 事件块。"""
        ...

    async def embed(self, texts: Sequence[str]) -> Sequence[Sequence[float]]: ...
