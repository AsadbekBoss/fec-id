from collections.abc import Iterator
from typing import Any

import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient

from camera import quality
from camera.api import create_app
from camera.config import Settings
from camera.core_client import CoreClient, SyncLoop
from camera.operations import Operations
from camera.providers import registry
from camera.supervisor import Supervisor

from .conftest import TOKEN, test_spec, wait_until

AUTH = {"Authorization": f"Bearer {TOKEN}"}


class FakeAnalyzer:
    def __init__(self, faces: list[dict[str, Any]]) -> None:
        self.faces, self.calls = faces, 0

    def analyze(self, jpeg: bytes) -> list[dict[str, Any]]:
        assert jpeg[:2] == b"\xff\xd8"
        self.calls += 1
        return self.faces


def face(width: float, passed: bool = True, reasons: list[str] | None = None) -> dict[str, Any]:
    return {"quality": {"passed": passed, "face_width_px": width, "reasons": reasons or []}}


def _ops(settings: Settings, analyzer: FakeAnalyzer | None = None) -> Operations:
    return Operations(settings, Supervisor(settings), lambda s: registry.create(s, settings), analyzer)


# --- quality verdicts -----------------------------------------------------------------------


def test_pattern_stream_is_inconclusive_without_faces(settings: Settings) -> None:
    r = _ops(settings).quality_test(test_spec(), 2.0)
    assert r["verdict"] == "INCONCLUSIVE" and r["reasons"] == ["no_face_assessed"]
    assert r["recognition_service"] == "not_configured" and r["faces"] is None
    assert r["stream"]["width"] == 640 and r["stream"]["measured_fps"] > 10
    assert "RECOGNITION_URL" in r["note"]


def test_stream_failures(settings: Settings) -> None:
    ops = _ops(settings)
    assert "frozen_stream" in ops.quality_test(test_spec(simulate="freeze"), 2.0)["reasons"]
    low = ops.quality_test(test_spec(width=320, height=240), 2.0)
    assert low["verdict"] == "FAILED" and "low_resolution" in low["reasons"]
    off = ops.quality_test(test_spec(simulate="offline"), 2.0)
    assert off["verdict"] == "FAILED" and off["reasons"] == ["no_frames"] and off["error_code"] == "UNREACHABLE"


def test_passed_only_with_good_faces(settings: Settings) -> None:
    good = FakeAnalyzer([face(140)])
    r = _ops(settings, good).quality_test(test_spec(), 2.0)
    assert r["verdict"] == "PASSED" and r["faces"]["pass_rate"] == 1.0 and good.calls == 5
    small = FakeAnalyzer([face(45, passed=False, reasons=["face_too_small"])])
    r = _ops(settings, small).quality_test(test_spec(), 2.0)
    assert r["verdict"] == "FAILED" and "faces_fail_quality_gate" in r["reasons"]
    assert r["faces"]["rejection_reasons"] == {"face_too_small": 5}
    # passes the recognition gate but is smaller than this camera's own target
    r = _ops(settings, FakeAnalyzer([face(70)])).quality_test(test_spec(), 2.0)
    assert r["verdict"] == "FAILED" and r["faces"]["median_face_width_px"] == 70


def test_verdict_thresholds() -> None:
    base = {
        "frames": 20,
        "width": 1280,
        "height": 720,
        "measured_fps": 15,
        "frozen_ratio": 0.0,
        "brightness_mean": 120,
        "sharpness_mean": 100,
    }
    assert quality.verdict(base, None, 15)[0] == "INCONCLUSIVE"
    assert quality.verdict({**base, "brightness_mean": 10}, None, 15)[1] == ["too_dark"]
    assert quality.verdict({**base, "sharpness_mean": 2}, None, 15)[1] == ["blurry"]
    assert quality.verdict({**base, "measured_fps": 1}, None, 15)[1] == ["low_fps"]


# --- API ------------------------------------------------------------------------------------


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as c:
        yield c


def _body(**test: object) -> dict[str, Any]:
    return {"source": test_spec(**test).model_dump(mode="json")}


def test_internal_routes_require_token(client: TestClient) -> None:
    assert client.get("/health").status_code == 200
    assert client.get("/ready").json()["recording"].startswith("disabled")
    for method, path in [
        ("GET", "/internal/v1/status"),
        ("POST", "/internal/v1/probe"),
        ("POST", "/internal/v1/snapshot"),
        ("POST", "/internal/v1/quality-test"),
    ]:
        assert client.request(method, path, json=_body()).status_code == 401
        assert client.request(method, path, json=_body(), headers={"Authorization": "Bearer nope"}).status_code == 401


def test_probe_and_snapshot(client: TestClient) -> None:
    r = client.post("/internal/v1/probe", json=_body(), headers=AUTH).json()
    assert r["ok"] is True and r["width"] == 640 and r["backend"] == "test:pattern"
    bad = client.post("/internal/v1/probe", json=_body(simulate="offline"), headers=AUTH).json()
    assert bad == {"ok": False, "error_code": "UNREACHABLE", "error": "simulated: camera offline"}
    snap = client.post("/internal/v1/snapshot", json=_body(), headers=AUTH)
    assert snap.status_code == 200 and snap.headers["content-type"] == "image/jpeg"
    assert snap.content[:2] == b"\xff\xd8" and snap.headers["cache-control"] == "no-store"
    off = client.post("/internal/v1/snapshot", json=_body(simulate="offline"), headers=AUTH)
    assert off.status_code == 502 and off.json()["error_code"] == "UNREACHABLE"


def test_quality_endpoint(client: TestClient) -> None:
    r = client.post("/internal/v1/quality-test", json={**_body(), "duration_seconds": 2}, headers=AUTH)
    assert r.status_code == 200 and r.json()["verdict"] == "INCONCLUSIVE"


def test_weak_token_refused() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="SERVICE_TOKEN"):
        Settings(service_token="short")  # type: ignore[arg-type]


# --- Core API sync --------------------------------------------------------------------------


def test_sync_loop_applies_config_and_reports_status(settings: Settings) -> None:
    spec = test_spec("cam-9").model_dump(mode="json")
    pushed: list[dict[str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.headers["authorization"] == f"Bearer {TOKEN}"
        if req.method == "GET" and req.url.path == "/internal/v1/cameras":
            return httpx.Response(200, json={"cameras": [spec]})
        if req.method == "POST" and req.url.path == "/internal/v1/cameras/status":
            pushed.append(__import__("json").loads(req.content))
            return httpx.Response(204)
        return httpx.Response(404)

    sup = Supervisor(settings)
    loop = SyncLoop(CoreClient("http://core", TOKEN, transport=httpx.MockTransport(handler)), sup, 1)
    try:
        loop.tick()
        assert sup.get("cam-9") is not None and loop.last_error is None
        assert wait_until(lambda: sup.statuses()[0]["state"] == "ONLINE")
        loop.tick()
        assert pushed[-1]["reports"][0]["camera_id"] == "cam-9"
        assert pushed[-1]["reports"][0]["state"] == "ONLINE"
    finally:
        sup.stop_all()


def test_sync_failure_keeps_workers(settings: Settings) -> None:
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(200, json={"cameras": [test_spec("keep").model_dump(mode="json")]})
        return httpx.Response(503)

    sup = Supervisor(settings)
    loop = SyncLoop(CoreClient("http://core", TOKEN, transport=httpx.MockTransport(handler)), sup, 1)
    try:
        loop.tick()  # fetch ok, status push fails -> error recorded
        loop.tick()  # fetch fails
        assert loop.last_error and "503" in loop.last_error
        assert sup.get("keep") is not None
    finally:
        sup.stop_all()


def test_frames_are_numpy(settings: Settings) -> None:
    samples, grabs, failures, elapsed = quality.collect(
        lambda decode: (True, np.zeros((2, 2, 3), np.uint8) if decode else None), 0.2, 10
    )
    assert grabs > 0 and failures == 0 and 1 <= len(samples) <= 11 and elapsed >= 0.2


# --- live MJPEG preview -----------------------------------------------------------------------


def _parts(body: bytes) -> list[bytes]:
    """Split a multipart/x-mixed-replace body into JPEG payloads using Content-Length."""
    out, i = [], 0
    while (h := body.find(b"\r\n\r\n", i)) != -1:
        head = body[i:h].decode()
        n = int(head.split("Content-Length: ")[1].split("\r\n")[0])
        out.append(body[h + 4 : h + 4 + n])
        i = h + 4 + n + 2
    return out


def test_stream_sends_live_jpeg_frames(settings: Settings) -> None:
    sup = Supervisor(settings)
    spec = test_spec("cam-live")
    sup.apply([spec])
    try:
        assert wait_until(lambda: sup.statuses()[0]["state"] == "ONLINE")
        with TestClient(create_app(settings, supervisor=sup)) as c:
            body = {"source": spec.model_dump(mode="json"), "fps": 10, "max_side": 320, "seconds": 1}
            r = c.post("/internal/v1/stream", json=body, headers=AUTH)
            assert r.status_code == 200
            assert r.headers["content-type"] == "multipart/x-mixed-replace; boundary=frame"
            assert r.headers["cache-control"] == "no-store"
            frames = _parts(r.content)
            assert 3 <= len(frames) <= 12  # ~10 fps for 1 s
            assert all(f[:2] == b"\xff\xd8" and f[-2:] == b"\xff\xd9" for f in frames)
            # a camera that is not monitored has no live stream
            other = {"source": test_spec("cam-x").model_dump(mode="json"), "seconds": 1}
            nl = c.post("/internal/v1/stream", json=other, headers=AUTH)
            assert nl.status_code == 409 and nl.json()["error_code"] == "NOT_LIVE"
            assert c.post("/internal/v1/stream", json=body).status_code == 401
    finally:
        sup.stop_all()
