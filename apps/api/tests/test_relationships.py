import uuid
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import (
    AttendanceDaily,
    AttendanceEvent,
    BiometricConsent,
    Camera,
    Department,
    Employee,
    User,
)
from app.models.enums import (
    AttendanceStatus,
    EventSource,
    PunchDirection,
    RecognitionDecision,
    UserRole,
)
from tests import factories as f
from tests.conftest import requires_db

pytestmark = requires_db


async def test_department_tree_and_head(session: AsyncSession) -> None:
    root = await f.department(session, name="Head office")
    child = await f.department(session, name="Accounting", parent_id=root.id)
    head = await f.employee(session, dept=child)
    child.head_employee_id = head.id
    await session.flush()
    session.expunge_all()

    loaded = (
        await session.execute(
            select(Department)
            .where(Department.id == root.id)
            .options(
                selectinload(Department.children).selectinload(Department.head),
                selectinload(Department.children).selectinload(Department.employees),
            )
        )
    ).scalar_one()
    assert [c.name for c in loaded.children] == ["Accounting"]
    assert loaded.children[0].head is not None
    assert loaded.children[0].head.id == head.id
    assert [e.id for e in loaded.children[0].employees] == [head.id]


async def test_employee_full_graph(session: AsyncSession) -> None:
    sched = await f.schedule(session)
    emp = await f.employee(session, work_schedule_id=sched.id)
    hr = await f.user(session, UserRole.ADMIN)
    portal = await f.user(session, UserRole.VIEWER, employee_id=emp.id)
    cons = await f.consent(session, emp, hr)
    fe = await f.embedding(session, emp, cons, hr)
    cam = await f.camera(session)
    ev = AttendanceEvent(
        event_uid=uuid.uuid4(),
        event_time=datetime(2026, 11, 6, 4, 17, tzinfo=UTC),
        source=EventSource.CAMERA,
        employee_id=emp.id,
        camera_id=cam.id,
        decision=RecognitionDecision.MATCHED,
        direction=PunchDirection.IN,
        work_date=date(2026, 11, 6),
        counts_for_attendance=True,
        similarity=0.68,
        quality_score=0.9,
        model_id="test-model@1",
    )
    day = AttendanceDaily(
        employee_id=emp.id,
        work_date=date(2026, 11, 6),
        work_schedule_id=sched.id,
        first_in=ev.event_time,
        status=AttendanceStatus.LATE,
        late_minutes=12,
        engine_version="0.1.0",
    )
    session.add_all([ev, day])
    await session.flush()
    session.expunge_all()

    e = (
        await session.execute(
            select(Employee)
            .where(Employee.id == emp.id)
            .options(
                selectinload(Employee.department),
                selectinload(Employee.work_schedule),
                selectinload(Employee.user),
                selectinload(Employee.consents).selectinload(BiometricConsent.face_embeddings),
                selectinload(Employee.face_embeddings),
                selectinload(Employee.attendance_events).selectinload(AttendanceEvent.camera),
                selectinload(Employee.attendance_days),
            )
        )
    ).scalar_one()
    assert e.work_schedule is not None and e.work_schedule.id == sched.id
    assert e.user is not None and e.user.id == portal.id
    assert [c.id for c in e.consents] == [cons.id]
    assert [x.id for x in e.consents[0].face_embeddings] == [fe.id]
    assert [x.id for x in e.face_embeddings] == [fe.id]
    assert e.attendance_events[0].camera is not None
    assert e.attendance_events[0].camera.id == cam.id
    assert e.attendance_days[0].status is AttendanceStatus.LATE

    u = (
        await session.execute(
            select(User).where(User.id == portal.id).options(selectinload(User.employee))
        )
    ).scalar_one()
    assert u.employee is not None and u.employee.id == emp.id

    c = (
        await session.execute(
            select(Camera)
            .where(Camera.id == cam.id)
            .options(selectinload(Camera.attendance_events))
        )
    ).scalar_one()
    assert len(c.attendance_events) == 1


async def test_computed_search_name(session: AsyncSession) -> None:
    emp = await f.employee(
        session, last_name="Karimov", first_name="Aziz", middle_name="Baxtiyor o‘g‘li"
    )
    await session.refresh(emp, ["search_name"])
    assert emp.search_name == "karimov aziz baxtiyor o‘g‘li"
