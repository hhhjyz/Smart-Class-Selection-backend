"""AI 选课顾问。

LLM 仅产出推荐与解释；任何落库都复用 enrollment_service.enroll()，
规则引擎是最终裁判。function calling 仅暴露只读工具；越界工具名 → 502 + audit。
对应《05 LLM-RAG 子系统》。
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

from app.core import db, errors
from app.core.auth import Principal
from app.domain.audit import AuditEntry
from app.domain.enums import Stage
from app.domain.ports import AuditRepository, LLMClient, OfferingCacheRepository
from app.services.enrollment_service import EnrollmentService

logger = logging.getLogger(__name__)

# function calling 允许的只读工具集（白名单）
_ALLOWED_TOOLS = frozenset(
    {"search_courses", "get_offering", "get_my_study_plan", "check_eligibility", "get_my_credit_progress"}
)

_SYSTEM_PROMPT = (
    "You are a study advisor for the Smart Course Selection subsystem. "
    "You may recommend, explain, or summarize, but you cannot enroll, drop, or modify any record. "
    "The user must explicitly accept your recommendation; only then does the deterministic rule engine decide. "
    'If the user asks you to "select for me", respond with a candidate list and end with "请确认采纳"。 '
    "Every course you mention must be cited by its offering_id."
)

SQL_SAVE_REC = """
INSERT INTO course_selection.ai_recommendation_logs
    (rec_id, student_id, offering_ids, prompt_hash, model, latency_ms, accepted, created_at)
VALUES (%s, %s, %s, %s, %s, %s, false, NOW())
"""

SQL_GET_REC = "SELECT offering_ids FROM course_selection.ai_recommendation_logs WHERE rec_id = %s AND student_id = %s"

SQL_MARK_ACCEPTED = """
UPDATE course_selection.ai_recommendation_logs
   SET accepted = true, accepted_results = %s WHERE rec_id = %s
"""


class AIAdvisor:
    def __init__(
        self,
        *,
        llm_client: LLMClient,
        offering_repo: OfferingCacheRepository,
        audit_repo: AuditRepository,
        enrollment_service: EnrollmentService,
    ) -> None:
        self._llm = llm_client
        self._offerings = offering_repo
        self._audit = audit_repo
        self._enroll = enrollment_service

    async def stream_message(self, principal: Principal, content: str) -> AsyncIterator[dict[str, object]]:
        """转发 LLM 流式输出，并对工具调用做白名单守卫。"""
        messages: list[dict[str, object]] = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": content},
        ]
        async for chunk in self._llm.stream_chat(messages, tools=[]):
            tool = chunk.get("tool_call")
            if isinstance(tool, dict) and tool.get("name") not in _ALLOWED_TOOLS:
                await self._record_guardrail(principal, str(tool.get("name")))
                raise errors.UpstreamDown("AI 工具调用越界")
            yield chunk

    async def accept(self, principal: Principal, rec_id: str) -> list[dict[str, object]]:
        """一键采纳：对每个 offering 复用常规选课路径，逐笔判定，聚合结果。"""
        async with db.connection() as conn:
            cur = await conn.execute(SQL_GET_REC, (rec_id, principal.user_id))
            row = await cur.fetchone()
        if row is None:
            raise errors.NotFound("推荐记录不存在")
        offering_ids: list[str] = list(row[0])

        results: list[dict[str, object]] = []
        for offering_id in offering_ids:
            try:
                outcome = await self._enroll.enroll(
                    principal, student_id=principal.user_id,
                    offering_id=offering_id, stage=Stage.ADD_DROP,
                )
                results.append({"offering_id": offering_id, "status": outcome.status.value})
            except errors.DomainError as exc:
                results.append({
                    "offering_id": offering_id, "status": "rejected",
                    "reason": exc.message, "code": exc.code,
                })

        import json

        async with db.transaction() as conn:
            await conn.execute(SQL_MARK_ACCEPTED, (json.dumps(results), rec_id))
        return results

    async def _record_guardrail(self, principal: Principal, tool_name: str) -> None:
        async with db.transaction() as conn:
            await self._audit.write(
                conn,
                AuditEntry(
                    actor_id=principal.user_id, actor_role=principal.role.value,
                    action="ai.guardrail.violated", target_type="ai_tool", target_id=tool_name,
                ),
            )
        logger.warning("AI 工具越界：%s", tool_name, extra={"event": "ai.guardrail.violated"})
