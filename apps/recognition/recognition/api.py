"""Internal HTTP API of the recognition service (Docker-internal network only; ARCHITECTURE §7.3).

NOT connected to the attendance system in this phase: nothing here creates attendance events.

* Every /internal/* and /stats, /metrics route requires `Authorization: Bearer <SERVICE_TOKEN>`
  (constant-time comparison).
* Responses never contain embeddings or images. An employee id is returned ONLY for a confident
  MATCHED decision. Uncertain observations stay anonymous (decision AMBIGUOUS).
"""

from __future__ import annotations

import base64
import hmac
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any

import anyio
from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import PlainTextResponse

from recognition import gallery_store
from recognition.config import Settings, get_settings
from recognition.core_sync import GallerySync
from recognition.crypto import Keyring
from recognition.engine import ImageRejected, ImageResult, RecognitionEngine, decode_image
from recognition.metrics import configure_logging
from recognition.types import Decision
from recognition.visitors import VisitorCache


def _require_token(request: Request, authorization: Annotated[str | None, Header()] = None) -> None:
    expected = request.app.state.settings.service_token.get_secret_value()
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(token.encode(), expected.encode()):
        raise HTTPException(401, "invalid service token", headers={"WWW-Authenticate": "Bearer"})


Auth = Depends(_require_token)


def _face_json(res: ImageResult) -> list[dict[str, Any]]:
    out = []
    for f in res.faces:
        q = f.quality
        out.append({
            "box": [round(v, 1) for v in f.detection.box],
            "detection_score": round(f.detection.score, 3),
            "quality": {
                "passed": q.passed,
                "reasons": q.reasons,
                "face_width_px": round(q.face_width_px, 1),
                "interocular_px": round(q.interocular_px, 1),
                "blur_variance": None if q.blur_variance is None else round(q.blur_variance, 1),
                "brightness": None if q.brightness is None else round(q.brightness, 1),
                "yaw_deg": None if q.yaw_deg is None else round(q.yaw_deg, 1),
                "roll_deg": None if q.roll_deg is None else round(q.roll_deg, 1),
            },
            "decision": f.match.decision.value,
            "employee_id": f.match.employee_id if f.match.decision is Decision.MATCHED else None,
            "top1_score": None if f.match.top1_score is None else round(f.match.top1_score, 4),
            "top2_score": None if f.match.top2_score is None else round(f.match.top2_score, 4),
            "timings_ms": {k: round(v, 2) for k, v in f.timings_ms.items()},
        })
    return out


def create_app(settings: Settings | None = None, engine: RecognitionEngine | None = None) -> FastAPI:
    s = settings or get_settings()
    configure_logging(s.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        eng = engine or RecognitionEngine.from_settings(s)
        if s.gallery_path and s.gallery_path.is_file():
            keyring = Keyring.parse(s.data_encryption_keys.get_secret_value(), s.data_encryption_active_key_id)
            eng.gallery.load(gallery_store.decrypt_templates(gallery_store.load_entries(s.gallery_path), keyring))
        app.state.engine = eng
        # same-person threshold of the matcher, a little stricter: merging two strangers would
        # hide a card; splitting one only adds a card
        app.state.visitors = VisitorCache(max(eng.policy.threshold, 0.40))
        sync: GallerySync | None = None
        if s.core_api_url and s.core_api_token:
            keyring = Keyring.parse(s.data_encryption_keys.get_secret_value(), s.data_encryption_active_key_id)
            sync = GallerySync(eng.gallery, s.core_api_url, s.core_api_token.get_secret_value(), keyring,
                               s.gallery_sync_seconds)
            sync.start()
        app.state.sync = sync
        try:
            yield
        finally:
            if sync is not None:
                sync.stop()

    app = FastAPI(title="Recognition service (internal)", version="0.1.0", lifespan=lifespan,
                  docs_url=None if s.app_env == "production" else "/docs", redoc_url=None)
    app.state.settings = s

    async def read_image(file: UploadFile) -> Any:
        data = await file.read(s.max_upload_bytes + 1)
        if len(data) > s.max_upload_bytes:
            raise HTTPException(413, "image too large")
        try:
            return await anyio.to_thread.run_sync(decode_image, data, s.max_image_side)
        except ImageRejected as e:
            raise HTTPException(422, str(e)) from None

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/ready")
    async def ready(request: Request) -> dict[str, Any]:
        eng: RecognitionEngine = request.app.state.engine
        sync: GallerySync | None = request.app.state.sync
        return {"status": "ok", "detector": eng.detector.model_id, "embedder": eng.embedder.model_id,
                "gallery_templates": len(eng.gallery), "gallery_employees": eng.gallery.employees,
                "gallery_sync": None if sync is None else {"last_ok": sync.last_ok, "last_error": sync.last_error,
                                                           "skipped": sync.skipped},
                "attendance_integration": "disabled (evaluation phase)"}

    @app.get("/stats", dependencies=[Auth])
    async def stats(request: Request) -> dict[str, object]:
        return request.app.state.engine.stats.snapshot()  # type: ignore[no-any-return]

    @app.get("/metrics", dependencies=[Auth], response_class=PlainTextResponse)
    async def metrics(request: Request) -> str:
        return request.app.state.engine.stats.prometheus()  # type: ignore[no-any-return]

    @app.post("/internal/v1/quality", dependencies=[Auth])
    async def quality(request: Request, file: Annotated[UploadFile, File()]) -> dict[str, Any]:
        img = await read_image(file)
        eng: RecognitionEngine = request.app.state.engine
        res = await anyio.to_thread.run_sync(lambda: eng.process(img, match=False))
        faces = _face_json(res)
        for f in faces:  # quality endpoint never identifies
            f["decision"], f["employee_id"], f["top1_score"], f["top2_score"] = None, None, None, None
        return {"faces": faces, "image_size": res.image_size, "timings_ms": res.timings_ms}

    @app.post("/internal/v1/templates/extract", dependencies=[Auth])
    async def extract_template(request: Request, file: Annotated[UploadFile, File()]) -> dict[str, Any]:
        """Enrolment: exactly ONE good-quality face -> embedding for the Core API to encrypt and store.

        The embedding is returned only here (internal network, service token) because the Core API
        owns template storage. It is never logged. `nearest` is the most similar enrolled employee,
        for the duplicate check (a face may not be enrolled for two people)."""
        img = await read_image(file)
        eng: RecognitionEngine = request.app.state.engine
        vec, face, err = await anyio.to_thread.run_sync(eng.embed_best_face, img)
        if vec is None or face is None:
            reasons = face.quality.reasons if face else []
            return {"ok": False, "reason": (err or "no_face").split(":")[0], "quality_reasons": reasons}
        near = eng.nearest(vec)
        threshold = eng.policy.threshold
        q = face.quality
        return {
            "ok": True,
            "model_id": eng.embedder.model_id,
            "dim": int(vec.shape[0]),
            "embedding_b64": base64.b64encode(vec.astype("<f4").tobytes()).decode(),
            "quality_score": round(float(face.detection.score), 4),
            "face_width_px": round(q.face_width_px, 1),
            # Head pose (landmark heuristic) so guided enrolment can check "look straight / turn".
            "yaw_deg": None if q.yaw_deg is None else round(q.yaw_deg, 1),
            "roll_deg": None if q.roll_deg is None else round(q.roll_deg, 1),
            "nearest": None if near is None else {
                "employee_id": near[0], "score": round(near[1], 4), "same_person_likely": near[1] >= threshold,
            },
        }

    @app.post("/internal/v1/recognize", dependencies=[Auth])
    async def recognize(request: Request, file: Annotated[UploadFile, File()]) -> dict[str, Any]:
        img = await read_image(file)
        eng: RecognitionEngine = request.app.state.engine
        res = await anyio.to_thread.run_sync(eng.process, img)
        faces = _face_json(res)
        visitors: VisitorCache = request.app.state.visitors
        now = time.monotonic()
        for f, vec in zip(faces, res.embeddings, strict=False):
            # anonymous id for unidentified faces, so the review queue gets one card per person
            if f["decision"] in ("UNKNOWN", "AMBIGUOUS") and vec is not None:
                f["visitor_id"] = visitors.assign(vec, now)
        return {"faces": faces, "image_size": res.image_size, "timings_ms": res.timings_ms,
                "model_id": eng.embedder.model_id}

    return app
