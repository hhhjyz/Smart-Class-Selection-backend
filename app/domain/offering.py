"""开课与学生域实体（上游 A/B 数据落地后的本地表示）。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class TimeSlot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    day: int = Field(ge=1, le=7)
    period: tuple[int, ...]
    weeks: str


class Offering(BaseModel):
    """开课实例，对应 cached_offerings 表（来源 B 组）。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    offering_id: str
    course_code: str
    course_name: str
    teacher_id: str
    teacher_name: str
    semester: str
    time_slots: tuple[TimeSlot, ...] = ()
    classroom: str | None = None
    campus: str | None = None


class StudentProfile(BaseModel):
    """学生身份与专业（来源 A 组）。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    student_id: str
    name: str
    major_code: str
    curriculum_version: str


class GradeRecord(BaseModel):
    """历史成绩记录（来源 A 组），用于前置依赖判定。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    course_code: str
    credit: float
    passed: bool
