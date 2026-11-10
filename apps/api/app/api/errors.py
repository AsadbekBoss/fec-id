"""RFC 9457 Problem Details responses (API_CONTRACTS §1.1).

Validation errors never echo the submitted values (they could contain passwords).
Unexpected exceptions return a generic 500 with no internal details.
"""

from __future__ import annotations

import logging
from typing import Any, cast

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger(__name__)

PROBLEM_JSON = "application/problem+json"
_ERROR_TYPE_BASE = "https://errors.attendance.local/"


class AppError(Exception):
    """Raise from anywhere in request handling to return a Problem Details response."""

    def __init__(
        self,
        status: int,
        code: str,
        title: str,
        detail: str | None = None,
        *,
        headers: dict[str, str] | None = None,
        errors: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(f"{status} {code}")
        self.status = status
        self.code = code
        self.title = title
        self.detail = detail
        self.headers = headers
        self.errors = errors


def problem(
    status: int,
    code: str,
    title: str,
    detail: str | None = None,
    *,
    headers: dict[str, str] | None = None,
    errors: list[dict[str, Any]] | None = None,
) -> JSONResponse:
    body: dict[str, Any] = {
        "type": _ERROR_TYPE_BASE + code.lower().replace("_", "-"),
        "title": title,
        "status": status,
        "code": code,
    }
    if detail:
        body["detail"] = detail
    if errors:
        body["errors"] = errors
    return JSONResponse(body, status_code=status, headers=headers, media_type=PROBLEM_JSON)


def from_error(err: AppError) -> JSONResponse:
    return problem(
        err.status, err.code, err.title, err.detail, headers=err.headers, errors=err.errors
    )


# --- common errors ----------------------------------------------------------------------------

BEARER_CHALLENGE = {"WWW-Authenticate": 'Bearer realm="attendance"'}


def unauthorized(code: str = "NOT_AUTHENTICATED", detail: str | None = None) -> AppError:
    return AppError(
        401,
        code,
        "Authentication required",
        detail,
        headers={**BEARER_CHALLENGE, "Cache-Control": "no-store"},
    )


def forbidden(detail: str = "You do not have permission to perform this action.") -> AppError:
    return AppError(403, "FORBIDDEN", "Forbidden", detail)


def not_found(what: str = "Resource") -> AppError:
    return AppError(404, "NOT_FOUND", "Not found", f"{what} not found.")


def conflict(code: str, detail: str) -> AppError:
    return AppError(409, code, "Conflict", detail)


def too_many_requests(retry_after_seconds: int, detail: str | None = None) -> AppError:
    return AppError(
        429,
        "TOO_MANY_ATTEMPTS",
        "Too many attempts",
        detail or "Too many attempts. Try again later.",
        headers={"Retry-After": str(max(1, retry_after_seconds))},
    )


def validation_error(field: str, code: str, message: str) -> AppError:
    return AppError(
        422,
        "VALIDATION_ERROR",
        "Validation failed",
        "One or more fields are invalid.",
        errors=[{"field": field, "code": code, "message": message}],
    )


# --- handlers ---------------------------------------------------------------------------------


def _field(loc: tuple[Any, ...]) -> str:
    parts = [str(p) for p in loc if p not in ("body", "query", "path", "header", "cookie")]
    return ".".join(parts) or "request"


async def _app_error_handler(_: Request, exc: Exception) -> JSONResponse:
    exc = cast(AppError, exc)
    return from_error(exc)


async def _validation_handler(_: Request, exc: Exception) -> JSONResponse:
    exc = cast(RequestValidationError, exc)
    errors = [
        {"field": _field(tuple(e.get("loc", ()))), "code": e.get("type"), "message": e.get("msg")}
        for e in exc.errors()
    ]  # NOTE: 'input' and 'ctx' are deliberately dropped
    return problem(
        422,
        "VALIDATION_ERROR",
        "Validation failed",
        "One or more fields are invalid.",
        errors=errors,
    )


async def _http_error_handler(_: Request, exc: Exception) -> JSONResponse:
    exc = cast(StarletteHTTPException, exc)
    codes = {
        404: "NOT_FOUND",
        405: "METHOD_NOT_ALLOWED",
        401: "NOT_AUTHENTICATED",
        403: "FORBIDDEN",
    }
    code = codes.get(exc.status_code, "HTTP_ERROR")
    return problem(
        exc.status_code,
        code,
        code.replace("_", " ").capitalize(),
        headers=getattr(exc, "headers", None),
    )


async def _unhandled_handler(_: Request, exc: Exception) -> JSONResponse:
    log.exception("Unhandled error: %s", type(exc).__name__)
    return problem(500, "INTERNAL_ERROR", "Internal server error")


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _app_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_handler)
    app.add_exception_handler(StarletteHTTPException, _http_error_handler)
    app.add_exception_handler(Exception, _unhandled_handler)
