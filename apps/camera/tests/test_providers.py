from pathlib import Path

import cv2
import numpy as np
import pytest
from pydantic import SecretStr

from camera.config import Settings
from camera.providers import registry
from camera.providers.base import ErrorCode, ProviderError, StreamInfo
from camera.providers.opencv_sources import RtspSource, UsbSource, rtsp_url
from camera.providers.test_source import TestSource
from camera.spec import RtspSpec, SourceSpec, TestSpec, UsbSpec, redact

from .conftest import test_spec


def _src(tmp: Path, **kw: object) -> TestSource:
    spec = TestSpec(**{"width": 320, "height": 240, "fps": 200, **kw})  # type: ignore[arg-type]
    return TestSource(spec, "unit", tmp)


def test_pattern_frames(tmp_path: Path) -> None:
    s = _src(tmp_path)
    info = s.open()
    assert (info.width, info.height, info.backend) == (320, 240, "test:pattern")
    assert s.grab()
    f1 = s.retrieve()
    assert s.grab()
    f2 = s.retrieve()
    assert f1 is not None and f2 is not None and f1.shape == (240, 320, 3)
    assert not np.array_equal(f1, f2)  # the scene moves
    s.close()
    assert not s.grab()


def test_simulations(tmp_path: Path) -> None:
    with pytest.raises(ProviderError) as e:
        _src(tmp_path, simulate="offline").open()
    assert e.value.code is ErrorCode.UNREACHABLE

    d = _src(tmp_path, simulate="disconnect", fps=10)
    d.open()
    assert all(d.grab() for _ in range(50)) and not d.grab()  # drops after 5 s worth of frames

    fr = _src(tmp_path, simulate="freeze")
    fr.open()
    fr.grab()
    a = fr.retrieve()
    fr.grab()
    assert np.array_equal(a, fr.retrieve())

    fl = _src(tmp_path, simulate="flaky")
    fl.open()
    results = [fl.grab() for _ in range(300)]
    assert 0 < results.count(False) < 90


def test_media_folder_and_traversal(tmp_path: Path) -> None:
    (tmp_path / "set1").mkdir()
    for i in range(2):
        cv2.imwrite(str(tmp_path / "set1" / f"{i}.png"), np.full((60, 80, 3), 50 * (i + 1), np.uint8))
    s = _src(tmp_path, source="media", media="set1")
    s.open()
    s.grab()
    first = s.retrieve()
    s.grab()
    second = s.retrieve()
    assert first is not None and second is not None and first.shape == (60, 80, 3)
    assert first.mean() != second.mean()
    with pytest.raises(ProviderError) as e:
        _src(tmp_path, source="media", media="../outside").open()
    assert e.value.code is ErrorCode.CONFIG_INVALID
    with pytest.raises(ProviderError) as e:
        _src(tmp_path, source="media", media="missing").open()
    assert e.value.code is ErrorCode.MEDIA_NOT_FOUND


def test_rtsp_url_encodes_credentials_and_spec_hides_password() -> None:
    spec = RtspSpec(host="10.0.0.5", port=554, path="/Streams/101", username="adm in", password=SecretStr("p@ss:w/rd"))
    assert rtsp_url(spec) == "rtsp://adm%20in:p%40ss%3Aw%2Frd@10.0.0.5:554/Streams/101"
    assert "p@ss" not in repr(spec) and "p@ss" not in str(spec.model_dump())
    assert rtsp_url(RtspSpec(host="h", path="/x")) == "rtsp://h:554/x"
    assert redact("open rtsp://user:secret@10.0.0.5:554/x failed") == "open rtsp://***@10.0.0.5:554/x failed"


def test_rtsp_unreachable_is_classified() -> None:
    src = RtspSource(RtspSpec(host="127.0.0.1", port=9, path="/x", username="u", password=SecretStr("pw")), 500, 500)
    with pytest.raises(ProviderError) as e:
        src.open()
    assert e.value.code is ErrorCode.UNREACHABLE and "pw" not in e.value.message


def test_usb_missing_device(monkeypatch: pytest.MonkeyPatch) -> None:
    class Closed:
        def __init__(self, *a: object) -> None:
            self.args = a

        def isOpened(self) -> bool:
            return False

        def release(self) -> None:
            pass

    monkeypatch.setattr(cv2, "VideoCapture", Closed)
    with pytest.raises(ProviderError) as e:
        UsbSource(UsbSpec(device="7")).open()
    assert e.value.code is ErrorCode.DEVICE_NOT_FOUND


def test_registry_is_replaceable(settings: Settings) -> None:
    class Dummy:
        def open(self) -> StreamInfo:
            return StreamInfo(1, 1, 1.0, "dummy")

        def grab(self) -> bool:
            return True

        def retrieve(self) -> np.ndarray | None:
            return np.zeros((1, 1, 3), np.uint8)

        def close(self) -> None:
            pass

    original = registry._PROVIDERS["TEST"]
    try:
        registry.register("TEST", lambda spec, s: Dummy())
        assert registry.create(test_spec(), settings).open().backend == "dummy"
    finally:
        registry.register("TEST", original)
    spec = SourceSpec(camera_id="x", provider="RTSP")
    with pytest.raises(ProviderError) as e:
        registry.create(spec, settings)
    assert e.value.code is ErrorCode.CONFIG_INVALID
