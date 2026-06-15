"""Gateway path compatibility tests."""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_gateway_course_selection_prefix_is_rewritten() -> None:
    from app.api.path_compat import CourseSelectionPathCompatMiddleware

    seen: dict[str, object] = {}

    async def app(scope, receive, send):  # type: ignore[no-untyped-def]
        seen["path"] = scope["path"]
        seen["raw_path"] = scope["raw_path"]

    middleware = CourseSelectionPathCompatMiddleware(app)
    await middleware(
        {"type": "http", "path": "/api/v1/course-selection/courses/search", "raw_path": b""},
        None,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
    )

    assert seen["path"] == "/api/course-selection/v1/courses/search"
    assert seen["raw_path"] == b"/api/course-selection/v1/courses/search"


@pytest.mark.asyncio
async def test_existing_course_selection_prefix_is_unchanged() -> None:
    from app.api.path_compat import CourseSelectionPathCompatMiddleware

    seen: dict[str, object] = {}

    async def app(scope, receive, send):  # type: ignore[no-untyped-def]
        seen["path"] = scope["path"]

    middleware = CourseSelectionPathCompatMiddleware(app)
    await middleware(
        {"type": "http", "path": "/api/course-selection/v1/courses/search", "raw_path": b""},
        None,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
    )

    assert seen["path"] == "/api/course-selection/v1/courses/search"
