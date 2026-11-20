"""/api/v1/settings: read system settings; change a small whitelist of them (audited)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from app.api.deps import DbSession
from app.api.errors import not_found, validation_error
from app.auth.client_ip import client_ip
from app.auth.dependencies import AuthContext, require
from app.auth.permissions import Permission
from app.db import audit
from app.models import SystemSetting
from app.models.enums import ActorType
from app.schemas.common import StrictModel

router = APIRouter(prefix="/settings", tags=["settings"])


class SettingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    key: str
    value: Any
    description: str | None
    updated_at: datetime


@router.get(
    "",
    response_model=list[SettingOut],
    dependencies=[Depends(require(Permission.SETTINGS_READ))],
    summary="List system settings",
)
async def list_settings(db: DbSession) -> list[SettingOut]:
    rows = (await db.execute(select(SystemSetting).order_by(SystemSetting.key))).scalars()
    return [SettingOut.model_validate(r) for r in rows]


# --- write (whitelisted keys only, SUPER_ADMIN via settings:write) ---------------------------


class SettingUpdate(StrictModel):
    value: Any


def _bool(v: Any) -> bool:
    if not isinstance(v, bool):
        raise ValueError("must be true or false")
    return v


def _int_range(lo: int, hi: int) -> Callable[[Any], int]:
    def check(v: Any) -> int:
        if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
            raise ValueError(f"must be an integer between {lo} and {hi}")
        return v

    return check


def _hhmm(v: Any) -> str:
    if not isinstance(v, str) or len(v) != 5 or v[2] != ":":
        raise ValueError("must be a time HH:MM")
    h, m = v[:2], v[3:]
    if not (h.isdigit() and m.isdigit() and int(h) < 24 and int(m) < 60):
        raise ValueError("must be a time HH:MM")
    return v


# Only these keys can be changed through the API. Everything else is read-only here.
WRITABLE: dict[str, Callable[[Any], Any]] = {
    "recognition.attendance_enabled": _bool,
    "recognition.review_enabled": _bool,
    "recognition.store_unknown_snapshots": _bool,
    "attendance.debounce_seconds": _int_range(10, 3600),
    "attendance.min_presence_minutes": _int_range(0, 720),
    "notify.telegram_enabled": _bool,
    "notify.daily_summary_time": _hhmm,
    "notify.review_interval_minutes": _int_range(5, 1440),
    "notify.camera_offline_minutes": _int_range(1, 1440),
}


@router.patch(
    "/{key}",
    response_model=SettingOut,
    responses={404: {}, 422: {}},
    summary="Change one writable setting (audited)",
)
async def update_setting(
    key: str,
    body: SettingUpdate,
    ctx: Annotated[AuthContext, Depends(require(Permission.SETTINGS_WRITE))],
    request: Request,
    db: DbSession,
) -> SettingOut:
    check = WRITABLE.get(key)
    row = await db.get(SystemSetting, key)
    if check is None or row is None:
        raise not_found("Writable setting")
    try:
        value = check(body.value)
    except ValueError as e:
        raise validation_error("value", "invalid", str(e)) from None
    old = row.value
    if old != value:
        row.value = value
        row.updated_by_user_id = ctx.user_id
        await db.flush()
        await audit.record(
            db,
            action="SETTING_UPDATED",
            actor_type=ActorType.USER,
            actor_user_id=ctx.user_id,
            entity_type="system_setting",
            entity_id=key,
            ip=client_ip(request),
            changes={"value": [old, value]},
        )
        await db.refresh(row)
    return SettingOut.model_validate(row)
