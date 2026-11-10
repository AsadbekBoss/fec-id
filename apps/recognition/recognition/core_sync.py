"""Keeps the in-memory gallery in sync with the Core API (enrolled, consented, active employees).

* Templates arrive ENCRYPTED (`GET /internal/v1/gallery`) and are decrypted here with the shared
  keyring. The AAD binds each ciphertext to its template, employee and model, exactly as the Core
  API wrote it (app/models/biometrics.py::FaceEmbedding._aad).
* Vectors exist only in process memory. Nothing is written to disk.
* A template that fails to decrypt is skipped and counted (never crashes the sync).
* If the Core API is unreachable the last good gallery stays in use.
"""

from __future__ import annotations

import base64
import logging
import threading
import time

import httpx

from recognition.crypto import DecryptionError, Keyring, decrypt_vector
from recognition.matcher import Gallery, Template

log = logging.getLogger(__name__)


def core_aad(template_id: str, employee_id: str, model_id: str) -> str:
    return f"face_embeddings:{template_id}:{employee_id}:{model_id}"


class GallerySync(threading.Thread):
    def __init__(self, gallery: Gallery, base_url: str, token: str, keyring: Keyring, interval: float,
                 transport: httpx.BaseTransport | None = None) -> None:
        super().__init__(name="gallery-sync", daemon=True)
        self.gallery, self.keyring, self.interval = gallery, keyring, interval
        self._http = httpx.Client(base_url=base_url.rstrip("/"), timeout=15, transport=transport,
                                  headers={"Authorization": f"Bearer {token}"})
        self._stop = threading.Event()
        self.last_ok: float | None = None
        self.last_error: str | None = None
        self.skipped = 0

    def tick(self) -> None:
        try:
            r = self._http.get("/internal/v1/gallery", params={"model_id": self.gallery.model_id})
            r.raise_for_status()
            templates: list[Template] = []
            skipped = 0
            for t in r.json()["templates"]:
                try:
                    vec = decrypt_vector(base64.b64decode(t["ciphertext"]),
                                         core_aad(t["template_id"], t["employee_id"], t["model_id"]), self.keyring)
                except (DecryptionError, ValueError, KeyError):
                    skipped += 1
                    continue
                templates.append(Template(t["template_id"], t["employee_id"], t["model_id"], vec))
            loaded = self.gallery.load(templates)
            self.skipped, self.last_ok, self.last_error = skipped, time.time(), None
            if skipped:
                log.warning("gallery sync: %d template(s) could not be decrypted and were skipped", skipped)
            log.info("gallery sync: %d templates, %d employees", loaded, self.gallery.employees)
        except (httpx.HTTPError, ValueError, KeyError) as e:
            code = f" {e.response.status_code}" if isinstance(e, httpx.HTTPStatusError) else ""
            self.last_error = type(e).__name__ + code
            log.warning("gallery sync failed: %s", self.last_error)

    def run(self) -> None:
        while not self._stop.is_set():
            self.tick()
            self._stop.wait(self.interval)

    def stop(self) -> None:
        self._stop.set()
        self._http.close()
