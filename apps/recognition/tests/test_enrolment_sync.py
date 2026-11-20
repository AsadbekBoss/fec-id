import base64
import threading
from typing import Any

import cv2
import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient

from recognition.api import create_app
from recognition.config import Settings
from recognition.core_sync import GallerySync, core_aad
from recognition.crypto import Keyring, encrypt_vector
from recognition.matcher import Gallery, Template

from .conftest import TEST_KEY, fake_engine, frontal_detection, textured_image, unit

TOKEN = "test-service-token-0123456789"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
EMP = unit([1, 0, 0, 0])


def png(img: np.ndarray) -> bytes:
    return cv2.imencode(".png", img)[1].tobytes()


def _settings() -> Settings:
    return Settings(service_token=TOKEN, data_encryption_keys=f"7:{TEST_KEY}", data_encryption_active_key_id=7,  # type: ignore[arg-type]
                    gallery_path=None, core_api_url=None, app_env="test")


def _client(detections: list[Any], templates: list[Template] | None = None) -> TestClient:
    return TestClient(create_app(_settings(), fake_engine(detections, EMP, templates)))


def test_extract_returns_embedding_and_nearest() -> None:
    with _client([frontal_detection()], [Template("t", "emp-9", "fake@1", EMP)]) as c:
        r = c.post("/internal/v1/templates/extract", headers=AUTH,
                   files={"file": ("f.png", png(textured_image()), "image/png")})
    body = r.json()
    assert r.status_code == 200 and body["ok"] is True and body["model_id"] == "fake@1" and body["dim"] == 4
    vec = np.frombuffer(base64.b64decode(body["embedding_b64"]), "<f4")
    assert np.allclose(vec, EMP)
    assert body["nearest"] == {"employee_id": "emp-9", "score": 1.0, "same_person_likely": True}
    assert abs(body["yaw_deg"]) < 5 and abs(body["roll_deg"]) < 5  # frontal detection


@pytest.mark.parametrize(("dets", "reason"), [
    ([], "no_face"),
    ([frontal_detection(), frontal_detection(offset=(300, 300))], "multiple_faces"),
    ([frontal_detection(scale=0.5)], "low_quality"),
])
def test_extract_rejections(dets: list[Any], reason: str) -> None:
    with _client(dets) as c:
        r = c.post("/internal/v1/templates/extract", headers=AUTH,
                   files={"file": ("f.png", png(textured_image()), "image/png")})
    assert r.json()["ok"] is False and r.json()["reason"] == reason and "embedding_b64" not in r.text


def test_extract_requires_token() -> None:
    with _client([frontal_detection()]) as c:
        assert c.post("/internal/v1/templates/extract", files={"file": ("f.png", b"x", "image/png")}).status_code == 401


def test_gallery_sync_decrypts_core_templates(keyring: Keyring) -> None:
    good = encrypt_vector(EMP, core_aad("t1", "emp-1", "fake@1"), keyring)
    moved = encrypt_vector(EMP, core_aad("t2", "emp-2", "fake@1"), keyring)  # relabelled below

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.headers["authorization"] == f"Bearer {TOKEN}" and req.url.params["model_id"] == "fake@1"
        return httpx.Response(200, json={"templates": [
            {"template_id": "t1", "employee_id": "emp-1", "model_id": "fake@1", "ciphertext": base64.b64encode(good).decode()},
            {"template_id": "t2", "employee_id": "emp-3", "model_id": "fake@1", "ciphertext": base64.b64encode(moved).decode()},
        ]})

    g = Gallery("fake@1", 4)
    sync = GallerySync(g, "http://core", TOKEN, keyring, 30, transport=httpx.MockTransport(handler))
    sync.tick()
    assert len(g) == 1 and g.scores(EMP)[0][0] == "emp-1"
    assert sync.skipped == 1 and sync.last_error is None  # tampered/relabelled template refused


def test_gallery_sync_failure_keeps_last_gallery(keyring: Keyring) -> None:
    g = Gallery("fake@1", 4)
    g.load([Template("t", "emp-1", "fake@1", EMP)])
    sync = GallerySync(g, "http://core", TOKEN, keyring, 30,
                       transport=httpx.MockTransport(lambda r: httpx.Response(503)))
    sync.tick()
    assert len(g) == 1 and sync.last_error == "HTTPStatusError 503"


def test_engine_is_safe_under_concurrent_requests() -> None:
    eng = fake_engine([frontal_detection()], EMP, [Template("t", "emp-1", "fake@1", EMP)])
    img = textured_image()
    errors: list[BaseException] = []

    def work() -> None:
        try:
            for _ in range(20):
                vec, _, err = eng.embed_best_face(img)
                assert err is None and vec is not None
                assert eng.process(img).faces[0].match.employee_id == "emp-1"
        except BaseException as e:  # pragma: no cover - reported below
            errors.append(e)

    threads = [threading.Thread(target=work) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
