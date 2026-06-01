"""integrations 客户端单测：用 httpx.MockTransport 模拟上游，无需真实服务。"""

from __future__ import annotations

import httpx
import pytest

import app.core.http as http_mod
from app.core import errors
from app.integrations.info_client import HttpInfoServiceClient
from app.integrations.llm_client import HttpLLMClient
from app.integrations.schedule_client import HttpScheduleServiceClient


def _install_transport(monkeypatch, handler) -> None:  # type: ignore[no-untyped-def]
    """把全局 httpx 客户端替换为带 MockTransport 的客户端。"""
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(http_mod, "_client", client)


@pytest.mark.asyncio
async def test_info_client_parses_student_and_grades(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/grades"):
            return httpx.Response(200, json={"data": {"grades": [
                {"course_code": "DS201", "credit": 3, "passed": True},
            ]}})
        return httpx.Response(200, json={"data": {
            "student_id": "S-1", "name": "李同学", "major_code": "CS", "curriculum_version": "2023",
        }})
    _install_transport(monkeypatch, handler)
    client = HttpInfoServiceClient()
    prof = await client.get_student("S-1")
    assert prof.name == "李同学"
    grades = await client.get_grades("S-1")
    assert grades[0].course_code == "DS201" and grades[0].passed


@pytest.mark.asyncio
async def test_info_client_4xx_raises_upstream(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _install_transport(monkeypatch, lambda req: httpx.Response(400, json={}))
    with pytest.raises(errors.UpstreamDown):
        await HttpInfoServiceClient().get_student("S-1")


@pytest.mark.asyncio
async def test_info_client_5xx_trips_breaker(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _install_transport(monkeypatch, lambda req: httpx.Response(500, json={}))
    client = HttpInfoServiceClient()
    with pytest.raises(errors.UpstreamDown):
        await client.get_student("S-1")  # 重试耗尽后熔断计数
    # 触发足够失败后进入熔断快速失败
    for _ in range(5):
        with pytest.raises(errors.UpstreamDown):
            await client.get_student("S-1")


@pytest.mark.asyncio
async def test_schedule_client_offerings_and_404(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/offerings"):
            return httpx.Response(200, json={"data": {"list": [{
                "offering_id": "B-CS101-2026-1-01", "course_code": "CS101", "course_name": "软件工程",
                "teacher_id": "T-9001", "teacher_name": "张老师", "semester": "2026-1",
                "time_slots": [{"day": 1, "period": [1, 2], "weeks": "1-16"}],
                "classroom": "201", "campus": "紫金港",
            }]}})
        return httpx.Response(404, json={})
    _install_transport(monkeypatch, handler)
    client = HttpScheduleServiceClient()
    offerings = await client.list_offerings("2026-1", page=1, page_size=50)
    assert offerings[0].course_code == "CS101"
    assert offerings[0].time_slots[0].day == 1
    assert await client.get_offering("missing") is None


@pytest.mark.asyncio
async def test_llm_client_stream_parsing(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    sse = (
        'data: {"choices":[{"delta":{"content":"你好"}}]}\n'
        'data: {"choices":[{"delta":{"tool_calls":[{"function":{"name":"search_courses","arguments":"{}"}}]}}]}\n'
        "data: [DONE]\n"
    )

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=sse)

    monkeypatch.setattr("app.integrations.llm_client.get_settings", _settings_with_llm)
    client = HttpLLMClient()
    # 用独立的 httpx mock：llm_client 自建 client，故 patch httpx.AsyncClient.stream
    import app.integrations.llm_client as llm_mod

    async_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(llm_mod.httpx, "AsyncClient", lambda *a, **k: async_client)
    chunks = [c async for c in client.stream_chat([{"role": "user", "content": "hi"}], tools=[])]
    assert {"content": "你好"} in chunks
    assert any("tool_call" in c for c in chunks)
    assert {"done": True} in chunks


def _settings_with_llm():  # type: ignore[no-untyped-def]
    from app.core.config import Settings

    return Settings(llm_base_url="http://llm", llm_api_key="k")


@pytest.mark.asyncio
async def test_llm_not_configured_raises(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    client = HttpLLMClient()
    client._settings = client._settings.model_copy(update={"llm_base_url": ""})
    with pytest.raises(errors.UpstreamDown):
        _ = [c async for c in client.stream_chat([], tools=[])]
