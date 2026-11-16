"""에러 응답 형식: {"error": {"code": "...", "message": "..."}} (PRD §7).

예상하지 못한 예외도 같은 형식(500 internal_error)으로 답하고, 원인은 응답에 싣지 않고 로그에 남긴다.
"""

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger(__name__)


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, extra: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.status, self.code, self.message, self.extra = status, code, message, extra or {}


def _body(code: str, message: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"error": {"code": code, "message": message}, **(extra or {})}


def install(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def api_error(_: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(status_code=exc.status, content=_body(exc.code, exc.message, exc.extra))

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        first = exc.errors()[0] if exc.errors() else {}
        where = ".".join(str(p) for p in first.get("loc", ()))
        return JSONResponse(
            status_code=422, content=_body("invalid_request", f"{where}: {first.get('msg', '')}")
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {401: "unauthorized", 403: "forbidden", 404: "not_found", 405: "method_not_allowed"}.get(
            exc.status_code, "http_error"
        )
        return JSONResponse(status_code=exc.status_code, content=_body(code, str(exc.detail)))

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        log.exception("처리하지 못한 예외: %s %s", request.method, request.url.path, exc_info=exc)
        return JSONResponse(
            status_code=500,
            content=_body("internal_error", "서버에서 문제가 생겼어요. 잠시 뒤 다시 시도해 주세요"),
        )
