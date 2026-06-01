"""领域枚举。与《02 数据库设计》《11 前端接口文档》附录 A 对齐。"""

from __future__ import annotations

import enum


class EnrollmentStatus(str, enum.Enum):
    PENDING_LOTTERY = "pending_lottery"
    ENROLLED = "enrolled"
    WAITLISTED = "waitlisted"
    CANCELED = "canceled"
    FAILED = "failed"


class Stage(str, enum.Enum):
    PREFERENCE = "preference"
    LOTTERY = "lottery"
    ADD_DROP = "add_drop"


class EnrollmentSource(str, enum.Enum):
    STUDENT_SELF = "student_self"
    ADMIN_PROXY = "admin_proxy"


class PlanStatus(str, enum.Enum):
    DRAFT = "draft"
    VALID = "valid"
    INVALID = "invalid"


class ItemCategory(str, enum.Enum):
    MAJOR_REQUIRED = "major_required"
    MAJOR_ELECTIVE = "major_elective"
    GENERAL = "general"


class RuleType(str, enum.Enum):
    MIN_CREDIT_TOTAL = "min_credit_total"
    MIN_CREDIT_CATEGORY = "min_credit_category"
    PREREQUISITE = "prerequisite"
    EXCLUSIVE = "exclusive"


class CancelReason(str, enum.Enum):
    STUDENT_DROP = "student_drop"
    LOTTERY_LOST = "lottery_lost"
    ADMIN_REVOKE = "admin_revoke"


class Severity(str, enum.Enum):
    HARD = "hard"
    SOFT = "soft"
