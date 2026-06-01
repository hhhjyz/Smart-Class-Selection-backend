"""core 层纯逻辑单测：auth / errors / schemas / http 熔断 / config。"""

from __future__ import annotations

import pytest

from app.core import errors
from app.core.auth import Principal, Role, principal_from_headers
from app.core.config import get_settings
from app.core.http import CircuitBreaker
from app.schemas.common import Envelope, Pagination


# ----------------------- auth -----------------------
def test_require_role_ok_and_forbidden() -> None:
    p = Principal(user_id="S-1", role=Role.STUDENT)
    p.require_role(Role.STUDENT)  # 不抛
    with pytest.raises(errors.Forbidden):
        p.require_role(Role.ADMIN)


def test_require_self_or_privileged() -> None:
    student = Principal(user_id="S-1", role=Role.STUDENT)
    student.require_self_or_privileged("S-1")  # 本人 ok
    with pytest.raises(errors.Forbidden):
        student.require_self_or_privileged("S-2")
    # teacher/admin 放行任意
    Principal(user_id="T-1", role=Role.TEACHER).require_self_or_privileged("S-9")
    Principal(user_id="A-1", role=Role.ADMIN).require_self_or_privileged("S-9")


def test_principal_from_headers() -> None:
    p = principal_from_headers("S-1", "student")
    assert p.role is Role.STUDENT
    with pytest.raises(errors.DomainError) as ei:
        principal_from_headers(None, "student")
    assert ei.value.code == errors.ERR_UNAUTHENTICATED
    with pytest.raises(errors.DomainError):
        principal_from_headers("S-1", "wizard")  # 未知角色


# ----------------------- errors -----------------------
def test_http_status_and_messages() -> None:
    assert errors.http_status_for(errors.ERR_FORBIDDEN) == 403
    assert errors.http_status_for(errors.ERR_QUEUED) == 202
    assert errors.http_status_for(999999) == 500  # 未知码
    assert errors.default_message(errors.ERR_CAPACITY_FULL) == "课程已满"
    assert errors.default_message(424242) == "未知错误"


def test_domain_error_subclasses() -> None:
    assert errors.Queued(3, 200).data == {"position": 3, "retry_after_ms": 200}
    assert errors.RuleRejected([{"x": 1}]).code == errors.ERR_RULE_REJECTED
    assert errors.CapacityFull().code == errors.ERR_CAPACITY_FULL
    assert errors.DuplicateEnrollment().code == errors.ERR_DUPLICATE
    assert errors.WindowClosed().code == errors.ERR_WINDOW_CLOSED
    assert errors.NotFound("x").message == "x"


# ----------------------- schemas -----------------------
def test_envelope_and_pagination() -> None:
    ok = Envelope.ok({"a": 1})
    assert ok.code == 0 and ok.data == {"a": 1}
    fail = Envelope.fail(errors.ERR_FORBIDDEN, "no")
    assert fail.code == errors.ERR_FORBIDDEN
    assert Pagination(page=3, page_size=20).offset == 40
    with pytest.raises(ValueError):
        Pagination(page=0)  # ge=1
    with pytest.raises(ValueError):
        Pagination(page_size=999)  # le=100


# ----------------------- http circuit breaker -----------------------
def test_circuit_breaker_opens_and_resets() -> None:
    cb = CircuitBreaker(threshold=3, cooldown_s=30)
    assert cb.is_open is False
    cb.record_failure()
    cb.record_failure()
    assert cb.is_open is False  # 未达阈值
    cb.record_failure()
    assert cb.is_open is True   # 第 3 次触发熔断
    cb.record_success()
    assert cb.is_open is False  # 成功后复位


def test_circuit_breaker_cooldown(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import app.core.http as http_mod

    t = {"now": 1000.0}
    monkeypatch.setattr(http_mod.time, "monotonic", lambda: t["now"])
    cb = CircuitBreaker(threshold=1, cooldown_s=30)
    cb.record_failure()
    assert cb.is_open is True
    t["now"] += 31  # 冷却期过
    assert cb.is_open is False  # 半开放行


# ----------------------- config -----------------------
def test_settings_singleton() -> None:
    assert get_settings() is get_settings()
    assert get_settings().service_name == "course-selection"
