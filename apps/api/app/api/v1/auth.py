"""/api/v1/auth: login, refresh, logout, current user, change password."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Cookie, Header, Request, Response, status
from fastapi.responses import JSONResponse

from app.api.deps import DbSession
from app.api.errors import AppError, problem, too_many_requests, unauthorized
from app.auth import service
from app.auth.client_ip import client_ip
from app.auth.dependencies import CurrentUser
from app.auth.rate_limit import SlidingWindowLimiter
from app.core.config import get_settings
from app.db import audit
from app.models import AuthSession, Branch
from app.models.enums import ActorType
from app.schemas.auth import (
    BranchRef,
    ChangePasswordRequest,
    LoginRequest,
    MeResponse,
    SessionInfo,
    TokenResponse,
    UserOut,
)

router = APIRouter(prefix="/auth", tags=["auth"])

NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}


def _limiter(request: Request, name: str) -> SlidingWindowLimiter:
    limiter: SlidingWindowLimiter = getattr(request.app.state, name)
    return limiter


def _set_refresh_cookie(response: Response, token: str, expires_at: datetime) -> None:
    s = get_settings()
    response.set_cookie(
        key=s.refresh_cookie_name,
        value=token,
        max_age=max(0, int((expires_at - datetime.now(UTC)).total_seconds())),
        httponly=True,
        secure=s.refresh_cookie_secure,
        samesite="strict",
        path=s.refresh_cookie_path,
    )


def _clear_refresh_cookie(response: Response) -> None:
    s = get_settings()
    response.delete_cookie(
        key=s.refresh_cookie_name,
        path=s.refresh_cookie_path,
        secure=s.refresh_cookie_secure,
        httponly=True,
        samesite="strict",
    )


def _token_response(issued: service.IssuedTokens) -> JSONResponse:
    body = TokenResponse(
        access_token=issued.access_token,
        expires_in=issued.expires_in,
        user=UserOut.model_validate(issued.user),
    )
    resp = JSONResponse(body.model_dump(mode="json"), headers=NO_STORE)
    _set_refresh_cookie(resp, issued.refresh_token, issued.session.expires_at)
    return resp


_INVALID_LOGIN = (
    401,
    "INVALID_CREDENTIALS",
    "Invalid credentials",
    "Username or password is incorrect.",
)


@router.post(
    "/login",
    response_model=TokenResponse,
    responses={401: {"description": "Invalid credentials"}, 422: {}, 429: {}},
    summary="Sign in with username and password",
)
async def login(body: LoginRequest, request: Request, db: DbSession) -> Response:
    ip = client_ip(request)
    allowed, retry = _limiter(request, "login_limiter").hit(ip or "unknown")
    if not allowed:  # cheap rejection before any hashing or DB work
        raise too_many_requests(retry)

    result = await service.login(
        db, body.username, body.password, ip, request.headers.get("user-agent")
    )
    if isinstance(result, service.Failure):
        # Returned (not raised) so the transaction commits the failure counter and audit row.
        if result.reason is service.FailureReason.RATE_LIMITED:
            err = too_many_requests(result.retry_after)
            return problem(err.status, err.code, err.title, err.detail, headers=err.headers)
        return problem(*_INVALID_LOGIN, headers=NO_STORE)
    return _token_response(result)


@router.post(
    "/refresh",
    response_model=TokenResponse,
    responses={401: {}, 403: {"description": "Missing X-Requested-With header"}},
    summary="Exchange the refresh cookie for a new access token (rotates the cookie)",
)
async def refresh(
    request: Request,
    db: DbSession,
    x_requested_with: Annotated[str | None, Header()] = None,
    refresh_cookie: Annotated[str | None, Cookie(alias=get_settings().refresh_cookie_name)] = None,
) -> Response:
    # CSRF defence in depth (cookie is also SameSite=Strict): browsers only send custom
    # headers cross-origin after a CORS preflight, which our CORS policy refuses.
    if not x_requested_with:
        raise AppError(403, "CSRF_CHECK_FAILED", "Forbidden", "X-Requested-With header required.")
    if not refresh_cookie or len(refresh_cookie) > 128:
        raise unauthorized("INVALID_REFRESH_TOKEN", "Refresh token missing.")

    ip = client_ip(request)
    result = await service.refresh(db, refresh_cookie, ip, request.headers.get("user-agent"))
    if isinstance(result, service.Failure):
        resp = problem(
            401,
            "INVALID_REFRESH_TOKEN",
            "Authentication required",
            "Session expired. Please sign in again.",
            headers=NO_STORE,
        )
        _clear_refresh_cookie(resp)
        return resp
    return _token_response(result)


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={401: {}},
    summary="End the current session",
)
async def logout(ctx: CurrentUser, request: Request, db: DbSession) -> Response:
    await service.revoke_session(db, ctx.session_id, "LOGOUT")
    await audit.record(
        db,
        action="AUTH_LOGOUT",
        actor_type=ActorType.USER,
        actor_user_id=ctx.user_id,
        entity_type="auth_session",
        entity_id=ctx.session_id,
        ip=client_ip(request),
    )
    resp = Response(status_code=status.HTTP_204_NO_CONTENT, headers=NO_STORE)
    _clear_refresh_cookie(resp)
    return resp


@router.get("/me", response_model=MeResponse, summary="Current user, permissions and session")
async def me(ctx: CurrentUser, db: DbSession, response: Response) -> MeResponse:
    sess = await db.get(AuthSession, ctx.session_id)
    if sess is None:  # cannot happen after get_current_user, but never assume
        raise unauthorized("SESSION_ENDED")
    own = await db.get(Branch, ctx.user.branch_id)
    response.headers.update(NO_STORE)
    return MeResponse(
        user=UserOut.model_validate(ctx.user),
        permissions=sorted(p.value for p in ctx.permissions),
        session=SessionInfo(id=sess.id, created_at=sess.created_at, expires_at=sess.expires_at),
        branch=BranchRef.model_validate(own, from_attributes=True),
        all_branches=ctx.all_branches,
    )


@router.post(
    "/password",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        401: {},
        422: {"description": "Wrong current password or weak new password"},
        429: {},
    },
    summary="Change own password (signs out all other sessions)",
)
async def change_password(
    body: ChangePasswordRequest, ctx: CurrentUser, request: Request, db: DbSession
) -> Response:
    allowed, retry = _limiter(request, "password_limiter").hit(str(ctx.user_id))
    if not allowed:
        raise too_many_requests(retry)
    problems = await service.change_password(
        db, ctx.user, ctx.session_id, body.current_password, body.new_password, client_ip(request)
    )
    if problems:
        raise AppError(
            422,
            "VALIDATION_ERROR",
            "Validation failed",
            "Password change rejected.",
            errors=[
                {
                    "field": "new_password" if "Current" not in p else "current_password",
                    "code": "password_policy",
                    "message": p,
                }
                for p in problems
            ],
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
