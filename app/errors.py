import logging
from uuid import uuid4

import psycopg
from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from app.schemas.contracts import ApiError


class ApiException(Exception):
    def __init__(self, status, code, message, retryable=False, retry_after_seconds=None, fields=None):
        self.status = status
        self.code = code
        self.message = message
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds
        self.fields = fields or []


ERROR_RESPONSES = {status: {"model": ApiError} for status in (401, 403, 404, 409, 422, 429, 503)}


def install_error_handlers(app):
    @app.middleware("http")
    async def request_id(request: Request, call_next):
        request.state.request_id = "req-" + uuid4().hex
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    def response(request, status, code, message, retryable=False, fields=None, headers=None, retry_after_seconds=None):
        return JSONResponse(status_code=status, headers={"Cache-Control": "no-store", **(headers or {})}, content={
            "success": False, "message": message, "data": None,
            "error": {"code": code, "retryable": retryable,
                      "retry_after_seconds": retry_after_seconds, "fields": fields or []},
            "request_id": getattr(request.state, "request_id", "req-" + uuid4().hex),
        })

    @app.exception_handler(ApiException)
    async def api_exception(request, exc):
        headers = {"Retry-After": str(exc.retry_after_seconds)} if exc.retry_after_seconds is not None else None
        return response(request, exc.status, exc.code, exc.message, exc.retryable,
                        fields=exc.fields, headers=headers, retry_after_seconds=exc.retry_after_seconds)

    @app.exception_handler(RequestValidationError)
    async def validation_exception(request, exc):
        fields = []
        for error in exc.errors():
            location = error["loc"]
            if location and location[0] in ("body", "query", "path"):
                location = location[1:]
            fields.append({"field": ".".join(map(str, location)) or "request", "message": error["msg"]})
        return response(request, 422, "VALIDATION_ERROR", "Periksa parameter atau data yang dikirim.", fields=fields)

    @app.exception_handler(HTTPException)
    async def http_exception(request, exc):
        codes = {401: "UNAUTHORIZED", 403: "FORBIDDEN", 404: "NOT_FOUND", 409: "CONFLICT",
                 422: "VALIDATION_ERROR", 429: "RATE_LIMITED", 503: "SOURCE_UNAVAILABLE"}
        return response(request, exc.status_code, codes.get(exc.status_code, "HTTP_ERROR"),
                        str(exc.detail), exc.status_code in (429, 503), headers=exc.headers)

    @app.exception_handler(psycopg.Error)
    async def database_exception(request, exc):
        logging.exception("Database request failed", exc_info=exc)
        return response(request, 503, "DATABASE_UNAVAILABLE",
                        "Database belum dapat diakses. Periksa koneksi dan coba kembali.", True)
