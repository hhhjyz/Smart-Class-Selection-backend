"""选课主流程编排。

依赖倒置：构造函数注入抽象 ports（repo / store / waiting room），
不 import 任何具体实现。被 handler、AI 采纳、admin 代选共同复用——
全系统只有这一条选课写路径。

事务纪律（防长事务）：
- Waiting Room 校验、规则校验所需数据、Redis 库存扣减都在事务**之外**完成；
- 仅 insert→capacity→audit→outbox 四步在一个短事务内，块内无任何外部 I/O；
- 任一步失败：事务回滚后补偿 Redis 库存。
对应《04 高并发引擎设计》「完整业务事务的步骤序列」。
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from app.core import db, errors
from app.core.auth import Principal
from app.domain.audit import AuditEntry, OutboxEvent
from app.domain.enrollment import Enrollment
from app.domain.enums import EnrollmentSource, EnrollmentStatus, Stage
from app.domain.offering import Offering
from app.domain.ports import (
    AuditRepository,
    CapacityRepository,
    EnrollmentRepository,
    InfoServiceClient,
    OfferingCacheRepository,
    OutboxRepository,
    StockStore,
    StudyPlanRepository,
    WaitingRoom,
)
from app.services.rule_engine import RuleContext, RuleEngine


@dataclass(slots=True)
class EnrollOutcome:
    enrollment_id: str
    status: EnrollmentStatus


class EnrollmentService:
    def __init__(
        self,
        *,
        enrollment_repo: EnrollmentRepository,
        capacity_repo: CapacityRepository,
        offering_repo: OfferingCacheRepository,
        study_plan_repo: StudyPlanRepository,
        audit_repo: AuditRepository,
        outbox_repo: OutboxRepository,
        stock: StockStore,
        waiting_room: WaitingRoom,
        info_client: InfoServiceClient,
        rule_engine: RuleEngine,
    ) -> None:
        self._enrollments = enrollment_repo
        self._capacity = capacity_repo
        self._offerings = offering_repo
        self._plans = study_plan_repo
        self._audit = audit_repo
        self._outbox = outbox_repo
        self._stock = stock
        self._waitroom = waiting_room
        self._info = info_client
        self._rules = rule_engine

    async def enroll(
        self,
        principal: Principal,
        *,
        student_id: str,
        offering_id: str,
        stage: Stage,
        idempotency_key: str | None = None,
        source: EnrollmentSource = EnrollmentSource.STUDENT_SELF,
        allow_override: bool = False,
    ) -> EnrollOutcome:
        """选课主入口。抛 DomainError 表达各类业务失败。"""
        # 0) 幂等短路：同 key 已处理过，直接返回首次结果（事务外只读）
        if idempotency_key is not None:
            async with db.connection() as conn:
                prior = await self._enrollments.find_by_idempotency_key(conn, idempotency_key)
            if prior is not None:
                return EnrollOutcome(prior.enrollment_id, prior.status)

        # 1) Waiting Room 校验（仅 preference / add_drop 阶段，事务外）
        if stage in (Stage.PREFERENCE, Stage.ADD_DROP) and source is EnrollmentSource.STUDENT_SELF:
            if not await self._waitroom.is_admitted(offering_id, principal.user_id):
                position = await self._waitroom.enqueue(offering_id, principal.user_id)
                from app.core.config import get_settings

                raise errors.Queued(position=position, retry_after_ms=get_settings().waitroom_tick_ms)
            await self._waitroom.consume_admission(offering_id, principal.user_id)

        # 2) 取规则校验所需数据（全部事务外 I/O）
        ctx = await self._build_rule_context(
            student_id=student_id, offering_id=offering_id, allow_override=allow_override
        )
        violations = self._rules.validate(ctx)
        if self._rules.has_hard_violation(violations):
            raise errors.RuleRejected([v.model_dump(mode="json") for v in violations])

        # 3) Redis 热路径库存扣减（事务外）
        if not await self._stock.try_consume(offering_id):
            raise errors.CapacityFull()

        # 4) 短事务：insert → capacity → audit → outbox（块内无外部 I/O）
        enrollment = Enrollment(
            enrollment_id=str(uuid.uuid4()),
            student_id=student_id,
            offering_id=offering_id,
            semester=ctx.target.semester,
            status=EnrollmentStatus.ENROLLED,
            stage=stage,
            source=source,
            idempotency_key=idempotency_key,
        )
        try:
            async with db.transaction() as conn:
                inserted_id = await self._enrollments.insert(conn, enrollment)
                if inserted_id is None:
                    # 唯一约束命中 → 重复选课，回退 Redis 后返回 30204
                    raise errors.DuplicateEnrollment()
                cap = await self._capacity.get(conn, offering_id)
                if cap is None:
                    raise errors.NotFound("开课容量记录不存在")
                updated = await self._capacity.increment_enrolled(conn, offering_id, cap.version)
                if updated is None:
                    # 乐观锁失败或越界 → 抛出让事务回滚（外层补偿后由客户端重试）
                    raise errors.CapacityFull()
                await self._audit.write(
                    conn,
                    AuditEntry(
                        actor_id=principal.user_id, actor_role=principal.role.value,
                        action="enroll.create", target_type="enrollment", target_id=inserted_id,
                        after={"offering_id": offering_id, "student_id": student_id},
                    ),
                )
                await self._outbox.emit(
                    conn,
                    OutboxEvent(
                        aggregate_type="enrollment", aggregate_id=inserted_id,
                        event_type="enrollment.created",
                        payload={
                            "enrollment_id": inserted_id, "student_id": student_id,
                            "offering_id": offering_id, "semester": ctx.target.semester,
                            "stage": stage.value,
                        },
                    ),
                )
        except errors.DomainError:
            await self._stock.release(offering_id)  # 补偿
            raise
        except Exception:
            await self._stock.release(offering_id)  # 补偿
            raise

        return EnrollOutcome(enrollment.enrollment_id, EnrollmentStatus.ENROLLED)

    async def drop(self, principal: Principal, enrollment_id: str) -> bool:
        """退课，幂等。返回是否实际退课（已退/不存在返回 False）。"""
        async with db.transaction() as conn:
            canceled = await self._enrollments.soft_cancel(conn, enrollment_id, "student_drop")
            if canceled is None:
                return False
            await self._capacity.decrement_enrolled(conn, canceled.offering_id)
            await self._audit.write(
                conn,
                AuditEntry(
                    actor_id=principal.user_id, actor_role=principal.role.value,
                    action="enroll.drop", target_type="enrollment", target_id=enrollment_id,
                    before={"offering_id": canceled.offering_id},
                ),
            )
            await self._outbox.emit(
                conn,
                OutboxEvent(
                    aggregate_type="enrollment", aggregate_id=enrollment_id,
                    event_type="enrollment.canceled",
                    payload={
                        "enrollment_id": enrollment_id, "student_id": canceled.student_id,
                        "offering_id": canceled.offering_id, "reason_code": "student_drop",
                    },
                ),
            )
        # 事务提交后补偿 Redis（事务外）
        await self._stock.release(canceled.offering_id)
        return True

    async def swap(self, principal: Principal, *, drop_id: str, add_offering_id: str) -> EnrollOutcome:
        """退一选一。先退后选；选课失败时退课已提交，遵循"整体一笔"语义由上层重试。

        注：跨 Redis + DB 的原子性以补偿构成轻量 Saga；这里顺序执行并复用 enroll/drop。
        """
        dropped = await self.drop(principal, drop_id)
        if not dropped:
            raise errors.NotFound("待退课记录不存在")
        return await self.enroll(
            principal, student_id=principal.user_id, offering_id=add_offering_id, stage=Stage.ADD_DROP
        )

    async def list_my_enrollments(
        self, student_id: str, semester: str, status: str | None
    ) -> list[tuple[Enrollment, Offering | None]]:
        """读路径：本人选课列表，附带开课信息（供视图拼装）。"""
        async with db.connection() as conn:
            enrollments = await self._enrollments.list_by_student(conn, student_id, semester, status)
            out: list[tuple[Enrollment, Offering | None]] = []
            for e in enrollments:
                off = await self._offerings.get(conn, e.offering_id)
                out.append((e, off))
        return out

    async def get_roster(
        self, offering_id: str, include_dropped: bool
    ) -> tuple[Offering | None, list[tuple[str, str, datetime | None]]]:
        """读路径：花名册。返回 (开课信息, [(student_id, name, enrolled_at)])。"""
        async with db.connection() as conn:
            offering = await self._offerings.get(conn, offering_id)
            rows = list(await self._enrollments.list_roster(conn, offering_id, include_dropped))
        # 学生姓名经上游 A 组补全（事务外）
        students: list[tuple[str, str, datetime | None]] = []
        for student_id, enrolled_at in rows:
            try:
                profile = await self._info.get_student(student_id)
                name = profile.name
            except errors.DomainError:
                name = ""
            students.append((student_id, name, enrolled_at))
        return offering, students

    async def _build_rule_context(
        self, *, student_id: str, offering_id: str, allow_override: bool
    ) -> RuleContext:
        """组装规则上下文。所有读操作在事务外完成。"""
        async with db.connection() as conn:
            target = await self._offerings.get(conn, offering_id)
            if target is None:
                raise errors.NotFound("开课实例不存在")
            existing = list(
                await self._offerings.list_for_student_timetable(conn, student_id, target.semester)
            )
            plan = await self._plans.get_by_student(conn, student_id)
            rules = (
                await self._plans.get_curriculum_rules(conn, plan.major_code, plan.curriculum_version)
                if plan is not None
                else []
            )
        grades = await self._info.get_grades(student_id)
        passed = frozenset(g.course_code for g in grades if g.passed)
        target_credit = next((g.credit for g in grades if g.course_code == target.course_code), 0.0)
        current_total = _sum_credits(existing)
        return RuleContext(
            target=target,
            existing_offerings=existing,
            passed_courses=passed,
            grades=grades,
            curriculum_rules=rules,
            current_total_credit=current_total,
            target_credit=target_credit,
            allow_override=allow_override,
        )


def _sum_credits(offerings: Sequence[Offering]) -> float:
    # 学分汇总占位：实际可由 offering 携带或查 grades；此处保守返回 0，
    # 学分上限规则以 ctx.target_credit + current 为准，详见 rule_engine。
    return 0.0
