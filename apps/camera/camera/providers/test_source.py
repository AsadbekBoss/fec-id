"""TEST provider: development without camera hardware.

* `pattern`: synthetic frames (moving block, frame counter, clock) at the configured fps.
* `media`: replays an image folder or a video file from TEST_MEDIA_DIR, looping.
* `simulate` injects failures to exercise monitoring and reconnection:
  offline (open fails), disconnect (stream drops after ~5 s, reopen works), flaky (10 % of
  grabs fail), freeze (the same frame forever).

Synthetic frames contain no faces, so a quality test on them is INCONCLUSIVE by design.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

import cv2
import numpy as np

from camera.providers.base import ErrorCode, ProviderError, StreamInfo
from camera.spec import TestSpec

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".bmp"}


def resolve_media(media_dir: Path, name: str | None) -> Path:
    if not name:
        raise ProviderError(ErrorCode.CONFIG_INVALID, "media name is required for source=media")
    base = media_dir.resolve()
    target = (base / name).resolve()
    if base not in target.parents and target != base:
        raise ProviderError(ErrorCode.CONFIG_INVALID, "media must be inside TEST_MEDIA_DIR")
    if not target.exists():
        raise ProviderError(ErrorCode.MEDIA_NOT_FOUND, f"test media '{name}' not found")
    return target


class TestSource:
    __test__ = False  # not a pytest test class

    def __init__(
        self,
        spec: TestSpec,
        label: str,
        media_dir: Path,
        *,
        seed: int = 0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.spec, self.label, self.media_dir = spec, label, media_dir
        self._clock, self._sleep = clock, sleep
        self._rng = np.random.default_rng(seed)
        self._n = 0
        self._next_due = 0.0
        self._frame: np.ndarray | None = None
        self._frozen: np.ndarray | None = None
        self._images: list[Path] = []
        self._video: cv2.VideoCapture | None = None
        self._opened = False

    def open(self) -> StreamInfo:
        s = self.spec
        if s.simulate == "offline":
            raise ProviderError(ErrorCode.UNREACHABLE, "simulated: camera offline")
        if s.source == "media":
            target = resolve_media(self.media_dir, s.media)
            if target.is_dir():
                self._images = sorted(p for p in target.iterdir() if p.suffix.lower() in IMAGE_EXT)
                if not self._images:
                    raise ProviderError(ErrorCode.MEDIA_NOT_FOUND, "media folder has no images")
            else:
                self._video = cv2.VideoCapture(str(target))
                if not self._video.isOpened():
                    raise ProviderError(ErrorCode.OPEN_FAILED, "media file could not be decoded")
        self._n, self._next_due, self._opened = 0, self._clock(), True
        return StreamInfo(s.width, s.height, float(s.fps), f"test:{s.source}")

    def grab(self) -> bool:
        if not self._opened:
            return False
        s = self.spec
        now = self._clock()
        if self._next_due > now:
            self._sleep(self._next_due - now)
        self._next_due = max(self._next_due + 1 / s.fps, self._clock() - 1 / s.fps)
        if s.simulate == "disconnect" and self._n >= s.fps * 5:
            return False
        if s.simulate == "flaky" and self._rng.random() < 0.1:
            return False
        self._n += 1
        self._frame = None
        return True

    def retrieve(self) -> np.ndarray | None:
        if not self._opened or self._n == 0:
            return None
        if self.spec.simulate == "freeze":
            if self._frozen is None:
                self._frozen = self._render()
            return self._frozen
        if self._frame is None:
            self._frame = self._render()
        return self._frame

    def close(self) -> None:
        self._opened = False
        if self._video is not None:
            self._video.release()
            self._video = None

    # --- frame generation -------------------------------------------------------------------
    def _render(self) -> np.ndarray:
        s = self.spec
        if self._images:
            img = cv2.imread(str(self._images[(self._n - 1) % len(self._images)]), cv2.IMREAD_COLOR)
            if img is None:
                raise ProviderError(ErrorCode.MEDIA_NOT_FOUND, "a media image could not be read")
            return img
        if self._video is not None:
            ok, img = self._video.read()
            if not ok:  # loop the file
                self._video.set(cv2.CAP_PROP_POS_FRAMES, 0)
                ok, img = self._video.read()
            if not ok:
                raise ProviderError(ErrorCode.STREAM_LOST, "media file ended unexpectedly")
            return np.asarray(img)
        h, w = s.height, s.width
        y = np.linspace(40, 200, h, dtype=np.float32)[:, None]
        frame = np.repeat(np.repeat(y, w, axis=1)[:, :, None], 3, axis=2).astype(np.uint8)
        frame += self._rng.integers(0, 12, frame.shape, dtype=np.uint8)  # mild sensor noise
        box = max(16, w // 10)
        x = int((self._n * 4) % max(1, w - box))
        cv2.rectangle(frame, (x, h // 3), (x + box, h // 3 + box), (30, 120, 220), -1)
        text = f"TEST {self.label[:24]}  #{self._n}  {time.strftime('%H:%M:%S')}"
        cv2.putText(
            frame,
            text,
            (12, max(24, h // 18)),
            cv2.FONT_HERSHEY_SIMPLEX,
            max(0.4, w / 1600),
            (255, 255, 255),
            2,
            cv2.LINE_AA,
        )
        return frame
