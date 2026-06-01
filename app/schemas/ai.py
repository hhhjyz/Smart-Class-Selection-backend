"""AI 助手相关 HTTP DTO。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class MessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str


class RecommendRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    goal: str
    semester: str


class RecommendedOffering(BaseModel):
    model_config = ConfigDict(extra="forbid")

    offering_id: str
    course_name: str
    reason: str
    eligibility: str  # valid | invalid


class RecommendResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rec_id: str
    offerings: list[RecommendedOffering]


class AcceptResultItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    offering_id: str
    status: str  # enrolled | rejected
    reason: str | None = None
    code: int | None = None


class AcceptResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    results: list[AcceptResultItem]
