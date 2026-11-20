"""/api/v1/system/status: one view of everything the system needs to run.

Read-only and side-effect free. Never returns secrets, URLs with credentials or file contents:
only states, counts, timestamps and sizes.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import time
from datetime import UTC, datetime
from typing import Any

import httpx
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text

from app import __version__
from app.api.deps import DbSession
from app.auth.dependencies import require
from app.auth.permissions import Permission
from app.backup import DEFAULT_DIR as BACKUP_DIR
from app.backup import NAME_RE as BACKUP_NAME
from app.backup import REPO_ROOT
from app.core.config import get_settings
from app.core.http import tls_context

router = APIRouter(prefix="/system", tags=["system"])

SUPERVISOR_STATE = REPO_ROOT / "logs" / "supervisor.json"
SUPERVISOR_STALE_SECONDS = 30
BACKUP_STALE_HOURS = 36
DISK_LOW_GB = 5.0
STARTED = time.time()


class Check(BaseModel):
    name: str
    title: str
    status: str  # ok | warning | error | not_configured
    detail: str | None = None
    latency_ms: float | None = None
    info: dict[str, Any] = {}


class SystemStatus(BaseModel):
    status: str  # ok | warning | error (worst of the checks)
    checked_at: datetime
    version: str
    environment: str
    api_uptime_seconds: int
    checks: list[Check]
    supervisor: dict[str, Any] | None


async def _db(db: DbSession) -> Check:
    t0 = time.perf_counter()
    try:
        rev = (await db.execute(text("SELECT version_num FROM alembic_version"))).scalar()
    except Exception:
        return Check(
            name="database", title="Maʼlumotlar bazasi", status="error", detail="Ulanib boʻlmadi"
        )
    ms = (time.perf_counter() - t0) * 1000
    return Check(
        name="database",
        title="Maʼlumotlar bazasi",
        status="ok",
        latency_ms=round(ms, 1),
        info={"migration": rev},
    )


async def _service(name: str, title: str, base: str | None) -> Check:
    """Probe an internal service's public /ready endpoint (no token needed, no secrets returned)."""
    if not base:
        return Check(name=name, title=title, status="not_configured", detail="Manzili sozlanmagan")
    t0 = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=4, verify=tls_context()) as c:
            r = await c.get(base.rstrip("/") + "/ready")
        data = r.json() if r.status_code == 200 else {}
    except (httpx.HTTPError, ValueError):
        return Check(name=name, title=title, status="error", detail="Javob bermayapti")
    ms = round((time.perf_counter() - t0) * 1000, 1)
    if r.status_code != 200:
        return Check(
            name=name, title=title, status="error", detail=f"HTTP {r.status_code}", latency_ms=ms
        )
    if name == "recognition":
        sync = data.get("gallery_sync") or {}
        info = {
            "templates": data.get("gallery_templates"),
            "employees": data.get("gallery_employees"),
            "detector": data.get("detector"),
            "embedder": data.get("embedder"),
        }
        if sync.get("last_error"):
            return Check(
                name=name,
                title=title,
                status="warning",
                latency_ms=ms,
                info=info,
                detail=f"Namunalarni yuklashda xato: {sync['last_error']}",
            )
        return Check(name=name, title=title, status="ok", latency_ms=ms, info=info)
    sync = data.get("core_sync") or {}
    info = {"cameras": data.get("cameras")}
    if sync.get("last_error"):
        return Check(
            name=name,
            title=title,
            status="warning",
            latency_ms=ms,
            info=info,
            detail=f"Core API bilan sinxronlash xatosi: {sync['last_error']}",
        )
    return Check(name=name, title=title, status="ok", latency_ms=ms, info=info)


def backup_check() -> Check:
    files = (
        sorted(p for p in BACKUP_DIR.glob("*") if BACKUP_NAME.match(p.name))
        if BACKUP_DIR.is_dir()
        else []
    )
    if not files:
        return Check(
            name="backup",
            title="Zaxira nusxa",
            status="warning",
            detail="Hali zaxira olinmagan (npm run backup)",
        )
    last = files[-1]
    st = last.stat()
    at = datetime.fromtimestamp(st.st_mtime, UTC)
    age_h = (datetime.now(UTC) - at).total_seconds() / 3600
    info = {
        "last_name": last.name,
        "last_at": at.isoformat(),
        "last_size_bytes": st.st_size,
        "count": len(files),
    }
    if age_h > BACKUP_STALE_HOURS:
        return Check(
            name="backup",
            title="Zaxira nusxa",
            status="warning",
            info=info,
            detail=f"Oxirgi zaxira {int(age_h)} soat oldin olingan",
        )
    return Check(name="backup", title="Zaxira nusxa", status="ok", info=info)


def _disk() -> Check:
    u = shutil.disk_usage(REPO_ROOT)
    free_gb = u.free / 1024**3
    info = {"free_gb": round(free_gb, 1), "total_gb": round(u.total / 1024**3, 1)}
    if free_gb < DISK_LOW_GB:
        return Check(
            name="disk", title="Disk", status="warning", info=info, detail="Diskda joy kam"
        )
    return Check(name="disk", title="Disk", status="ok", info=info)


def _supervisor() -> dict[str, Any] | None:
    """State written by tools/run.mjs (absent when services are started by hand or in Docker)."""
    try:
        data = json.loads(SUPERVISOR_STATE.read_text(encoding="utf-8"))
        updated = datetime.fromisoformat(str(data["updated_at"]).replace("Z", "+00:00"))
    except (OSError, ValueError, KeyError, TypeError):
        return None
    fresh = (datetime.now(UTC) - updated).total_seconds() < SUPERVISOR_STALE_SECONDS and data.get(
        "pid"
    )
    services = [
        {
            k: s.get(k)
            for k in (
                "name",
                "title",
                "port",
                "state",
                "started_at",
                "restarts",
                "last_exit_code",
                "last_exit_at",
                "last_error",
                "log",
            )
        }
        for s in (data.get("services") or {}).values()
        if isinstance(s, dict)
    ]
    return {
        "running": bool(fresh),
        "mode": data.get("mode"),
        "started_at": data.get("started_at"),
        "updated_at": data.get("updated_at"),
        "services": services,
    }


RANK = {"ok": 0, "not_configured": 1, "warning": 1, "error": 2}


@router.get(
    "/status",
    response_model=SystemStatus,
    dependencies=[Depends(require(Permission.BRANCHES_MANAGE))],
    summary="Health of the database, services, backups and disk",
)
async def system_status(db: DbSession) -> SystemStatus:
    s = get_settings()
    rec, cam = await asyncio.gather(
        _service("recognition", "Yuzni tanish xizmati", s.recognition_service_url),
        _service("camera", "Kamera xizmati", s.camera_service_url),
    )
    checks = [
        Check(name="api", title="Core API", status="ok", info={"version": __version__}),
        await _db(db),
        rec,
        cam,
        backup_check(),
        _disk(),
    ]
    worst = max(checks, key=lambda c: RANK[c.status]).status
    overall = {"not_configured": "warning"}.get(worst, worst)
    return SystemStatus(
        status=overall,
        checked_at=datetime.now(UTC),
        version=__version__,
        environment=s.app_env,
        api_uptime_seconds=int(time.time() - STARTED),
        checks=checks,
        supervisor=_supervisor(),
    )
