"""/api/v1/notifications/telegram: bot status, linking chats, subscriptions, test message.

The bot token lives in apps/api/.env (TELEGRAM_BOT_TOKEN) and is never returned. Reading needs
notifications:read; changing recipients needs notifications:write. Every change is audited.

Branches: a branch user sees and links only its branch's chats (and its employees' chats); a
chat linked while "all branches" is selected is the centre's (gets every branch's news).
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import or_, select, true

from app.api import scope
from app.api.deps import DbSession
from app.api.errors import AppError, forbidden, not_found, validation_error
from app.auth.client_ip import client_ip
from app.auth.dependencies import AuthContext, require
from app.auth.permissions import Permission
from app.db import audit
from app.models import Employee, NotificationLog, TelegramRecipient
from app.models.enums import ActorType
from app.models.notifications import (
    ADMIN_SUBSCRIPTIONS,
    CENTRE_ONLY_SUBSCRIPTIONS,
    SELF_SUBSCRIPTIONS,
    SUBSCRIPTIONS,
)
from app.schemas.common import StrictModel
from app.services import director
from app.services import notifications as notif
from app.services.telegram import Telegram, TelegramError, telegram_client

router = APIRouter(prefix="/notifications/telegram", tags=["notifications"])

Reader = Annotated[AuthContext, Depends(require(Permission.NOTIFICATIONS_READ))]
Writer = Annotated[AuthContext, Depends(require(Permission.NOTIFICATIONS_WRITE))]
TelegramDep = Annotated[Telegram | None, Depends(telegram_client)]

_bot_cache: dict[str, Any] = {}
BOT_CACHE_SECONDS = 600


class RecipientOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    username: str | None
    is_active: bool
    subscriptions: list[str]
    last_error: str | None
    created_at: datetime
    employee_id: uuid.UUID | None = None
    branch_id: uuid.UUID | None = None  # None = the centre (every branch)


class LogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    kind: str
    created_at: datetime
    recipients: int
    delivered: int
    summary: str | None


class TelegramStatus(BaseModel):
    configured: bool
    bot_username: str | None
    bot_error: str | None
    enabled: bool
    daily_summary_time: str
    review_interval_minutes: int
    camera_offline_minutes: int
    worker_running: bool
    worker_last_run: datetime | None
    worker_error: str | None
    recipients: list[RecipientOut]
    recent: list[LogOut]


async def _bot_username(tg: Telegram | None) -> tuple[str | None, str | None]:
    if tg is None:
        return None, None
    hit = _bot_cache.get("me")
    if hit and time.monotonic() - hit[0] < BOT_CACHE_SECONDS:
        return hit[1], None
    try:
        me = await tg.me()
    except TelegramError as e:
        return None, str(e)
    name = str(me.get("username") or "") or None
    _bot_cache["me"] = (time.monotonic(), name)
    return name, None


def _visible(ctx: AuthContext) -> Any:
    """Chats of the selected branch: its admin chats and its employees' own chats."""
    if ctx.branch_id is None:
        return true()
    return or_(
        TelegramRecipient.branch_id == ctx.branch_id,
        TelegramRecipient.employee_id.in_(scope.employee_ids(ctx)),
    )


@router.get("", response_model=TelegramStatus, summary="Telegram bot status and recipients")
async def get_status(ctx: Reader, db: DbSession, tg: TelegramDep) -> TelegramStatus:
    s = await notif.load_settings(db)
    bot, bot_error = await _bot_username(tg)
    recipients = (
        await db.execute(
            select(TelegramRecipient).where(_visible(ctx)).order_by(TelegramRecipient.created_at)
        )
    ).scalars()
    # The send log names people of every branch: only for whoever sees every branch.
    recent: list[NotificationLog] = []
    if ctx.all_branches:
        recent = list(
            (
                await db.execute(
                    select(NotificationLog).order_by(NotificationLog.id.desc()).limit(20)
                )
            ).scalars()
        )
    w = notif.worker
    return TelegramStatus(
        configured=tg is not None,
        bot_username=bot,
        bot_error=bot_error,
        enabled=s.enabled,
        daily_summary_time=f"{s.daily_time[0]:02d}:{s.daily_time[1]:02d}",
        review_interval_minutes=int(s.review_interval.total_seconds() // 60),
        camera_offline_minutes=int(s.camera_offline.total_seconds() // 60),
        worker_running=w is not None,
        worker_last_run=w.last_run if w else None,
        worker_error=w.last_error if w else None,
        recipients=[RecipientOut.model_validate(r) for r in recipients],
        recent=[LogOut.model_validate(r) for r in recent],
    )


def _require_bot(tg: Telegram | None) -> Telegram:
    if tg is None:
        raise AppError(
            409,
            "TELEGRAM_NOT_CONFIGURED",
            "Telegram bot not configured",
            "Set TELEGRAM_BOT_TOKEN in apps/api/.env and restart the API.",
        )
    return tg


class LinkIn(StrictModel):
    title: str = Field(min_length=1, max_length=120)


class LinkOut(BaseModel):
    code: str
    link: str | None
    bot_username: str | None
    expires_at: datetime


@router.post("/link", response_model=LinkOut, summary="One-time code to link a Telegram chat")
async def create_link(
    body: LinkIn, ctx: Writer, request: Request, db: DbSession, tg: TelegramDep
) -> LinkOut:
    bot, _ = await _bot_username(_require_bot(tg))
    code, expires = notif.link_codes.create(
        body.title.strip(), ctx.user_id, branch_id=ctx.branch_id
    )
    await _audit(db, ctx, request, "TELEGRAM_LINK_CREATED", None, {"title": [None, body.title]})
    return LinkOut(
        code=code,
        link=f"https://t.me/{bot}?start={code}" if bot else None,
        bot_username=bot,
        expires_at=datetime.fromtimestamp(expires, UTC),
    )


class RecipientPatch(StrictModel):
    title: str | None = Field(default=None, min_length=1, max_length=120)
    is_active: bool | None = None
    subscriptions: list[str] | None = None

    @field_validator("subscriptions")
    @classmethod
    def _known(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return v
        bad = [x for x in v if x not in SUBSCRIPTIONS]
        if bad:
            raise ValueError(f"unknown subscriptions: {bad}; allowed: {list(SUBSCRIPTIONS)}")
        return sorted(set(v), key=SUBSCRIPTIONS.index)


async def _recipient(db: DbSession, ctx: AuthContext, rid: uuid.UUID) -> TelegramRecipient:
    r = (
        await db.execute(
            select(TelegramRecipient).where(TelegramRecipient.id == rid, _visible(ctx))
        )
    ).scalar_one_or_none()
    if r is None:
        raise not_found("Telegram recipient")
    return r


@router.patch("/recipients/{rid}", response_model=RecipientOut, summary="Change a recipient")
async def update_recipient(
    rid: uuid.UUID, body: RecipientPatch, ctx: Writer, request: Request, db: DbSession
) -> RecipientOut:
    r = await _recipient(db, ctx, rid)
    added = set(body.subscriptions or []) - set(r.subscriptions or [])
    if r.branch_id is not None and added & set(CENTRE_ONLY_SUBSCRIPTIONS):
        raise validation_error(
            "subscriptions",
            "centre_only",
            "The director's report covers every branch: only centre chats can receive it.",
        )
    changes: dict[str, list[Any]] = {}
    for field in ("title", "is_active", "subscriptions"):
        new = getattr(body, field)
        if new is not None and new != getattr(r, field):
            changes[field] = [getattr(r, field), new]
            setattr(r, field, new)
    if changes:
        await db.flush()
        await _audit(db, ctx, request, "TELEGRAM_RECIPIENT_UPDATED", r.id, changes)
        await db.refresh(r)
    return RecipientOut.model_validate(r)


@router.delete("/recipients/{rid}", status_code=status.HTTP_204_NO_CONTENT, summary="Unlink a chat")
async def delete_recipient(rid: uuid.UUID, ctx: Writer, request: Request, db: DbSession) -> None:
    r = await _recipient(db, ctx, rid)
    await db.delete(r)
    await _audit(db, ctx, request, "TELEGRAM_RECIPIENT_DELETED", rid, {"title": [r.title, None]})


class TestIn(StrictModel):
    recipient_id: uuid.UUID | None = None  # omitted = every active recipient


class TestOut(BaseModel):
    recipients: int
    delivered: int
    errors: list[str]


@router.post("/test", response_model=TestOut, summary="Send a test message")
async def send_test(body: TestIn, ctx: Writer, db: DbSession, tg: TelegramDep) -> TestOut:
    bot = _require_bot(tg)
    if body.recipient_id:
        people = [await _recipient(db, ctx, body.recipient_id)]
    else:
        active = (
            await db.execute(
                select(TelegramRecipient).where(
                    TelegramRecipient.is_active.is_(True), _visible(ctx)
                )
            )
        ).scalars()
        # administrators' chats only: employees' own chats get no test messages
        people = [
            r
            for r in active
            if r.employee_id is None or any(k in ADMIN_SUBSCRIPTIONS for k in r.subscriptions or [])
        ]
    delivered = await notif.deliver(
        db, bot, people, "🔔 Sinov xabari: AI Davomat bildirishnomalari ishlayapti."
    )
    return TestOut(
        recipients=len(people),
        delivered=delivered,
        errors=[f"{p.title}: {p.last_error}" for p in people if p.last_error],
    )


class PreviewOut(BaseModel):
    text: str | None  # None = nobody is scheduled today


@router.get("/daily-preview", response_model=PreviewOut, summary="Today's summary (not sent)")
async def daily_preview(ctx: Reader, db: DbSession) -> PreviewOut:
    s = await notif.load_settings(db)
    today = datetime.now(UTC).astimezone(s.tz).date()
    names = await notif.branch_names(db)
    if ctx.branch_id is None and len(names) > 1:  # the centre's overview
        per_branch = []
        for bid, name in names.items():
            results, _ = await notif.today_results(db, today, bid)
            per_branch.append((name, results))
        return PreviewOut(text=notif.branches_summary(today, per_branch))
    results, on_leave = await notif.today_results(db, today, ctx.branch_id)
    label = names.get(ctx.branch_id) if ctx.branch_id and len(names) > 1 else None
    return PreviewOut(text=notif.daily_summary(today, results, on_leave, s.tz, label))


@router.get(
    "/director-preview",
    response_model=PreviewOut,
    responses={403: {}},
    summary="The director's report (every branch) for today / last week / last month, not sent",
)
async def director_preview(
    ctx: Reader, db: DbSession, period: Literal["day", "week", "month"] = "day"
) -> PreviewOut:
    if not ctx.all_branches:
        raise forbidden()
    s = await notif.load_settings(db)
    today = datetime.now(UTC).astimezone(s.tz).date()
    if period == "day":
        return PreviewOut(text=director.daily_text(today, await director.collect(db, today, today)))
    start, end, title = (director.last_week if period == "week" else director.last_month)(today)
    return PreviewOut(text=director.period_text(title, await director.collect(db, start, end)))


async def _audit(
    db: DbSession,
    ctx: AuthContext,
    request: Request,
    action: str,
    entity_id: uuid.UUID | None,
    changes: dict[str, list[Any]],
) -> None:
    await audit.record(
        db,
        action=action,
        actor_type=ActorType.USER,
        actor_user_id=ctx.user_id,
        entity_type="telegram_recipient",
        entity_id=entity_id,
        ip=client_ip(request),
        changes=changes,
    )


# --- an employee's own chat (employee page) ---------------------------------------------------

employee_router = APIRouter(prefix="/employees/{employee_id}/telegram", tags=["notifications"])
EmpReader = Annotated[AuthContext, Depends(require(Permission.EMPLOYEES_READ))]
EmpWriter = Annotated[AuthContext, Depends(require(Permission.EMPLOYEES_WRITE))]


class EmployeeTelegramOut(BaseModel):
    configured: bool
    enabled: bool  # notify.telegram_enabled (messages are only sent while it is on)
    bot_username: str | None
    linked: bool
    username: str | None = None
    is_active: bool | None = None
    subscriptions: list[str] = []  # self kinds only
    last_error: str | None = None
    linked_at: datetime | None = None


async def _employee(db: DbSession, ctx: AuthContext, employee_id: uuid.UUID) -> Employee:
    return await scope.employee(db, ctx, employee_id)


async def _employee_chat(db: DbSession, employee_id: uuid.UUID) -> TelegramRecipient | None:
    return (
        await db.execute(
            select(TelegramRecipient).where(TelegramRecipient.employee_id == employee_id)
        )
    ).scalar_one_or_none()


async def _employee_out(
    db: DbSession, tg: Telegram | None, employee_id: uuid.UUID
) -> EmployeeTelegramOut:
    s = await notif.load_settings(db)
    bot, _ = await _bot_username(tg)
    r = await _employee_chat(db, employee_id)
    base = {"configured": tg is not None, "enabled": s.enabled, "bot_username": bot}
    if r is None:
        return EmployeeTelegramOut(linked=False, **base)
    return EmployeeTelegramOut(
        linked=True,
        username=r.username,
        is_active=r.is_active,
        subscriptions=[k for k in r.subscriptions or [] if k in SELF_SUBSCRIPTIONS],
        last_error=r.last_error,
        linked_at=r.updated_at,
        **base,
    )


@employee_router.get("", response_model=EmployeeTelegramOut, summary="Employee's Telegram link")
async def employee_telegram(
    employee_id: uuid.UUID, ctx: EmpReader, db: DbSession, tg: TelegramDep
) -> EmployeeTelegramOut:
    await _employee(db, ctx, employee_id)
    return await _employee_out(db, tg, employee_id)


@employee_router.post("/link", response_model=LinkOut, summary="One-time link for the employee")
async def employee_link(
    employee_id: uuid.UUID, ctx: EmpWriter, request: Request, db: DbSession, tg: TelegramDep
) -> LinkOut:
    e = await _employee(db, ctx, employee_id)
    bot, _ = await _bot_username(_require_bot(tg))
    name = " ".join(p for p in (e.first_name, e.last_name) if p)
    code, expires = notif.link_codes.create(name, ctx.user_id, employee_id=e.id)
    await _audit(
        db, ctx, request, "TELEGRAM_EMPLOYEE_LINK_CREATED", None, {"employee_id": [None, str(e.id)]}
    )
    return LinkOut(
        code=code,
        link=f"https://t.me/{bot}?start={code}" if bot else None,
        bot_username=bot,
        expires_at=datetime.fromtimestamp(expires, UTC),
    )


class EmployeeTelegramPatch(StrictModel):
    is_active: bool | None = None
    subscriptions: list[str] | None = None

    @field_validator("subscriptions")
    @classmethod
    def _self_only(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return v
        bad = [x for x in v if x not in SELF_SUBSCRIPTIONS]
        if bad:
            raise ValueError(f"allowed: {list(SELF_SUBSCRIPTIONS)}")
        return sorted(set(v), key=SELF_SUBSCRIPTIONS.index)


@employee_router.patch("", response_model=EmployeeTelegramOut, summary="Change what it receives")
async def employee_telegram_update(
    employee_id: uuid.UUID,
    body: EmployeeTelegramPatch,
    ctx: EmpWriter,
    request: Request,
    db: DbSession,
    tg: TelegramDep,
) -> EmployeeTelegramOut:
    await _employee(db, ctx, employee_id)
    r = await _employee_chat(db, employee_id)
    if r is None:
        raise not_found("Telegram link")
    changes: dict[str, list[Any]] = {}
    if body.subscriptions is not None:
        keep = [k for k in r.subscriptions or [] if k not in SELF_SUBSCRIPTIONS]  # admin kinds
        new = keep + body.subscriptions
        if new != r.subscriptions:
            changes["subscriptions"] = [r.subscriptions, new]
            r.subscriptions = new
    if body.is_active is not None and body.is_active != r.is_active:
        changes["is_active"] = [r.is_active, body.is_active]
        r.is_active = body.is_active
    if changes:
        await db.flush()
        await _audit(db, ctx, request, "TELEGRAM_RECIPIENT_UPDATED", r.id, changes)
    return await _employee_out(db, tg, employee_id)


@employee_router.delete("", status_code=status.HTTP_204_NO_CONTENT, summary="Unlink")
async def employee_telegram_unlink(
    employee_id: uuid.UUID, ctx: EmpWriter, request: Request, db: DbSession
) -> None:
    await _employee(db, ctx, employee_id)
    r = await _employee_chat(db, employee_id)
    if r is None:
        raise not_found("Telegram link")
    rid = r.id
    await notif.unlink_employee(db, r)
    await _audit(
        db,
        ctx,
        request,
        "TELEGRAM_EMPLOYEE_UNLINKED",
        rid,
        {"employee_id": [str(employee_id), None]},
    )
