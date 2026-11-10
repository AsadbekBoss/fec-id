from pathlib import Path

import numpy as np
import pytest
import yaml

from recognition import registry
from recognition.config import REPO_ROOT
from recognition.models import SCRFDDetector


def _manifest(tmp: Path, **over: object) -> dict[str, registry.ModelSpec]:
    (tmp / "w.onnx").write_bytes(b"weights")
    m: dict[str, object] = {
        "name": "m", "version": "1", "task": "embedding", "backend": "opencv", "file": "w.onnx", "url": None,
        "sha256": registry.sha256_of(tmp / "w.onnx"), "license": "MIT", "commercial_use": "allowed",
    }
    m.update(over)
    (tmp / "manifest.yaml").write_text(yaml.safe_dump({"models": [m]}))
    return registry.load_manifest(tmp / "manifest.yaml")


def test_allowed_model_resolves(tmp_path: Path) -> None:
    spec, path = registry.resolve("m", _manifest(tmp_path), tmp_path, app_env="production", allow_research=False)
    assert spec.model_id == "m@1" and path.name == "w.onnx"


@pytest.mark.parametrize(("over", "env", "research", "msg"), [
    ({"sha256": "0" * 64}, "development", False, "checksum mismatch"),
    ({"sha256": None}, "development", False, "no sha256"),
    ({"file": "missing.onnx"}, "development", False, "missing"),
    ({"commercial_use": "forbidden"}, "production", True, "production"),
    ({"commercial_use": "forbidden"}, "development", False, "ALLOW_RESEARCH_MODELS"),
])
def test_refusals(tmp_path: Path, over: dict[str, object], env: str, research: bool, msg: str) -> None:
    with pytest.raises(registry.ModelRefused, match=msg):
        registry.resolve("m", _manifest(tmp_path, **over), tmp_path, app_env=env, allow_research=research)


def test_research_model_allowed_only_with_flag_outside_production(tmp_path: Path) -> None:
    m = _manifest(tmp_path, commercial_use="forbidden")
    spec, _ = registry.resolve("m", m, tmp_path, app_env="development", allow_research=True)
    assert spec.commercial_use == "forbidden"


def test_unknown_model_and_forbidden_download(tmp_path: Path) -> None:
    m = _manifest(tmp_path, commercial_use="forbidden", url="https://example.invalid/w.onnx")
    with pytest.raises(registry.ModelRefused, match="not in the manifest"):
        registry.resolve("nope", m, tmp_path, app_env="development", allow_research=True)
    with pytest.raises(registry.ModelRefused):
        registry.download(m["m"], tmp_path)


def test_invalid_commercial_use_value(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="commercial_use"):
        _manifest(tmp_path, commercial_use="maybe")


def test_repo_manifest_licences() -> None:
    m = registry.load_manifest(REPO_ROOT / "models" / "manifest.yaml")
    assert m["yunet"].commercial_use == "allowed" and m["sface"].commercial_use == "allowed"
    assert m["scrfd_10g"].commercial_use == "forbidden" and m["arcface_w600k_r50"].commercial_use == "forbidden"
    assert all(s.sha256 for s in (m["yunet"], m["sface"]))


def test_scrfd_anchor_centers() -> None:
    c = SCRFDDetector.anchor_centers(2, 3, 8, 2)
    assert c.shape == (12, 2)
    assert np.array_equal(c[:4], [[0, 0], [0, 0], [8, 0], [8, 0]])
    assert np.array_equal(c[-1], [16, 8])
