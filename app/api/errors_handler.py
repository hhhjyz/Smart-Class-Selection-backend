"""统一异常 → 响应壳映射。

业务层只抛 DomainError；接入层在此把它渲染成 { code, message, data, trace_id }
并设置正确的 HTTP 状态码。Pydantic 校验错误统一映射为 30001。
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.errors import ERR_BAD_REQUEST, DomainError, default_message, http_status_for
from app.core.logging import current_request_id

logger = logging.getLogger(__name__)


def _envelope(code: int, message: str, data: object | None) -> dict[str, object]:
    return {"code": code, "message": message, "data": data, "trace_id": current_request_id()}


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def _domain_error(_request: Request, exc: DomainError) -> JSONResponse:
        return JSONResponse(
            status_code=http_status_for(exc.code),
            content=_envelope(exc.code, exc.message, exc.data),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=400,
            content=_envelope(ERR_BAD_REQUEST, default_message(ERR_BAD_REQUEST), {"errors": exc.errors()}),
        )

    @app.exception_handler(Exception)
    async def _unhandled(_request: Request, exc: Exception) -> JSONResponse:
        logger.exception("未处理异常", extra={"event": "error.unhandled"})
        return JSONResponse(status_code=500, content=_envelope(500, "内部错误", None))
