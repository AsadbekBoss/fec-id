"""Review queue for uncertain recognitions (Phase 15).

* The camera service sends AMBIGUOUS faces (and UNKNOWN ones if
  `recognition.store_unknown_snapshots`) with a small face crop. The crop is stored ENCRYPTED
  (purpose `snapshot`), never the full frame.
* No suggestion of who it might be is stored or shown: an uncertain observation is never linked
  to a person automatically. A human looks at the crop and decides.
* ASSIGN creates a MANUAL punch through the normal corrections flow (all sequence rules apply) and
  does NOT enrol the face. DISMISS ends the review. In both cases the crop is deleted at once.
* Pending reviews expire after `retention.unknown_snapshot_days`; their crops are deleted.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import storage
from app.models import AttendanceEvent, SystemSetting


async def setting(db: AsyncSession, key: str, default: object) -> object:
    row = await db.get(SystemSetting, key)
    return default if row is None else row.value


async def purge_expired(db: AsyncSession, now: datetime) -> int:
    """Expire old pending reviews: delete their crops, mark them DISMISSED (no reviewer)."""
    try:
        days = int(await setting(db, "retention.unknown_snapshot_days", 7))  # type: ignore[call-overload]
    except (TypeError, ValueError):
        days = 7
    rows = (
        (
            await db.execute(
                select(AttendanceEvent)
                .where(
                    AttendanceEvent.review_status == "PENDING",
                    AttendanceEvent.event_time < now - timedelta(days=days),
                )
                .limit(500)
            )
        )
        .scalars()
        .all()
    )
    for ev in rows:
        await storage.delete(ev.snapshot_key)
        ev.snapshot_key = None
        ev.review_status = "DISMISSED"
        ev.reviewed_at = now
    return len(rows)
