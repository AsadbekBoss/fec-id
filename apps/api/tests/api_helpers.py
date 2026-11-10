"""Shared API-test helpers: app/client fixtures, users, login, tokens.

Fixtures `app` and `api` are registered in conftest.py (they need the `session` fixture).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import passwords
from app.core.config import get_settings
from app.db.session import get_session
from app.main import create_app
from app.models import AuditLog, User
from app.models.enums import UserRole

PASSWORD = "very-strong-pass-2026"
XRW = {"X-Requested-With": "XMLHttpRequest"}


def build_app(session: AsyncSession) -> FastAPI:
    application = create_app()

    async def _session() -> AsyncIterator[AsyncSession]:
        # Same unit-of-work semantics as production (commit / rollback), but inside the
        # test's outer transaction, so nothing leaks between tests.
        # First persist whatever the test itself created since the last request. Otherwise a
        # failing request would roll back the test's own fixtures together with its own work.
        await session.commit()
        try:
            yield session
            await session.commit()
        except BaseException:
            await session.rollback()
            raise

    application.dependency_overrides[get_session] = _session
    return application


async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://testserver"
    ) as c:
        yield c


@dataclass(frozen=True)
class Account:
    """Plain values captured at creation. The ORM object (`obj`) may expire after a request
    rolls back the shared test session; use `await session.refresh(acc.obj)` before reading it."""

    id: uuid.UUID
    username: str
    obj: User


async def make_user(
    session: AsyncSession,
    username: str | None = None,
    role: UserRole = UserRole.VIEWER,
    password: str = PASSWORD,
    **kw: object,
) -> Account:
    u = User(
        username=username or f"u_{uuid.uuid4().hex[:8]}",
        password_hash=await passwords.hash_password(password),
        role=role,
        **kw,
    )
    session.add(u)
    await session.flush()
    return Account(u.id, u.username, u)


async def login(api: httpx.AsyncClient, username: str, password: str = PASSWORD) -> httpx.Response:
    return await api.post("/api/v1/auth/login", json={"username": username, "password": password})


async def token_for(api: httpx.AsyncClient, user: Account) -> str:
    r = await login(api, user.username)
    assert r.status_code == 200, r.text
    return str(r.json()["access_token"])


def use_refresh_cookie(api: httpx.AsyncClient, value: str) -> None:
    api.cookies.clear()
    # No explicit domain: the stdlib cookie jar would treat "testserver" as a domain cookie
    # that does not match the dotless host, and silently not send it.
    api.cookies.set(get_settings().refresh_cookie_name, value)


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def audit_actions(session: AsyncSession) -> list[str]:
    return list((await session.execute(select(AuditLog.action).order_by(AuditLog.id))).scalars())


def oid(obj: object) -> object:
    """Primary key of an ORM object WITHOUT touching the database (works on expired objects:
    a failed request rolls back the shared session, which expires every loaded object)."""
    from sqlalchemy import inspect

    identity = inspect(obj).identity
    assert identity is not None
    return identity[0]
