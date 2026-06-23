"""integrations 客户端单测：A 组基础信息 + B 组排课（httpx MockTransport）+ LLM。"""

from __future__ import annotations

import httpx
import pytest

import app.core.http as http_mod
from app.core import errors
from app.integrations.info_client import HttpInfoServiceClient
from app.integrations.llm_client import HttpLLMClient
from app.integrations.schedule_client import HttpScheduleServiceClient


def _install_transport(monkeypatch, handler) -> None:  # type: ignore[no-untyped-def]
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(http_mod, "_client", client)


@pytest.mark.asyncio
async def test_info_get_student_maps_user_no_and_full_name(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # A 组 data-provision UserDataResponse：映射 user_no→学号、full_name→姓名
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/api/v1/info/data-provision/users/S-1"
        return httpx.Response(
            200,
            json={
                "code": 0,
                "message": "success",
                "data": {"user_id": "1", "user_no": "S-1", "username": "stu", "full_name": "李同学"},
            },
        )

    _install_transport(monkeypatch, handler)
    prof = await HttpInfoServiceClient().get_student("S-1")
    assert prof.student_id == "S-1" and prof.name == "李同学"


@pytest.mark.asyncio
async def test_info_get_course(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/api/v1/info/courses/7"
        return httpx.Response(
            200, json={"code": 0, "data": {"id": 7, "course_code": "CS101", "course_name": "软件工程", "credit": 3}}
        )

    _install_transport(monkeypatch, handler)
    c = await HttpInfoServiceClient().get_course(7)
    assert c is not None and c.course_code == "CS101" and c.course_name == "软件工程" and c.credit == 3


@pytest.mark.asyncio
async def test_info_list_offerings(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # A 组 OfferingResponse 列表壳；仅取 ACTIVE
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/api/v1/info/offerings"
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "items": [
                        {
                            "id": 11,
                            "course_id": 7,
                            "course_code": "CS101",
                            "course_name": "软件工程",
                            "term_code": "2026-1",
                            "class_no": "01",
                            "capacity": 100,
                            "status": "ACTIVE",
                        },
                        {
                            "id": 12,
                            "course_id": 8,
                            "course_code": "X",
                            "course_name": "停开",
                            "term_code": "2026-1",
                            "class_no": "01",
                            "capacity": 0,
                            "status": "CLOSED",
                        },
                    ],
                    "pagination": {"total": 2, "page": 1, "page_size": 100},
                },
            },
        )

    _install_transport(monkeypatch, handler)
    offs = await HttpInfoServiceClient().list_offerings("2026-1")
    assert len(offs) == 1 and offs[0].offering_id == "11" and offs[0].capacity == 100  # CLOSED 被过滤


@pytest.mark.asyncio
async def test_info_list_training_programs(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # data-provision 列表壳：data:{items, pagination, snapshot_time}
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/api/v1/info/data-provision/training-programs"
        assert req.url.params.get("major_code") == "CS"
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "items": [
                        {
                            "id": 1,
                            "program_code": "CS-2023",
                            "major_code": "CS",
                            "grade": "2023",
                            "version": "1.0",
                            "required_course_ids": [7, 8, 9],
                        }
                    ],
                    "pagination": {"total": 1, "page": 1, "page_size": 100},
                },
            },
        )

    _install_transport(monkeypatch, handler)
    progs = await HttpInfoServiceClient().list_training_programs("CS", grade="2023")
    assert len(progs) == 1 and progs[0].major_code == "CS"
    assert progs[0].required_course_ids == (7, 8, 9)


@pytest.mark.asyncio
async def test_info_sends_bearer_token(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["auth"] = req.headers.get("Authorization")
        return httpx.Response(200, json={"code": 0, "data": {"id": "S-1", "name": "x"}})

    _install_transport(monkeypatch, handler)
    client = HttpInfoServiceClient()
    client._settings = client._settings.model_copy(update={"info_service_token": "svc-tok"})
    await client.get_student("S-1")
    assert seen["auth"] == "Bearer svc-tok"


@pytest.mark.asyncio
async def test_info_fetches_service_token_when_not_preconfigured(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/api/v1/auth/sys/login":
            assert req.url.host == "auth"
            assert req.method == "POST"
            return httpx.Response(200, json={"code": 0, "data": {"service_token": "issued-token", "expires_in": 3600}})
        seen["auth"] = req.headers.get("Authorization")
        return httpx.Response(
            200,
            json={"code": 0, "data": {"user_id": "1", "user_no": "S-1", "username": "stu", "full_name": ""}},
        )

    _install_transport(monkeypatch, handler)
    client = HttpInfoServiceClient()
    client._settings = client._settings.model_copy(
        update={
            "auth_service_base_url": "http://auth",
            "course_selection_service_client_secret": "secret",
            "info_service_token": "",
        }
    )
    await client.get_student("S-1")
    assert seen["auth"] == "Bearer issued-token"


@pytest.mark.asyncio
async def test_schedule_list_offerings_aggregates_entries(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # B 组真实契约：{code,msg,data:[...]}，/schedule/entries + /classrooms，通过 Gateway + service token 调用
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/api/v1/auth/sys/login":
            return httpx.Response(200, json={"code": 0, "data": {"service_token": "schedule-token"}})
        if req.url.path == "/api/v1/schedule/entries":
            assert req.headers.get("Authorization") == "Bearer schedule-token"
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "msg": "success",
                    "data": [
                        {
                            "id": 1,
                            "semester": "2026-1",
                            "offering_id": "OFFERING-CS101-01",
                            "course_id": "CS101",
                            "course_code": "CS101",
                            "course_name": "软件工程",
                            "teacher_ids": ["T-9001"],
                            "classroom_id": 10,
                            "day_of_week": 1,
                            "slot_start": 1,
                            "slot_end": 2,
                            "week_start": 1,
                            "week_end": 16,
                            "week_parity": "ALL",
                        },
                        {
                            "id": 2,
                            "semester": "2026-1",
                            "offering_id": "OFFERING-CS101-01",
                            "course_id": "CS101",
                            "course_code": "CS101",
                            "course_name": "软件工程",
                            "teacher_ids": ["T-9001"],
                            "classroom_id": 10,
                            "day_of_week": 3,
                            "slot_start": 3,
                            "slot_end": 4,
                            "week_start": 1,
                            "week_end": 15,
                            "week_parity": "ODD",
                        },
                    ],
                },
            )
        if req.url.path == "/api/v1/classrooms":
            assert req.headers.get("Authorization") == "Bearer schedule-token"
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": [
                        {
                            "id": 10,
                            "code": "A101",
                            "name": "紫金港西1-201",
                            "campus": "紫金港",
                            "building": "西1",
                            "capacity": 120,
                            "room_type": "LECTURE",
                            "available_time": [],
                            "is_active": True,
                        }
                    ],
                },
            )
        return httpx.Response(404, json={})

    _install_transport(monkeypatch, handler)
    client = HttpScheduleServiceClient()
    client._settings = client._settings.model_copy(
        update={
            "auth_service_base_url": "http://auth",
            "course_selection_service_client_secret": "secret",
            "schedule_service_base_url": "http://gateway",
        }
    )
    offs = await client.list_offerings("2026-1")
    assert len(offs) == 1
    o = offs[0]
    assert o.offering_id == "OFFERING-CS101-01" and o.course_code == "CS101" and o.teacher_id == "T-9001"
    assert len(o.time_slots) == 2
    assert o.time_slots[0].day == 1 and o.time_slots[0].period == (1, 2) and o.time_slots[0].weeks == "1-16周"
    assert o.time_slots[1].period == (3, 4) and o.time_slots[1].weeks == "1-15周(单)"
    assert o.classroom == "紫金港西1-201" and o.campus == "紫金港"


@pytest.mark.asyncio
async def test_schedule_business_error_code(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _install_transport(monkeypatch, lambda req: httpx.Response(200, json={"code": 2005, "msg": "x", "data": None}))
    with pytest.raises(errors.UpstreamDown):
        await HttpScheduleServiceClient().list_offerings("2026-1")


@pytest.mark.asyncio
async def test_info_business_error_code(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _install_transport(monkeypatch, lambda req: httpx.Response(200, json={"code": 1001, "data": {}}))
    with pytest.raises(errors.UpstreamDown):
        await HttpInfoServiceClient().get_student("S-1")


def _settings_with_llm():  # type: ignore[no-untyped-def]
    from app.core.config import Settings

    return Settings(llm_base_url="http://llm", llm_api_key="k")


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
    import app.integrations.llm_client as llm_mod

    async_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(llm_mod.httpx, "AsyncClient", lambda *a, **k: async_client)
    chunks = [c async for c in client.stream_chat([{"role": "user", "content": "hi"}], tools=[])]
    assert {"content": "你好"} in chunks
    assert any("tool_call" in c for c in chunks)
    assert {"done": True} in chunks


@pytest.mark.asyncio
async def test_llm_not_configured_raises(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    client = HttpLLMClient()
    client._settings = client._settings.model_copy(update={"llm_base_url": ""})
    with pytest.raises(errors.UpstreamDown):
        _ = [c async for c in client.stream_chat([], tools=[])]
