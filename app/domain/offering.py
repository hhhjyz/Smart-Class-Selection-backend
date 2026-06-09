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


class CourseInfo(BaseModel):
    """课程目录条目（来源 A 组 `GET /api/v1/info/courses/{id}` → CourseResponse）。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    course_id: int
    course_code: str
    course_name: str
    credit: float = 0


class OfferingCatalogEntry(BaseModel):
    """A 组开课实体（`GET /api/v1/info/offerings` → OfferingResponse）。

    A 组持有"开课"本身（课/学期/班/容量）；时段与教室由 B 组排课提供，按
    (course_id, term_code) 关联。一门课多个班时 B 组排课不区分班（无 class_no），
    其时段对该课所有班共用。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    offering_id: str  # A 组 offering.id（本系统开课主键）
    course_id: int
    course_code: str
    course_name: str
    term_code: str
    class_no: str = ""
    capacity: int = 0


class StudentProfile(BaseModel):
    """用户身份（来源 A 组 `GET /api/v1/info/users/{id}` → UserResponse）。

    A 组 UserResponse 实际形如 ``data: {"id": 1, "user_no": "32101", "username": "...",
    "profile": {"full_name": "张三", ...}}``。映射：student_id ← user_no，name ← profile.full_name。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    student_id: str  # ← A 组 data.user_no（学号/工号）
    name: str  # ← A 组 data.profile.full_name


class GradeRecord(BaseModel):
    """历史成绩记录，用于前置依赖判定。

    注：成绩属另组域；当前 A/B 的 OpenAPI 未见按学生查成绩端点，passed_courses 暂留空。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    course_code: str
    credit: float
    passed: bool
