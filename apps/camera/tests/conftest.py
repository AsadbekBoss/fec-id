from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from camera.config import Settings
from camera.spec import SourceSpec, TestSpec

TOKEN = "camera-test-token-0123456789abcdef"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        service_token=TOKEN,
        test_media_dir=tmp_path,
        app_env="test",  # type: ignore[arg-type]
        reconnect_initial_seconds=0.05,
        reconnect_max_seconds=0.2,
        stall_timeout_seconds=1.0,
        read_timeout_ms=1000,
        open_timeout_ms=1000,
        core_api_url=None,
    )


def test_spec(camera_id: str = "cam-1", version: int = 1, **test: object) -> SourceSpec:
    return SourceSpec(
        camera_id=camera_id,
        config_version=version,
        name="Unit",
        provider="TEST",
        sample_fps=10,
        test=TestSpec(**{"width": 640, "height": 480, "fps": 50, **test}),
    )  # type: ignore[arg-type]


test_spec.__test__ = False  # type: ignore[attr-defined]


def wait_until(cond: Callable[[], bool], timeout: float = 5.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return cond()


@pytest.fixture
def stopper() -> Iterator[list[object]]:
    things: list[object] = []
    yield things
    for t in things:
        t.stop()  # type: ignore[attr-defined]
