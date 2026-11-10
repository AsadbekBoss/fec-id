"""Migration tests on a dedicated database: upgrade, downgrade, re-upgrade, model drift."""

import asyncio
from collections.abc import Iterator

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

import app.models  # noqa: F401
from app.db.base import Base
from tests.conftest import alembic_config, create_database, drop_database, requires_db

pytestmark = requires_db

EXPECTED_TABLES = {
    "users",
    "employees",
    "departments",
    "biometric_consents",
    "face_embeddings",
    "cameras",
    "attendance_events",
    "attendance_daily",
    "work_schedules",
    "audit_logs",
    "system_settings",
    "auth_sessions",
    "refresh_tokens",
}


@pytest.fixture
def fresh_db() -> Iterator[URL]:
    url = create_database()
    try:
        yield url
    finally:
        drop_database(url)


def _query(url: URL, sql: str) -> list[tuple[object, ...]]:
    async def run() -> list[tuple[object, ...]]:
        eng = create_async_engine(url, poolclass=NullPool)
        try:
            async with eng.connect() as conn:
                return [tuple(r) for r in await conn.execute(text(sql))]
        finally:
            await eng.dispose()

    return asyncio.run(run())


def _tables(url: URL) -> set[str]:
    rows = _query(url, "SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
    return {str(r[0]) for r in rows} - {"alembic_version"}


def test_upgrade_downgrade_roundtrip(fresh_db: URL) -> None:
    cfg = alembic_config(fresh_db)

    command.upgrade(cfg, "head")
    assert _tables(fresh_db) == EXPECTED_TABLES
    head = ScriptDirectory.from_config(cfg).get_current_head()
    assert _query(fresh_db, "SELECT version_num FROM alembic_version") == [(head,)]
    assert _query(fresh_db, "SELECT count(*) FROM system_settings")[0][0] == 16

    command.downgrade(cfg, "base")
    assert _tables(fresh_db) == set()
    leftover_types = _query(
        fresh_db,
        "SELECT typname FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace "
        "WHERE n.nspname = 'public' AND t.typtype = 'e'",
    )
    assert leftover_types == []
    leftover_funcs = _query(
        fresh_db,
        "SELECT proname FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
        "WHERE n.nspname = 'public' AND p.prorettype = 'trigger'::regtype",
    )
    assert leftover_funcs == []

    command.upgrade(cfg, "head")  # re-applying after a full downgrade must work
    assert _tables(fresh_db) == EXPECTED_TABLES


def test_models_match_migrations(migrated_db_url: URL) -> None:
    """Fails if a model changed without a migration (autogenerate would produce a diff)."""

    async def run() -> list[object]:
        eng = create_async_engine(migrated_db_url, poolclass=NullPool)
        try:
            async with eng.connect() as conn:
                return await conn.run_sync(
                    lambda sync_conn: compare_metadata(
                        MigrationContext.configure(sync_conn, opts={"compare_type": True}),
                        Base.metadata,
                    )
                )
        finally:
            await eng.dispose()

    diff = asyncio.run(run())
    assert diff == [], f"Model/migration drift: {diff}"


def test_0002_maps_existing_roles(fresh_db: URL) -> None:
    """Existing users keep sensible access when the role enum changes (data migration)."""
    cfg = alembic_config(fresh_db)
    command.upgrade(cfg, "0001")
    h = "$argon2id$v=19$m=65536,t=3,p=4$c2FsdA$aGFzaA"
    asyncio.run(
        _exec(fresh_db, "INSERT INTO departments (id, name) VALUES (gen_random_uuid(), 'D')")
    )
    asyncio.run(
        _exec(
            fresh_db,
            "INSERT INTO employees (id, employee_code, last_name, first_name, department_id, "
            "hire_date) SELECT gen_random_uuid(), 'E1', 'A', 'B', id, '2025-01-01' "
            "FROM departments",
        )
    )
    for name, role in [
        ("sa_user", "SUPER_ADMIN"),
        ("hr_user", "HR_ADMIN"),
        ("sec_user", "SECURITY_OPERATOR"),
    ]:
        asyncio.run(
            _exec(
                fresh_db,
                f"INSERT INTO users (id, username, password_hash, role) "
                f"VALUES (gen_random_uuid(), '{name}', '{h}', '{role}')",
            )
        )
    asyncio.run(
        _exec(
            fresh_db,
            f"INSERT INTO users (id, username, password_hash, role, employee_id) "
            f"SELECT gen_random_uuid(), 'mgr_user', '{h}', 'MANAGER', id FROM employees",
        )
    )

    command.upgrade(cfg, "0002")
    roles = dict(_query(fresh_db, "SELECT username, role::text FROM users"))
    assert roles == {
        "sa_user": "SUPER_ADMIN",
        "hr_user": "ADMIN",
        "sec_user": "VIEWER",
        "mgr_user": "VIEWER",
    }

    command.downgrade(cfg, "0001")
    roles = dict(_query(fresh_db, "SELECT username, role::text FROM users"))
    assert roles == {
        "sa_user": "SUPER_ADMIN",
        "hr_user": "HR_ADMIN",
        "sec_user": "SECURITY_OPERATOR",
        "mgr_user": "SECURITY_OPERATOR",
    }


async def _exec(url: URL, sql: str) -> None:
    eng = create_async_engine(url, poolclass=NullPool)
    try:
        async with eng.begin() as conn:
            await conn.execute(text(sql))
    finally:
        await eng.dispose()


def test_offline_sql_generation(fresh_db: URL) -> None:
    """`alembic upgrade --sql` must render (used for DBA review of production changes)."""
    command.upgrade(alembic_config(fresh_db), "head", sql=True)
