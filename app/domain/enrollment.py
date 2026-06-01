"""选课域实体。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import EnrollmentSource, EnrollmentStatus, Stage


class Enrollment(BaseModel):
    """选课记录实体，对应 enrollments 表。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    enrollment_id: str
    student_id: str
    offering_id: str
    semester: str
    status: EnrollmentStatus
    stage: Stage
    source: EnrollmentSource = EnrollmentSource.STUDENT_SELF
    idempotency_key: str | None = None
    enrolled_at: datetime | None = None
    canceled_at: datetime | None = None


class EnrollmentIntent(BaseModel):
    """意愿初选志愿，对应 enrollment_intents 表。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    intent_id: str
    student_id: str
    offering_id: str
    semester: str
    priority: int = Field(ge=1)


class Capacity(BaseModel):
    """课程容量权威记录，对应 course_capacity 表。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    offering_id: str
    semester: str
    max_capacity: int = Field(ge=0)
    enrolled_count: int = Field(ge=0)
    waitlist_count: int = Field(ge=0, default=0)
    version: int = Field(ge=0)
