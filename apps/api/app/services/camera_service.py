"""Core API side of the camera service link (apps/camera).

* `source_spec()` builds the stream description the camera service needs, INCLUDING decrypted
  credentials. It is only ever sent to the camera service over the internal network, never to
  a browser, never logged.
* `CameraServiceClient` calls the camera service for on-demand operations (probe, snapshot,
  quality test). Tests replace it through the `camera_service` dependency.
"""

from __future__ import annotations

from typing import Annotated, Any, Protocol

import httpx
from fastapi import Depends

from app.api.errors import AppError
from app.core.config import get_settings
from app.models import Camera
from app.models.enums import CameraProvider


def stream_url_redacted(c: Camera) -> str | None:
    """What the UI may show: never the username or password."""
    if c.provider is not CameraProvider.RTSP or not c.host:
        return None
    auth = "***@" if c.has_credentials else ""
    return f"rtsp://{auth}{c.host}:{c.rtsp_port}{c.main_stream_path or ''}"


def source_spec(c: Camera) -> dict[str, Any]:
    """Full stream description for the camera service (contains secrets)."""
    settings = c.settings or {}
    spec: dict[str, Any] = {
        "camera_id": str(c.id),
        "config_version": c.config_version,
        "name": c.name,
        "provider": c.provider.value,
        "direction": c.direction.value,
        # Live recognition only on cameras whose stream quality test PASSED (enforced by the API).
        "recognition_enabled": bool(c.recognition_enabled and c.is_enabled),
        "sample_fps": int(settings.get("sample_fps", 5)),
        "min_face_px": int(settings.get("min_face_px", 80)),
    }
    if c.provider is CameraProvider.RTSP:
        creds = c.get_credentials()
        spec["rtsp"] = {
            "host": c.host,
            "port": c.rtsp_port,
            "path": c.main_stream_path,
            "transport": settings.get("transport", "tcp"),
            "username": creds[0] if creds else None,
            "password": creds[1] if creds else None,
        }
    elif c.provider is CameraProvider.USB:
        spec["usb"] = {"device": c.device}
    else:
        spec["test"] = settings.get("test") or {}
    return spec


def unavailable(detail: str) -> AppError:
    return AppError(503, "CAMERA_SERVICE_UNAVAILABLE", "Camera service unavailable", detail)


class CameraService(Protocol):
    async def probe(self, spec: dict[str, Any]) -> dict[str, Any]: ...
    async def snapshot(self, spec: dict[str, Any]) -> bytes: ...
    async def quality_test(self, spec: dict[str, Any], duration_seconds: int) -> dict[str, Any]: ...
    async def observations(self, since: int, camera_id: str | None) -> dict[str, Any]: ...


class CameraServiceClient:
    def __init__(self, base_url: str, token: str, timeout: float) -> None:
        self._base, self._token, self._timeout = base_url.rstrip("/"), token, timeout

    async def _post(
        self, path: str, body: dict[str, Any], extra_seconds: float = 0
    ) -> httpx.Response:
        try:
            async with httpx.AsyncClient(timeout=self._timeout + extra_seconds) as client:
                r = await client.post(
                    self._base + path, json=body, headers={"Authorization": f"Bearer {self._token}"}
                )
        except httpx.HTTPError:
            raise unavailable("The camera service did not respond.") from None
        if r.status_code in (401, 403):
            raise unavailable("The camera service rejected the service token.")
        if r.status_code >= 500 and r.status_code != 502:
            raise unavailable(f"The camera service failed (HTTP {r.status_code}).")
        return r

    async def probe(self, spec: dict[str, Any]) -> dict[str, Any]:
        r = await self._post("/internal/v1/probe", {"source": spec})
        return dict(r.json())

    async def snapshot(self, spec: dict[str, Any]) -> bytes:
        r = await self._post("/internal/v1/snapshot", {"source": spec})
        if r.status_code != 200 or r.headers.get("content-type") != "image/jpeg":
            data = _json(r)
            raise AppError(
                502,
                "CAMERA_UNREACHABLE",
                "Camera unreachable",
                str(data.get("error") or "No frame could be read from the camera."),
                errors=[{"code": data.get("error_code") or "UNKNOWN"}],
            )
        return r.content

    async def quality_test(self, spec: dict[str, Any], duration_seconds: int) -> dict[str, Any]:
        r = await self._post(
            "/internal/v1/quality-test",
            {"source": spec, "duration_seconds": duration_seconds},
            extra_seconds=duration_seconds,
        )
        return dict(r.json())

    async def observations(self, since: int, camera_id: str | None) -> dict[str, Any]:
        params: dict[str, Any] = {"since": since}
        if camera_id:
            params["camera_id"] = camera_id
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(
                    self._base + "/internal/v1/observations",
                    params=params,
                    headers={"Authorization": f"Bearer {self._token}"},
                )
        except httpx.HTTPError:
            raise unavailable("The camera service did not respond.") from None
        if r.status_code != 200:
            raise unavailable(f"The camera service failed (HTTP {r.status_code}).")
        return dict(r.json())


def _json(r: httpx.Response) -> dict[str, Any]:
    try:
        data = r.json()
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


def camera_service() -> CameraService:
    s = get_settings()
    if not s.camera_service_url or s.camera_service_token is None:
        raise unavailable("CAMERA_SERVICE_URL / CAMERA_SERVICE_TOKEN are not configured.")
    return CameraServiceClient(
        s.camera_service_url,
        s.camera_service_token.get_secret_value(),
        s.camera_service_timeout_seconds,
    )


CameraServiceDep = Annotated[CameraService, Depends(camera_service)]
