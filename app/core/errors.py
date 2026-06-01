"""错误码常量（30xx 段）与领域异常。

错误码集中在此，禁止在业务代码里散落魔法数字。对应《03 API 设计》§6。
异常在接入层由统一 handler 映射成响应壳 + HTTP status。
"""

from __future__ import annotations

# --- 错误码常量（30xx 业务段）---
ERR_OK = 0
ERR_BAD_REQUEST = 30001
ERR_UNAUTHENTICATED = 30002
ERR_FORBIDDEN = 30003
ERR_NOT_FOUND = 30004

ERR_PLAN_RULE_FAILED = 30101
ERR_TIME_CONFLICT = 30102
ERR_PREREQUISITE = 30103
ERR_CREDIT_CAP = 30104

ERR_QUEUED = 30201
ERR_RULE_REJECTED = 30202
ERR_CAPACITY_FULL = 30203
ERR_DUPLICATE = 30204
ERR_WINDOW_CLOSED = 30205

ERR_UPSTREAM_DOWN = 30301
ERR_RATE_LIMITED = 30401

# 错误码 → 默认 HTTP 状态码
_HTTP_STATUS: dict[int, int] = {
    ERR_OK: 200,
    ERR_BAD_REQUEST: 400,
    ERR_UNAUTHENTICATED: 401,
    ERR_FORBIDDEN: 403,
    ERR_NOT_FOUND: 404,
    ERR_PLAN_RULE_FAILED: 422,
    ERR_TIME_CONFLICT: 422,
    ERR_PREREQUISITE: 422,
    ERR_CREDIT_CAP: 422,
    ERR_QUEUED: 202,
    ERR_RULE_REJECTED: 422,
    ERR_CAPACITY_FULL: 409,
    ERR_DUPLICATE: 409,
    ERR_WINDOW_CLOSED: 423,
    ERR_UPSTREAM_DOWN: 503,
    ERR_RATE_LIMITED: 429,
}

# 错误码 → 默认中文消息
_MESSAGES: dict[int, str] = {
    ERR_BAD_REQUEST: "请求参数非法",
    ERR_UNAUTHENTICATED: "网关身份头缺失",
    ERR_FORBIDDEN: "角色越权",
    ERR_NOT_FOUND: "资源不存在",
    ERR_PLAN_RULE_FAILED: "培养方案规则未通过",
    ERR_TIME_CONFLICT: "时间冲突",
    ERR_PREREQUISITE: "前置课程未修",
    ERR_CREDIT_CAP: "学分超限",
    ERR_QUEUED: "已进入排队",
    ERR_RULE_REJECTED: "规则拒绝",
    ERR_CAPACITY_FULL: "课程已满",
    ERR_DUPLICATE: "重复选课",
    ERR_WINDOW_CLOSED: "选课窗口非开放",
    ERR_UPSTREAM_DOWN: "上游服务故障",
    ERR_RATE_LIMITED: "请求过于频繁",
}


def http_status_for(code: int) -> int:
    """业务错误码对应的 HTTP 状态码，未知码按 500。"""
    return _HTTP_STATUS.get(code, 500)


def default_message(code: int) -> str:
    """业务错误码的默认消息。"""
    return _MESSAGES.get(code, "未知错误")


class DomainError(Exception):
    """领域异常基类。携带业务错误码与可选结构化 ``data``。

    业务层只抛此异常族；接入层统一捕获并渲染成响应壳，
    业务代码不感知 HTTP。
    """

    def __init__(self, code: int, message: str | None = None, data: object | None = None) -> None:
        self.code = code
        self.message = message or default_message(code)
        self.data = data
        super().__init__(self.message)


class BadRequest(DomainError):
    def __init__(self, message: str | None = None, data: object | None = None) -> None:
        super().__init__(ERR_BAD_REQUEST, message, data)


class Forbidden(DomainError):
    def __init__(self, message: str | None = None) -> None:
        super().__init__(ERR_FORBIDDEN, message)


class NotFound(DomainError):
    def __init__(self, message: str | None = None) -> None:
        super().__init__(ERR_NOT_FOUND, message)


class Queued(DomainError):
    """进入 Waiting Room。``data`` 含 position、retry_after_ms。"""

    def __init__(self, position: int, retry_after_ms: int) -> None:
        super().__init__(
            ERR_QUEUED,
            data={"position": position, "retry_after_ms": retry_after_ms},
        )


class RuleRejected(DomainError):
    """规则引擎拒绝。``data.violations`` 聚合多条 violation。"""

    def __init__(self, violations: list[dict[str, object]]) -> None:
        super().__init__(ERR_RULE_REJECTED, data={"violations": violations})


class CapacityFull(DomainError):
    def __init__(self) -> None:
        super().__init__(ERR_CAPACITY_FULL)


class DuplicateEnrollment(DomainError):
    def __init__(self) -> None:
        super().__init__(ERR_DUPLICATE)


class WindowClosed(DomainError):
    def __init__(self) -> None:
        super().__init__(ERR_WINDOW_CLOSED)


class UpstreamDown(DomainError):
    def __init__(self, message: str | None = None) -> None:
        super().__init__(ERR_UPSTREAM_DOWN, message)
