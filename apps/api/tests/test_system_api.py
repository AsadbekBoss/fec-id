"""/api/v1/system/status: database, internal services, backups, disk and the run.mjs supervisor."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1 import system
from app.core.config import get_settings
from app.models.enums import UserRole
from tests.api_helpers import bearer, make_user, token_for
from tests.conftest import requires_db

pytestmark = requires_db
URL = "/api/v1/system/status"


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    s = get_settings()
    monkeypatch.setattr(s, "recognition_service_url", None)
    monkeypatch.setattr(s, "camera_service_url", "http://127.0.0.1:9")  # nothing listens there
    monkeypatch.setattr(system, "BACKUP_DIR", tmp_path / "backups")
    monkeypatch.setattr(system, "SUPERVISOR_STATE", tmp_path / "supervisor.json")
    return tmp_path


async def _admin(
    api: httpx.AsyncClient, session: AsyncSession, role: UserRole = UserRole.SUPER_ADMIN
) -> dict[str, str]:
    return bearer(await token_for(api, await make_user(session, role=role)))


async def test_status_reports_each_part(api: httpx.AsyncClient, session: AsyncSession) -> None:
    r = await api.get(URL, headers=await _admin(api, session))
    assert r.status_code == 200, r.text
    body = r.json()
    checks = {c["name"]: c for c in body["checks"]}
    assert checks["database"]["status"] == "ok" and checks["database"]["info"]["migration"]
    assert checks["recognition"]["status"] == "not_configured"
    assert checks["camera"]["status"] == "error"
    assert checks["backup"]["status"] == "warning"  # no backup yet
    assert checks["disk"]["info"]["free_gb"] > 0
    assert body["status"] == "error" and body["supervisor"] is None
    assert "token" not in r.text.lower() and "password" not in r.text.lower()


async def test_backup_and_supervisor_state(
    api: httpx.AsyncClient, session: AsyncSession, isolated: Path
) -> None:
    (isolated / "backups").mkdir()
    (isolated / "backups" / "attendance-20261111-200000.attbak").write_bytes(b"x" * 10)
    now = datetime.now(UTC)
    (isolated / "supervisor.json").write_text(
        json.dumps(
            {
                "pid": 1234,
                "mode": "production",
                "started_at": now.isoformat(),
                "updated_at": now.isoformat(),
                "services": {
                    "api": {
                        "name": "api",
                        "title": "Core API",
                        "port": 8000,
                        "state": "running",
                        "restarts": 2,
                        "secret_field": "never shown",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    body = (await api.get(URL, headers=await _admin(api, session))).json()
    backup = next(c for c in body["checks"] if c["name"] == "backup")
    assert backup["status"] == "ok" and backup["info"]["count"] == 1
    sup = body["supervisor"]
    assert sup["running"] is True and sup["services"][0]["restarts"] == 2
    assert "secret_field" not in sup["services"][0]

    stale = (now - timedelta(minutes=5)).isoformat()
    (isolated / "supervisor.json").write_text(
        json.dumps({"pid": 1, "updated_at": stale, "services": {}})
    )
    body = (await api.get(URL, headers=await _admin(api, session))).json()
    assert body["supervisor"]["running"] is False


async def test_viewer_cannot_see_status(api: httpx.AsyncClient, session: AsyncSession) -> None:
    r = await api.get(URL, headers=await _admin(api, session, UserRole.VIEWER))
    assert r.status_code == 403
    assert (await api.get(URL)).status_code == 401
