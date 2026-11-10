import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.engine import URL
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.db import audit
from app.db.session import savepoint, session_scope
from app.models import AuditLog, Department
from app.models.enums import ActorType
from tests import factories as f
from tests.conftest import requires_db
from tests.test_constraints import rejects

pytestmark = requires_db


# --- audit log ------------------------------------------------------------------------------


async def test_audit_log_is_append_only(session: AsyncSession) -> None:
    hr = await f.user(session)
    entry = await audit.record(
        session,
        action="EMPLOYEE_CREATED",
        actor_type=ActorType.USER,
        actor_user_id=hr.id,
        entity_type="employee",
        entity_id=uuid.uuid4(),
        changes={"last_name": [None, "Testov"]},
    )
    await session.flush()

    for sql in (
        "UPDATE audit_logs SET action = 'TAMPERED' WHERE id = :id",
        "DELETE FROM audit_logs WHERE id = :id",
    ):

        async def tamper(q: str = sql) -> None:
            await session.execute(text(q), {"id": entry.id})

        await rejects(session, tamper, "audit_logs is append-only")

    async def truncate() -> None:
        await session.execute(text("TRUNCATE audit_logs"))

    await rejects(session, truncate, "audit_logs is append-only")


@pytest.mark.parametrize(
    "changes",
    [
        {"password": "x"},
        {"password_hash": "x"},
        {"embedding_enc": "x"},
        {"face_embedding": [0.1]},
        {"refresh_token": "x"},
        {"nested": {"mfa_secret": "x"}},
        {"photo": b"\x89PNG"},
    ],
)
async def test_audit_refuses_sensitive_fields(
    session: AsyncSession, changes: dict[str, object]
) -> None:
    with pytest.raises(audit.SensitiveAuditDataError):
        await audit.record(session, action="X_Y", actor_type=ActorType.SYSTEM, changes=changes)


async def test_audit_checks(session: AsyncSession) -> None:
    async def user_without_id() -> None:
        await audit.record(session, action="LOGIN", actor_type=ActorType.USER)

    await rejects(session, user_without_id, "ck_audit_logs_user_actor_has_id")

    async def bad_action() -> None:
        await audit.record(session, action="lower-case", actor_type=ActorType.SYSTEM)

    await rejects(session, bad_action, "ck_audit_logs_action_format")


# --- transactions ---------------------------------------------------------------------------


@pytest.fixture
async def factory(migrated_db_url: URL) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    eng: AsyncEngine = create_async_engine(migrated_db_url, poolclass=NullPool)
    yield async_sessionmaker(eng, expire_on_commit=False)
    await eng.dispose()


async def _dept_count(factory: async_sessionmaker[AsyncSession], name: str) -> int:
    async with factory() as s:
        return (
            await s.execute(
                select(func.count()).select_from(Department).where(Department.name == name)
            )
        ).scalar_one()


async def test_session_scope_commits(factory: async_sessionmaker[AsyncSession]) -> None:
    name = f"commit-{uuid.uuid4().hex[:8]}"
    async with session_scope(factory) as s:
        s.add(Department(name=name))
    assert await _dept_count(factory, name) == 1
    async with session_scope(factory) as s:  # cleanup
        await s.execute(text("DELETE FROM departments WHERE name = :n"), {"n": name})


async def test_session_scope_rolls_back_on_error_including_audit(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    name = f"rollback-{uuid.uuid4().hex[:8]}"
    with pytest.raises(RuntimeError):
        async with session_scope(factory) as s:
            s.add(Department(name=name))
            await audit.record(
                s, action="DEPARTMENT_CREATED", actor_type=ActorType.SYSTEM, entity_id=name
            )
            await s.flush()
            raise RuntimeError("business rule failed")
    assert await _dept_count(factory, name) == 0
    async with factory() as s:
        n = (
            await s.execute(
                select(func.count()).select_from(AuditLog).where(AuditLog.entity_id == name)
            )
        ).scalar_one()
    assert n == 0  # audit row rolled back together with the change


async def test_savepoint_isolates_failed_step(factory: async_sessionmaker[AsyncSession]) -> None:
    keep, fail = f"keep-{uuid.uuid4().hex[:8]}", f"fail-{uuid.uuid4().hex[:8]}"
    async with session_scope(factory) as s:
        s.add(Department(name=keep))
        await s.flush()
        with pytest.raises(DBAPIError, match="fk_departments_parent_id_departments"):
            async with savepoint(s):
                s.add(Department(name=fail, parent_id=uuid.uuid4()))  # FK violation
                await s.flush()
    assert await _dept_count(factory, keep) == 1
    assert await _dept_count(factory, fail) == 0
    async with session_scope(factory) as s:
        await s.execute(text("DELETE FROM departments WHERE name = :n"), {"n": keep})
