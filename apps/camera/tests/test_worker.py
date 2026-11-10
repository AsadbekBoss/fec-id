import os
from pathlib import Path

from camera.config import Settings
from camera.providers import registry
from camera.supervisor import Supervisor
from camera.worker import Backoff, CameraWorker, max_grab_rate

from .conftest import test_spec, wait_until


def _worker(settings: Settings, stopper: list[object], **test: object) -> CameraWorker:
    w = CameraWorker(
        test_spec(**test), lambda s: registry.create(s, settings), stall_timeout=0.5, backoff=Backoff(0.05, 0.2)
    )
    stopper.append(w)
    w.start()
    return w


def test_backoff_grows_and_caps() -> None:
    b = Backoff(1, 8, jitter=0)
    assert [b.next_delay() for _ in range(5)] == [1, 2, 4, 8, 8]
    b.reset()
    assert b.next_delay() == 1


def test_online_status_and_latest_frame(settings: Settings, stopper: list[object]) -> None:
    w = _worker(settings, stopper)
    assert wait_until(lambda: w.state == "ONLINE" and w.stats.frames_total >= 5)
    st = w.status()
    assert st["state"] == "ONLINE" and st["width"] == 640 and st["error_code"] is None
    assert st["measured_fps"] and st["measured_fps"] > 10
    frame = w.latest_frame()
    assert frame is not None and frame.shape == (480, 640, 3)
    w.stop()
    assert w.state == "STOPPED" and not w.is_alive()


def test_reconnects_after_stream_loss(settings: Settings, stopper: list[object]) -> None:
    w = _worker(settings, stopper, simulate="disconnect", fps=60)  # drops after 300 frames
    assert wait_until(lambda: w.stats.reconnects >= 1, timeout=15)
    assert w.error_code in (None, "STREAM_LOST")
    assert wait_until(lambda: w.state == "ONLINE", timeout=5)


def test_offline_camera_keeps_retrying(settings: Settings, stopper: list[object]) -> None:
    w = _worker(settings, stopper, simulate="offline")
    assert wait_until(lambda: w.stats.reconnects >= 3, timeout=5)
    st = w.status()
    assert st["state"] in ("OFFLINE", "CONNECTING") and st["error_code"] == "UNREACHABLE"
    assert st["frames_total"] == 0


def test_flaky_stream_is_degraded_not_offline(settings: Settings, stopper: list[object]) -> None:
    w = _worker(settings, stopper, simulate="flaky", fps=100)
    assert wait_until(lambda: w.state == "DEGRADED", timeout=5)


def test_consumers_receive_frames_in_memory(settings: Settings, stopper: list[object], tmp_path: Path) -> None:
    got: list[int] = []
    w = _worker(settings, stopper)
    w.add_consumer(lambda f, t: got.append(f.shape[1]))
    assert wait_until(lambda: len(got) >= 3)
    w.stop()
    assert os.listdir(tmp_path) == []  # nothing recorded


def test_supervisor_apply(settings: Settings) -> None:
    sup = Supervisor(settings)
    try:
        assert sup.apply([test_spec("a"), test_spec("b")]) == {"started": 2, "restarted": 0, "stopped": 0}
        first = sup.get("a")
        assert sup.apply([test_spec("a"), test_spec("b")]) == {"started": 0, "restarted": 0, "stopped": 0}
        assert sup.apply([test_spec("a", version=2)]) == {"started": 0, "restarted": 1, "stopped": 1}
        assert sup.get("a") is not first and sup.get("b") is None
        assert first is not None and not first.is_alive()
        assert wait_until(lambda: sup.statuses()[0]["state"] == "ONLINE")
    finally:
        sup.stop_all()
    assert sup.statuses() == []


def test_grab_rate_is_capped_for_non_blocking_drivers(settings: Settings, stopper: list[object]) -> None:
    """Regression: a DirectShow webcam reported ~56 000 fps because grab() never blocked."""
    from camera.providers.base import StreamInfo

    class NonBlocking:
        def open(self) -> StreamInfo:
            return StreamInfo(640, 480, 30.0, "fake")

        def grab(self) -> bool:
            return True  # returns immediately, like some webcam drivers

        def retrieve(self):  # type: ignore[no-untyped-def]
            import numpy as np

            return np.zeros((480, 640, 3), np.uint8)

        def close(self) -> None:
            pass

    assert max_grab_rate(30) == 45 and max_grab_rate(None) == 60 and max_grab_rate(1000) == 60
    w = CameraWorker(test_spec(), lambda s: NonBlocking(), stall_timeout=1, backoff=Backoff(0.05, 0.2))
    stopper.append(w)
    w.start()
    assert wait_until(lambda: (w.measured_fps() or 0) > 0 and len(w.stats.grab_times) > 50, timeout=5)
    fps = w.measured_fps()
    assert fps is not None and 30 <= fps <= 50
