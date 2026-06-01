"""通用响应壳与分页参数。"""

from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from app.core.errors import ERR_OK
from app.core.logging import current_request_id

T = TypeVar("T")


class Envelope(BaseModel, Generic[T]):
    """统一响应壳 ``{ code, message, data, trace_id }``。"""

    model_config = ConfigDict(extra="forbid")

    code: int = ERR_OK
    message: str = "Success"
    data: T | None = None
    trace_id: str = ""

    @classmethod
    def ok(cls, data: T | None = None) -> Envelope[T]:
        return cls(code=ERR_OK, message="Success", data=data, trace_id=current_request_id())

    @classmethod
    def fail(cls, code: int, message: str, data: object | None = None) -> Envelope[object]:
        return Envelope[object](code=code, message=message, data=data, trace_id=current_request_id())


class Pagination(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size
