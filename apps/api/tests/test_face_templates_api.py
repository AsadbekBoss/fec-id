"""Face enrolment, the recognition gallery for the recognition service, and the live view."""

from __future__ import annotations

import base64
import io
import struct
import uuid
from collections.abc import AsyncIterator
from datetime import date
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from PIL import Image
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError
from app.core.config import get_settings
from app.models import AuditLog, FaceEmbedding
from app.models.enums import AttendanceMethod, EmployeeStatus, UserRole
from app.services.camera_service import camera_service
from app.services.recognition_service import recognition_service
from tests import factories as f
from tests.api_helpers import bearer, make_user, oid, token_for
from tests.conftest import requires_db

pytestmark = requires_db

REC_TOKEN = "recognition-service-token-0123456789"
CAM_TOKEN = "camera-service-token-0123456789abcd"
VECTOR = [0.5, -0.25, 0.125, 1.0] * 32  # 128-d


def jpeg(w: int = 320, h: int = 240) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (120, 110, 100)).save(buf, format="JPEG")
    return buf.getvalue()


class FakeRecognition:
    def __init__(self) -> None:
        self.result: dict[str, Any] = self.ok()
        self.calls = 0

    @staticmethod
    def ok(nearest: dict[str, Any] | None = None) -> dict[str, Any]:
        raw = struct.pack(f"<{len(VECTOR)}f", *VECTOR)
        return {
            "ok": True,
            "model_id": "sface@2021dec",
            "dim": len(VECTOR),
            "embedding_b64": base64.b64encode(raw).decode(),
            "quality_score": 0.93,
            "face_width_px": 150.0,
            "nearest": nearest,
        }

    async def extract(self, image: bytes) -> dict[str, Any]:
        assert image[:3] == b"\xff\xd8\xff"  # re-encoded JPEG, never the raw upload
        self.calls += 1
        return self.result

    async def quality(self, image: bytes) -> dict[str, Any]:
        quality = {"passed": True, "reasons": [], "face_width_px": 160.0, "yaw_deg": -15.2}
        small = {**quality, "passed": False, "reasons": ["too_small"], "face_width_px": 40.0}
        return {
            "image_size": [640, 480],
            "faces": [
                {"box": [320, 120, 160, 200], "quality": quality},
                {"box": [10, 10, 40, 50], "quality": small},
            ],
        }


class FakeCameras:
    def __init__(self) -> None:
        self.obs: dict[str, Any] = {"observations": [], "next": 0, "cameras": []}

    async def snapshot(self, spec: dict[str, Any]) -> bytes:
        return jpeg(640, 480)

    async def observations(self, since: int, camera_id: str | None) -> dict[str, Any]:
        return self.obs

    live = True

    async def stream(
        self, spec: dict[str, Any], fps: float, max_side: int, seconds: int
    ) -> tuple[str, AsyncIterator[bytes]]:
        if not self.live:
            raise AppError(409, "CAMERA_NOT_LIVE", "Camera not monitored", "monitoring off")
        self.stream_args = (fps, max_side, seconds)
        part = b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: 3\r\n\r\nabc\r\n"

        async def chunks() -> AsyncIterator[bytes]:
            for _ in range(3):
                yield part

        return "multipart/x-mixed-replace; boundary=frame", chunks()


@pytest.fixture
def rec(app: FastAPI) -> FakeRecognition:
    fake = FakeRecognition()
    app.dependency_overrides[recognition_service] = lambda: fake
    return fake


@pytest.fixture
def cams(app: FastAPI) -> FakeCameras:
    fake = FakeCameras()
    app.dependency_overrides[camera_service] = lambda: fake
    return fake


@pytest.fixture
async def admin(api: httpx.AsyncClient, session: AsyncSession) -> tuple[dict[str, str], Any]:
    acc = await make_user(session, role=UserRole.ADMIN)
    return bearer(await token_for(api, acc)), acc


async def _ready_employee(session: AsyncSession, by: Any, **kw: Any) -> Any:
    await session.refresh(by)  # may be expired by an earlier request
    emp = await f.employee(session, attendance_method=AttendanceMethod.FACE, **kw)
    await f.consent(session, emp, by)
    return emp


def _url(emp: Any) -> str:
    return f"/api/v1/employees/{oid(emp)}/face-templates"


def _file() -> dict[str, Any]:
    return {"file": ("face.jpg", jpeg(), "image/jpeg")}


# --- enrolment ----------------------------------------------------------------------------


async def test_enroll_stores_only_encrypted_template(
    api: httpx.AsyncClient, session: AsyncSession, admin: Any, rec: FakeRecognition
) -> None:
    h, acc = admin
    emp = await _ready_employee(session, acc.obj)
    r = await api.post(_url(emp), files=_file(), headers=h)
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["model_id"] == "sface@2021dec" and out["dim"] == 128
    assert out["quality_score"] == pytest.approx(0.93, abs=1e-6)
    assert "embedding" not in r.text and "vector" not in r.text
    t = await session.get(FaceEmbedding, uuid.UUID(out["id"]))
    assert t is not None
    assert struct.pack("<4f", *VECTOR[:4]) not in t.embedding_enc  # ciphertext only
    assert t.get_vector()[:4] == VECTOR[:4]
    listing = await api.get(_url(emp), headers=h)
    assert [x["id"] for x in listing.json()] == [out["id"]]
    detail = (await api.get(f"/api/v1/employees/{oid(emp)}", headers=h)).json()
    assert detail["biometric"]["status"] == "ENROLLED"
    actions = (await session.execute(select(AuditLog.action, AuditLog.changes))).all()
    assert any(a == "BIOMETRIC_TEMPLATE_ENROLLED" for a, _ in actions)
    assert "0.125" not in str(actions)


@pytest.mark.parametrize(
    ("setup", "code"),
    [
        ("no_consent", "CONSENT_REQUIRED"),
        ("undocumented", "CONSENT_REQUIRED"),
        ("manual", "NOT_FACE_METHOD"),
        ("terminated", "EMPLOYEE_TERMINATED"),
    ],
)
async def test_enroll_requires_documented_consent_and_face_method(
    api: httpx.AsyncClient,
    session: AsyncSession,
    admin: Any,
    rec: FakeRecognition,
    setup: str,
    code: str,
) -> None:
    h, acc = admin
    await session.refresh(acc.obj)
    if setup == "no_consent":
        emp = await f.employee(session, attendance_method=AttendanceMethod.FACE)
    elif setup == "undocumented":
        emp = await f.employee(session, attendance_method=AttendanceMethod.FACE)
        await f.consent(session, emp, acc.obj, documented=False)
    elif setup == "manual":
        emp = await f.employee(session, attendance_method=AttendanceMethod.MANUAL)
    else:
        emp = await _ready_employee(
            session, acc.obj, status=EmployeeStatus.TERMINATED, termination_date=date(2026, 1, 31)
        )
    r = await api.post(_url(emp), files=_file(), headers=h)
    assert r.status_code == 409 and r.json()["code"] == code, r.text
    assert rec.calls == 0  # nothing is sent for biometric processing without the right basis


async def test_face_rejections(
    api: httpx.AsyncClient, session: AsyncSession, admin: Any, rec: FakeRecognition
) -> None:
    h, acc = admin
    emp = await _ready_employee(session, acc.obj)
    rec.result = {"ok": False, "reason": "low_quality", "quality_reasons": ["face_too_small"]}
    r = await api.post(_url(emp), files=_file(), headers=h)
    assert r.status_code == 422 and r.json()["code"] == "FACE_NOT_ACCEPTED"
    assert r.json()["errors"][0]["message"] == "face_too_small"
    r = await api.post(
        _url(emp), files={"file": ("x.jpg", b"not an image", "image/jpeg")}, headers=h
    )
    assert r.status_code == 422 and r.json()["code"] == "INVALID_FILE"


async def test_duplicate_face_of_another_employee_is_refused(
    api: httpx.AsyncClient, session: AsyncSession, admin: Any, rec: FakeRecognition
) -> None:
    h, acc = admin
    other = await _ready_employee(session, acc.obj, employee_code="E-OTHER")
    emp = await _ready_employee(session, acc.obj)
    rec.result = FakeRecognition.ok(
        {"employee_id": str(other.id), "score": 0.71, "same_person_likely": True}
    )
    r = await api.post(_url(emp), files=_file(), headers=h)
    assert r.status_code == 409 and r.json()["code"] == "FACE_MATCHES_OTHER_EMPLOYEE"
    assert "E-OTHER" in r.json()["detail"]
    # the same person adding another template is fine
    rec.result = FakeRecognition.ok(
        {"employee_id": str(oid(emp)), "score": 0.8, "same_person_likely": True}
    )
    assert (await api.post(_url(emp), files=_file(), headers=h)).status_code == 201


async def test_template_limit_and_delete(
    api: httpx.AsyncClient,
    session: AsyncSession,
    admin: Any,
    rec: FakeRecognition,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_settings(), "max_face_templates", 2)
    h, acc = admin
    emp = await _ready_employee(session, acc.obj)
    ids = [(await api.post(_url(emp), files=_file(), headers=h)).json()["id"] for _ in range(2)]
    r = await api.post(_url(emp), files=_file(), headers=h)
    assert r.status_code == 409 and r.json()["code"] == "TEMPLATE_LIMIT"
    assert (await api.delete(f"{_url(emp)}/{ids[0]}", headers=h)).status_code == 204
    assert (await api.delete(f"{_url(emp)}/{ids[0]}", headers=h)).status_code == 404
    assert (await api.post(_url(emp), files=_file(), headers=h)).status_code == 201


async def test_enroll_from_camera(
    api: httpx.AsyncClient,
    session: AsyncSession,
    admin: Any,
    rec: FakeRecognition,
    cams: FakeCameras,
) -> None:
    h, acc = admin
    emp = await _ready_employee(session, acc.obj)
    cam = await f.camera(session)
    r = await api.post(f"{_url(emp)}/from-camera", json={"camera_id": str(cam.id)}, headers=h)
    assert r.status_code == 201, r.text
    r = await api.post(f"{_url(emp)}/from-camera", json={"camera_id": str(uuid.uuid4())}, headers=h)
    assert r.status_code == 404


async def test_recognition_service_not_configured(
    api: httpx.AsyncClient, session: AsyncSession, admin: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "recognition_service_url", None)  # independent of .env
    h, acc = admin
    emp = await _ready_employee(session, acc.obj)
    r = await api.post(_url(emp), files=_file(), headers=h)
    assert r.status_code == 503 and r.json()["code"] == "RECOGNITION_SERVICE_UNAVAILABLE"


async def test_viewer_cannot_read_or_enroll(
    api: httpx.AsyncClient, session: AsyncSession, admin: Any, rec: FakeRecognition
) -> None:
    _, acc = admin
    emp = await _ready_employee(session, acc.obj)
    h = bearer(await token_for(api, await make_user(session, role=UserRole.VIEWER)))
    assert (await api.get(_url(emp), headers=h)).status_code == 403
    assert (await api.post(_url(emp), files=_file(), headers=h)).status_code == 403
    assert (await api.get("/api/v1/recognition/live", headers=h)).status_code == 403
    assert rec.calls == 0


# --- gallery for the recognition service ---------------------------------------------------


@pytest.fixture
def tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    s = get_settings()
    monkeypatch.setattr(s, "recognition_gallery_token", SecretStr(REC_TOKEN))
    monkeypatch.setattr(s, "recognition_service_token", SecretStr(CAM_TOKEN[::-1]))
    monkeypatch.setattr(s, "camera_service_token", SecretStr(CAM_TOKEN))


async def test_gallery_only_for_recognition_token_and_eligible_employees(
    api: httpx.AsyncClient, session: AsyncSession, tokens: None
) -> None:
    by = await f.user(session)
    ok = await _ready_employee(session, by)
    ok_t = await f.embedding(
        session,
        ok,
        (
            await session.execute(
                select(f.BiometricConsent).where(f.BiometricConsent.employee_id == ok.id)
            )
        ).scalar_one(),
        by,
        vector=VECTOR,
        model_id="sface@2021dec",
    )
    other_model = await _ready_employee(session, by)
    c2 = (
        await session.execute(
            select(f.BiometricConsent).where(f.BiometricConsent.employee_id == other_model.id)
        )
    ).scalar_one()
    await f.embedding(session, other_model, c2, by, vector=VECTOR, model_id="arcface@1")
    leaving = await _ready_employee(session, by, status=EmployeeStatus.SUSPENDED)
    c3 = (
        await session.execute(
            select(f.BiometricConsent).where(f.BiometricConsent.employee_id == leaving.id)
        )
    ).scalar_one()
    await f.embedding(session, leaving, c3, by, vector=VECTOR, model_id="sface@2021dec")

    url = "/internal/v1/gallery?model_id=sface@2021dec"
    assert (await api.get(url)).status_code == 401
    assert (await api.get(url, headers={"Authorization": f"Bearer {CAM_TOKEN}"})).status_code == 401
    # the token used to CALL the recognition service (also held by the camera service) is refused
    assert (
        await api.get(url, headers={"Authorization": f"Bearer {CAM_TOKEN[::-1]}"})
    ).status_code == 401
    r = await api.get(url, headers={"Authorization": f"Bearer {REC_TOKEN}"})
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    [t] = r.json()["templates"]
    assert t["template_id"] == str(oid(ok_t)) and t["employee_id"] == str(oid(ok))
    await session.refresh(ok_t)
    assert base64.b64decode(t["ciphertext"]) == ok_t.embedding_enc


# --- live view -----------------------------------------------------------------------------


async def test_live_view_names_only_confident_matches(
    api: httpx.AsyncClient, session: AsyncSession, admin: Any, cams: FakeCameras
) -> None:
    h, _ = admin
    emp = await f.employee(session, employee_code="E-LIVE", last_name="Karimov", first_name="Ali")
    cam = await f.camera(session, name="Gate 1")
    cams.obs = {
        "next": 3,
        "cameras": [{"camera_id": str(cam.id), "recognition": "active"}],
        "observations": [
            {
                "seq": 1,
                "camera_id": str(cam.id),
                "decision": "MATCHED",
                "employee_id": str(emp.id),
                "score": 0.72,
            },
            {
                "seq": 2,
                "camera_id": str(cam.id),
                "decision": "AMBIGUOUS",
                "employee_id": str(emp.id),
                "score": 0.38,
            },
            {
                "seq": 3,
                "camera_id": str(cam.id),
                "decision": "UNKNOWN",
                "employee_id": None,
                "score": 0.1,
            },
        ],
    }
    r = await api.get("/api/v1/recognition/live", headers=h)
    assert r.status_code == 200, r.text
    obs = r.json()["observations"]
    assert obs[0]["employee"] == {
        "id": str(emp.id),
        "employee_code": "E-LIVE",
        "full_name": "Karimov Ali",
    }
    assert obs[1]["employee"] is None and "employee_id" not in obs[1]  # uncertain: anonymous
    assert obs[2]["employee"] is None
    assert r.json()["cameras"][0]["name"] == "Gate 1"


# --- guided enrolment ------------------------------------------------------------------------


async def test_scan_frame_preview_box_pose_and_audit(
    api: httpx.AsyncClient,
    session: AsyncSession,
    admin: Any,
    rec: FakeRecognition,
    cams: FakeCameras,
) -> None:
    h, acc = admin
    emp = await _ready_employee(session, acc.obj)
    cam = await f.camera(session)
    url = f"{_url(emp)}/scan-frame"
    for _ in range(3):
        r = await api.post(url, json={"camera_id": str(cam.id)}, headers=h)
        assert r.status_code == 200, r.text
    body = r.json()
    assert body["preview"].startswith("data:image/jpeg;base64,")
    img = Image.open(io.BytesIO(base64.b64decode(body["preview"].split(",", 1)[1])))
    assert max(img.size) <= 480  # downscaled preview
    assert body["faces"] == 2
    assert body["face"]["box"] == [0.5, 0.25, 0.25, 0.4167]  # largest face, fractions
    assert body["face"]["yaw_deg"] == -15.2 and body["face"]["passed"] is True
    assert "embedding" not in r.text and rec.calls == 0  # never extracts or identifies
    q = select(AuditLog).where(AuditLog.action == "ENROLMENT_PREVIEW_VIEWED")
    assert len((await session.execute(q)).scalars().all()) == 1  # throttled while polling


@pytest.mark.parametrize(
    ("pose", "sign", "yaw", "problem"),
    [
        ("frontal", None, 20.0, "not_frontal"),
        ("turned", None, 4.0, "not_turned"),
        ("turned", -1, 18.0, "wrong_side"),
        ("frontal", None, None, "pose_unknown"),
    ],
)
async def test_from_camera_rejects_wrong_pose(
    api: httpx.AsyncClient,
    session: AsyncSession,
    admin: Any,
    rec: FakeRecognition,
    cams: FakeCameras,
    pose: str,
    sign: int | None,
    yaw: float | None,
    problem: str,
) -> None:
    h, acc = admin
    emp = await _ready_employee(session, acc.obj)
    cam = await f.camera(session)
    rec.result = {**FakeRecognition.ok(), "yaw_deg": yaw}
    body: dict[str, Any] = {"camera_id": str(cam.id), "pose": pose}
    if sign is not None:
        body["yaw_sign"] = sign
    r = await api.post(f"{_url(emp)}/from-camera", json=body, headers=h)
    assert r.status_code == 422 and r.json()["code"] == "POSE_MISMATCH"
    assert r.json()["errors"][0]["code"] == problem
    assert (await session.execute(select(FaceEmbedding))).scalars().all() == []


async def test_from_camera_accepts_matching_pose_and_returns_yaw(
    api: httpx.AsyncClient,
    session: AsyncSession,
    admin: Any,
    rec: FakeRecognition,
    cams: FakeCameras,
) -> None:
    h, acc = admin
    emp = await _ready_employee(session, acc.obj)
    cam = await f.camera(session)
    rec.result = {**FakeRecognition.ok(), "yaw_deg": 17.5}
    body = {"camera_id": str(cam.id), "pose": "turned", "yaw_sign": 1}
    r = await api.post(f"{_url(emp)}/from-camera", json=body, headers=h)
    assert r.status_code == 201, r.text
    assert r.json()["yaw_deg"] == 17.5


async def test_scan_frame_needs_biometrics_write(
    api: httpx.AsyncClient, session: AsyncSession, cams: FakeCameras, rec: FakeRecognition
) -> None:
    viewer = bearer(await token_for(api, await make_user(session, role=UserRole.VIEWER)))
    r = await api.post(
        f"/api/v1/employees/{uuid.uuid4()}/face-templates/scan-frame",
        json={"camera_id": str(uuid.uuid4())},
        headers=viewer,
    )
    assert r.status_code == 403


async def test_scan_stream_relays_mjpeg_and_audits(
    api: httpx.AsyncClient, session: AsyncSession, admin: Any, cams: FakeCameras
) -> None:
    h, acc = admin
    emp = await _ready_employee(session, acc.obj)
    cam = await f.camera(session)
    r = await api.get(f"{_url(emp)}/scan-stream", params={"camera_id": str(cam.id)}, headers=h)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "multipart/x-mixed-replace; boundary=frame"
    assert r.headers["cache-control"] == "no-store"
    assert r.content.count(b"--frame") == 3
    assert cams.stream_args == (15, 640, 120)
    q = select(AuditLog).where(AuditLog.action == "ENROLMENT_PREVIEW_VIEWED")
    assert len((await session.execute(q)).scalars().all()) == 1
    cams.live = False
    r = await api.get(f"{_url(emp)}/scan-stream", params={"camera_id": str(cam.id)}, headers=h)
    assert r.status_code == 409 and r.json()["code"] == "CAMERA_NOT_LIVE"


async def test_scan_frame_without_preview(
    api: httpx.AsyncClient,
    session: AsyncSession,
    admin: Any,
    rec: FakeRecognition,
    cams: FakeCameras,
) -> None:
    h, acc = admin
    emp = await _ready_employee(session, acc.obj)
    cam = await f.camera(session)
    body = {"camera_id": str(cam.id), "preview": False}
    r = await api.post(f"{_url(emp)}/scan-frame", json=body, headers=h)
    assert r.status_code == 200 and r.json()["preview"] is None
    assert r.json()["face"]["yaw_deg"] == -15.2
