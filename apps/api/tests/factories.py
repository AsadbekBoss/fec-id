"""Small builders for test rows. Uses obviously fake, non-personal data."""

from __future__ import annotations

import random
import uuid
from datetime import UTC, date, datetime, time

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    BiometricConsent,
    Branch,
    Camera,
    Department,
    Employee,
    FaceEmbedding,
    User,
    WorkSchedule,
)
from app.models.enums import UserRole

FAKE_ARGON2 = "$argon2id$v=19$m=65536,t=3,p=4$c2FsdHNhbHQ$aGFzaGhhc2hoYXNoaGFzaA"


def _uid() -> str:
    return uuid.uuid4().hex[:8]


async def branch(s: AsyncSession, **kw: object) -> Branch:
    b = Branch(name=kw.pop("name", f"Filial {_uid()}"), **kw)
    s.add(b)
    await s.flush()
    return b


async def department(s: AsyncSession, **kw: object) -> Department:
    d = Department(name=kw.pop("name", f"Dept {_uid()}"), **kw)
    s.add(d)
    await s.flush()
    return d


async def schedule(s: AsyncSession, **kw: object) -> WorkSchedule:
    w = WorkSchedule(
        name=kw.pop("name", f"5/2 {_uid()}"),
        start_time=kw.pop("start_time", time(9, 0)),
        end_time=kw.pop("end_time", time(18, 0)),
        **kw,
    )
    s.add(w)
    await s.flush()
    return w


async def employee(s: AsyncSession, dept: Department | None = None, **kw: object) -> Employee:
    dept = dept or await department(s)
    e = Employee(
        employee_code=kw.pop("employee_code", f"E{_uid()}".upper()),
        last_name=kw.pop("last_name", "Testov"),
        first_name=kw.pop("first_name", "Test"),
        department_id=dept.id,
        branch_id=kw.pop("branch_id", dept.branch_id),
        hire_date=kw.pop("hire_date", date(2025, 1, 15)),
        **kw,
    )
    s.add(e)
    await s.flush()
    return e


async def user(s: AsyncSession, role: UserRole = UserRole.ADMIN, **kw: object) -> User:
    u = User(
        username=kw.pop("username", f"user_{_uid()}"),
        password_hash=kw.pop("password_hash", FAKE_ARGON2),
        role=role,
        **kw,
    )
    s.add(u)
    await s.flush()
    return u


async def consent(
    s: AsyncSession, emp: Employee, by: User, documented: bool = True
) -> BiometricConsent:
    """Documented by default: face embeddings require a signed form (revision 0003)."""
    c = BiometricConsent(employee_id=emp.id, consent_text_version="v1", recorded_by_user_id=by.id)
    if documented:
        c.signed_document_key = f"consent_documents/{emp.id}/{uuid.uuid4()}.bin"
        c.document_content_type = "application/pdf"
        c.document_uploaded_at = datetime.now(UTC)
    s.add(c)
    await s.flush()
    return c


def random_vector(dim: int = 512) -> list[float]:
    return [random.uniform(-1, 1) for _ in range(dim)]


async def embedding(
    s: AsyncSession,
    emp: Employee,
    cons: BiometricConsent,
    by: User,
    vector: list[float] | None = None,
    **kw: object,
) -> FaceEmbedding:
    fe = FaceEmbedding(
        employee_id=emp.id,
        consent_id=cons.id,
        model_id=kw.pop("model_id", "test-model@1"),
        quality_score=kw.pop("quality_score", 0.9),
        created_by_user_id=by.id,
        **kw,
    )
    fe.set_vector(vector or random_vector())
    s.add(fe)
    await s.flush()
    return fe


async def camera(s: AsyncSession, **kw: object) -> Camera:
    c = Camera(
        name=kw.pop("name", f"Cam {_uid()}"),
        host=kw.pop("host", "10.20.0.11"),
        main_stream_path=kw.pop("main_stream_path", "/Streams/Channels/101"),
        **kw,
    )
    c.set_credentials("viewer", "not-a-real-password")
    s.add(c)
    await s.flush()
    return c
