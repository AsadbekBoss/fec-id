"""Reports: monthly timesheet, lateness, absence — JSON preview, Excel and PDF exports."""

from __future__ import annotations

import io
from datetime import date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest
from openpyxl import load_workbook
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog
from app.models.enums import AttendanceMethod, UserRole
from tests import factories as f
from tests.api_helpers import bearer, make_user, oid, token_for
from tests.conftest import requires_db

pytestmark = requires_db

TZ = ZoneInfo("Asia/Tashkent")
# October 2026: Thu 1st. Mon 5, Tue 6, Wed 7 are working days in the 5/2 schedule.
MON, TUE, WED = date(2026, 10, 5), date(2026, 10, 6), date(2026, 10, 7)


def L(d: date, h: int, m: int = 0) -> datetime:
    return datetime.combine(d, time(h, m), TZ)


@pytest.fixture
async def setup(api: httpx.AsyncClient, session: AsyncSession) -> dict[str, Any]:
    ws = await f.schedule(
        session, name="Ofis 9-18", start_time=time(9, 0), end_time=time(18, 0), grace_in_minutes=5
    )
    dept = await f.department(session, name="Hisobot boʻlimi", work_schedule_id=ws.id)
    emp = await f.employee(
        session,
        dept,
        employee_code="E-REP",
        last_name="Karimov",
        first_name="Ali",
        hire_date=date(2026, 9, 1),
        attendance_method=AttendanceMethod.FACE,
    )
    h = bearer(await token_for(api, await make_user(session, role=UserRole.SUPER_ADMIN)))

    async def punch(d: date, ops: list[tuple[str, datetime]]) -> None:
        r = await api.post(
            "/api/v1/attendance/corrections",
            headers=h,
            json={
                "employee_id": str(oid(emp)),
                "work_date": d.isoformat(),
                "reason": "test",
                "operations": [
                    {"op": "add", "direction": dr, "event_time": t.isoformat()} for dr, t in ops
                ],
            },
        )
        assert r.status_code == 200, r.text

    await punch(MON, [("IN", L(MON, 8, 55)), ("OUT", L(MON, 18, 0))])  # on time
    await punch(TUE, [("IN", L(TUE, 9, 25)), ("OUT", L(TUE, 18, 0))])  # 25 min late
    # WED: no punches -> absent
    return {"h": h, "dept": str(oid(dept))}


async def test_timesheet_json(api: httpx.AsyncClient, setup: dict[str, Any]) -> None:
    r = await api.get("/api/v1/reports/timesheet", params={"month": "2026-10"}, headers=setup["h"])
    assert r.status_code == 200, r.text
    body = r.json()
    [row] = [x for x in body["rows"] if x["code"] == "E-REP"]
    assert (row["d5"], row["d6"], row["d7"], row["d3"]) == ("+", "K", "Y", "D")
    assert row["late_days"] == 1 and row["late_minutes"] == 25 and row["absent_days"] >= 1
    assert row["came"] == 2 and row["department"] == "Hisobot boʻlimi"
    assert any("Belgilar" in line for line in body["footer"])


async def test_timesheet_excel_and_pdf_are_audited(
    api: httpx.AsyncClient, setup: dict[str, Any], session: AsyncSession
) -> None:
    params = {"month": "2026-10", "department_id": setup["dept"]}
    x = await api.get(
        "/api/v1/reports/timesheet", params={**params, "format": "xlsx"}, headers=setup["h"]
    )
    assert x.status_code == 200
    assert x.headers["content-type"].startswith("application/vnd.openxmlformats")
    assert 'filename="tabel_2026-10.xlsx"' in x.headers["content-disposition"]
    wb = load_workbook(io.BytesIO(x.content))
    assert wb.sheetnames == ["Tabel", "Kunlar"]
    ws = wb["Tabel"]
    values = [[c.value for c in row] for row in ws.iter_rows()]
    assert any("E-REP" in r for r in values) and values[0][0].startswith("Davomat tabeli")
    days = [[c.value for c in row] for row in wb["Kunlar"].iter_rows()]
    assert any(r[0] == "2026-10-06" and r[3] == "Kechikdi" and r[4] == "09:25" for r in days)

    p = await api.get(
        "/api/v1/reports/timesheet", params={**params, "format": "pdf"}, headers=setup["h"]
    )
    assert (
        p.status_code == 200
        and p.content.startswith(b"%PDF")
        and p.headers["content-type"] == "application/pdf"
    )
    logs = (
        (await session.execute(select(AuditLog).where(AuditLog.action == "REPORT_EXPORTED")))
        .scalars()
        .all()
    )
    assert sorted(lg.changes["format"][1] for lg in logs) == ["pdf", "xlsx"]


async def test_lateness_and_absence(api: httpx.AsyncClient, setup: dict[str, Any]) -> None:
    q = {"date_from": "2026-10-05", "date_to": "2026-10-07", "department_id": setup["dept"]}
    late = (await api.get("/api/v1/reports/lateness", params=q, headers=setup["h"])).json()
    assert [(r["date"], r["arrived"], r["late_minutes"], r["scheduled"]) for r in late["rows"]] == [
        ("2026-10-06", "09:25", 25, "09:00")
    ]
    absent = (await api.get("/api/v1/reports/absence", params=q, headers=setup["h"])).json()
    assert [(r["date"], r["code"], r["schedule"]) for r in absent["rows"]] == [
        ("2026-10-07", "E-REP", "09:00–18:00")
    ]
    for kind in ("lateness", "absence"):
        for fmt in ("xlsx", "pdf"):
            r = await api.get(
                f"/api/v1/reports/{kind}", params={**q, "format": fmt}, headers=setup["h"]
            )
            assert r.status_code == 200 and len(r.content) > 500


async def test_validation_and_permissions(
    api: httpx.AsyncClient, setup: dict[str, Any], session: AsyncSession
) -> None:
    h = setup["h"]
    r = await api.get(
        "/api/v1/reports/lateness",
        params={"date_from": "2026-10-10", "date_to": "2026-10-01"},
        headers=h,
    )
    assert r.status_code == 422
    r = await api.get(
        "/api/v1/reports/absence",
        params={"date_from": "2026-01-01", "date_to": "2026-12-31"},
        headers=h,
    )
    assert r.status_code == 422 and "92" in r.text
    assert (
        await api.get("/api/v1/reports/timesheet", params={"month": "2026-13"}, headers=h)
    ).status_code == 422
    r = await api.get(
        "/api/v1/reports/timesheet", params={"month": "2026-10", "format": "doc"}, headers=h
    )
    assert r.status_code == 422
    viewer = bearer(await token_for(api, await make_user(session, role=UserRole.VIEWER)))
    assert (
        await api.get("/api/v1/reports/timesheet", params={"month": "2026-10"}, headers=viewer)
    ).status_code == 200
    assert (
        await api.get("/api/v1/reports/timesheet", params={"month": "2026-10"})
    ).status_code == 401


def test_pdf_handles_uzbek_letters_and_empty_report() -> None:
    from app.services import reports as rpt

    rep = rpt.Report(
        "absence", "Kelmaganlar — boʻsh", "Boʻlim: Oʻquv", [rpt.Column("a", "Ism")], []
    )
    assert rpt.to_pdf(rep).startswith(b"%PDF")
    rep.rows = [{"a": "Gʻulomov Oʻtkir"}]
    assert rpt.to_pdf(rep).startswith(b"%PDF") and rpt.to_xlsx(rep)[:2] == b"PK"
