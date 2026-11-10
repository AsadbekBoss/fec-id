"""Async SQLAlchemy engine, sessions and transaction handling.

The engine is created lazily, so the API process starts even if PostgreSQL is down
(`/ready` reports database availability).

Transaction rules (DEVELOPMENT_RULES §4):
* One request = one transaction. `get_session` (FastAPI dependency) commits when the request
  handler returns normally and rolls back on any exception.
* Background jobs use `session_scope()` (same semantics).
* `savepoint(session)` wraps a sub-step that may fail without aborting the outer transaction.
* Business code must never call `commit()` itself; it uses `flush()` when it needs generated
  values.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def build_engine(url: URL | str | None = None) -> AsyncEngine:
    s = get_settings()
    return create_async_engine(
        url or s.database_url,
        pool_size=s.db_pool_size,
        max_overflow=s.db_max_overflow,
        pool_pre_ping=True,
        connect_args={
            "timeout": s.db_connect_timeout_seconds,
            "server_settings": {
                "application_name": "attendance-api",
                "timezone": "UTC",
                "statement_timeout": str(s.db_statement_timeout_ms),
            },
        },
    )


def get_engine() -> AsyncEngine:
    global _engine, _session_factory
    if _engine is None:
        _engine = build_engine()
        _session_factory = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    get_engine()
    if _session_factory is None:  # pragma: no cover - set by get_engine()
        raise RuntimeError("Session factory not initialised")
    return _session_factory


@asynccontextmanager
async def session_scope(
    factory: async_sessionmaker[AsyncSession] | None = None,
) -> AsyncIterator[AsyncSession]:
    """Unit of work: commit on success, roll back on any exception."""
    async with (factory or get_session_factory())() as session:
        try:
            yield session
            await session.commit()
        except BaseException:
            await session.rollback()
            raise


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: one transaction per request."""
    async with session_scope() as session:
        yield session


@asynccontextmanager
async def savepoint(session: AsyncSession) -> AsyncIterator[None]:
    """Nested transaction (SAVEPOINT). Failure rolls back only this block, then re-raises."""
    async with session.begin_nested():
        yield


async def check_database() -> bool:
    try:
        async with get_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


async def dispose_engine() -> None:
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None
