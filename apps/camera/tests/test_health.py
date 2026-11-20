"""Automatic camera health: verdicts and advice from measured values."""

from typing import Any

from camera.health import Measurements, assess, codec_name
from camera.supervisor import Supervisor

from .conftest import test_spec, wait_until


def m(**kw: Any) -> Measurements:
    base: dict[str, Any] = {
        "provider": "RTSP",
        "state": "ONLINE",
        "uptime_seconds": 120,
        "width": 1920,
        "height": 1080,
        "codec": "H.264",
        "measured_fps": 15,
        "cpu_percent": 20,
        "failures_5m": 0,
        "reconnects_1h": 0,
    }
    return Measurements(**{**base, **kw})


def codes(h: dict[str, Any]) -> dict[str, str]:
    return {i["code"]: i["severity"] for i in h["issues"]}


def test_codec_names() -> None:
    assert codec_name("hevc") == "H.265" and codec_name("h264") == "H.264"
    assert codec_name("MJPG") == "MJPEG" and codec_name(None) is None


def test_good_camera_has_no_issues() -> None:
    assert assess(m()) == {"verdict": "good", "issues": []}


def test_offline_is_bad() -> None:
    assert assess(m(state="OFFLINE"))["verdict"] == "bad"


def test_heavy_4mp_h265_camera() -> None:
    """The real case: 2560x1440 H.265 at 21 fps over Wi-Fi."""
    h = assess(m(width=2560, height=1440, codec="H.265", measured_fps=21, cpu_percent=55, failures_5m=40))
    assert h["verdict"] == "warn"
    assert codes(h) == {"frame_loss": "warn", "h265": "warn", "resolution_high": "warn", "fps_high": "tip"}


def test_light_camera_gets_tips_only() -> None:
    h = assess(m(width=2560, height=1440, codec="H.265", cpu_percent=15))
    assert h["verdict"] == "good" and codes(h) == {"h265": "tip", "resolution_high": "tip"}


def test_unstable_network_low_fps_cpu_and_small_picture() -> None:
    h = assess(m(reconnects_1h=4, measured_fps=3, cpu_percent=85, width=640, height=480))
    assert codes(h) == {
        "unstable_connection": "warn",
        "low_fps": "warn",
        "cpu_high": "warn",
        "resolution_low": "tip",
    }
    # rates are not judged in the first 30 s after connecting
    assert "low_fps" not in codes(assess(m(measured_fps=3, uptime_seconds=10)))
    # a USB webcam at 640x480 is not an RTSP door camera: no "too small" tip
    assert "resolution_low" not in codes(assess(m(provider="USB", width=640, height=480)))


def test_worker_reports_health(settings: Any) -> None:
    sup = Supervisor(settings)
    sup.apply([test_spec("cam-h")])
    try:
        assert wait_until(lambda: sup.statuses()[0]["state"] == "ONLINE")
        st = sup.statuses()[0]
        assert st["failures_5m"] == 0 and st["reconnects_1h"] == 0
        assert st["health"]["verdict"] in ("good", "warn")
        assert {"codec", "cpu_percent"} <= set(st)
    finally:
        sup.stop_all()
