"""/api/v1/settings: read system settings (write comes with the settings UI phase)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from app.api.deps import DbSession
from app.auth.dependencies import require
from app.auth.permissions import Permission
from app.models import SystemSetting

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
