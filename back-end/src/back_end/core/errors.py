from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from back_end.core.logging import get_logger

log = get_logger(__name__)


class AppError(Exception):
    status_code = 400
    code = "bad_request"

    def __init__(self, message: str, *, code: str | None = None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code


class BadRequest(AppError):
    status_code = 400
    code = "bad_request"


class NotFound(AppError):
    status_code = 404
    code = "not_found"

    def __init__(self, what: str):
        super().__init__(f"{what} not found")


class Forbidden(AppError):
    status_code = 403
    code = "forbidden"


class Unauthorized(AppError):
    status_code = 401
    code = "unauthorized"


class Conflict(AppError):
    status_code = 409
    code = "conflict"


class Unprocessable(AppError):
    status_code = 422
    code = "unprocessable"


class PayloadTooLarge(AppError):
    status_code = 413
    code = "payload_too_large"


class UpstreamUnavailable(AppError):
    status_code = 503
    code = "upstream_unavailable"


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        headers = {"WWW-Authenticate": "Bearer"} if exc.status_code == 401 else None
        return JSONResponse({"detail": exc.message, "code": exc.code}, status_code=exc.status_code, headers=headers)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        errors = exc.errors()
        first = errors[0] if errors else {}
        field = ".".join(str(p) for p in first.get("loc", []) if p not in ("body", "query"))
        message = first.get("msg", "Invalid request")
        detail = f"{field}: {message}" if field else message
        return JSONResponse(
            {
                "detail": detail,
                "code": "validation_error",
                "errors": [{"loc": e.get("loc"), "msg": e.get("msg"), "type": e.get("type")} for e in errors],
            },
            status_code=422,
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled_error", path=request.url.path)
        return JSONResponse({"detail": "Internal server error", "code": "internal"}, status_code=500)
