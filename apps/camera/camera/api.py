"""Internal HTTP API of the camera service (internal network only, called by the Core API).

* Every /internal route requires `Authorization: Bearer <SERVICE_TOKEN>` (constant-time).
* Frames are processed locally; snapshots are returned in memory and never stored.
* Responses never contain credentials or stream URLs.
"""

from __future__ import annotations

import hmac
import logging
import sys
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any

import anyio
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from camera.config import Settings, get_settings
from camera.core_client import CoreClient, SyncLoop
from camera.live import RecognitionClient
from camera.operations import OperationError, Operations, RecognitionQualityClient, encode_jpeg
from camera.providers import registry
from camera.spec import SourceSpec
from camera.supervisor import Supervisor


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        stream=sys.stdout,
        level=level,
        format='{"ts":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}',
    )


class SourceBody(BaseModel):
    source: SourceSpec


class StreamBody(SourceBody):
    fps: float = Field(default=15, ge=1, le=25)
    max_side: int = Field(default=640, ge=160, le=1280)
    seconds: int = Field(default=120, ge=1, le=600)


STREAM_BOUNDARY = b"frame"
STREAM_JPEG_QUALITY = 75
MAX_STREAMS = 4


def mjpeg_part(jpeg: bytes) -> bytes:
    head = b"--%s\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n" % (
        STREAM_BOUNDARY,
        len(jpeg),
    )
    return head + jpeg + b"\r\n"


class QualityBody(SourceBody):
    duration_seconds: int = Field(default=5, ge=2, le=20)


def create_app(
    settings: Settings | None = None,
    *,
    supervisor: Supervisor | None = None,
    operations: Operations | None = None,
    core: CoreClient | None = None,
) -> FastAPI:
    s = settings or get_settings()
    _configure_logging(s.log_level)

    def require_token(authorization: Annotated[str | None, Header()] = None) -> None:
        scheme, _, token = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(
            token.encode(), s.service_token.get_secret_value().encode()
        ):
            raise HTTPException(401, "invalid service token", headers={"WWW-Authenticate": "Bearer"})

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        recognizer = (
            RecognitionClient(s.recognition_url, s.recognition_token.get_secret_value())
            if s.recognition_url and s.recognition_token
            else None
        )
        client = core or (CoreClient(s.core_api_url, s.service_token.get_secret_value()) if s.core_api_url else None)
        sup = supervisor or Supervisor(s, recognizer=recognizer, sink=client)
        ops = operations
        if ops is None:
            analyzer = None
            if s.recognition_url and s.recognition_token:
                analyzer = RecognitionQualityClient(s.recognition_url, s.recognition_token.get_secret_value())
            ops = Operations(s, sup, lambda spec: registry.create(spec, s), analyzer)
        app.state.supervisor, app.state.ops = sup, ops
        sync: SyncLoop | None = None
        if client is not None:
            sync = SyncLoop(client, sup, s.sync_interval_seconds)
            sync.start()
        app.state.sync = sync
        try:
            yield
        finally:
            if sync is not None:
                sync.stop()
            sup.stop_all()
            if client is not None:
                client.close()

    app = FastAPI(
        title="Camera service (internal)",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=None if s.app_env == "production" else "/docs",
        redoc_url=None,
    )
    auth = [Depends(require_token)]

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/ready")
    async def ready(request: Request) -> dict[str, Any]:
        sync: SyncLoop | None = request.app.state.sync
        return {
            "status": "ok",
            "mode": "synced" if sync else "standalone (CORE_API_URL not set)",
            "core_sync": None if sync is None else {"last_ok": sync.last_ok, "last_error": sync.last_error},
            "cameras": len(request.app.state.supervisor.statuses()),
            "recording": "disabled (frames are processed in memory only)",
        }

    @app.get("/internal/v1/status", dependencies=auth)
    async def status(request: Request) -> dict[str, Any]:
        return {"cameras": request.app.state.supervisor.statuses()}

    @app.get("/internal/v1/observations", dependencies=auth)
    async def observations(request: Request, since: int = 0, camera_id: str | None = None) -> dict[str, Any]:
        """Recent live-recognition observations (in memory only; no images, no embeddings)."""
        sup: Supervisor = request.app.state.supervisor
        items, last = sup.observations.since(since, camera_id)
        return {
            "observations": items,
            "next": last,
            "cameras": sup.live_statuses(),
            "configured": sup.recognizer is not None,
        }

    @app.post("/internal/v1/probe", dependencies=auth)
    async def probe(body: SourceBody, request: Request) -> dict[str, Any]:
        ops: Operations = request.app.state.ops
        return await anyio.to_thread.run_sync(ops.probe, body.source)

    @app.post("/internal/v1/snapshot", dependencies=auth, response_model=None)
    async def snapshot(body: SourceBody, request: Request) -> Response:
        ops: Operations = request.app.state.ops
        try:
            jpeg = await anyio.to_thread.run_sync(ops.snapshot, body.source)
        except OperationError as e:
            return JSONResponse({"error_code": e.code, "error": e.message}, status_code=502)
        return Response(jpeg, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    streams = {"open": 0}

    @app.post("/internal/v1/stream", dependencies=auth, response_model=None)
    async def stream(body: StreamBody, request: Request) -> Response:
        """Live MJPEG preview of a MONITORED camera (the running worker's latest frames).

        In memory only: frames are encoded on the fly and never stored. Ends after `seconds`,
        when the client disconnects, or when the camera stops delivering frames."""
        ops: Operations = request.app.state.ops
        worker = ops.live_worker(body.source)
        if worker is None:
            return JSONResponse({"error_code": "NOT_LIVE", "error": "camera is not monitored"}, status_code=409)
        if streams["open"] >= MAX_STREAMS:
            return JSONResponse({"error_code": "BUSY", "error": "too many live previews"}, status_code=429)

        async def frames() -> AsyncIterator[bytes]:
            streams["open"] += 1
            try:
                end, period, last = time.monotonic() + body.seconds, 1.0 / body.fps, None
                while time.monotonic() < end and not await request.is_disconnected():
                    t0 = time.monotonic()
                    item = worker.latest_with_time()
                    if item is None:
                        break  # stream lost: the client falls back or reconnects
                    frame, ts = item
                    if ts != last:
                        last = ts
                        jpeg = await anyio.to_thread.run_sync(encode_jpeg, frame, body.max_side, STREAM_JPEG_QUALITY)
                        yield mjpeg_part(jpeg)
                    await anyio.sleep(max(0.0, period - (time.monotonic() - t0)))
            finally:
                streams["open"] -= 1

        return StreamingResponse(
            frames(),
            media_type="multipart/x-mixed-replace; boundary=" + STREAM_BOUNDARY.decode(),
            headers={"Cache-Control": "no-store"},
        )

    @app.post("/internal/v1/quality-test", dependencies=auth)
    async def quality_test(body: QualityBody, request: Request) -> dict[str, Any]:
        ops: Operations = request.app.state.ops
        return await anyio.to_thread.run_sync(ops.quality_test, body.source, float(body.duration_seconds))

    return app
