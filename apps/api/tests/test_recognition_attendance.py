"""Recognition -> attendance: master switch, camera gate, engine rules, settings write API."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models import AttendanceEvent, AuditLog, SystemSetting
from app.models.enums import AttendanceMethod, CameraDirection, CameraQualityStatus, UserRole
from tests import factories as f
from tests.api_helpers import bearer, make_user, oid, token_for
from tests.conftest import requires_db

pytestmark = requires_db

CAM_TOKEN = "camera-service-token-0123456789abcd"
H = {"Authorization": f"Bearer {CAM_TOKEN}"}
URL = "/internal/v1/recognition-events"


@pytest.fixture(autouse=True)
def camera_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "camera_service_token", SecretStr(CAM_TOKEN))


async def _switch(session: AsyncSession, on: bool) -> None:
    row = await session.get(SystemSetting, "recognition.attendance_enabled")
    assert row is not None, "migration 0006 seeds the switch"
    row.value = on
    await session.flush()


async def _setup(session: AsyncSession, **cam: Any) -> tuple[Any, Any]:
    emp = await f.employee(session, attendance_method=AttendanceMethod.FACE)
    c = await f.camera(
        session,
        direction=cam.pop("direction", CameraDirection.IN),
        is_enabled=cam.pop("is_enabled", True),
        recognition_enabled=cam.pop("recognition_enabled", True),
        quality_status=cam.pop("quality_status", CameraQualityStatus.PASSED),
    )
    return emp, c


def _event(emp: Any, cam: Any, at: datetime | None = None, **over: Any) -> dict[str, Any]:
    return {
        "event_uid": str(uuid.uuid4()),
        "camera_id": str(oid(cam)),
        "employee_id": str(oid(emp)),
        "event_time": (at or datetime.now(UTC)).isoformat(),
        "decision": "MATCHED",
        "model_id": "sface@2021dec",
        "similarity": 0.81,
        "second_similarity": 0.12,
        "quality_score": 0.95,
        **over,
    }


async def test_switch_off_by_default_records_nothing(
    api: httpx.AsyncClient, session: AsyncSession
) -> None:
    emp, cam = await _setup(session)
    r = await api.post(URL, json=_event(emp, cam), headers=H)
    assert r.json() == {"recorded": False, "reason": "attendance_disabled"}
    assert (await session.execute(select(AttendanceEvent))).first() is None


async def test_switch_on_creates_attendance_with_engine_rules(
    api: httpx.AsyncClient, session: AsyncSession
) -> None:
    emp, cam = await _setup(session)
    await _switch(session, True)
    t = datetime.now(UTC) - timedelta(minutes=5)
    r = await api.post(URL, json=_event(emp, cam, t), headers=H)
    body = r.json()
    assert r.status_code == 200 and body["recorded"] is True and body["counted"] is True
    assert body["direction"] == "IN" and body["work_date"]
    # a second sighting 20 s later at the same camera is debounced, not a second IN
    r = await api.post(URL, json=_event(emp, cam, t + timedelta(seconds=20)), headers=H)
    assert r.json()["counted"] is False and r.json()["ignore_reason"] == "DEBOUNCED"
    # idempotent replay of the same event_uid
    ev = _event(emp, cam, t + timedelta(minutes=2))
    first = (await api.post(URL, json=ev, headers=H)).json()
    again = (await api.post(URL, json=ev, headers=H)).json()
    assert again["created"] is False and first["created"] is True
    rows = (await session.execute(select(AttendanceEvent))).scalars().all()
    assert {r.model_id for r in rows} == {"sface@2021dec"} and len(rows) == 3


@pytest.mark.parametrize(
    "cam",
    [
        {"is_enabled": False},
        {"recognition_enabled": False},
        {"quality_status": CameraQualityStatus.INCONCLUSIVE},
    ],
)
async def test_camera_must_be_enabled_and_quality_passed(
    api: httpx.AsyncClient, session: AsyncSession, cam: dict[str, Any]
) -> None:
    emp, c = await _setup(session, **cam)
    await _switch(session, True)
    r = await api.post(URL, json=_event(emp, c), headers=H)
    assert r.json() == {"recorded": False, "reason": "camera_not_enabled"}


async def test_only_matched_and_valid_events(api: httpx.AsyncClient, session: AsyncSession) -> None:
    emp, cam = await _setup(session)
    await _switch(session, True)
    r = await api.post(URL, json=_event(emp, cam, decision="AMBIGUOUS"), headers=H)
    assert r.status_code == 422  # uncertain observations can never become attendance
    future = datetime.now(UTC) + timedelta(hours=1)
    r = await api.post(URL, json=_event(emp, cam, future), headers=H)
    assert r.status_code == 422 and r.json()["errors"][0]["code"] == "in_future"
    assert (await api.post(URL, json=_event(emp, cam))).status_code == 401


async def test_manual_method_employee_is_not_counted(
    api: httpx.AsyncClient, session: AsyncSession
) -> None:
    _, cam = await _setup(session)
    manual = await f.employee(session, attendance_method=AttendanceMethod.MANUAL)
    await _switch(session, True)
    r = await api.post(URL, json=_event(manual, cam), headers=H)
    assert r.json()["counted"] is False and r.json()["ignore_reason"] == "NOT_BIOMETRIC"


# --- settings write API --------------------------------------------------------------------


async def test_super_admin_toggles_switch_and_it_is_audited(
    api: httpx.AsyncClient, session: AsyncSession
) -> None:
    h = bearer(await token_for(api, await make_user(session, role=UserRole.SUPER_ADMIN)))
    url = "/api/v1/settings/recognition.attendance_enabled"
    r = await api.patch(url, json={"value": True}, headers=h)
    assert r.status_code == 200 and r.json()["value"] is True
    assert (await api.patch(url, json={"value": "yes"}, headers=h)).status_code == 422
    assert (await api.patch(url, json={"value": 1}, headers=h)).status_code == 422
    r = await api.patch(
        "/api/v1/settings/recognition.match_threshold", json={"value": 0.1}, headers=h
    )
    assert r.status_code == 404  # not writable through the API
    r = await api.patch(
        "/api/v1/settings/attendance.debounce_seconds", json={"value": 5}, headers=h
    )
    assert r.status_code == 422
    logs = (
        (await session.execute(select(AuditLog).where(AuditLog.action == "SETTING_UPDATED")))
        .scalars()
        .all()
    )
    assert len(logs) == 1 and logs[0].changes == {"value": [False, True]}


async def test_admin_cannot_change_settings(api: httpx.AsyncClient, session: AsyncSession) -> None:
    h = bearer(await token_for(api, await make_user(session, role=UserRole.ADMIN)))
    r = await api.patch(
        "/api/v1/settings/recognition.attendance_enabled", json={"value": True}, headers=h
    )
    assert r.status_code == 403
