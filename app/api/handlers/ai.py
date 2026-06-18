"""AI 助手 handlers（SSE 流式）。"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter
from sse_starlette.sse import EventSourceResponse

from app.api.deps import AIAdvisorDep, CurrentUser
from app.core.auth import Role
from app.schemas.ai import (
    AcceptResult,
    AcceptResultItem,
    MessageRequest,
    RecommendRequest,
    RecommendResult,
)
from app.schemas.common import Envelope

router = APIRouter(prefix="/api/course-selection/v1/ai", tags=["ai"])


@router.post("/conversations")
async def new_conversation(principal: CurrentUser) -> Envelope[dict[str, object]]:
    principal.require_role(Role.STUDENT)
    return Envelope.ok({"conversation_id": str(uuid.uuid4())})


@router.post("/conversations/{conversation_id}/messages")
async def send_message(
    conversation_id: str, body: MessageRequest, principal: CurrentUser, advisor: AIAdvisorDep
) -> EventSourceResponse:
    principal.require_role(Role.STUDENT)

    async def event_stream() -> AsyncIterator[dict[str, str]]:
        async for chunk in advisor.stream_message(principal, body.content):
            if chunk.get("done"):
                yield {"event": "done", "data": json.dumps({"conversation_id": conversation_id})}
            elif "tool_call" in chunk:
                yield {"event": "tool_call", "data": json.dumps(chunk["tool_call"])}
            elif "content" in chunk:
                yield {"event": "delta", "data": json.dumps({"content": chunk["content"]})}

    return EventSourceResponse(event_stream())


@router.post("/recommendations")
async def recommend(body: RecommendRequest, principal: CurrentUser, advisor: AIAdvisorDep) -> Envelope[RecommendResult]:
    principal.require_role(Role.STUDENT)
    # 推荐生成的完整 RAG 流程见 ai_advisor / 05_LLM-RAG 子系统；此处返回会话句柄占位
    rec_id = f"rec-{uuid.uuid4().hex[:8]}"
    return Envelope.ok(RecommendResult(rec_id=rec_id, offerings=[]))


@router.post("/recommendations/{rec_id}/accept")
async def accept_recommendation(rec_id: str, principal: CurrentUser, advisor: AIAdvisorDep) -> Envelope[AcceptResult]:
    principal.require_role(Role.STUDENT)
    results = await advisor.accept(principal, rec_id)
    return Envelope.ok(AcceptResult(results=[AcceptResultItem.model_validate(r) for r in results]))
