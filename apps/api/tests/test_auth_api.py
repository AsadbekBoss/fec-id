"""End-to-end auth tests through the real FastAPI app and a real PostgreSQL database."""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi import FastAPI
from fastapi.routing import iter_route_contexts
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import tokens
from app.auth.dependencies import get_current_user
from app.auth.rate_limit import SlidingWindowLimiter
from app.core.config import get_settings
from app.models import AuditLog, AuthSession, RefreshToken, User
from app.models.enums import UserRole
from tests.api_helpers import (
    PASSWORD,
    XRW,
    audit_actions,
    bearer,
    login,
    make_user,
    token_for,
    use_refresh_cookie,
)
from tests.conftest import requires_db

pytestmark = requires_db

# --- login --------------------------------------------------------------------------------


async def test_login_success(api: httpx.AsyncClient, session: AsyncSession) -> None:
    user = await make_user(session, role=UserRole.ADMIN)
    r = await login(api, user.username)
    assert r.status_code == 200
    body = r.json()
    assert body["token_type"] == "Bearer"
    assert body["expires_in"] == get_settings().access_token_minutes * 60
    assert body["user"]["username"] == user.username and body["user"]["role"] == "ADMIN"
    assert "password" not in r.text and "hash" not in r.text
    assert r.headers["cache-control"] == "no-store"

    cookie = r.headers["set-cookie"].lower()
    for attr in ("httponly", "secure", "samesite=strict", "path=/api/v1/auth"):
        assert attr in cookie, cookie
    raw_refresh = r.cookies[get_settings().refresh_cookie_name]

    stored = (await session.execute(select(RefreshToken))).scalars().all()
    assert len(stored) == 1
    assert stored[0].token_hash == tokens.hash_refresh_token(raw_refresh)
    assert raw_refresh.encode() not in stored[0].token_hash  # only the hash is stored
    await session.refresh(user.obj)
    assert user.obj.last_login_at is not None
    assert "AUTH_LOGIN_SUCCEEDED" in await audit_actions(session)


async def test_username_is_case_insensitive(api: httpx.AsyncClient, session: AsyncSession) -> None:
    await make_user(session, username="Admin.User")
    assert (await login(api, "admin.user")).status_code == 200


async def test_wrong_password_and_unknown_user_look_identical(
    api: httpx.AsyncClient, session: AsyncSession
) -> None:
    user = await make_user(session)
    wrong = await login(api, user.username, "wrong-password-123")
    unknown = await login(api, "no-such-user", "wrong-password-123")
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()
    assert wrong.json()["code"] == "INVALID_CREDENTIALS"
    assert wrong.headers["content-type"].startswith("application/problem+json")

    await session.refresh(user.obj)
    assert user.obj.failed_login_count == 1
    failed = (
        (await session.execute(select(AuditLog).where(AuditLog.action == "AUTH_LOGIN_FAILED")))
        .scalars()
        .all()
    )
    assert len(failed) == 2
    # The unknown username is not recorded anywhere.
    assert all(
        "no-such-user" not in str(a.changes) and a.entity_id != "no-such-user" for a in failed
    )


async def test_inactive_user_cannot_login(api: httpx.AsyncClient, session: AsyncSession) -> None:
    user = await make_user(session, is_active=False)
    r = await login(api, user.username)
    assert r.status_code == 401 and r.json()["code"] == "INVALID_CREDENTIALS"


async def test_account_lockout(
    api: httpx.AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "login_max_failures", 3)
    user = await make_user(session)
    for _ in range(3):
        assert (await login(api, user.username, "bad-password-000")).status_code == 401
    locked = await login(api, user.username)  # correct password, but locked
    assert locked.status_code == 429
    assert locked.json()["code"] == "TOO_MANY_ATTEMPTS"
    assert int(locked.headers["retry-after"]) > 0
    assert "AUTH_LOGIN_BLOCKED" in await audit_actions(session)

    # When the lock expires, the correct password works and counters reset.
    await session.execute(
        update(User)
        .where(User.id == user.id)
        .values(locked_until=datetime.now(UTC) - timedelta(seconds=1))
    )
    assert (await login(api, user.username)).status_code == 200
    await session.refresh(user.obj)
    assert user.obj.failed_login_count == 0 and user.obj.locked_until is None


async def test_ip_failure_limit(
    api: httpx.AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "login_ip_max_failures", 3)
    user = await make_user(session)
    for i in range(3):  # spread over different (unknown) accounts: no single account locks
        await login(api, f"ghost{i}", "bad-password-000")
    r = await login(api, user.username)
    assert r.status_code == 429 and "retry-after" in r.headers


async def test_in_memory_flood_limit(
    api: httpx.AsyncClient, app: FastAPI, session: AsyncSession
) -> None:
    app.state.login_limiter = SlidingWindowLimiter(limit=2, window_seconds=60)
    user = await make_user(session)
    assert (await login(api, user.username)).status_code == 200
    assert (await login(api, user.username)).status_code == 200
    r = await login(api, user.username)
    assert r.status_code == 429


@pytest.mark.parametrize(
    "payload",
    [
        {"username": "someone"},
        {"password": "x"},
        {"username": "someone", "password": "P" * 200},
        {"username": "someone", "password": "pw", "role": "SUPER_ADMIN"},
        {"username": "", "password": "pw"},
        {"username": ["a"], "password": "pw"},
    ],
    ids=["no-password", "no-username", "too-long", "extra-field", "empty-username", "wrong-type"],
)
async def test_login_input_validation(api: httpx.AsyncClient, payload: dict[str, object]) -> None:
    r = await api.post("/api/v1/auth/login", json=payload)
    assert r.status_code == 422
    body = r.json()
    assert body["code"] == "VALIDATION_ERROR" and body["errors"]
    assert "P" * 50 not in r.text  # submitted values are never echoed


async def test_login_rejects_non_json(api: httpx.AsyncClient) -> None:
    r = await api.post(
        "/api/v1/auth/login",
        content=b"username=a&password=b",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert r.status_code == 422


# --- token validation on protected endpoints ---------------------------------------------


async def test_me(api: httpx.AsyncClient, session: AsyncSession) -> None:
    user = await make_user(session, role=UserRole.VIEWER)
    r = await api.get("/api/v1/auth/me", headers=bearer(await token_for(api, user)))
    assert r.status_code == 200
    body = r.json()
    assert body["user"]["id"] == str(user.id)
    assert "employees:read" in body["permissions"] and "users:write" not in body["permissions"]
    assert body["session"]["expires_at"]


@pytest.mark.parametrize(
    ("headers", "code"),
    [
        ({}, "NOT_AUTHENTICATED"),
        ({"Authorization": "Basic dXNlcjpwYXNz"}, "NOT_AUTHENTICATED"),
        ({"Authorization": "Bearer"}, "NOT_AUTHENTICATED"),
        ({"Authorization": "Bearer abc.def.ghi"}, "INVALID_TOKEN"),
    ],
)
async def test_me_rejects_bad_credentials(
    api: httpx.AsyncClient, headers: dict[str, str], code: str
) -> None:
    r = await api.get("/api/v1/auth/me", headers=headers)
    assert r.status_code == 401
    assert r.json()["code"] == code
    assert r.headers["www-authenticate"].startswith("Bearer")


async def test_expired_token_rejected(api: httpx.AsyncClient, session: AsyncSession) -> None:
    user = await make_user(session)
    await token_for(api, user)
    sess = (await session.execute(select(AuthSession))).scalars().one()
    old, _ = tokens.issue_access_token(
        user.id, sess.id, "VIEWER", now=datetime.now(UTC) - timedelta(hours=2)
    )
    r = await api.get("/api/v1/auth/me", headers=bearer(old))
    assert r.status_code == 401 and r.json()["code"] == "TOKEN_EXPIRED"


async def test_token_for_unknown_session_rejected(
    api: httpx.AsyncClient, session: AsyncSession
) -> None:
    user = await make_user(session)
    forged, _ = tokens.issue_access_token(user.id, uuid.uuid4(), "SUPER_ADMIN")
    r = await api.get("/api/v1/auth/me", headers=bearer(forged))
    assert r.status_code == 401 and r.json()["code"] == "SESSION_ENDED"


async def test_role_claim_in_token_is_not_trusted(
    api: httpx.AsyncClient, session: AsyncSession
) -> None:
    """A token claiming SUPER_ADMIN for a VIEWER's real session grants nothing extra."""
    user = await make_user(session, role=UserRole.VIEWER)
    await token_for(api, user)
    sess = (await session.execute(select(AuthSession))).scalars().one()
    t, _ = tokens.issue_access_token(user.id, sess.id, "SUPER_ADMIN")
    assert (await api.get("/api/v1/users", headers=bearer(t))).status_code == 403


async def test_role_change_applies_immediately(
    api: httpx.AsyncClient, session: AsyncSession
) -> None:
    user = await make_user(session, role=UserRole.VIEWER)
    t = await token_for(api, user)
    assert (await api.get("/api/v1/users", headers=bearer(t))).status_code == 403
    await session.execute(update(User).where(User.id == user.id).values(role=UserRole.ADMIN))
    session.expire_all()
    assert (await api.get("/api/v1/users", headers=bearer(t))).status_code == 200


async def test_deactivation_ends_sessions_immediately(
    api: httpx.AsyncClient, session: AsyncSession
) -> None:
    sa = await make_user(session, role=UserRole.SUPER_ADMIN)
    victim = await make_user(session, role=UserRole.ADMIN)
    sa_t, victim_t = await token_for(api, sa), await token_for(api, victim)
    r = await api.patch(
        f"/api/v1/users/{victim.id}", json={"is_active": False}, headers=bearer(sa_t)
    )
    assert r.status_code == 200
    r = await api.get("/api/v1/auth/me", headers=bearer(victim_t))
    assert r.status_code == 401 and r.json()["code"] == "SESSION_ENDED"


# --- refresh & logout ---------------------------------------------------------------------


async def test_refresh_rotates_and_detects_reuse(
    api: httpx.AsyncClient, session: AsyncSession
) -> None:
    name = get_settings().refresh_cookie_name
    user = await make_user(session)
    first = await login(api, user.username)
    old_refresh = first.cookies[name]

    assert (await api.post("/api/v1/auth/refresh")).status_code == 403  # CSRF header missing

    r = await api.post("/api/v1/auth/refresh", headers=XRW)
    assert r.status_code == 200
    new_access, new_refresh = r.json()["access_token"], r.cookies[name]
    assert new_refresh != old_refresh
    assert (await api.get("/api/v1/auth/me", headers=bearer(new_access))).status_code == 200

    # Attacker replays the old (already rotated) refresh token.
    api.cookies.clear()
    use_refresh_cookie(api, old_refresh)
    stolen = await api.post("/api/v1/auth/refresh", headers=XRW)
    assert stolen.status_code == 401 and stolen.json()["code"] == "INVALID_REFRESH_TOKEN"
    # The whole session is now revoked: the legitimate tokens stop working too.
    assert (await api.get("/api/v1/auth/me", headers=bearer(new_access))).status_code == 401
    use_refresh_cookie(api, new_refresh)
    again = await api.post("/api/v1/auth/refresh", headers=XRW)
    assert again.status_code == 401
    sess = (await session.execute(select(AuthSession))).scalars().one()
    await session.refresh(sess)
    assert sess.revoke_reason == "TOKEN_REUSE"
    assert "AUTH_REFRESH_REUSE_DETECTED" in await audit_actions(session)


async def test_refresh_without_cookie_or_with_garbage(api: httpx.AsyncClient) -> None:
    assert (await api.post("/api/v1/auth/refresh", headers=XRW)).status_code == 401
    use_refresh_cookie(api, "garbage")
    r = await api.post("/api/v1/auth/refresh", headers=XRW)
    assert r.status_code == 401 and r.json()["code"] == "INVALID_REFRESH_TOKEN"


async def test_refresh_after_session_expiry(api: httpx.AsyncClient, session: AsyncSession) -> None:
    user = await make_user(session)
    await login(api, user.username)
    await session.execute(
        update(AuthSession).values(
            created_at=datetime.now(UTC) - timedelta(days=10),
            expires_at=datetime.now(UTC) - timedelta(seconds=1),
        )
    )
    assert (await api.post("/api/v1/auth/refresh", headers=XRW)).status_code == 401


async def test_logout(api: httpx.AsyncClient, session: AsyncSession) -> None:
    user = await make_user(session)
    t = await token_for(api, user)
    r = await api.post("/api/v1/auth/logout", headers=bearer(t))
    assert r.status_code == 204
    assert "max-age=0" in r.headers["set-cookie"].lower()
    assert (await api.get("/api/v1/auth/me", headers=bearer(t))).status_code == 401
    assert (await api.post("/api/v1/auth/refresh", headers=XRW)).status_code == 401
    assert "AUTH_LOGOUT" in await audit_actions(session)
    assert (await api.post("/api/v1/auth/logout")).status_code == 401


async def test_sessions_are_independent(api: httpx.AsyncClient, session: AsyncSession) -> None:
    user = await make_user(session)
    t1, t2 = await token_for(api, user), await token_for(api, user)
    await api.post("/api/v1/auth/logout", headers=bearer(t1))
    assert (await api.get("/api/v1/auth/me", headers=bearer(t2))).status_code == 200


# --- password change ----------------------------------------------------------------------


async def test_change_password(api: httpx.AsyncClient, session: AsyncSession) -> None:
    user = await make_user(session)
    current, other = await token_for(api, user), await token_for(api, user)
    url = "/api/v1/auth/password"

    r = await api.post(
        url,
        headers=bearer(current),
        json={"current_password": "wrong-one-123", "new_password": "brand-new-pass-77"},
    )
    assert r.status_code == 422 and r.json()["errors"][0]["field"] == "current_password"
    r = await api.post(
        url, headers=bearer(current), json={"current_password": PASSWORD, "new_password": "short"}
    )
    assert r.status_code == 422
    r = await api.post(
        url,
        headers=bearer(current),
        json={"current_password": PASSWORD, "new_password": "brand-new-pass-77"},
    )
    assert r.status_code == 204

    assert (await api.get("/api/v1/auth/me", headers=bearer(current))).status_code == 200
    assert (await api.get("/api/v1/auth/me", headers=bearer(other))).status_code == 401
    assert (await login(api, user.username)).status_code == 401
    assert (await login(api, user.username, "brand-new-pass-77")).status_code == 200


# --- RBAC ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path", "expected"),
    [
        ("GET", "/api/v1/users", {"SUPER_ADMIN": 200, "ADMIN": 200, "VIEWER": 403}),
        ("POST", "/api/v1/users", {"SUPER_ADMIN": 201, "ADMIN": 403, "VIEWER": 403}),
        ("GET", "/api/v1/settings", {"SUPER_ADMIN": 200, "ADMIN": 200, "VIEWER": 403}),
        ("GET", "/api/v1/auth/me", {"SUPER_ADMIN": 200, "ADMIN": 200, "VIEWER": 200}),
    ],
)
async def test_rbac_matrix(
    api: httpx.AsyncClient,
    session: AsyncSession,
    method: str,
    path: str,
    expected: dict[str, int],
) -> None:
    for role_name, status_code in expected.items():
        user = await make_user(session, role=UserRole(role_name))
        body = None
        if method == "POST":
            body = {
                "username": f"new_{uuid.uuid4().hex[:6]}",
                "password": "another-strong-pw-1",
                "role": "VIEWER",
            }
        r = await api.request(method, path, json=body, headers=bearer(await token_for(api, user)))
        assert r.status_code == status_code, (role_name, r.text)
        if status_code == 403:
            assert r.json()["code"] == "FORBIDDEN"


PUBLIC_ROUTES = {
    ("POST", "/api/v1/auth/login"),
    ("POST", "/api/v1/auth/refresh"),  # authenticated by the refresh cookie instead
    ("GET", "/api/v1/system/version"),
}


def _api_routes(app: FastAPI) -> Iterator[tuple[str, str, object]]:
    for ctx in iter_route_contexts(app.router.routes):
        if ctx.path.startswith("/api/v1") and ctx.dependant is not None:
            for method in ctx.methods:
                if (method, ctx.path) not in PUBLIC_ROUTES:
                    yield method, ctx.path, ctx.dependant


def _depends_on(dependant: object, target: object) -> bool:
    deps = getattr(dependant, "dependencies", [])
    return any(getattr(d, "call", None) is target or _depends_on(d, target) for d in deps)


def test_every_private_route_requires_authentication(app: FastAPI) -> None:
    """Guard rail: a new /api/v1 endpoint without auth fails this test."""
    routes = list(_api_routes(app))
    assert len(routes) >= 10
    for method, path, dependant in routes:
        assert _depends_on(dependant, get_current_user), f"{method} {path} is unprotected"


async def test_private_routes_return_401_without_token(
    api: httpx.AsyncClient, app: FastAPI
) -> None:
    routes = list(_api_routes(app))
    assert routes
    for method, template, _ in routes:
        path = re.sub(r"{[a-z_]+}", str(uuid.uuid4()), template)
        r = await api.request(method, path, json={})
        assert r.status_code == 401, f"{method} {path} -> {r.status_code}"


# --- user administration ------------------------------------------------------------------


async def test_create_user_validation_and_audit(
    api: httpx.AsyncClient, session: AsyncSession
) -> None:
    sa = await make_user(session, role=UserRole.SUPER_ADMIN)
    h = bearer(await token_for(api, sa))

    weak = await api.post(
        "/api/v1/users",
        headers=h,
        json={"username": "newbie", "password": "123456", "role": "VIEWER"},
    )
    assert weak.status_code == 422 and weak.json()["errors"][0]["code"] == "password_policy"

    bad_role = await api.post(
        "/api/v1/users",
        headers=h,
        json={"username": "newbie", "password": "fine-password-42", "role": "GOD"},
    )
    assert bad_role.status_code == 422

    ok = await api.post(
        "/api/v1/users",
        headers=h,
        json={"username": "newbie", "password": "fine-password-42", "role": "ADMIN"},
    )
    assert ok.status_code == 201
    assert "password" not in ok.text.replace("password_changed_at", "")

    dup = await api.post(
        "/api/v1/users",
        headers=h,
        json={"username": "NEWBIE", "password": "fine-password-42", "role": "ADMIN"},
    )
    assert dup.status_code == 409 and dup.json()["code"] == "USER_EXISTS"

    entry = (
        await session.execute(select(AuditLog).where(AuditLog.action == "USER_CREATED"))
    ).scalar_one()
    assert entry.actor_user_id == sa.id and "fine-password" not in str(entry.changes)
    assert (await login(api, "newbie", "fine-password-42")).status_code == 200


async def test_last_super_admin_is_protected(api: httpx.AsyncClient, session: AsyncSession) -> None:
    sa = await make_user(session, role=UserRole.SUPER_ADMIN)
    h = bearer(await token_for(api, sa))
    r = await api.patch(f"/api/v1/users/{sa.id}", headers=h, json={"role": "VIEWER"})
    assert r.status_code == 409 and r.json()["code"] == "LAST_SUPER_ADMIN"
    r = await api.patch(f"/api/v1/users/{sa.id}", headers=h, json={"is_active": False})
    assert r.status_code == 409

    await make_user(session, role=UserRole.SUPER_ADMIN)  # a second one exists now
    r = await api.patch(f"/api/v1/users/{sa.id}", headers=h, json={"role": "ADMIN"})
    assert r.status_code == 200 and r.json()["role"] == "ADMIN"


async def test_admin_password_reset_and_unlock(
    api: httpx.AsyncClient, session: AsyncSession
) -> None:
    sa = await make_user(session, role=UserRole.SUPER_ADMIN)
    target = await make_user(
        session, failed_login_count=3, locked_until=datetime.now(UTC) + timedelta(minutes=10)
    )
    h = bearer(await token_for(api, sa))
    assert (await api.post(f"/api/v1/users/{target.id}/unlock", headers=h)).status_code == 204
    t = await token_for(api, target)
    r = await api.post(
        f"/api/v1/users/{target.id}/password", headers=h, json={"new_password": "reset-by-admin-99"}
    )
    assert r.status_code == 204
    assert (await api.get("/api/v1/auth/me", headers=bearer(t))).status_code == 401
    assert (await login(api, target.username, "reset-by-admin-99")).status_code == 200


async def test_user_not_found_and_bad_ids(api: httpx.AsyncClient, session: AsyncSession) -> None:
    sa = await make_user(session, role=UserRole.SUPER_ADMIN)
    h = bearer(await token_for(api, sa))
    assert (await api.get(f"/api/v1/users/{uuid.uuid4()}", headers=h)).status_code == 404
    assert (await api.get("/api/v1/users/not-a-uuid", headers=h)).status_code == 422
    assert (await api.get("/api/v1/users?page_size=1000", headers=h)).status_code == 422


async def test_list_users_filters(api: httpx.AsyncClient, session: AsyncSession) -> None:
    sa = await make_user(session, role=UserRole.SUPER_ADMIN)
    await make_user(session, username="viewer_one", role=UserRole.VIEWER)
    h = bearer(await token_for(api, sa))
    r = await api.get("/api/v1/users?role=VIEWER&q=viewer_", headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1 and body["items"][0]["username"] == "viewer_one"
    assert "password_hash" not in r.text


async def test_security_headers(api: httpx.AsyncClient) -> None:
    r = await api.get("/api/v1/system/version")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["cache-control"] == "no-store"


async def test_failed_login_is_committed_even_though_request_failed(
    api: httpx.AsyncClient, session: AsyncSession
) -> None:
    """Regression guard for the transaction design: failure counters must persist."""
    user = await make_user(session)
    await login(api, user.username, "nope-nope-nope")
    n = (
        await session.execute(
            select(func.count()).select_from(AuditLog).where(AuditLog.action == "AUTH_LOGIN_FAILED")
        )
    ).scalar_one()
    assert n == 1
