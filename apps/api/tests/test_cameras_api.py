"""Camera configuration API, credential protection, quality gate and camera-service link."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from pydantic import SecretStr
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError
from app.core.config import get_settings
from app.models import AuditLog, Camera
from app.models.enums import (
    CameraDirection,
    CameraProvider,
    CameraQualityStatus,
    CameraStatus,
    UserRole,
)
from app.services.camera_service import camera_service, source_spec
from tests.api_helpers import audit_actions, bearer, make_user, token_for
from tests.conftest import requires_db

pytestmark = requires_db

BASE = "/api/v1/cameras"
SECRET = "Sup3r-Secret-Pa55"
SERVICE_TOKEN = "camera-service-token-0123456789abcdef"

RTSP = {
    "name": "Main entrance",
    "provider": "RTSP",
    "host": "10.20.0.11",
    "main_stream_path": "/Streams/Channels/101",
    "username": "attendance",
    "password": SECRET,
    "direction": "IN",
}


class FakeCameraService:
    def __init__(self, verdict: str = "PASSED") -> None:
        self.verdict = verdict
        self.specs: list[dict[str, Any]] = []

    async def probe(self, spec: dict[str, Any]) -> dict[str, Any]:
        self.specs.append(spec)
        return {"ok": True, "width": 1920, "height": 1080, "reported_fps": 25.0, "open_ms": 120.0}

    async def snapshot(self, spec: dict[str, Any]) -> bytes:
        self.specs.append(spec)
        return b"\xff\xd8\xff\xe0fakejpeg"

    async def quality_test(self, spec: dict[str, Any], duration_seconds: int) -> dict[str, Any]:
        self.specs.append(spec)
        return {"verdict": self.verdict, "reasons": [], "duration_seconds": duration_seconds}


@pytest.fixture
def fake_service(app: FastAPI) -> FakeCameraService:
    svc = FakeCameraService()
    app.dependency_overrides[camera_service] = lambda: svc
    return svc


@pytest.fixture
async def admin(api: httpx.AsyncClient, session: AsyncSession) -> dict[str, str]:
    return bearer(await token_for(api, await make_user(session, role=UserRole.SUPER_ADMIN)))


async def _create(api: httpx.AsyncClient, h: dict[str, str], **over: Any) -> dict[str, Any]:
    r = await api.post(BASE, json={**RTSP, **over}, headers=h)
    assert r.status_code == 201, r.text
    return dict(r.json())


# --- configuration & credentials -----------------------------------------------------------


async def test_create_rtsp_camera_never_returns_credentials(
    api: httpx.AsyncClient, admin: dict[str, str], session: AsyncSession
) -> None:
    cam = await _create(api, admin)
    assert cam["status"] == "DRAFT" and cam["is_enabled"] is False
    assert cam["quality_status"] == "UNTESTED" and cam["recognition_enabled"] is False
    assert cam["has_credentials"] is True
    assert cam["stream_url_redacted"] == "rtsp://***@10.20.0.11:554/Streams/Channels/101"
    for r in (
        await api.get(f"{BASE}/{cam['id']}", headers=admin),
        await api.get(BASE, headers=admin),
    ):
        assert SECRET not in r.text and "attendance" not in r.text.replace("attendance-", "")
        assert "password" not in r.text and "username" not in r.text
    row = (
        await session.execute(
            text("SELECT password_enc FROM cameras WHERE id = :i"), {"i": cam["id"]}
        )
    ).scalar_one()
    assert SECRET.encode() not in bytes(row)
    assert SECRET not in str((await session.execute(select(AuditLog.changes))).all())


@pytest.mark.parametrize(
    ("body", "fragment"),
    [
        ({"provider": "RTSP", "host": None}, "host"),
        ({"provider": "USB", "device": None, "username": None, "password": None}, "device"),
        ({"provider": "USB", "device": "0"}, "credentials"),
        ({"password": None}, "together"),
        ({"main_stream_path": "rtsp://u:p@cam/x"}, "credentials"),
        ({"host": "bad host;rm"}, "host"),
        ({"provider": "USB", "device": "C:\\x", "username": None, "password": None}, "device"),
        ({"roi": [[0, 0], [2, 0], [1, 1]]}, "roi"),
        ({"settings": {"test": {"source": "media", "media": "../etc"}}}, "media"),
    ],
)
async def test_create_validation(
    api: httpx.AsyncClient, admin: dict[str, str], body: dict[str, Any], fragment: str
) -> None:
    r = await api.post(BASE, json={**RTSP, **body}, headers=admin)
    assert r.status_code == 422, r.text
    assert fragment in r.text


async def test_usb_and_test_providers(api: httpx.AsyncClient, admin: dict[str, str]) -> None:
    usb = await _create(
        api,
        admin,
        name="Webcam",
        provider="USB",
        device="0",
        host="1.2.3.4",
        main_stream_path=None,
        username=None,
        password=None,
    )
    assert usb["device"] == "0" and usb["host"] is None and usb["has_credentials"] is False
    assert usb["stream_url_redacted"] is None
    test = await _create(
        api,
        admin,
        name="Simulated exit",
        provider="TEST",
        direction="OUT",
        host=None,
        main_stream_path=None,
        username=None,
        password=None,
        settings={"test": {"fps": 10, "simulate": "flaky"}},
    )
    assert test["direction"] == "OUT" and test["settings"]["test"]["simulate"] == "flaky"
    r = await api.get(BASE, params={"provider": "TEST"}, headers=admin)
    assert [c["name"] for c in r.json()["items"]] == ["Simulated exit"]


async def test_duplicate_name_conflict(api: httpx.AsyncClient, admin: dict[str, str]) -> None:
    await _create(api, admin)
    r = await api.post(BASE, json={**RTSP, "name": "MAIN ENTRANCE"}, headers=admin)
    assert r.status_code == 409 and r.json()["code"] == "CAMERA_EXISTS"


async def test_update_credentials_bumps_version_and_resets_quality(
    api: httpx.AsyncClient, admin: dict[str, str], session: AsyncSession
) -> None:
    cam = await _create(api, admin)
    c = await session.get(Camera, uuid.UUID(cam["id"]))
    assert c is not None
    c.quality_status = CameraQualityStatus.PASSED
    c.recognition_enabled = True
    await session.flush()

    r = await api.patch(f"{BASE}/{cam['id']}", json={"location": "Lobby"}, headers=admin)
    assert r.json()["quality_status"] == "PASSED" and r.json()["config_version"] == 2

    r = await api.patch(
        f"{BASE}/{cam['id']}", json={"username": "u2", "password": "new-pass"}, headers=admin
    )
    out = r.json()
    assert out["quality_status"] == "UNTESTED" and out["recognition_enabled"] is False
    assert out["config_version"] == 3
    await session.refresh(c)
    assert c.get_credentials() == ("u2", "new-pass")

    r = await api.patch(f"{BASE}/{cam['id']}", json={"clear_credentials": True}, headers=admin)
    assert r.json()["has_credentials"] is False
    assert "CAMERA_UPDATED" in await audit_actions(session)


async def test_switching_provider_drops_rtsp_fields_and_credentials(
    api: httpx.AsyncClient, admin: dict[str, str]
) -> None:
    cam = await _create(api, admin)
    r = await api.patch(f"{BASE}/{cam['id']}", json={"provider": "TEST"}, headers=admin)
    out = r.json()
    assert r.status_code == 200, r.text
    assert out["provider"] == "TEST" and out["host"] is None and out["has_credentials"] is False
    r = await api.patch(f"{BASE}/{cam['id']}", json={"provider": "USB"}, headers=admin)
    assert r.status_code == 422 and "device" in r.text


async def test_recognition_requires_passed_quality(
    api: httpx.AsyncClient, admin: dict[str, str], fake_service: FakeCameraService
) -> None:
    r = await api.post(BASE, json={**RTSP, "recognition_enabled": True}, headers=admin)
    assert r.status_code == 409 and r.json()["code"] == "QUALITY_NOT_PASSED"
    cam = await _create(api, admin)
    r = await api.patch(f"{BASE}/{cam['id']}", json={"recognition_enabled": True}, headers=admin)
    assert r.status_code == 409 and r.json()["code"] == "QUALITY_NOT_PASSED"

    fake_service.verdict = "INCONCLUSIVE"
    r = await api.post(
        f"{BASE}/{cam['id']}/quality-test", json={"duration_seconds": 3}, headers=admin
    )
    assert r.json()["quality_status"] == "INCONCLUSIVE"
    r = await api.patch(f"{BASE}/{cam['id']}", json={"recognition_enabled": True}, headers=admin)
    assert r.status_code == 409

    fake_service.verdict = "PASSED"
    r = await api.post(f"{BASE}/{cam['id']}/quality-test", headers=admin)
    assert (
        r.json()["quality_status"] == "PASSED" and r.json()["quality_report"]["verdict"] == "PASSED"
    )
    r = await api.patch(f"{BASE}/{cam['id']}", json={"recognition_enabled": True}, headers=admin)
    assert r.status_code == 200 and r.json()["recognition_enabled"] is True

    fake_service.verdict = "FAILED"  # a later failed test switches recognition off again
    r = await api.post(f"{BASE}/{cam['id']}/quality-test", headers=admin)
    assert r.json()["quality_status"] == "FAILED" and r.json()["recognition_enabled"] is False


async def test_invalid_quality_verdict_is_rejected(
    api: httpx.AsyncClient, admin: dict[str, str], fake_service: FakeCameraService
) -> None:
    cam = await _create(api, admin)
    fake_service.verdict = "UNTESTED"
    r = await api.post(f"{BASE}/{cam['id']}/quality-test", headers=admin)
    assert r.status_code == 502 and r.json()["code"] == "BAD_QUALITY_REPORT"


async def test_enable_disable_delete(
    api: httpx.AsyncClient, admin: dict[str, str], session: AsyncSession
) -> None:
    cam = await _create(api, admin)
    r = await api.post(f"{BASE}/{cam['id']}/enable", headers=admin)
    assert r.json()["is_enabled"] is True and r.json()["status"] == "OFFLINE"
    assert r.json()["monitoring"]["stale"] is True
    r = await api.post(f"{BASE}/{cam['id']}/disable", headers=admin)
    assert r.json()["status"] == "DISABLED" and r.json()["monitoring"] is None
    assert (await api.delete(f"{BASE}/{cam['id']}", headers=admin)).status_code == 204
    assert (await api.get(f"{BASE}/{cam['id']}", headers=admin)).status_code == 404
    actions = await audit_actions(session)
    assert {"CAMERA_ENABLED", "CAMERA_DISABLED", "CAMERA_DELETED"} <= set(actions)


# --- on-demand operations through the camera service --------------------------------------


async def test_probe_snapshot_pass_credentials_only_to_service(
    api: httpx.AsyncClient,
    admin: dict[str, str],
    fake_service: FakeCameraService,
    session: AsyncSession,
) -> None:
    cam = await _create(api, admin)
    r = await api.post(f"{BASE}/{cam['id']}/test", headers=admin)
    assert r.status_code == 200 and r.json()["ok"] is True and SECRET not in r.text
    r = await api.get(f"{BASE}/{cam['id']}/snapshot", headers=admin)
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    assert r.headers["cache-control"] == "no-store"
    assert fake_service.specs[0]["rtsp"]["password"] == SECRET
    actions = await audit_actions(session)
    assert "CAMERA_TESTED" in actions and "CAMERA_SNAPSHOT_VIEWED" in actions


async def test_camera_service_not_configured_returns_503(
    api: httpx.AsyncClient, admin: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "camera_service_url", None)  # independent of .env
    cam = await _create(api, admin)
    r = await api.post(f"{BASE}/{cam['id']}/test", headers=admin)
    assert r.status_code == 503 and r.json()["code"] == "CAMERA_SERVICE_UNAVAILABLE"


async def test_camera_service_unreachable_returns_503(
    api: httpx.AsyncClient, admin: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    s = get_settings()
    monkeypatch.setattr(s, "camera_service_url", "http://127.0.0.1:9")  # nothing listens
    monkeypatch.setattr(s, "camera_service_token", SecretStr(SERVICE_TOKEN))
    monkeypatch.setattr(s, "camera_service_timeout_seconds", 2.0)
    cam = await _create(api, admin)
    r = await api.get(f"{BASE}/{cam['id']}/snapshot", headers=admin)
    assert r.status_code == 503 and r.json()["code"] == "CAMERA_SERVICE_UNAVAILABLE"


# --- permissions ---------------------------------------------------------------------------


@pytest.mark.parametrize("role", [UserRole.ADMIN, UserRole.VIEWER])
async def test_only_super_admin_writes_and_views_snapshots(
    api: httpx.AsyncClient,
    admin: dict[str, str],
    session: AsyncSession,
    role: UserRole,
    fake_service: FakeCameraService,
) -> None:
    cam = await _create(api, admin)
    h = bearer(await token_for(api, await make_user(session, role=role)))
    assert (await api.get(BASE, headers=h)).status_code == 200
    for method, path in [
        ("POST", BASE),
        ("PATCH", f"{BASE}/{cam['id']}"),
        ("DELETE", f"{BASE}/{cam['id']}"),
        ("POST", f"{BASE}/{cam['id']}/enable"),
        ("POST", f"{BASE}/{cam['id']}/test"),
        ("GET", f"{BASE}/{cam['id']}/snapshot"),
        ("POST", f"{BASE}/{cam['id']}/quality-test"),
    ]:
        r = await api.request(method, path, json=RTSP if method == "POST" else {}, headers=h)
        assert r.status_code == 403, (method, path)
    assert fake_service.specs == []


# --- internal endpoints for the camera service ---------------------------------------------


@pytest.fixture
def service_token(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    monkeypatch.setattr(get_settings(), "camera_service_token", SecretStr(SERVICE_TOKEN))
    return {"Authorization": f"Bearer {SERVICE_TOKEN}"}


async def test_internal_requires_service_token(
    api: httpx.AsyncClient, admin: dict[str, str]
) -> None:
    # Not configured: everything is rejected, even a user's access token.
    assert (await api.get("/internal/v1/cameras", headers=admin)).status_code == 401
    assert (await api.get("/internal/v1/cameras")).status_code == 401


async def test_internal_config_and_status_flow(
    api: httpx.AsyncClient,
    admin: dict[str, str],
    service_token: dict[str, str],
    session: AsyncSession,
) -> None:
    assert (await api.get("/internal/v1/cameras", headers=admin)).status_code == 401
    cam = await _create(api, admin)
    await _create(api, admin, name="Disabled one", host="10.20.0.12")
    await api.post(f"{BASE}/{cam['id']}/enable", headers=admin)

    r = await api.get("/internal/v1/cameras", headers=service_token)
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    [spec] = r.json()["cameras"]
    assert spec["camera_id"] == cam["id"] and spec["rtsp"]["password"] == SECRET
    version = spec["config_version"]

    now = datetime.now(UTC).isoformat()
    report = {
        "camera_id": cam["id"],
        "config_version": version,
        "state": "ONLINE",
        "last_frame_at": now,
        "measured_fps": 14.8,
        "frames_total": 300,
        "reconnects": 0,
    }
    r = await api.post(
        "/internal/v1/cameras/status", json={"reports": [report]}, headers=service_token
    )
    assert r.status_code == 204
    out = (await api.get(f"{BASE}/{cam['id']}", headers=admin)).json()
    assert out["status"] == "ACTIVE" and out["monitoring"]["stale"] is False
    assert out["monitoring"]["runtime"]["measured_fps"] == 14.8

    stale = {**report, "config_version": version - 1, "state": "OFFLINE"}
    await api.post("/internal/v1/cameras/status", json={"reports": [stale]}, headers=service_token)
    assert (await api.get(f"{BASE}/{cam['id']}", headers=admin)).json()["status"] == "ACTIVE"

    lost = {**report, "state": "OFFLINE", "last_error": "stream lost", "error_code": "STREAM_LOST"}
    await api.post("/internal/v1/cameras/status", json={"reports": [lost]}, headers=service_token)
    out = (await api.get(f"{BASE}/{cam['id']}", headers=admin)).json()
    assert out["status"] == "OFFLINE" and out["last_error"] == "stream lost"
    changes = (
        (await session.execute(select(AuditLog).where(AuditLog.action == "CAMERA_STATUS_CHANGED")))
        .scalars()
        .all()
    )
    assert len(changes) == 2 and changes[0].actor_service == "camera-service"


async def test_status_reports_go_stale(
    api: httpx.AsyncClient, admin: dict[str, str], session: AsyncSession
) -> None:
    cam = await _create(api, admin)
    await api.post(f"{BASE}/{cam['id']}/enable", headers=admin)
    c = await session.get(Camera, uuid.UUID(cam["id"]))
    assert c is not None
    c.runtime_reported_at = datetime.now(UTC) - timedelta(minutes=5)
    c.status = CameraStatus.ACTIVE
    await session.flush()
    out = (await api.get(f"{BASE}/{cam['id']}", headers=admin)).json()
    assert out["status"] == "ACTIVE" and out["monitoring"]["stale"] is True


def test_source_spec_shapes() -> None:
    from app.core import crypto

    kr = crypto.get_keyring()
    c = Camera(
        name="x",
        provider=CameraProvider.RTSP,
        host="h",
        rtsp_port=8554,
        main_stream_path="/s",
        settings={"transport": "udp"},
        config_version=4,
        direction=CameraDirection.IN,
    )
    c.set_credentials("user", "p@ss:word/", kr)
    spec = source_spec(c)
    assert spec["rtsp"] == {
        "host": "h",
        "port": 8554,
        "path": "/s",
        "transport": "udp",
        "username": "user",
        "password": "p@ss:word/",
    }
    t = Camera(
        name="t",
        provider=CameraProvider.TEST,
        settings={"test": {"fps": 5}},
        config_version=1,
        direction=CameraDirection.NONE,
    )
    assert source_spec(t)["test"] == {"fps": 5} and "rtsp" not in source_spec(t)
    assert isinstance(AppError(503, "X", "Y"), Exception)


def test_every_internal_route_requires_service_token(app: FastAPI) -> None:
    """Guard rail like test_every_private_route_requires_authentication, for /internal."""
    from fastapi.routing import iter_route_contexts

    from app.api.internal import require_camera_service, require_recognition_service

    guards = (require_camera_service, require_recognition_service)

    def depends(dep: object) -> bool:
        return any(
            getattr(d, "call", None) in guards or depends(d)
            for d in getattr(dep, "dependencies", [])
        )

    routes = [c for c in iter_route_contexts(app.router.routes) if c.path.startswith("/internal")]
    assert len(routes) >= 3
    for ctx in routes:
        assert depends(ctx.dependant), f"{ctx.path} is unprotected"
