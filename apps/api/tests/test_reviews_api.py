"""Review queue: gated ingestion of uncertain faces, encrypted crops, assign / dismiss, expiry."""

from __future__ import annotations

import io
import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from PIL import Image
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
BASE = "/api/v1/recognition/reviews"


@pytest.fixture(autouse=True)
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    s = get_settings()
    monkeypatch.setattr(s, "camera_service_token", SecretStr(CAM_TOKEN))
    monkeypatch.setattr(s, "storage_dir", str(tmp_path))
    return tmp_path


def face_jpeg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (160, 200), (150, 120, 100)).save(buf, format="JPEG")
    return buf.getvalue()


async def _set(session: AsyncSession, key: str, value: Any) -> None:
    row = await session.get(SystemSetting, key)
    assert row is not None
    row.value = value
    await session.flush()


async def _camera(session: AsyncSession, **kw: Any) -> Any:
    return await f.camera(
        session,
        direction=kw.pop("direction", CameraDirection.IN),
        is_enabled=True,
        recognition_enabled=True,
        quality_status=kw.pop("quality_status", CameraQualityStatus.PASSED),
    )


async def _send(
    api: httpx.AsyncClient, cam: Any, decision: str = "AMBIGUOUS", at: datetime | None = None
) -> httpx.Response:
    meta = {
        "event_uid": str(uuid.uuid4()),
        "camera_id": str(oid(cam)),
        "event_time": (at or datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
        "decision": decision,
        "model_id": "sface@2021dec",
        "similarity": 0.38,
        "second_similarity": 0.35,
        "quality_score": 0.93,
    }
    return await api.post(
        "/internal/v1/recognition-reviews",
        headers=H,
        data={"meta": json.dumps(meta)},
        files={"file": ("face.jpg", face_jpeg(), "image/jpeg")},
    )


@pytest.fixture
async def admin(api: httpx.AsyncClient, session: AsyncSession) -> dict[str, str]:
    return bearer(await token_for(api, await make_user(session, role=UserRole.ADMIN)))


async def test_ingest_is_gated(api: httpx.AsyncClient, session: AsyncSession) -> None:
    cam = await _camera(session)
    assert (await _send(api, cam)).json() == {"stored": False, "reason": "attendance_disabled"}
    await _set(session, "recognition.attendance_enabled", True)
    await _set(session, "recognition.store_unknown_snapshots", False)
    assert (await _send(api, cam, "UNKNOWN")).json()["reason"] == "unknown_not_stored"
    bad = await _camera(session, quality_status=CameraQualityStatus.INCONCLUSIVE)
    assert (await _send(api, bad)).json()["reason"] == "camera_not_enabled"
    r = await _send(api, cam)
    assert r.json()["stored"] is True
    assert (
        await api.post(
            "/internal/v1/recognition-reviews",
            data={"meta": "{}"},
            files={"file": ("f.jpg", b"x", "image/jpeg")},
        )
    ).status_code == 401


async def test_crop_is_encrypted_and_viewing_is_audited(
    api: httpx.AsyncClient, session: AsyncSession, admin: dict[str, str], env: Path
) -> None:
    await _set(session, "recognition.attendance_enabled", True)
    cam = await _camera(session)
    rid = (await _send(api, cam)).json()["review_id"]
    files = [p for p in env.rglob("*") if p.is_file()]
    assert len(files) == 1 and b"JFIF" not in files[0].read_bytes()  # ciphertext only
    listing = (await api.get(BASE, headers=admin)).json()
    item = listing["items"][0]
    assert item["id"] == rid and item["decision"] == "AMBIGUOUS" and item["has_snapshot"] is True
    assert item["employee"] is None  # no suggestion of who it might be
    img = await api.get(f"{BASE}/{rid}/snapshot", headers=admin)
    assert img.status_code == 200 and img.content[:2] == b"\xff\xd8"
    assert img.headers["cache-control"] == "no-store"
    actions = (await session.execute(select(AuditLog.action))).scalars().all()
    assert "REVIEW_SNAPSHOT_VIEWED" in actions


async def test_assign_creates_manual_punch_and_deletes_crop(
    api: httpx.AsyncClient, session: AsyncSession, admin: dict[str, str], env: Path
) -> None:
    await _set(session, "recognition.attendance_enabled", True)
    cam = await _camera(session)
    emp = await f.employee(session, attendance_method=AttendanceMethod.FACE)
    rid = (await _send(api, cam)).json()["review_id"]
    r = await api.post(f"{BASE}/{rid}/assign", json={"employee_id": str(oid(emp))}, headers=admin)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["review_status"] == "ASSIGNED" and out["has_snapshot"] is False
    assert out["employee"]["id"] == str(oid(emp)) and out["punch_event_id"]
    assert not [p for p in env.rglob("*") if p.is_file()]  # crop deleted
    punch = await session.get(AttendanceEvent, out["punch_event_id"])
    assert punch is not None and punch.source.value == "MANUAL" and punch.direction.value == "IN"
    assert punch.counts_for_attendance and "Koʻrib chiqish" in (punch.note or "")
    again = await api.post(
        f"{BASE}/{rid}/assign", json={"employee_id": str(oid(emp))}, headers=admin
    )
    assert again.status_code == 409 and again.json()["code"] == "REVIEW_CLOSED"
    assert (await api.get(f"{BASE}/{rid}/snapshot", headers=admin)).status_code == 410


async def test_both_direction_camera_needs_direction_and_dismiss(
    api: httpx.AsyncClient, session: AsyncSession, admin: dict[str, str]
) -> None:
    await _set(session, "recognition.attendance_enabled", True)
    cam = await _camera(session, direction=CameraDirection.BOTH)
    emp = await f.employee(session, attendance_method=AttendanceMethod.FACE)
    rid = (await _send(api, cam)).json()["review_id"]
    r = await api.post(f"{BASE}/{rid}/assign", json={"employee_id": str(oid(emp))}, headers=admin)
    assert r.status_code == 422 and "direction" in r.text
    r = await api.post(f"{BASE}/{rid}/dismiss", json={"reason": "begona odam"}, headers=admin)
    assert r.json()["review_status"] == "DISMISSED" and r.json()["has_snapshot"] is False
    assert (await api.get(BASE, headers=admin)).json()["total"] == 0
    assert (await api.get(BASE, params={"status": "ALL"}, headers=admin)).json()["total"] == 1


async def test_pending_reviews_expire_and_clock_checks(
    api: httpx.AsyncClient, session: AsyncSession, admin: dict[str, str], env: Path
) -> None:
    await _set(session, "recognition.attendance_enabled", True)
    cam = await _camera(session)
    assert (await _send(api, cam, at=datetime.now(UTC) - timedelta(days=8))).status_code == 422
    assert (await _send(api, cam, at=datetime.now(UTC) + timedelta(hours=1))).status_code == 422
    assert (await _send(api, cam)).json()["stored"] is True
    assert (await api.get(BASE, headers=admin)).json()["total"] == 1
    await _set(session, "retention.unknown_snapshot_days", 0)  # everything older than now expires
    assert (await api.get(BASE, headers=admin)).json()["total"] == 0
    assert not [p for p in env.rglob("*") if p.is_file()]  # crop deleted
    closed = (await api.get(BASE, params={"status": "DISMISSED"}, headers=admin)).json()
    assert closed["items"][0]["reviewed_by_user_id"] is None  # expired, not decided by a person


async def test_permissions(api: httpx.AsyncClient, session: AsyncSession) -> None:
    viewer = bearer(await token_for(api, await make_user(session, role=UserRole.VIEWER)))
    assert (await api.get(BASE, headers=viewer)).status_code == 403
    assert (await api.post(f"{BASE}/1/dismiss", headers=viewer)).status_code == 403
