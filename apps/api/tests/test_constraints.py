"""Each test triggers one constraint inside a SAVEPOINT and checks the database rejects it."""

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, time

import pytest
from sqlalchemy import text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AttendanceDaily, AttendanceEvent, Camera, Department, Employee, User
from app.models.enums import (
    AttendanceStatus,
    EmployeeStatus,
    EventSource,
    PunchDirection,
    RecognitionDecision,
    UserRole,
)
from tests import factories as f
from tests.conftest import requires_db

pytestmark = requires_db


async def rejects(
    session: AsyncSession, action: Callable[[], Awaitable[object]], match: str
) -> None:
    with pytest.raises(DBAPIError, match=match):
        async with session.begin_nested():
            await action()
            await session.flush()


# --- uniqueness -----------------------------------------------------------------------------


async def test_employee_code_unique(session: AsyncSession) -> None:
    dept = await f.department(session)
    await f.employee(session, dept, employee_code="E001")

    async def dup() -> None:
        await f.employee(session, dept, employee_code="E001")

    await rejects(session, dup, "uq_employees_branch_id_employee_code")


async def test_username_unique_case_insensitive(session: AsyncSession) -> None:
    await f.user(session, username="Admin.User")

    async def dup() -> None:
        await f.user(session, username="admin.user")

    await rejects(session, dup, "uq_users_username")


async def test_department_sibling_names_unique_but_soft_delete_frees_name(
    session: AsyncSession,
) -> None:
    root = await f.department(session)
    first = await f.department(session, name="IT", parent_id=root.id)

    async def dup() -> None:
        await f.department(session, name="it", parent_id=root.id)

    await rejects(session, dup, "uq_departments_parent_name_active")

    first.deleted_at = datetime.now(UTC)
    await session.flush()
    await f.department(session, name="IT", parent_id=root.id)  # allowed now


async def test_one_daily_row_per_employee_and_date(session: AsyncSession) -> None:
    emp = await f.employee(session)
    session.add(
        AttendanceDaily(
            employee_id=emp.id,
            work_date=date(2026, 11, 2),
            status=AttendanceStatus.ABSENT,
            engine_version="t",
        )
    )
    await session.flush()

    async def dup() -> None:
        session.add(
            AttendanceDaily(
                employee_id=emp.id,
                work_date=date(2026, 11, 2),
                status=AttendanceStatus.ABSENT,
                engine_version="t",
            )
        )

    await rejects(session, dup, "uq_attendance_daily_employee_id_work_date")


async def test_event_uid_idempotency(session: AsyncSession) -> None:
    cam = await f.camera(session)
    uid = uuid.uuid4()

    def ev() -> AttendanceEvent:
        return AttendanceEvent(
            event_uid=uid,
            event_time=datetime.now(UTC),
            source=EventSource.CAMERA,
            camera_id=cam.id,
            decision=RecognitionDecision.UNKNOWN,
            model_id="m@1",
            ignore_reason="UNMATCHED",
        )

    session.add(ev())
    await session.flush()

    async def dup() -> None:
        session.add(ev())

    await rejects(session, dup, "uq_attendance_events_event_uid")


# --- foreign keys ---------------------------------------------------------------------------


async def test_employee_requires_existing_department(session: AsyncSession) -> None:
    async def bad() -> None:
        session.add(
            Employee(
                employee_code="X1",
                last_name="A",
                first_name="B",
                department_id=uuid.uuid4(),
                hire_date=date(2025, 1, 1),
            )
        )

    await rejects(session, bad, "fk_employees_department_id_departments")


async def test_cannot_hard_delete_department_with_employees(session: AsyncSession) -> None:
    dept = await f.department(session)
    await f.employee(session, dept)

    async def bad() -> None:
        await session.execute(text("DELETE FROM departments WHERE id = :id"), {"id": dept.id})

    await rejects(session, bad, "fk_employees_department_id_departments")


async def test_deleting_schedule_unassigns_employees(session: AsyncSession) -> None:
    sched = await f.schedule(session)
    emp = await f.employee(session, work_schedule_id=sched.id)
    await session.execute(text("DELETE FROM work_schedules WHERE id = :id"), {"id": sched.id})
    await session.refresh(emp)
    assert emp.work_schedule_id is None


# --- check constraints ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("changes", "constraint"),
    [
        ({"employee_code": "bad code!"}, "ck_employees_code_format"),
        ({"phone": "12-34"}, "ck_employees_phone_format"),
        ({"email": "not-an-email"}, "ck_employees_email_format"),
        ({"first_name": "  "}, "ck_employees_names_present"),
        (
            {"status": EmployeeStatus.TERMINATED, "termination_date": date(2024, 1, 1)},
            "ck_employees_termination_after_hire",
        ),
        ({"status": EmployeeStatus.TERMINATED}, "ck_employees_terminated_has_date"),
    ],
)
async def test_employee_checks(
    session: AsyncSession, changes: dict[str, object], constraint: str
) -> None:
    dept = await f.department(session)

    async def bad() -> None:
        await f.employee(session, dept, **changes)

    await rejects(session, bad, constraint)


async def test_department_cannot_be_own_parent(session: AsyncSession) -> None:
    dept = await f.department(session)

    async def bad() -> None:
        await session.execute(
            update(Department).where(Department.id == dept.id).values(parent_id=dept.id)
        )

    await rejects(session, bad, "ck_departments_not_own_parent")


@pytest.mark.parametrize(
    ("changes", "constraint"),
    [
        ({"password_hash": "plaintext"}, "ck_users_password_argon2id"),
        ({"password_hash": "$2b$12$bcrypthash"}, "ck_users_password_argon2id"),
        ({"mfa_enabled": True}, "ck_users_mfa_has_secret"),
        ({"locale": "de"}, "ck_users_locale_supported"),
        ({"username": "a b"}, "ck_users_username_format"),
    ],
)
async def test_user_checks(
    session: AsyncSession, changes: dict[str, object], constraint: str
) -> None:
    role = changes.pop("role", UserRole.ADMIN)

    async def bad() -> None:
        await f.user(session, role, **changes)  # type: ignore[arg-type]

    await rejects(session, bad, constraint)


@pytest.mark.parametrize(
    ("changes", "constraint"),
    [
        ({"start_time": time(9), "end_time": time(9)}, "ck_work_schedules_start_ne_end"),
        ({"working_days": [1, 8]}, "ck_work_schedules_working_days_valid"),
        ({"working_days": []}, "ck_work_schedules_working_days_valid"),
        ({"break_minutes": -1}, "ck_work_schedules_break_range"),
        ({"grace_in_minutes": 999}, "ck_work_schedules_grace_in_range"),
    ],
)
async def test_schedule_checks(
    session: AsyncSession, changes: dict[str, object], constraint: str
) -> None:
    async def bad() -> None:
        await f.schedule(session, **changes)

    await rejects(session, bad, constraint)


async def test_night_shift_schedule_allowed(session: AsyncSession) -> None:
    w = await f.schedule(session, start_time=time(22), end_time=time(6), working_days=[1, 2, 3])
    assert w.id is not None


@pytest.mark.parametrize(
    ("changes", "constraint"),
    [
        ({"main_stream_path": "rtsp://admin:pw@10.0.0.1/stream"}, "stream_paths_no_credentials"),
        ({"sub_stream_path": "/x@y"}, "stream_paths_no_credentials"),
        ({"rtsp_port": 70000}, "ck_cameras_rtsp_port_range"),
        ({"host": "bad host;rm"}, "ck_cameras_host_format"),
        ({"settings": []}, "ck_cameras_settings_is_object"),
    ],
)
async def test_camera_checks(
    session: AsyncSession, changes: dict[str, object], constraint: str
) -> None:
    async def bad() -> None:
        await f.camera(session, **changes)

    await rejects(session, bad, constraint)


async def test_camera_credentials_must_be_ciphertext(session: AsyncSession) -> None:
    async def bad() -> None:
        session.add(
            Camera(
                name="raw",
                host="10.0.0.9",
                main_stream_path="/s",
                username_enc=b"admin",
                password_enc=b"12345",
            )
        )

    await rejects(session, bad, "ck_cameras_credentials_encrypted")


async def test_camera_name_unique_among_active(session: AsyncSession) -> None:
    await f.camera(session, name="Main entrance IN")

    async def dup() -> None:
        await f.camera(session, name="MAIN ENTRANCE in")

    await rejects(session, dup, "uq_cameras_name_active")


async def test_attendance_event_rules(session: AsyncSession) -> None:
    emp = await f.employee(session)
    cam = await f.camera(session)
    hr = await f.user(session)
    now = datetime.now(UTC)

    def ev(**kw: object) -> AttendanceEvent:
        return AttendanceEvent(event_uid=uuid.uuid4(), event_time=now, **kw)

    def cam_unmatched(**kw: object) -> AttendanceEvent:
        base: dict[str, object] = {
            "source": EventSource.CAMERA,
            "camera_id": cam.id,
            "decision": RecognitionDecision.UNKNOWN,
            "model_id": "m",
            "ignore_reason": "UNMATCHED",
        }
        return ev(**(base | kw))

    def manual(**kw: object) -> AttendanceEvent:
        base: dict[str, object] = {
            "source": EventSource.MANUAL,
            "employee_id": emp.id,
            "direction": PunchDirection.IN,
            "created_by_user_id": hr.id,
            "correction_id": uuid.uuid4(),
            "work_date": now.date(),
            "counts_for_attendance": True,
        }
        return ev(**(base | kw))

    cases: list[tuple[AttendanceEvent, str]] = [
        (
            ev(source=EventSource.CAMERA, employee_id=emp.id, ignore_reason="UNMATCHED"),
            "camera_event_fields",
        ),
        (manual(created_by_user_id=None), "manual_event_fields"),
        (
            cam_unmatched(decision=RecognitionDecision.MATCHED, ignore_reason="DEBOUNCED"),
            "matched_has_employee",
        ),
        (cam_unmatched(employee_id=emp.id), "unmatched_has_no_employee"),
        (manual(work_date=None), "counted_event_complete"),
        (manual(is_voided=True, voided_at=now, voided_by_user_id=hr.id), "void_has_reason"),
        (manual(is_voided=True, void_reason="x"), "void_fields_together"),
        (cam_unmatched(similarity=1.5), "similarity_range"),
        (cam_unmatched(ignore_reason=None), "ignore_reason_iff_not_counted"),
        (manual(ignore_reason="DEBOUNCED"), "ignore_reason_iff_not_counted"),
        (cam_unmatched(ignore_reason="BECAUSE"), "ignore_reason_known"),
        (manual(correction_id=None), "manual_has_correction"),
    ]
    for event, constraint in cases:

        async def bad(e: AttendanceEvent = event) -> None:
            session.add(e)

        await rejects(session, bad, constraint)

    # A valid manual punch is accepted ...
    session.add(manual(note="Forgot to look at camera"))
    await session.flush()

    # ... but a second counted punch at the same instant is a duplicate.
    async def same_instant() -> None:
        session.add(manual(direction=PunchDirection.OUT))

    await rejects(session, same_instant, "uq_attendance_events_counted_instant")


async def test_attendance_events_are_immutable(session: AsyncSession) -> None:
    emp = await f.employee(session)
    hr = await f.user(session)
    now = datetime.now(UTC)
    e = AttendanceEvent(
        event_uid=uuid.uuid4(),
        event_time=now,
        source=EventSource.MANUAL,
        employee_id=emp.id,
        direction=PunchDirection.IN,
        created_by_user_id=hr.id,
        correction_id=uuid.uuid4(),
        work_date=now.date(),
        counts_for_attendance=True,
    )
    session.add(e)
    await session.flush()

    for sql in (
        "UPDATE attendance_events SET event_time = event_time - interval '1 hour' WHERE id = :id",
        "UPDATE attendance_events SET direction = 'OUT' WHERE id = :id",
        "DELETE FROM attendance_events WHERE id = :id",
    ):

        async def tamper(q: str = sql) -> None:
            await session.execute(text(q), {"id": e.id})

        await rejects(session, tamper, "cannot be deleted|immutable")

    await session.execute(
        text(
            "UPDATE attendance_events SET is_voided = true, void_reason = 'wrong', "
            "voided_at = now(), voided_by_user_id = :u WHERE id = :id"
        ),
        {"id": e.id, "u": hr.id},
    )  # voiding is the only allowed change

    async def unvoid() -> None:
        await session.execute(
            text(
                "UPDATE attendance_events SET is_voided = false, void_reason = NULL, "
                "voided_at = NULL, voided_by_user_id = NULL WHERE id = :id"
            ),
            {"id": e.id},
        )

    await rejects(session, unvoid, "cannot be restored")


@pytest.mark.parametrize(
    ("changes", "constraint"),
    [
        ({"worked_minutes": -5}, "ck_attendance_daily_minutes_range"),
        ({"status": AttendanceStatus.PRESENT}, "ck_attendance_daily_present_has_in"),
        (
            {
                "first_in": datetime(2026, 11, 2, 10, tzinfo=UTC),
                "last_out": datetime(2026, 11, 2, 9, tzinfo=UTC),
            },
            "ck_attendance_daily_out_after_in",
        ),
        (
            {"status": AttendanceStatus.ABSENT, "worked_minutes": 30},
            "ck_attendance_daily_absent_has_no_work",
        ),
    ],
)
async def test_daily_checks(
    session: AsyncSession, changes: dict[str, object], constraint: str
) -> None:
    emp = await f.employee(session)
    values: dict[str, object] = {"status": AttendanceStatus.INCOMPLETE} | changes

    async def bad() -> None:
        session.add(
            AttendanceDaily(
                employee_id=emp.id, work_date=date(2026, 11, 2), engine_version="t", **values
            )
        )

    await rejects(session, bad, constraint)


async def test_system_setting_key_format(session: AsyncSession) -> None:
    async def bad() -> None:
        await session.execute(
            text("INSERT INTO system_settings (key, value) VALUES ('BadKey', '1')")
        )

    await rejects(session, bad, "ck_system_settings_key_format")


async def test_updated_at_trigger(session: AsyncSession) -> None:
    u = await f.user(session)
    await session.refresh(u)
    before = u.updated_at
    # now() is the transaction start time; use a different statement timestamp via a savepoint
    # is not enough, so set the column explicitly old and verify the trigger overwrites it.
    await session.execute(
        text("UPDATE users SET updated_at = '2000-01-01', locale = 'ru' WHERE id = :id"),
        {"id": u.id},
    )
    await session.refresh(u)
    assert u.updated_at == before  # trigger forced now(), ignoring the supplied old value
    assert u.locale == "ru"


async def test_server_defaults(session: AsyncSession) -> None:
    emp = await f.employee(session)
    cam = await f.camera(session)
    await session.refresh(emp)
    await session.refresh(cam)
    assert emp.status is EmployeeStatus.ACTIVE
    assert emp.created_at is not None and emp.updated_at is not None
    assert cam.status.value == "DRAFT" and cam.is_enabled is False and cam.settings == {}
    u: User = await f.user(session)
    await session.refresh(u)
    assert u.failed_login_count == 0 and u.mfa_enabled is False
