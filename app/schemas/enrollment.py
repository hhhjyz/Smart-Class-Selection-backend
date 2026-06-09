"""选课相关 HTTP DTO。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import EnrollmentStatus, Stage


class EnrollRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    offering_id: str
    stage: Stage
    idempotency_key: str | None = None


class SwapRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    drop_id: str
    add_offering_id: str


class EnrollResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enrollment_id: str
    status: EnrollmentStatus


class EnrollmentView(BaseModel):
    """学生选课列表 / 跨组 enrollments 接口的单条视图。"""

    model_config = ConfigDict(extra="forbid")

    enrollment_id: str
    offering_id: str
    course_code: str
    course_name: str
    teacher_id: str
    teacher_name: str
    status: EnrollmentStatus
    stage: Stage
    enrolled_at: datetime | None = None


class EnrollmentListView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    list: list[EnrollmentView]
    total: int


class QueuePosition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    position: int
    retry_after_ms: int


class RosterStudent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    student_id: str
    # 姓名属 A 组数据，A 组未发布身份查询端点，故暂为空（不依赖不存在的接口）
    name: str = ""
    enrolled_at: datetime | None = None


class RosterView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    offering_id: str
    course_code: str
    semester: str
    students: list[RosterStudent]
    total: int
    snapshot_at: datetime | None = None


class ProxyEnrollRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    student_id: str
    offering_id: str
    reason: str = Field(min_length=1)


class CapacityAdjustRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    delta: int
    reason: str = Field(min_length=1)


class ThrottleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tick_interval_ms: int | None = Field(default=None, ge=10)
    capacity_per_tick: int | None = Field(default=None, ge=1)
    per_user_rps: int | None = Field(default=None, ge=1)


class WindowRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    semester: str
    stage: Stage
    start_at: str
    end_at: str


class LotteryRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    semester: str
    seed: int | None = None
