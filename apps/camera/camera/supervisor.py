"""Keeps one worker per enabled camera in sync with the configuration from the Core API."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

from camera.config import Settings
from camera.live import LiveRecognizer, ObservationLog, Recognizer
from camera.providers import registry
from camera.providers.base import FrameSource
from camera.spec import SourceSpec
from camera.worker import Backoff, CameraWorker

log = logging.getLogger(__name__)


class Supervisor:
    def __init__(
        self,
        settings: Settings,
        factory: Callable[[SourceSpec], FrameSource] | None = None,
        recognizer: Recognizer | None = None,
    ) -> None:
        self.settings = settings
        self._factory = factory or (lambda spec: registry.create(spec, settings))
        self._workers: dict[str, CameraWorker] = {}
        self._live: dict[str, LiveRecognizer] = {}
        self._lock = threading.Lock()
        self.recognizer = recognizer  # None = live recognition not configured
        self.observations = ObservationLog()

    def _start(self, spec: SourceSpec) -> CameraWorker:
        s = self.settings
        w = CameraWorker(
            spec,
            self._factory,
            stall_timeout=s.stall_timeout_seconds,
            backoff=Backoff(s.reconnect_initial_seconds, s.reconnect_max_seconds),
        )
        w.start()
        if spec.recognition_enabled and self.recognizer is not None:
            live = LiveRecognizer(w, self.recognizer, self.observations, fps=s.live_recognition_fps)
            live.start()
            self._live[spec.camera_id] = live
        return w

    def _stop_worker(self, w: CameraWorker) -> None:
        live = self._live.pop(w.spec.camera_id, None)
        if live is not None and live.worker is w:
            live.stop()
        elif live is not None:  # a newer worker's recognizer: keep it
            self._live[w.spec.camera_id] = live
        w.stop()

    def live_statuses(self) -> list[dict[str, Any]]:
        with self._lock:
            lives = list(self._live.values())
        return [lv.status() for lv in lives]

    def apply(self, specs: list[SourceSpec]) -> dict[str, int]:
        """Start new cameras, restart changed ones (config_version), stop removed ones."""
        started = restarted = stopped = 0
        desired = {s.camera_id: s for s in specs}
        to_stop: list[CameraWorker] = []
        with self._lock:
            for cid in list(self._workers):
                if cid not in desired:
                    to_stop.append(self._workers.pop(cid))
                    stopped += 1
            for cid, spec in desired.items():
                cur = self._workers.get(cid)
                if cur is None:
                    self._workers[cid] = self._start(spec)
                    started += 1
                elif cur.spec.config_version != spec.config_version:
                    to_stop.append(cur)
                    old_live = self._live.pop(cid, None)
                    if old_live is not None:
                        old_live.stop()
                    self._workers[cid] = self._start(spec)
                    restarted += 1
        for w in to_stop:
            self._stop_worker(w)
        if started or restarted or stopped:
            log.info("cameras: %d started, %d restarted, %d stopped", started, restarted, stopped)
        return {"started": started, "restarted": restarted, "stopped": stopped}

    def get(self, camera_id: str) -> CameraWorker | None:
        with self._lock:
            return self._workers.get(camera_id)

    def statuses(self) -> list[dict[str, Any]]:
        with self._lock:
            workers = list(self._workers.values())
        return [w.status() for w in workers]

    def stop_all(self) -> None:
        with self._lock:
            workers, self._workers = list(self._workers.values()), {}
        for w in workers:
            self._stop_worker(w)
