from collections.abc import Iterator

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from recognition.api import create_app
from recognition.config import REPO_ROOT, Settings
from recognition.engine import ImageRejected, RecognitionEngine, decode_image
from recognition.matcher import Gallery, MatchPolicy, Template
from recognition.quality import QualityLimits
from recognition.types import Decision

from .conftest import TEST_KEY, FakeDetector, FakeEmbedder, fake_engine, frontal_detection, textured_image, unit

TOKEN = "test-service-token-0123456789"
EMP = unit([1, 0, 0, 0])


def png(img: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".png", img)
    assert ok
    return buf.tobytes()


def _settings(**kw: object) -> Settings:
    return Settings(service_token=TOKEN, data_encryption_keys=f"7:{TEST_KEY}", data_encryption_active_key_id=7,
                    gallery_path=None, app_env="test", **kw)  # type: ignore[arg-type]


# --- engine ---------------------------------------------------------------------------------------


def test_engine_matched_uncertain_unknown_and_low_quality() -> None:
    tpl = [Template("t1", "emp-1", "fake@1", EMP), Template("t2", "emp-2", "fake@1", unit([0, 1, 0, 0]))]
    img = textured_image()
    m = fake_engine([frontal_detection()], EMP, tpl).process(img)
    assert m.faces[0].match.decision is Decision.MATCHED and m.faces[0].match.employee_id == "emp-1"
    assert {"detect", "total"} <= set(m.timings_ms) and "embed" in m.faces[0].timings_ms

    # cosine ~0.51 with emp-1: inside the uncertain band [0.50, 0.53) -> held, never identified
    amb = fake_engine([frontal_detection()], unit([0.51, 0, 0.86, 0]), tpl).process(img)
    assert amb.faces[0].match.decision is Decision.AMBIGUOUS and amb.faces[0].match.employee_id is None
    assert amb.faces[0].match.candidate_id == "emp-1"

    unk = fake_engine([frontal_detection()], unit([0, 0, 0, 1]), tpl).process(img)
    assert unk.faces[0].match.decision is Decision.UNKNOWN

    small = fake_engine([frontal_detection(scale=0.5)], EMP, tpl).process(img)
    f = small.faces[0]
    assert f.match.decision is Decision.LOW_QUALITY and "face_too_small" in f.quality.reasons
    assert "embed" not in f.timings_ms  # low-quality faces are never embedded or compared


def test_engine_refuses_mismatched_gallery() -> None:
    with pytest.raises(ValueError, match="different models"):
        RecognitionEngine(FakeDetector([]), FakeEmbedder(EMP), Gallery("other@1", 4), MatchPolicy(0.5), QualityLimits())


def test_enrollment_requires_exactly_one_good_face() -> None:
    img = textured_image()
    assert fake_engine([], EMP).embed_best_face(img)[2] == "no_face"
    two = [frontal_detection(), frontal_detection(offset=(300, 300))]
    assert fake_engine(two, EMP).embed_best_face(img)[2] == "multiple_faces"
    assert fake_engine([frontal_detection(scale=0.5)], EMP).embed_best_face(img)[2].startswith("low_quality:")
    vec, face, err = fake_engine([frontal_detection()], EMP).embed_best_face(img)
    assert err is None and face is not None and vec is not None and np.allclose(vec, EMP)


def test_decode_image_validation() -> None:
    with pytest.raises(ImageRejected):
        decode_image(b"", 1920)
    with pytest.raises(ImageRejected):
        decode_image(b"not an image", 1920)
    big = decode_image(png(np.zeros((1000, 3000, 3), np.uint8)), 1500)
    assert max(big.shape[:2]) == 1500


MODELS = REPO_ROOT / "models"


@pytest.mark.skipif(not (MODELS / "face_detection_yunet_2023mar.onnx").is_file()
                    or not (MODELS / "face_recognition_sface_2021dec.onnx").is_file(),
                    reason="model weights not downloaded")
def test_real_models_smoke() -> None:
    eng = RecognitionEngine.from_settings(_settings())
    assert eng.detector.model_id == "yunet@2023mar" and eng.embedder.model_id == "sface@2021dec"
    assert eng.policy.threshold == pytest.approx(0.363)
    res = eng.process(np.full((720, 1280, 3), 128, np.uint8))
    assert res.faces == [] and res.timings_ms["detect"] > 0
    v = eng.embedder.embed(textured_image(112, 112))
    assert v.shape == (128,) and abs(float(np.linalg.norm(v)) - 1) < 1e-4


# --- API ------------------------------------------------------------------------------------------


@pytest.fixture
def client() -> Iterator[TestClient]:
    eng = fake_engine([frontal_detection()], EMP, [Template("t1", "emp-1", "fake@1", EMP)])
    with TestClient(create_app(_settings(max_upload_bytes=2_000_000), eng)) as c:
        yield c


AUTH = {"Authorization": f"Bearer {TOKEN}"}


def test_health_ready_public(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}
    r = client.get("/ready").json()
    assert r["gallery_templates"] == 1 and "disabled" in r["attendance_integration"]


@pytest.mark.parametrize("path", ["/stats", "/metrics"])
def test_stats_require_token(client: TestClient, path: str) -> None:
    assert client.get(path).status_code == 401
    assert client.get(path, headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get(path, headers=AUTH).status_code == 200


@pytest.mark.parametrize("path", ["/internal/v1/recognize", "/internal/v1/quality"])
def test_internal_routes_require_token(client: TestClient, path: str) -> None:
    files = {"file": ("f.png", png(textured_image(200, 200)), "image/png")}
    assert client.post(path, files=files).status_code == 401
    assert client.post(path, files=files, headers={"Authorization": f"Basic {TOKEN}"}).status_code == 401


def test_recognize_returns_match_without_embeddings(client: TestClient) -> None:
    r = client.post("/internal/v1/recognize", headers=AUTH,
                    files={"file": ("f.png", png(textured_image(500, 500)), "image/png")})
    assert r.status_code == 200
    face = r.json()["faces"][0]
    assert face["decision"] == "MATCHED" and face["employee_id"] == "emp-1"
    assert "embedding" not in r.text and "vector" not in r.text
    stats = client.get("/stats", headers=AUTH).json()
    assert stats["decisions"] == {"MATCHED": 1}
    assert "recognition_decisions_total" in client.get("/metrics", headers=AUTH).text


def test_quality_endpoint_never_identifies(client: TestClient) -> None:
    r = client.post("/internal/v1/quality", headers=AUTH,
                    files={"file": ("f.png", png(textured_image(500, 500)), "image/png")})
    face = r.json()["faces"][0]
    assert face["quality"]["passed"] is True and face["employee_id"] is None and face["decision"] is None


def test_upload_validation(client: TestClient) -> None:
    bad = client.post("/internal/v1/recognize", headers=AUTH, files={"file": ("x.png", b"garbage", "image/png")})
    assert bad.status_code == 422
    big = client.post("/internal/v1/recognize", headers=AUTH, files={"file": ("x.bin", b"\0" * 2_000_001, "image/png")})
    assert big.status_code == 413


@pytest.mark.parametrize("weak", ["", "short", "generate-me"])
def test_weak_service_token_is_refused(weak: str) -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="SERVICE_TOKEN"):
        Settings(service_token=weak, data_encryption_keys=f"7:{TEST_KEY}")  # type: ignore[arg-type]
