"""依赖装配点（Composition Root）。

这里是唯一把具体实现（repo / engine / integration）注入抽象 service 的地方，
体现依赖倒置：业务层只见接口，装配只发生在边缘。

具体实现是无状态的（只持有连接句柄/客户端单例），故可进程级单例复用，
按请求注入 Principal 等请求态。
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from fastapi import Depends, Header

from app.core.auth import Principal, principal_from_headers
from app.core.redis import get_redis
from app.engine.capacity_lock import RedisStockStore
from app.engine.waiting_room import RedisWaitingRoom
from app.integrations.info_client import HttpInfoServiceClient
from app.integrations.llm_client import HttpLLMClient
from app.repositories.audit_repo import PgAuditRepository
from app.repositories.capacity_repo import PgCapacityRepository
from app.repositories.enrollment_repo import PgEnrollmentRepository
from app.repositories.offering_cache_repo import PgOfferingCacheRepository
from app.repositories.outbox_repo import PgOutboxRepository
from app.repositories.study_plan_repo import PgStudyPlanRepository
from app.services.ai_advisor import AIAdvisor
from app.services.enrollment_service import EnrollmentService
from app.services.lottery_service import LotteryService
from app.services.reconciler import Reconciler
from app.services.rule_engine import RuleEngine
from app.services.study_plan_service import StudyPlanService


# --- 请求态：从网关 header 解析当前身份 ---
async def get_principal(
    x_user_id: Annotated[str | None, Header(alias="X-User-ID")] = None,
    x_user_role: Annotated[str | None, Header(alias="X-User-Role")] = None,
) -> Principal:
    return principal_from_headers(x_user_id, x_user_role)


CurrentUser = Annotated[Principal, Depends(get_principal)]


# --- 进程级单例：无状态实现，复用 ---
@lru_cache(maxsize=1)
def _build_enrollment_service() -> EnrollmentService:
    redis = get_redis()
    return EnrollmentService(
        enrollment_repo=PgEnrollmentRepository(),
        capacity_repo=PgCapacityRepository(),
        offering_repo=PgOfferingCacheRepository(),
        study_plan_repo=PgStudyPlanRepository(),
        audit_repo=PgAuditRepository(),
        outbox_repo=PgOutboxRepository(),
        stock=RedisStockStore(redis),
        waiting_room=RedisWaitingRoom(redis),
        info_client=HttpInfoServiceClient(),
        rule_engine=RuleEngine(),
    )


@lru_cache(maxsize=1)
def _build_study_plan_service() -> StudyPlanService:
    return StudyPlanService(study_plan_repo=PgStudyPlanRepository())


@lru_cache(maxsize=1)
def _build_lottery_service() -> LotteryService:
    return LotteryService(audit_repo=PgAuditRepository(), outbox_repo=PgOutboxRepository())


@lru_cache(maxsize=1)
def _build_ai_advisor() -> AIAdvisor:
    return AIAdvisor(
        llm_client=HttpLLMClient(),
        offering_repo=PgOfferingCacheRepository(),
        audit_repo=PgAuditRepository(),
        enrollment_service=_build_enrollment_service(),
    )


def _build_reconciler() -> Reconciler:
    return Reconciler(
        capacity_repo=PgCapacityRepository(),
        audit_repo=PgAuditRepository(),
        stock=RedisStockStore(get_redis()),
    )


# --- FastAPI 依赖提供函数 ---
def get_enrollment_service() -> EnrollmentService:
    return _build_enrollment_service()


def get_study_plan_service() -> StudyPlanService:
    return _build_study_plan_service()


def get_lottery_service() -> LotteryService:
    return _build_lottery_service()


def get_ai_advisor() -> AIAdvisor:
    return _build_ai_advisor()


def get_offering_repo() -> PgOfferingCacheRepository:
    return PgOfferingCacheRepository()


def get_enrollment_repo() -> PgEnrollmentRepository:
    return PgEnrollmentRepository()


def get_capacity_repo() -> PgCapacityRepository:
    return PgCapacityRepository()


EnrollmentServiceDep = Annotated[EnrollmentService, Depends(get_enrollment_service)]
StudyPlanServiceDep = Annotated[StudyPlanService, Depends(get_study_plan_service)]
LotteryServiceDep = Annotated[LotteryService, Depends(get_lottery_service)]
AIAdvisorDep = Annotated[AIAdvisor, Depends(get_ai_advisor)]
