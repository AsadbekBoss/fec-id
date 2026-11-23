"""Holidays and employee leave (Phase 24): excused days are never "absent"."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.attendance import engine
from app.models.enums import UserRole
from tests import factories as f
from tests.api_helpers import bearer, make_user, token_for
from tests.conftest import requires_db

MON, TUE, WED = date(2026, 11, 2), date(2026, 11, 3), date(2026, 11, 4)


def _day(status: engine.DayStatus, first_in: datetime | None = None, late: int = 0) -> Any:
    return engine.DayResult(work_date=MON, status=status, first_in=first_in, late_minutes=late)


def test_excuse_rules() -> None:
    s = engine.DayStatus
    assert engine.excuse(_day(s.ABSENT), None, "SICK").status is s.LEAVE
    r = engine.excuse(_day(s.PENDING), "Mustaqillik kuni", "SICK")
    assert r.status is s.HOLIDAY and r.holiday_name == "Mustaqillik kuni"  # holiday wins
    came = engine.excuse(_day(s.LATE, datetime.now(UTC), 20), "Bayram", None)
    assert came.status is s.PRESENT and came.late_minutes == 0  # no lateness on a holiday
    assert engine.excuse(_day(s.WEEKEND), "Bayram", "SICK").status is s.WEEKEND
    worked = engine.excuse(_day(s.PRESENT, datetime.now(UTC)), None, "VACATION")
    assert worked.status is s.PRESENT  # came during leave: a worked day


async def _setup(session: AsyncSession) -> dict[str, Any]:
    b = await f.branch(session)
    ws = await f.schedule(session, branch_id=b.id, working_days=[1, 2, 3, 4, 5, 6, 7])
    d = await f.department(session, branch_id=b.id, work_schedule_id=ws.id)
    emp = await f.employee(session, d, hire_date=date(2026, 1, 1))
    main_ws = await f.schedule(session, working_days=[1, 2, 3, 4, 5, 6, 7])
    main_dept = await f.department(session, work_schedule_id=main_ws.id)
    other = await f.employee(session, main_dept, hire_date=date(2026, 1, 1))
    admin = await make_user(session, role=UserRole.ADMIN, branch_id=b.id)
    boss = await make_user(session, role=UserRole.SUPER_ADMIN)
    await session.flush()
    return {"branch": b.id, "emp": emp.id, "other": other.id, "admin": admin, "boss": boss}


async def _statuses(
    api: httpx.AsyncClient, h: dict[str, str], emp: Any, start: date = MON, end: date = WED
) -> dict[str, str]:
    r = await api.get(
        "/api/v1/attendance/days",
        headers=h,
        params={"date_from": str(start), "date_to": str(end), "employee_id": str(emp)},
    )
    assert r.status_code == 200, r.text
    return {i["work_date"]: i["status"] for i in r.json()["items"]}


def _leave(emp: Any, kind: str, start: date, end: date, **kw: Any) -> dict[str, Any]:
    return {
        "employee_id": str(emp),
        "kind": kind,
        "date_from": str(start),
        "date_to": str(end),
        **kw,
    }


@requires_db
async def test_leave_and_holiday_are_not_absent(
    api: httpx.AsyncClient, session: AsyncSession
) -> None:
    o = await _setup(session)
    h = bearer(await token_for(api, o["admin"]))
    assert (await _statuses(api, h, o["emp"]))[str(MON)] == "ABSENT"
    r = await api.post("/api/v1/leaves", headers=h, json=_leave(o["emp"], "SICK", MON, TUE))
    assert r.status_code == 201, r.text
    leave_id = r.json()["id"]
    r = await api.post("/api/v1/holidays", headers=h, json={"day": str(WED), "name": "Filial"})
    assert r.status_code == 201 and r.json()["branch_id"] == str(o["branch"])
    st = await _statuses(api, h, o["emp"])
    assert st == {str(MON): "LEAVE", str(TUE): "LEAVE", str(WED): "HOLIDAY"}
    # the branch holiday does not touch the main branch
    hb = bearer(await token_for(api, o["boss"]))
    assert (await _statuses(api, hb, o["other"]))[str(WED)] == "ABSENT"
    # absence report: nothing left for this employee
    r = await api.get(
        "/api/v1/reports/absence", headers=h, params={"date_from": str(MON), "date_to": str(WED)}
    )
    assert r.status_code == 200 and r.json()["rows"] == []
    # overlap refused, deleting restores ABSENT
    r = await api.post("/api/v1/leaves", headers=h, json=_leave(o["emp"], "VACATION", TUE, WED))
    assert r.status_code == 409 and r.json()["code"] == "LEAVE_OVERLAP"
    assert (await api.delete(f"/api/v1/leaves/{leave_id}", headers=h)).status_code == 204
    assert (await _statuses(api, h, o["emp"]))[str(MON)] == "ABSENT"


@requires_db
async def test_holiday_scopes_and_rights(api: httpx.AsyncClient, session: AsyncSession) -> None:
    o = await _setup(session)
    hb = bearer(await token_for(api, o["boss"]))
    body = {"day": "2026-12-08", "name": "Konstitutsiya kuni"}
    r = await api.post("/api/v1/holidays", headers=hb, json=body)
    assert r.status_code == 201 and r.json()["branch_id"] is None  # centre: every branch
    global_id = r.json()["id"]
    assert (await api.post("/api/v1/holidays", headers=hb, json=body)).status_code == 409
    h = bearer(await token_for(api, o["admin"]))
    listed = (await api.get("/api/v1/holidays", headers=h, params={"year": 2026})).json()
    assert next(x for x in listed if x["id"] == global_id)["editable"] is False
    assert (await api.delete(f"/api/v1/holidays/{global_id}", headers=h)).status_code == 403
    r = await api.post("/api/v1/holidays", headers=h, json={"day": "2026-12-09", "name": "F"})
    assert r.status_code == 201
    day = date(2026, 12, 8)
    assert (await _statuses(api, h, o["emp"], day, day))[str(day)] == "HOLIDAY"
    assert (await api.delete(f"/api/v1/holidays/{global_id}", headers=hb)).status_code == 204


@requires_db
async def test_leave_of_another_branch_is_hidden(
    api: httpx.AsyncClient, session: AsyncSession
) -> None:
    o = await _setup(session)
    h = bearer(await token_for(api, o["admin"]))
    r = await api.post("/api/v1/leaves", headers=h, json=_leave(o["other"], "TRIP", MON, MON))
    assert r.status_code == 404
    r = await api.post("/api/v1/leaves", headers=h, json=_leave(o["emp"], "TRIP", TUE, MON))
    assert r.status_code == 422
    hb = bearer(await token_for(api, o["boss"]))
    r = await api.post(
        "/api/v1/leaves", headers=hb, json=_leave(o["other"], "VACATION", MON, WED, note="yillik")
    )
    assert r.status_code == 201
    listed = (await api.get("/api/v1/leaves", headers=h, params={"date_from": str(MON)})).json()
    assert all(x["employee_id"] != str(o["other"]) for x in listed)
    assert (await api.delete(f"/api/v1/leaves/{r.json()['id']}", headers=h)).status_code == 404


@requires_db
async def test_timesheet_marks_excused_days(api: httpx.AsyncClient, session: AsyncSession) -> None:
    o = await _setup(session)
    h = bearer(await token_for(api, o["admin"]))
    await api.post("/api/v1/leaves", headers=h, json=_leave(o["emp"], "SICK", MON, MON))
    await api.post("/api/v1/holidays", headers=h, json={"day": str(TUE), "name": "Bayram"})
    r = await api.get("/api/v1/reports/timesheet", headers=h, params={"month": "2026-11"})
    assert r.status_code == 200, r.text
    row = r.json()["rows"][0]
    assert row["d2"] == "B" and row["d3"] == "H" and row["d4"] == "Y"
    assert row["leave_days"] == 1 and row["absent_days"] >= 1
