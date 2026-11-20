"""Cameras serve departments: everyone is identified, only the served departments are counted."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.attendance import service as att
from app.models.enums import (
    AttendanceMethod,
    CameraDirection,
    RecognitionDecision,
    UserRole,
)
from tests import factories as f
from tests.api_helpers import bearer, make_user, oid, token_for
from tests.conftest import requires_db

pytestmark = requires_db


async def _org(session: AsyncSession) -> dict[str, Any]:
    """AKT (with a sub-department "AKT / Tarmoq") and Moliya; one employee in each."""
    akt = await f.department(session, name=f"AKT {uuid.uuid4().hex[:6]}")
    net = await f.department(session, name=f"Tarmoq {uuid.uuid4().hex[:6]}", parent_id=akt.id)
    fin = await f.department(session, name=f"Moliya {uuid.uuid4().hex[:6]}")
    ws = await f.schedule(
        session, name=f"24/7 {uuid.uuid4().hex[:6]}", working_days=[1, 2, 3, 4, 5, 6, 7]
    )
    for d in (akt, net, fin):
        d.work_schedule_id = ws.id
    emps = {
        name: await f.employee(session, dept=d, attendance_method=AttendanceMethod.FACE)
        for name, d in (("akt", akt), ("net", net), ("fin", fin))
    }
    cam = await f.camera(
        session, name="AKT xonasi", direction=CameraDirection.IN, recognition_enabled=True
    )
    cam.department_ids = [akt.id]
    await session.flush()
    return {"akt": akt, "fin": fin, "emps": emps, "cam": cam}


async def _seen(session: AsyncSession, cam: Any, emp: Any) -> Any:
    r = await att.ingest_camera_event(
        session,
        event_uid=uuid.uuid4(),
        camera_id=cam.id,
        event_time=datetime.now(UTC) - timedelta(seconds=5),
        decision=RecognitionDecision.MATCHED,
        employee_id=emp.id,
        model_id="sface@2021dec",
        similarity=0.8,
    )
    return r.event


async def test_only_served_departments_count(session: AsyncSession) -> None:
    o = await _org(session)
    own = await _seen(session, o["cam"], o["emps"]["akt"])
    assert own.counts_for_attendance and own.direction.value == "IN"
    sub = await _seen(session, o["cam"], o["emps"]["net"])  # sub-department of AKT
    assert sub.counts_for_attendance
    other = await _seen(session, o["cam"], o["emps"]["fin"])
    # identified as who they are (never "mistaken" for an AKT member), but not counted
    assert other.employee_id == o["emps"]["fin"].id
    assert other.counts_for_attendance is False and other.ignore_reason == "OTHER_DEPARTMENT"


async def test_camera_without_departments_serves_everyone(session: AsyncSession) -> None:
    o = await _org(session)
    o["cam"].department_ids = []
    await session.flush()
    assert (await _seen(session, o["cam"], o["emps"]["fin"])).counts_for_attendance


async def test_api_set_departments_without_reconnecting_the_camera(
    api: httpx.AsyncClient, session: AsyncSession
) -> None:
    h = bearer(await token_for(api, await make_user(session, role=UserRole.SUPER_ADMIN)))
    akt_id = oid(await f.department(session, name=f"AKT {uuid.uuid4().hex[:6]}"))
    body = {"name": "AKT xonasi", "provider": "TEST", "department_ids": [str(akt_id)]}
    r = await api.post("/api/v1/cameras", json=body, headers=h)
    assert r.status_code == 201, r.text
    cam = r.json()
    assert cam["department_ids"] == [str(akt_id)]

    unknown = await api.patch(
        f"/api/v1/cameras/{cam['id']}", json={"department_ids": [str(uuid.uuid4())]}, headers=h
    )
    assert unknown.status_code == 422

    cleared = await api.patch(
        f"/api/v1/cameras/{cam['id']}", json={"department_ids": []}, headers=h
    )
    assert cleared.status_code == 200
    out = cleared.json()
    assert out["department_ids"] == []
    assert out["config_version"] == cam["config_version"]  # counting rule only: no reconnect
