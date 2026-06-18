"""LLM 客户端（OpenAI 兼容），实现 ports.LLMClient。

流式 chat + 嵌入；并发用 Semaphore 限制；超时降级。对应《05 LLM-RAG 子系统》。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Sequence

import httpx

from app.core import errors
from app.core.config import get_settings


class HttpLLMClient:
    def __init__(self) -> None:
        self._settings = get_settings()
        self._sem = asyncio.Semaphore(self._settings.llm_max_concurrency)
        self._timeout = self._settings.llm_timeout_ms / 1000

    async def stream_chat(
        self, messages: Sequence[dict[str, object]], tools: Sequence[dict[str, object]]
    ) -> AsyncIterator[dict[str, object]]:
        if not self._settings.llm_base_url:
            raise errors.UpstreamDown("LLM 未配置")
        payload = {
            "model": self._settings.llm_chat_model,
            "messages": list(messages),
            "stream": True,
        }
        if tools:
            payload["tools"] = list(tools)
        async with self._sem:
            try:
                async with (
                    httpx.AsyncClient(timeout=self._timeout) as client,
                    client.stream(
                        "POST",
                        f"{self._settings.llm_base_url}/chat/completions",
                        json=payload,
                        headers=self._headers(),
                    ) as resp,
                ):
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        chunk = _parse_sse_line(line)
                        if chunk is not None:
                            yield chunk
            except (TimeoutError, httpx.HTTPError) as exc:
                raise errors.UpstreamDown("LLM 调用失败") from exc

    async def embed(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        if not self._settings.llm_base_url:
            raise errors.UpstreamDown("LLM 未配置")
        async with self._sem, httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                f"{self._settings.llm_base_url}/embeddings",
                json={"model": self._settings.llm_embedding_model, "input": list(texts)},
                headers=self._headers(),
            )
            resp.raise_for_status()
            return [item["embedding"] for item in resp.json()["data"]]

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._settings.llm_api_key}"}


def _parse_sse_line(line: str) -> dict[str, object] | None:
    """把 OpenAI 流式行解析成 {content} / {tool_call} / done 块。"""
    if not line or not line.startswith("data:"):
        return None
    data = line[len("data:") :].strip()
    if data == "[DONE]":
        return {"done": True}
    try:
        obj = json.loads(data)
    except json.JSONDecodeError:
        return None
    choice = (obj.get("choices") or [{}])[0]
    delta = choice.get("delta", {})
    if "tool_calls" in delta and delta["tool_calls"]:
        tc = delta["tool_calls"][0].get("function", {})
        return {"tool_call": {"name": tc.get("name"), "arguments": tc.get("arguments")}}
    if delta.get("content"):
        return {"content": delta["content"]}
    return None
