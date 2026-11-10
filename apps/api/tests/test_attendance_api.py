"""Attendance API integration tests (real PostgreSQL, frozen server clock)."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.attendance import service as att
from app.models import AttendanceDaily, AttendanceEvent, AuditLog, Department, Employee
from app.models.enums import AttendanceMethod, CameraDirection, EmployeeStatus, UserRole
from tests import factories as f
from tests.api_helpers import bearer, make_user, oid, token_for
from tests.conftest import requires_db

pytestmark = requires_db

TZ = ZoneInfo("Asia/Tashkent")
MON = date(2026, 11, 2)
TUE, SAT = MON + timedelta(days=1), date(2026, 10, 31)
NOW = datetime(2026, 11, 4, 12, 0, tzinfo=TZ)  # Wednesday noon


def L(d: date, hh: int, mm: int = 0) -> datetime:
    return datetime.combine(d, time(hh, mm), tzinfo=TZ)


@pytest.fixture(autouse=True)
def frozen_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(att, "now", lambda: NOW.astimezone(UTC))


@pytest.fixture
async def tokens(api: httpx.AsyncClient, session: AsyncSession) -> dict[str, dict[str, str]]:
    out = {}
    for role in UserRole:
        u = await make_user(session, role=role)
        out[role.value] = bearer(await token_for(api, u))
    return out


@pytest.fixture
async def setup(api: httpx.AsyncClient, session: AsyncSession, tokens: dict) -> dict[str, Any]:  # type: ignore[type-arg]
    h = tokens["ADMIN"]
    r = await api.post(
        "/api/v1/work-schedules",
        headers=h,
        json={
            "name": "Ofis 9-18",
            "start_time": "09:00",
            "end_time": "18:00",
            "working_days": [1, 2, 3, 4, 5],
            "break_minutes": 60,
            "grace_in_minutes": 5,
        },
    )
    assert r.status_code == 201, r.text
    sched_id = r.json()["id"]
    dept = await f.department(session, name="Ofis")
    dept.work_schedule_id = uuid.UUID(sched_id)
    emp = await f.employee(session, dept=dept)
    cams = {
        d: await f.camera(session, name=f"Cam {d.value}", direction=d, recognition_enabled=True)
        for d in (
            CameraDirection.IN,
            CameraDirection.OUT,
            CameraDirection.BOTH,
            CameraDirection.NONE,
        )
    }
    await session.flush()
    return {"h": h, "sched": sched_id, "dept": dept, "emp": emp, "cams": cams}


async def send(
    api: httpx.AsyncClient,
    s: dict[str, Any],
    t: datetime,
    cam: CameraDirection = CameraDirection.BOTH,
    emp: Employee | None = None,
    uid: uuid.UUID | None = None,
    decision: str = "MATCHED",
    **extra: Any,
) -> httpx.Response:
    body: dict[str, Any] = {
        "event_uid": str(uid or uuid.uuid4()),
        "camera_id": str(oid(s["cams"][cam])),
        "event_time": t.isoformat(),
        "decision": decision,
        "model_id": "sim@1",
    }
    if decision == "MATCHED":
        body["employee_id"] = str(oid(emp or s["emp"]))
    body.update(extra)
    return await api.post("/api/v1/attendance/events", headers=s["h"], json=body)


async def get_day(
    api: httpx.AsyncClient, s: dict[str, Any], d: date, emp: Employee | None = None
) -> dict:  # type: ignore[type-arg]
    r = await api.get(f"/api/v1/attendance/days/{oid(emp or s['emp'])}/{d}", headers=s["h"])
    assert r.status_code == 200, r.text
    return r.json()  # type: ignore[no-any-return]


async def correct(
    api: httpx.AsyncClient,
    s: dict[str, Any],
    d: date,
    ops: list[dict[str, Any]],
    reason: str = "Xodim kamerani chetlab o'tdi",
    emp: Employee | None = None,
) -> httpx.Response:
    return await api.post(
        "/api/v1/attendance/corrections",
        headers=s["h"],
        json={
            "employee_id": str(oid(emp or s["emp"])),
            "work_date": d.isoformat(),
            "reason": reason,
            "operations": ops,
        },
    )


# --- work schedules ---------------------------------------------------------------------------


async def test_work_schedule_crud_and_validation(api: httpx.AsyncClient, tokens: dict) -> None:  # type: ignore[type-arg]
    h = tokens["ADMIN"]
    ok = await api.post(
        "/api/v1/work-schedules",
        headers=h,
        json={
            "name": "Tungi",
            "start_time": "22:00",
            "end_time": "06:00",
            "working_days": [5, 1, 3],
        },
    )
    assert ok.status_code == 201
    body = ok.json()
    assert body["crosses_midnight"] is True and body["working_days"] == [1, 3, 5]
    assert body["timezone"] == "Asia/Tashkent"

    bad = [
        {"timezone": "Mars/Base"},
        {"start_time": "09:00", "end_time": "09:00"},
        {"break_minutes": 600},
        {"working_days": [0, 8]},
        {"working_days": [1, 1]},
        {"working_days": []},
        {"grace_in_minutes": -1},
        {"unknown": 1},
    ]
    for extra in bad:
        payload = {
            "name": f"x{uuid.uuid4().hex[:4]}",
            "start_time": "09:00",
            "end_time": "18:00",
        } | extra
        r = await api.post("/api/v1/work-schedules", headers=h, json=payload)
        assert r.status_code == 422, (extra, r.text)

    dup = await api.post(
        "/api/v1/work-schedules",
        headers=h,
        json={"name": "Tungi", "start_time": "09:00", "end_time": "18:00"},
    )
    assert dup.status_code == 409

    sid = body["id"]
    upd = await api.patch(f"/api/v1/work-schedules/{sid}", headers=h, json={"grace_in_minutes": 15})
    assert upd.status_code == 200 and upd.json()["grace_in_minutes"] == 15
    shape = await api.patch(f"/api/v1/work-schedules/{sid}", headers=h, json={"break_minutes": 500})
    assert shape.status_code == 422
    assert (await api.get("/api/v1/work-schedules", headers=tokens["VIEWER"])).status_code == 200
    assert (
        await api.post(
            "/api/v1/work-schedules",
            headers=tokens["VIEWER"],
            json={"name": "v", "start_time": "09:00", "end_time": "18:00"},
        )
    ).status_code == 403
    assert (await api.delete(f"/api/v1/work-schedules/{sid}", headers=h)).status_code == 204


async def test_schedule_in_use_cannot_be_deleted(api: httpx.AsyncClient, setup: dict) -> None:  # type: ignore[type-arg]
    r = await api.delete(f"/api/v1/work-schedules/{setup['sched']}", headers=setup["h"])
    assert r.status_code == 409 and r.json()["code"] == "SCHEDULE_IN_USE"


async def test_schedule_resolution_through_department_tree(
    api: httpx.AsyncClient,
    setup: dict,
    session: AsyncSession,  # type: ignore[type-arg]
) -> None:
    sub = await f.department(session, name="Ofis / IT", parent_id=setup["dept"].id)
    inherited = await f.employee(session, dept=sub)
    own = await api.post(
        "/api/v1/work-schedules",
        headers=setup["h"],
        json={"name": "Smena 8-17", "start_time": "08:00", "end_time": "17:00"},
    )
    personal = await f.employee(session, dept=sub, work_schedule_id=uuid.UUID(own.json()["id"]))
    loose = await f.employee(session, dept=await f.department(session, name="Jadvalsiz"))
    assert (await get_day(api, setup, MON, inherited))["schedule"]["name"] == "Ofis 9-18"
    assert (await get_day(api, setup, MON, personal))["schedule"]["name"] == "Smena 8-17"
    nos = await get_day(api, setup, MON, loose)
    assert nos["schedule"] is None and nos["status"] == "NOT_SCHEDULED"


# --- ingestion --------------------------------------------------------------------------------


async def test_full_day_with_both_direction_camera(api: httpx.AsyncClient, setup: dict) -> None:  # type: ignore[type-arg]
    r = await send(api, setup, L(MON, 8, 55))
    assert r.status_code == 201
    ev = r.json()["event"]
    assert (
        ev["direction"] == "IN" and ev["counts_for_attendance"] and ev["work_date"] == "2026-11-02"
    )
    # Monday is already over (frozen clock = Wednesday): an IN without OUT is INCOMPLETE.
    assert r.json()["day"]["status"] == "INCOMPLETE"

    # Business rule 2: seen again 10 minutes later is NOT a departure.
    again = (await send(api, setup, L(MON, 9, 5))).json()["event"]
    assert (
        again["counts_for_attendance"] is False and again["ignore_reason"] == "REPEATED_DETECTION"
    )

    out = (await send(api, setup, L(MON, 18, 10))).json()
    assert out["event"]["direction"] == "OUT"
    d = out["day"]
    assert d["arrival"].startswith("2026-11-02T03:55") and d["departure"].startswith(
        "2026-11-02T13:10"
    )
    assert d["worked_minutes"] == 9 * 60 + 15 - 60
    assert d["status"] == "PRESENT" and d["late_minutes"] == 0 and d["early_leave_minutes"] == 0


async def test_idempotent_replay(
    api: httpx.AsyncClient, setup: dict, session: AsyncSession
) -> None:  # type: ignore[type-arg]
    uid = uuid.uuid4()
    first = await send(api, setup, L(MON, 9), uid=uid)
    second = await send(api, setup, L(MON, 9), uid=uid)
    assert first.status_code == 201 and second.status_code == 200
    assert first.json()["event"]["id"] == second.json()["event"]["id"]
    n = (await session.execute(select(func.count()).select_from(AttendanceEvent))).scalar_one()
    assert n == 1


async def test_debounce_and_fixed_direction_rules(api: httpx.AsyncClient, setup: dict) -> None:  # type: ignore[type-arg]
    assert (await send(api, setup, L(MON, 9), CameraDirection.IN)).json()["event"][
        "direction"
    ] == "IN"
    deb = (await send(api, setup, L(MON, 9, 0) + timedelta(seconds=30), CameraDirection.IN)).json()[
        "event"
    ]
    assert deb["ignore_reason"] == "DEBOUNCED"
    dup = (await send(api, setup, L(MON, 10), CameraDirection.IN)).json()["event"]
    assert dup["ignore_reason"] == "DUPLICATE_IN"
    out = (await send(api, setup, L(MON, 18), CameraDirection.OUT)).json()["event"]
    assert out["direction"] == "OUT" and out["counts_for_attendance"]
    orphan = (await send(api, setup, L(TUE, 18), CameraDirection.OUT)).json()["event"]
    assert orphan["ignore_reason"] == "OUT_WITHOUT_IN"
    mon = (await send(api, setup, L(TUE, 9), CameraDirection.NONE)).json()["event"]
    assert mon["ignore_reason"] == "MONITOR_ONLY"


async def test_unmatched_and_payload_rules(api: httpx.AsyncClient, setup: dict) -> None:  # type: ignore[type-arg]
    unk = await send(api, setup, L(MON, 9), decision="UNKNOWN")
    assert unk.status_code == 201
    assert (
        unk.json()["event"]["employee_id"] is None
        and unk.json()["event"]["ignore_reason"] == "UNMATCHED"
    )
    assert (
        await send(api, setup, L(MON, 9), decision="UNKNOWN", employee_id=str(oid(setup["emp"])))
    ).status_code == 422
    no_emp = await api.post(
        "/api/v1/attendance/events",
        headers=setup["h"],
        json={
            "event_uid": str(uuid.uuid4()),
            "camera_id": str(oid(setup["cams"][CameraDirection.IN])),
            "event_time": L(MON, 9).isoformat(),
            "decision": "MATCHED",
            "model_id": "m",
        },
    )
    assert no_emp.status_code == 422


@pytest.mark.parametrize(
    ("t", "code"),
    [
        (NOW + timedelta(minutes=5), "in_future"),
        (NOW - timedelta(days=8), "too_old"),
    ],
)
async def test_reliable_timestamps(
    api: httpx.AsyncClient, setup: dict, t: datetime, code: str
) -> None:  # type: ignore[type-arg]
    r = await send(api, setup, t)
    assert r.status_code == 422 and r.json()["errors"][0]["code"] == code


async def test_naive_timestamp_and_unknown_camera(api: httpx.AsyncClient, setup: dict) -> None:  # type: ignore[type-arg]
    naive = await send(api, setup, L(MON, 9), event_time="2026-11-02T09:00:00")
    assert naive.status_code == 422
    cam = await send(api, setup, L(MON, 9), camera_id=str(uuid.uuid4()))
    assert cam.status_code == 422 and cam.json()["errors"][0]["field"] == "camera_id"


async def test_employee_eligibility(
    api: httpx.AsyncClient, setup: dict, session: AsyncSession
) -> None:  # type: ignore[type-arg]
    dept = setup["dept"]
    suspended = await f.employee(session, dept=dept, status=EmployeeStatus.SUSPENDED)
    manual = await f.employee(session, dept=dept, attendance_method=AttendanceMethod.MANUAL)
    future = await f.employee(session, dept=dept, hire_date=date(2026, 11, 3))
    for emp, reason in (
        (suspended, "EMPLOYEE_INACTIVE"),
        (manual, "NOT_BIOMETRIC"),
        (future, "OUTSIDE_EMPLOYMENT"),
    ):
        ev = (await send(api, setup, L(MON, 9), emp=emp)).json()["event"]
        assert ev["ignore_reason"] == reason, emp


async def test_late_and_early(api: httpx.AsyncClient, setup: dict) -> None:  # type: ignore[type-arg]
    await send(api, setup, L(MON, 9, 20), CameraDirection.IN)
    d = (await send(api, setup, L(MON, 17), CameraDirection.OUT)).json()["day"]
    assert d["status"] == "LATE" and d["late_minutes"] == 20 and d["early_leave_minutes"] == 60
    assert d["worked_minutes"] == 7 * 60 + 40 - 60


async def test_night_shift_out_belongs_to_previous_day(
    api: httpx.AsyncClient,
    setup: dict,
    session: AsyncSession,  # type: ignore[type-arg]
) -> None:
    r = await api.post(
        "/api/v1/work-schedules",
        headers=setup["h"],
        json={
            "name": "Tun",
            "start_time": "22:00",
            "end_time": "06:00",
            "working_days": [1, 2, 3, 4, 5],
        },
    )
    guard = await f.employee(
        session, dept=setup["dept"], work_schedule_id=uuid.UUID(r.json()["id"])
    )
    await send(api, setup, L(MON, 21, 55), CameraDirection.IN, emp=guard)
    out = (await send(api, setup, L(TUE, 6, 2), CameraDirection.OUT, emp=guard)).json()
    assert out["event"]["work_date"] == "2026-11-02"
    assert out["day"]["work_date"] == "2026-11-02" and out["day"]["status"] == "PRESENT"


# --- days, absence, board ---------------------------------------------------------------------


async def test_absence_weekend_pending(api: httpx.AsyncClient, setup: dict) -> None:  # type: ignore[type-arg]
    assert (await get_day(api, setup, MON))["status"] == "ABSENT"
    assert (await get_day(api, setup, SAT))["status"] == "WEEKEND"
    today = await get_day(api, setup, NOW.date())
    assert today["status"] == "PENDING"  # Wednesday noon, nobody arrived yet: not absent yet


async def test_days_list_filters_and_departments(
    api: httpx.AsyncClient,
    setup: dict,
    session: AsyncSession,  # type: ignore[type-arg]
) -> None:
    other_dept = await f.department(session, name="Ombor")
    other_dept.work_schedule_id = uuid.UUID(setup["sched"])
    other = await f.employee(session, dept=other_dept)
    await session.flush()
    await send(api, setup, L(MON, 9), CameraDirection.IN)
    await send(api, setup, L(MON, 18), CameraDirection.OUT)

    h = setup["h"]
    r = await api.get(f"/api/v1/attendance/days?date_from={MON}&date_to={MON}", headers=h)
    statuses = {i["employee"]["id"]: i["status"] for i in r.json()["items"]}
    assert statuses[str(setup["emp"].id)] == "PRESENT" and statuses[str(other.id)] == "ABSENT"

    only = await api.get(
        f"/api/v1/attendance/days?date_from={MON}&date_to={MON}&department_id={other_dept.id}",
        headers=h,
    )
    assert [i["employee"]["id"] for i in only.json()["items"]] == [str(other.id)]

    week = await api.get(
        f"/api/v1/attendance/days?date_from={SAT}&date_to={MON + timedelta(days=4)}"
        f"&employee_id={setup['emp'].id}&status=ABSENT",
        headers=h,
    )
    assert [i["work_date"] for i in week.json()["items"]] == [
        "2026-11-03"
    ]  # Tue; Wed+ pending/future

    too_long = await api.get(
        "/api/v1/attendance/days?date_from=2026-01-01&date_to=2026-03-01", headers=h
    )
    assert too_long.status_code == 422
    backwards = await api.get(f"/api/v1/attendance/days?date_from={TUE}&date_to={MON}", headers=h)
    assert backwards.status_code == 422


async def test_board(api: httpx.AsyncClient, setup: dict, session: AsyncSession) -> None:  # type: ignore[type-arg]
    late = await f.employee(session, dept=setup["dept"])
    await f.employee(session, dept=setup["dept"])  # absent
    await send(api, setup, L(MON, 9), CameraDirection.IN)
    await send(api, setup, L(MON, 18), CameraDirection.OUT)
    await send(api, setup, L(MON, 9, 40), CameraDirection.IN, emp=late)
    b = (
        await api.get(
            f"/api/v1/attendance/board?date={MON}&department_id={setup['dept'].id}",
            headers=setup["h"],
        )
    ).json()
    assert (
        b["totals"]["PRESENT"] == 1
        and b["totals"]["ABSENT"] == 1
        and b["totals"]["INCOMPLETE"] == 1
    )
    assert b["totals"]["EMPLOYEES"] == 3


async def test_recompute_persists_snapshots(
    api: httpx.AsyncClient,
    setup: dict,
    session: AsyncSession,  # type: ignore[type-arg]
) -> None:
    await send(api, setup, L(MON, 9), CameraDirection.IN)
    r = await api.post(
        "/api/v1/attendance/recompute",
        headers=setup["h"],
        json={
            "date_from": SAT.isoformat(),
            "date_to": NOW.date().isoformat(),
            "employee_id": str(setup["emp"].id),
        },
    )
    assert r.status_code == 200
    assert r.json() == {
        "days_stored": 4,
        "days_pending": 1,
    }  # Sat, Sun, Mon, Tue stored; Wed pending
    rows = dict(
        (
            await session.execute(
                select(AttendanceDaily.work_date, AttendanceDaily.status).where(
                    AttendanceDaily.employee_id == setup["emp"].id
                )
            )
        ).all()
    )
    assert (
        rows[MON].value == "INCOMPLETE"
        and rows[TUE].value == "ABSENT"
        and rows[SAT].value == "WEEKEND"
    )
    assert (
        await session.execute(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.action == "ATTENDANCE_RECOMPUTED")
        )
    ).scalar_one() == 1


# --- corrections ------------------------------------------------------------------------------


async def test_correction_adds_missing_out_and_is_audited(
    api: httpx.AsyncClient,
    setup: dict,
    session: AsyncSession,  # type: ignore[type-arg]
) -> None:
    await send(api, setup, L(MON, 9), CameraDirection.IN)
    assert (await get_day(api, setup, MON))["status"] == "INCOMPLETE"
    r = await correct(
        api, setup, MON, [{"op": "add", "direction": "OUT", "event_time": L(MON, 18).isoformat()}]
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["day"]["status"] == "PRESENT" and body["day"]["has_manual_events"] is True
    assert (
        body["added"][0]["source"] == "MANUAL"
        and body["added"][0]["correction_id"] == body["correction_id"]
    )

    entry = (
        await session.execute(select(AuditLog).where(AuditLog.action == "ATTENDANCE_CORRECTED"))
    ).scalar_one()
    assert entry.entity_id == body["correction_id"]
    assert entry.changes["reason"][1] == "Xodim kamerani chetlab o'tdi"
    assert entry.changes["added"][1][0]["direction"] == "OUT"
    stored = (
        await session.execute(
            select(AttendanceDaily).where(
                AttendanceDaily.employee_id == setup["emp"].id, AttendanceDaily.work_date == MON
            )
        )
    ).scalar_one()
    assert stored.status.value == "PRESENT" and stored.has_manual_events


async def test_correction_changes_time_atomically(
    api: httpx.AsyncClient,
    setup: dict,
    session: AsyncSession,  # type: ignore[type-arg]
) -> None:
    ev = (await send(api, setup, L(MON, 9, 40), CameraDirection.IN)).json()["event"]
    await send(api, setup, L(MON, 18), CameraDirection.OUT)
    r = await correct(
        api,
        setup,
        MON,
        [
            {"op": "void", "event_id": ev["id"]},
            {
                "op": "add",
                "direction": "IN",
                "event_time": L(MON, 9).isoformat(),
                "note": "Kamera kech ko'rdi",
            },
        ],
        reason="Kamera ishlamay qoldi",
    )
    assert r.status_code == 200
    assert r.json()["day"]["status"] == "PRESENT" and r.json()["day"]["late_minutes"] == 0
    voided = r.json()["voided"][0]
    assert (
        voided["is_voided"]
        and voided["void_reason"] == "Kamera ishlamay qoldi"
        and voided["voided_by_user_id"]
    )
    detail = await get_day(api, setup, MON)
    assert len(detail["events"]) == 3  # voided original still visible in history


@pytest.mark.parametrize(
    "ops",
    [
        [
            {"op": "add", "direction": "IN", "event_time": "2026-11-02T10:00:00+05:00"}
        ],  # IN while IN
        [
            {"op": "add", "direction": "OUT", "event_time": "2026-11-02T08:00:00+05:00"}
        ],  # OUT before IN
    ],
)
async def test_correction_rejects_invalid_sequence(
    api: httpx.AsyncClient,
    setup: dict,
    session: AsyncSession,
    ops: list[dict[str, Any]],  # type: ignore[type-arg]
) -> None:
    await send(api, setup, L(MON, 9), CameraDirection.IN)
    r = await correct(api, setup, MON, ops)
    assert r.status_code == 409 and r.json()["code"] == "INVALID_SEQUENCE"
    n = (
        await session.execute(
            select(func.count())
            .select_from(AttendanceEvent)
            .where(AttendanceEvent.source == "MANUAL")
        )
    ).scalar_one()
    assert n == 0  # nothing applied


async def test_correction_void_that_breaks_sequence(api: httpx.AsyncClient, setup: dict) -> None:  # type: ignore[type-arg]
    ev_in = (await send(api, setup, L(MON, 9), CameraDirection.IN)).json()["event"]
    await send(api, setup, L(MON, 18), CameraDirection.OUT)
    r = await correct(api, setup, MON, [{"op": "void", "event_id": ev_in["id"]}])
    assert r.status_code == 409  # would leave an OUT without IN


async def test_correction_validation(api: httpx.AsyncClient, setup: dict, tokens: dict) -> None:  # type: ignore[type-arg]
    add_out = {"op": "add", "direction": "OUT", "event_time": L(MON, 18).isoformat()}
    cases = [
        (await correct(api, setup, MON, [{"op": "void", "event_id": 999999}]), 422),
        (
            await correct(
                api,
                setup,
                MON,
                [{"op": "add", "direction": "IN", "event_time": L(TUE, 9).isoformat()}],
            ),
            422,
        ),  # other work date
        (
            await correct(
                api,
                setup,
                MON,
                [{"op": "add", "direction": "IN", "event_time": "2026-11-02T09:00:00"}],
            ),
            422,
        ),  # naive
        (await correct(api, setup, MON, [add_out], reason="x"), 422),  # reason too short
        (await correct(api, setup, MON, []), 422),
        (await correct(api, setup, date(2024, 1, 1), [add_out]), 422),  # before hire
        (await correct(api, setup, MON, [{"op": "explode"}]), 422),
    ]
    for r, code in cases:
        assert r.status_code == code, r.text
    missing = await api.post(
        "/api/v1/attendance/corrections",
        headers=setup["h"],
        json={
            "employee_id": str(uuid.uuid4()),
            "work_date": MON.isoformat(),
            "reason": "test reason",
            "operations": [add_out],
        },
    )
    assert missing.status_code == 404
    viewer = await api.post(
        "/api/v1/attendance/corrections",
        headers=tokens["VIEWER"],
        json={
            "employee_id": str(oid(setup["emp"])),
            "work_date": MON.isoformat(),
            "reason": "test reason",
            "operations": [add_out],
        },
    )
    assert viewer.status_code == 403


async def test_manual_only_employee_gets_attendance_via_corrections(
    api: httpx.AsyncClient,
    setup: dict,
    session: AsyncSession,  # type: ignore[type-arg]
) -> None:
    manual = await f.employee(
        session, dept=setup["dept"], attendance_method=AttendanceMethod.MANUAL
    )
    r = await correct(
        api,
        setup,
        MON,
        [
            {"op": "add", "direction": "IN", "event_time": L(MON, 9).isoformat()},
            {"op": "add", "direction": "OUT", "event_time": L(MON, 18).isoformat()},
        ],
        reason="Qo'lda davomat jurnali",
        emp=manual,
    )
    assert r.status_code == 200 and r.json()["day"]["status"] == "PRESENT"


# --- history & permissions --------------------------------------------------------------------


async def test_event_history_and_permissions(
    api: httpx.AsyncClient, setup: dict, tokens: dict
) -> None:  # type: ignore[type-arg]
    await send(api, setup, L(MON, 9))
    await send(api, setup, L(MON, 9, 10))  # repeated detection
    await send(api, setup, L(MON, 9), decision="UNKNOWN")
    v = tokens["VIEWER"]
    all_ = (await api.get("/api/v1/attendance/events", headers=v)).json()
    assert all_["total"] == 3
    counted = (
        await api.get(
            f"/api/v1/attendance/events?employee_id={setup['emp'].id}&counted=true", headers=v
        )
    ).json()
    assert counted["total"] == 1
    paged = (await api.get("/api/v1/attendance/events?page_size=1&page=2", headers=v)).json()
    assert len(paged["items"]) == 1 and paged["total"] == 3
    # VIEWER reads but cannot write.
    r = await api.post("/api/v1/attendance/events", headers=v, json={})
    assert r.status_code == 403
    assert (
        await api.post(
            "/api/v1/attendance/recompute",
            headers=v,
            json={"date_from": MON.isoformat(), "date_to": MON.isoformat()},
        )
    ).status_code == 403


async def test_department_default_schedule_validation(
    api: httpx.AsyncClient,
    setup: dict,
    session: AsyncSession,  # type: ignore[type-arg]
) -> None:
    d: Department = await f.department(session, name="Yangi")
    bad = await api.patch(
        f"/api/v1/departments/{d.id}",
        headers=setup["h"],
        json={"work_schedule_id": str(uuid.uuid4())},
    )
    assert bad.status_code == 422
    ok = await api.patch(
        f"/api/v1/departments/{oid(d)}",
        headers=setup["h"],
        json={"work_schedule_id": setup["sched"]},
    )
    assert ok.status_code == 200 and ok.json()["work_schedule_id"] == setup["sched"]
