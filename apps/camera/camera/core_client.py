"""Sync with the Core API: pull enabled camera configuration, push status reports.

If the Core API is briefly unavailable the current workers keep running with their last known
configuration; the next successful sync reconciles them.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

import httpx

from camera.spec import SourceSpec
from camera.supervisor import Supervisor

log = logging.getLogger(__name__)


class CoreClient:
    def __init__(self, base_url: str, token: str, transport: httpx.BaseTransport | None = None) -> None:
        self._http = httpx.Client(
            base_url=base_url.rstrip("/"), timeout=10, transport=transport, headers={"Authorization": f"Bearer {token}"}
        )

    def fetch_cameras(self) -> list[SourceSpec]:
        r = self._http.get("/internal/v1/cameras")
        r.raise_for_status()
        return [SourceSpec.model_validate(c) for c in r.json()["cameras"]]

    def push_status(self, reports: list[dict[str, Any]]) -> None:
        if reports:
            self._http.post("/internal/v1/cameras/status", json={"reports": reports}).raise_for_status()

    def close(self) -> None:
        self._http.close()


class SyncLoop(threading.Thread):
    def __init__(self, client: CoreClient, supervisor: Supervisor, interval: float) -> None:
        super().__init__(name="core-sync", daemon=True)
        self.client, self.supervisor, self.interval = client, supervisor, interval
        self._stop = threading.Event()
        self.last_ok: float | None = None
        self.last_error: str | None = None

    def tick(self) -> None:
        try:
            self.supervisor.apply(self.client.fetch_cameras())
            time.sleep(0)  # let freshly started workers update their state
            self.client.push_status(self.supervisor.statuses())
            self.last_ok, self.last_error = time.time(), None
        except (httpx.HTTPError, ValueError, KeyError) as e:
            self.last_error = type(e).__name__ + (
                f" {e.response.status_code}" if isinstance(e, httpx.HTTPStatusError) else ""
            )
            log.warning("core sync failed: %s", self.last_error)

    def run(self) -> None:
        while not self._stop.is_set():
            self.tick()
            self._stop.wait(self.interval)

    def stop(self) -> None:
        self._stop.set()
