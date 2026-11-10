"""Core API side of the recognition service link (apps/recognition).

Only template extraction is called from here. The embedding it returns is encrypted immediately
(FaceEmbedding.set_vector) and never logged or returned to a browser. Tests replace the client
through the `recognition_service` dependency.
"""

from __future__ import annotations

from typing import Annotated, Any, Protocol

import httpx
from fastapi import Depends

from app.api.errors import AppError
from app.core.config import get_settings


def unavailable(detail: str) -> AppError:
    return AppError(
        503, "RECOGNITION_SERVICE_UNAVAILABLE", "Recognition service unavailable", detail
    )


class RecognitionService(Protocol):
    async def extract(self, jpeg: bytes) -> dict[str, Any]: ...


class RecognitionServiceClient:
    def __init__(self, base_url: str, token: str) -> None:
        self._url = base_url.rstrip("/") + "/internal/v1/templates/extract"
        self._token = token

    async def extract(self, jpeg: bytes) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                r = await client.post(
                    self._url,
                    files={"file": ("face.jpg", jpeg, "image/jpeg")},
                    headers={"Authorization": f"Bearer {self._token}"},
                )
        except httpx.HTTPError:
            raise unavailable("The recognition service did not respond.") from None
        if r.status_code in (401, 403):
            raise unavailable("The recognition service rejected the service token.")
        if r.status_code == 422:
            return {"ok": False, "reason": "invalid_image", "quality_reasons": []}
        if r.status_code != 200:
            raise unavailable(f"The recognition service failed (HTTP {r.status_code}).")
        return dict(r.json())


def recognition_service() -> RecognitionService:
    s = get_settings()
    if not s.recognition_service_url or s.recognition_service_token is None:
        raise unavailable("RECOGNITION_SERVICE_URL / RECOGNITION_SERVICE_TOKEN are not configured.")
    return RecognitionServiceClient(
        s.recognition_service_url, s.recognition_service_token.get_secret_value()
    )


RecognitionServiceDep = Annotated[RecognitionService, Depends(recognition_service)]
