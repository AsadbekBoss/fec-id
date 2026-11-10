"""Audit writer. Runs inside the caller's transaction, so the audit row commits or rolls back
together with the change it describes."""

from __future__ import annotations

import re
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import ActorType
from app.models.system import AuditLog

# Field names whose values must never be written to the audit log (ARCHITECTURE §9 P4).
_FORBIDDEN = re.compile(
    r"(^|_)(password|passwd|secret|token|embedding|vector|image|snapshot_data)($|_)|_enc$",
    re.IGNORECASE,
)


class SensitiveAuditDataError(ValueError):
    pass


def _check(changes: dict[str, Any] | None) -> None:
    if not changes:
        return
    for key, value in changes.items():
        if _FORBIDDEN.search(key):
            raise SensitiveAuditDataError(f"Field '{key}' must not be audited with its value")
        if isinstance(value, dict):
            _check(value)
        if isinstance(value, (bytes, bytearray, memoryview)):
            raise SensitiveAuditDataError(f"Binary value for '{key}' must not be audited")


async def record(
    session: AsyncSession,
    *,
    action: str,
    actor_type: ActorType,
    actor_user_id: uuid.UUID | None = None,
    actor_service: str | None = None,
    entity_type: str | None = None,
    entity_id: str | uuid.UUID | int | None = None,
    changes: dict[str, Any] | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
    request_id: str | None = None,
) -> AuditLog:
    _check(changes)
    entry = AuditLog(
        action=action,
        actor_type=actor_type,
        actor_user_id=actor_user_id,
        actor_service=actor_service,
        entity_type=entity_type,
        entity_id=None if entity_id is None else str(entity_id),
        changes=changes,
        ip=ip,
        user_agent=user_agent[:512] if user_agent else None,
        request_id=request_id,
    )
    session.add(entry)
    return entry
