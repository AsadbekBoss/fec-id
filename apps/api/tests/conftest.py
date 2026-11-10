"""Test configuration.

Unit tests need nothing. Database tests need a PostgreSQL server where the given role may
CREATE DATABASE. Point TEST_DATABASE_URL at it, e.g.

    TEST_DATABASE_URL=postgresql+asyncpg://attendance:<pw>@127.0.0.1:5432/postgres

Each test session creates a fresh, uniquely named database, migrates it with Alembic, and
drops it afterwards. Without TEST_DATABASE_URL the database tests are skipped (not failed).
"""

from __future__ import annotations

import asyncio
import base64
import os
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest

# --- Environment for the app under test (test-only values, not secrets) -------------------
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("POSTGRES_PASSWORD", "test-only-not-a-secret")
os.environ.setdefault(
    "DATA_ENCRYPTION_KEYS",
    "1:" + base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip("="),
)
os.environ.setdefault(
    "JWT_SIGNING_KEYS", "1:" + base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip("=")
)
# Cheap Argon2 parameters keep the suite fast; production defaults are asserted separately.
os.environ.setdefault("ARGON2_TIME_COST", "1")
os.environ.setdefault("ARGON2_MEMORY_KIB", "1024")
os.environ.setdefault("ARGON2_PARALLELISM", "1")

from alembic import command
from alembic.config import Config
from sqlalchemy import make_url, text
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

API_DIR = Path(__file__).resolve().parents[1]
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")

requires_db = pytest.mark.skipif(
    not TEST_DATABASE_URL, reason="TEST_DATABASE_URL not set (database tests skipped)"
)


def alembic_config(url: URL) -> Config:
    cfg = Config(str(API_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_DIR / "migrations"))
    cfg.attributes["database_url"] = url
    cfg.attributes["configure_logger"] = False
    return cfg


async def _admin_exec(sql: str) -> None:
    assert TEST_DATABASE_URL
    engine = create_async_engine(
        TEST_DATABASE_URL, poolclass=NullPool, isolation_level="AUTOCOMMIT"
    )
    try:
        async with engine.connect() as conn:
            await conn.execute(text(sql))
    finally:
        await engine.dispose()


def create_database() -> URL:
    """Creates an empty uniquely named database and returns its URL."""
    assert TEST_DATABASE_URL
    name = f"attendance_test_{uuid.uuid4().hex[:12]}"
    asyncio.run(_admin_exec(f'CREATE DATABASE "{name}"'))
    return make_url(TEST_DATABASE_URL).set(database=name)


def drop_database(url: URL) -> None:
    asyncio.run(_admin_exec(f'DROP DATABASE IF EXISTS "{url.database}" WITH (FORCE)'))


@pytest.fixture(scope="session")
def migrated_db_url() -> Iterator[URL]:
    """A database migrated to `head`, shared by the whole test session."""
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL not set")
    url = create_database()
    try:
        command.upgrade(alembic_config(url), "head")
        yield url
    finally:
        drop_database(url)


@pytest.fixture
async def engine(migrated_db_url: URL) -> AsyncIterator[AsyncEngine]:
    eng = create_async_engine(
        migrated_db_url,
        poolclass=NullPool,
        connect_args={"server_settings": {"timezone": "UTC"}},
    )
    yield eng
    await eng.dispose()


@pytest.fixture
async def session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """Session inside an outer transaction that is always rolled back, so tests are isolated.
    `session.commit()` inside a test only releases a SAVEPOINT."""
    async with engine.connect() as conn:
        outer = await conn.begin()
        s = AsyncSession(
            bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False
        )
        try:
            yield s
        finally:
            await s.close()
            await outer.rollback()


@pytest.fixture
def app(session: AsyncSession):  # type: ignore[no-untyped-def]
    """FastAPI app whose DB session is the test's transactional session."""
    from tests.api_helpers import build_app

    return build_app(session)


@pytest.fixture
async def api(app):  # type: ignore[no-untyped-def]
    from tests.api_helpers import client

    async for c in client(app):
        yield c
