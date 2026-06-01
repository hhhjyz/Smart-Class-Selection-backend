"""身份与 RBAC。

网关已校验 JWT 并透传身份头，本服务只从 header 读取 user_id / role，
做本地角色守卫，不再解 JWT。对应《03 API 设计》。
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from app.core.errors import ERR_UNAUTHENTICATED, DomainError, Forbidden


class Role(str, enum.Enum):
    STUDENT = "student"
    TEACHER = "teacher"
    ADMIN = "admin"


@dataclass(frozen=True, slots=True)
class Principal:
    """当前请求的身份主体。由接入层从 header 构造，向下传递。"""

    user_id: str
    role: Role

    def require_role(self, *allowed: Role) -> None:
        """断言当前角色在允许集合内，否则抛 Forbidden。"""
        if self.role not in allowed:
            raise Forbidden()

    def require_self_or_privileged(self, target_user_id: str) -> None:
        """学生仅可访问自身资源；teacher / admin 放行。"""
        if self.role in (Role.TEACHER, Role.ADMIN):
            return
        if self.user_id != target_user_id:
            raise Forbidden()


def principal_from_headers(user_id: str | None, role: str | None) -> Principal:
    """从网关透传的 header 值构造 Principal。

    缺失或非法身份头抛 30002（运行期应由网关保证不缺失）。
    """
    if not user_id or not role:
        raise DomainError(ERR_UNAUTHENTICATED)
    try:
        parsed = Role(role.lower())
    except ValueError as exc:
        raise DomainError(ERR_UNAUTHENTICATED, "未知角色") from exc
    return Principal(user_id=user_id, role=parsed)
