"""结构化日志配置与请求上下文中间件。

日志字段约定：request_id、user_id（已知时）、offering_id（相关时）、
event（动作动词，如 enroll.create / waitroom.admit）。对应《06 代码规范》。
"""

from __future__ import annotations

import logging
from contextvars import ContextVar

from rich.logging import RichHandler
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.core.config import get_settings

# 跨协程透传 request_id / user_id，供日志 filter 注入
_request_id: ContextVar[str] = ContextVar("request_id", default="-")
_user_id: ContextVar[str] = ContextVar("user_id", default="-")


def current_request_id() -> str:
    return _request_id.get()


class _ContextFilter(logging.Filter):
    """把 ContextVar 中的上下文注入每条 LogRecord。"""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _request_id.get()
        record.user_id = _user_id.get()
        return True


def setup_logging() -> None:
    """初始化根 logger。幂等，可重复调用。"""
    settings = get_settings()
    handler = RichHandler(rich_tracebacks=True, show_path=False)
    handler.addFilter(_ContextFilter())
    handler.setFormatter(logging.Formatter("%(message)s | rid=%(request_id)s uid=%(user_id)s"))
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(settings.log_level.upper())


class RequestContextMiddleware(BaseHTTPMiddleware):
    """从请求头取 X-Request-ID / X-User-ID 写入 ContextVar，并回显 trace_id。"""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        settings = get_settings()
        rid = request.headers.get(settings.request_id_header, "-")
        uid = request.headers.get(settings.jwt_user_header, "-")
        rid_token = _request_id.set(rid)
        uid_token = _user_id.set(uid)
        try:
            response: Response = await call_next(request)
            response.headers[settings.request_id_header] = rid
            return response
        finally:
            _request_id.reset(rid_token)
            _user_id.reset(uid_token)
