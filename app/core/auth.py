"""身份与 RBAC。

网关（STSS-gateway / nginx）经 auth_service `POST /api/v1/internal/verify` 校验 JWT 后，
向下游注入 `X-User-Id` / `X-User-Role` / `X-User-Permissions`（逗号分隔）。本服务信任这些
网关头，只读身份、做本地角色守卫，不解 JWT。对应《03 API 设计》。
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from app.core.errors import ERR_UNAUTHENTICATED, DomainError, Forbidden


class Role(str, enum.Enum):
    STUDENT = "student"
    TEACHER = "teacher"
    ADMIN = "admin"


_ROLE_ALIASES = {
    "academic_admin": Role.ADMIN,
    "sys_admin": Role.ADMIN,
}


@dataclass(frozen=True, slots=True)
class Principal:
    """当前请求的身份主体。由接入层从 header 构造，向下传递。"""

    user_id: str
    role: Role
    permissions: tuple[str, ...] = ()

    def has_permission(self, code: str) -> bool:
        """是否持有网关透传的某权限码（X-User-Permissions 之一）。"""
        return code in self.permissions

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


def principal_from_headers(user_id: str | None, role: str | None, permissions: str | None = None) -> Principal:
    """从网关透传的 header 值构造 Principal。

    role 大小写不敏感（网关发 STUDENT/TEACHER/ADMIN，本地枚举为小写）；
    permissions 为逗号分隔字符串。缺失或非法身份头抛 30002（运行期应由网关保证不缺失）。
    """
    if not user_id or not role:
        raise DomainError(ERR_UNAUTHENTICATED)
    normalized_role = role.strip().lower()
    try:
        parsed = _ROLE_ALIASES.get(normalized_role)
        if parsed is None:
            parsed = Role(normalized_role)
    except ValueError as exc:
        raise DomainError(ERR_UNAUTHENTICATED, "未知角色") from exc
    perms = tuple(p.strip() for p in permissions.split(",") if p.strip()) if permissions else ()
    return Principal(user_id=user_id, role=parsed, permissions=perms)
