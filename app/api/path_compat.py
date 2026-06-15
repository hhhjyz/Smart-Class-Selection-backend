"""Gateway path compatibility helpers."""

from __future__ import annotations

from starlette.types import ASGIApp, Receive, Scope, Send


class CourseSelectionPathCompatMiddleware:
    """Accept the unified gateway prefix without changing existing handlers.

    The service historically exposes ``/api/course-selection/v1``. The unified
    STSS gateway routes course selection traffic as ``/api/v1/course-selection``.
    This middleware rewrites the incoming ASGI path internally so both contracts
    remain valid.
    """

    _GATEWAY_PREFIX = "/api/v1/course-selection"
    _SERVICE_PREFIX = "/api/course-selection/v1"

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if path == self._GATEWAY_PREFIX or path.startswith(f"{self._GATEWAY_PREFIX}/"):
            rewritten = f"{self._SERVICE_PREFIX}{path.removeprefix(self._GATEWAY_PREFIX)}"
            scope = {
                **scope,
                "path": rewritten,
                "raw_path": rewritten.encode("utf-8"),
            }

        await self.app(scope, receive, send)
