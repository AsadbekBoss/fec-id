"""Telegram notifications: what to send, when, and to whom.

Kinds (a recipient subscribes to any of them):
* daily   - attendance summary once a day at notify.daily_summary_time (who came, who is late,
            who has not arrived yet). Skipped on days nobody is scheduled.
* late    - a message when someone's first arrival today is late (only arrivals of the last
            30 minutes, so switching it on mid-day does not send a flood).
* reviews - "N uncertain faces wait for review", at most every notify.review_interval_minutes.
* system  - a camera delivered no frame for notify.camera_offline_minutes (and when it is back),
            the newest backup is older than 36 h.
* director - centre chats only: branches ranked by attendance + who is missing, daily; last
            week's ranking on Mondays, last month's on the 1st (app/services/director.py).

Employee chats (linked from the employee page) get messages about THEIR OWN day only:
* self_punch - "your arrival / departure was recorded at 08:55" (+ lateness on a late arrival)
* self_late  - only the late-arrival message (for those who do not want every punch)

Branches (Phase 21): an administrator chat linked by a branch user belongs to that branch and
gets news about that branch only; a chat linked by the super administrator in "all branches"
mode (branch_id NULL) is the centre and gets everything. With several branches the centre's
daily message is a per-branch overview; each branch gets its own detailed summary.

Privacy: messages contain names and times only. No photos, no biometric data, no links that
would work outside the office network. Everything is off until notify.telegram_enabled is on.

Every message is first recorded in notification_log (kind + key unique) and only then sent, so a
restart never repeats one. `run_checks` is the whole periodic job; the worker calls it.
"""

from __future__ import annotations

import asyncio
import contextlib
import html
import logging
import secrets
import time
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.attendance import engine
from app.attendance import service as att
from app.models import AttendanceEvent, Branch, Camera, Employee, SystemSetting
from app.models.enums import EmployeeStatus, PunchDirection
from app.models.notifications import (
    ADMIN_SUBSCRIPTIONS,
    DEFAULT_SELF_SUBSCRIPTIONS,
    DEFAULT_SUBSCRIPTIONS,
    SELF_SUBSCRIPTIONS,
    NotificationLog,
    TelegramRecipient,
)
from app.services.telegram import Telegram, TelegramError

log = logging.getLogger(__name__)

LATE_WINDOW = timedelta(minutes=30)
LIST_LIMIT = 25
LINK_CODE_TTL = 15 * 60
WEEKDAYS = ["Dushanba", "Seshanba", "Chorshanba", "Payshanba", "Juma", "Shanba", "Yakshanba"]


# --- settings ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class NotifySettings:
    enabled: bool
    daily_time: tuple[int, int]
    review_interval: timedelta
    camera_offline: timedelta
    tz: ZoneInfo


def parse_hhmm(v: Any) -> tuple[int, int] | None:
    try:
        h, m = (int(x) for x in str(v).split(":"))
    except ValueError:
        return None
    return (h, m) if 0 <= h <= 23 and 0 <= m <= 59 else None


async def load_settings(db: AsyncSession) -> NotifySettings:
    rows: dict[str, Any] = dict(
        (
            await db.execute(
                select(SystemSetting.key, SystemSetting.value).where(
                    SystemSetting.key.like("notify.%")
                    | (SystemSetting.key == "attendance.default_timezone")
                )
            )
        ).all()
    )

    def num(key: str, default: int) -> int:
        try:
            return max(1, int(rows.get(key, default)))
        except (TypeError, ValueError):
            return default

    try:
        tz = ZoneInfo(str(rows.get("attendance.default_timezone", "Asia/Tashkent")))
    except (KeyError, ValueError):
        tz = ZoneInfo("Asia/Tashkent")
    return NotifySettings(
        enabled=rows.get("notify.telegram_enabled") is True,
        daily_time=parse_hhmm(rows.get("notify.daily_summary_time")) or (10, 0),
        review_interval=timedelta(minutes=num("notify.review_interval_minutes", 15)),
        camera_offline=timedelta(minutes=num("notify.camera_offline_minutes", 5)),
        tz=tz,
    )


# --- message text (pure functions) ------------------------------------------------------------


def esc(s: object) -> str:
    return html.escape(str(s), quote=False)


def full_name(e: Employee) -> str:
    return " ".join(p for p in (e.last_name, e.first_name) if p)


def hhmm(t: datetime | None, tz: ZoneInfo) -> str:
    return t.astimezone(tz).strftime("%H:%M") if t else "—"


def _names(items: list[str]) -> str:
    shown = items[:LIST_LIMIT]
    more = len(items) - len(shown)
    text = "\n".join(f"  • {s}" for s in shown)
    return text + (f"\n  … va yana {more} kishi" if more > 0 else "")


def daily_summary(
    day: date,
    results: Iterable[tuple[Employee, engine.DayResult]],
    on_leave: int,
    tz: ZoneInfo,
    branch: str | None = None,
) -> str | None:
    """The daily message, or None when nobody is scheduled to work that day."""
    rows = [(e, r) for e, r in results if r.status not in _OFF_STATUSES]
    if not rows:
        return None
    came = [(e, r) for e, r in rows if r.first_in is not None]
    late = sorted(((e, r) for e, r in came if r.late_minutes > 0), key=lambda x: -x[1].late_minutes)
    missing = [e for e, r in rows if r.first_in is None]
    where = f" — {esc(branch)}" if branch else ""
    lines = [
        f"📊 <b>Davomat{where} — {day:%d.%m.%Y}, {WEEKDAYS[day.weekday()]}</b>",
        "",
        f"✅ Keldi: <b>{len(came)}</b> / {len(rows)}",
    ]
    if late:
        lines += [
            f"⏰ Kechikdi: <b>{len(late)}</b>",
            _names(
                [
                    f"{esc(full_name(e))} — {hhmm(r.first_in, tz)} (+{r.late_minutes} daq)"
                    for e, r in late
                ]
            ),
        ]
    if missing:
        lines += [
            f"❌ Hali kelmagan: <b>{len(missing)}</b>",
            _names([esc(full_name(e)) for e in missing]),
        ]
    if on_leave:
        lines.append(f"🏖 Taʼtilda: {on_leave}")
    if not late and not missing:
        lines.append("👏 Hamma oʻz vaqtida keldi.")
    return "\n".join(lines)


_OFF_STATUSES = {engine.DayStatus.WEEKEND, engine.DayStatus.NOT_SCHEDULED}


def branches_summary(
    day: date, per_branch: list[tuple[str, list[tuple[Employee, engine.DayResult]]]]
) -> str | None:
    """The centre's daily overview: one line per branch (counts only), plus the total."""
    head = f"📊 <b>Davomat — {day:%d.%m.%Y}, {WEEKDAYS[day.weekday()]}</b> (barcha filiallar)"
    lines = [head, ""]
    came_all = total_all = 0
    for name, results in per_branch:
        rows = [r for _, r in results if r.status not in _OFF_STATUSES]
        if not rows:
            continue
        came = [r for r in rows if r.first_in is not None]
        late = sum(1 for r in came if r.late_minutes > 0)
        missing = len(rows) - len(came)
        came_all, total_all = came_all + len(came), total_all + len(rows)
        lines.append(
            f"🏢 <b>{esc(name)}</b>: ✅ {len(came)}/{len(rows)} · ⏰ {late} · ❌ {missing}"
        )
    if total_all == 0:
        return None
    lines += ["", f"Jami keldi: <b>{came_all}</b> / {total_all}"]
    return "\n".join(lines)


def late_message(e: Employee, r: engine.DayResult, tz: ZoneInfo) -> str:
    return (
        f"⏰ <b>{esc(full_name(e))}</b> kechikdi: {hhmm(r.first_in, tz)} "
        f"(ish {hhmm(r.scheduled_start, tz)} da boshlanadi, +{r.late_minutes} daq)"
    )


def first_name(e: Employee) -> str:
    return e.first_name or full_name(e)


def punch_message(
    e: Employee, direction: PunchDirection, t: datetime, late: engine.DayResult | None, tz: ZoneInfo
) -> str:
    """Message to the employee about their own punch. `late` = today's result when this punch
    is a late first arrival."""
    if direction is PunchDirection.OUT:
        return f"👋 {esc(first_name(e))}, ishdan ketishingiz qayd etildi: <b>{hhmm(t, tz)}</b>"
    text = f"✅ {esc(first_name(e))}, ishga kelishingiz qayd etildi: <b>{hhmm(t, tz)}</b>"
    if late is not None:
        text += (
            f"\n⏰ Ish {hhmm(late.scheduled_start, tz)} da boshlanadi — "
            f"<b>{late.late_minutes} daqiqa</b> kechikish."
        )
    return text


def self_late_message(e: Employee, r: engine.DayResult, tz: ZoneInfo) -> str:
    return (
        f"⏰ {esc(first_name(e))}, bugun ishga {hhmm(r.first_in, tz)} da keldingiz. "
        f"Ish {hhmm(r.scheduled_start, tz)} da boshlanadi — <b>{r.late_minutes} daqiqa</b> "
        "kechikish qayd etildi."
    )


def reviews_message(new: int, pending: int) -> str:
    return (
        f"🔍 <b>{new}</b> ta yangi noaniq yuz koʻrib chiqishni kutmoqda (jami: {pending}).\n"
        "Sayt → AI tanish → Koʻrib chiqish"
    )


def camera_down_message(name: str, since: datetime | None, tz: ZoneInfo) -> str:
    when = f" ({hhmm(since, tz)} dan beri kadr yoʻq)" if since else ""
    return f"📷❗ <b>{esc(name)}</b> kamerasi ishlamayapti{when}."


def camera_up_message(name: str) -> str:
    return f"📷✅ <b>{esc(name)}</b> kamerasi qayta ishlayapti."


# Advice for camera health findings (same codes as apps/camera/camera/health.py).
HEALTH_ADVICE = {
    "unstable_connection": "ulanish tez-tez uziladi ({count} marta / soat). Kabel bilan ulang "
    "yoki bitreytni kamaytiring",
    "frame_loss": "kadrlar yoʻqolyapti ({count} ta / 5 daq). Tarmoq sekin: kabel ulang yoki "
    "bitreytni kamaytiring",
    "low_fps": "kadr tezligi past ({fps} kadr/s)",
    "cpu_high": "kompyuter protsessorini koʻp yuklayapti ({cpu}%)",
    "h265": "kodekni H.264 ga oʻzgartiring (H.265 ni ochish ~2 baravar ogʻir)",
    "resolution_high": "ruxsatni 1920×1080 ga tushiring ({width}×{height} keragidan katta)",
}


def camera_health_message(name: str, issues: list[dict[str, Any]]) -> str | None:
    lines = []
    for i in issues:
        if i.get("severity") != "warn" or i.get("code") not in HEALTH_ADVICE:
            continue
        try:
            lines.append("  • " + esc(HEALTH_ADVICE[i["code"]].format(**i)))
        except (KeyError, IndexError):
            lines.append("  • " + esc(HEALTH_ADVICE[i["code"]]))
    if not lines:
        return None
    return f"📷⚠️ <b>{esc(name)}</b> kamerasi bilan muammo:\n" + "\n".join(lines)


def backup_message(detail: str) -> str:
    return f"💾❗ Zaxira nusxa: {esc(detail)}. Tekshiring: npm run backup"


# --- sending ----------------------------------------------------------------------------------


async def _claim(
    db: AsyncSession, kind: str, key: str, summary: str, data: dict[str, Any] | None = None
) -> NotificationLog | None:
    """Insert the log row; None if this (kind, key) was already handled."""
    stmt = (
        insert(NotificationLog)
        .values(kind=kind, key=key[:160], summary=summary[:500], data=data)
        .on_conflict_do_nothing(constraint="uq_notification_log_kind_key")
        .returning(NotificationLog.id)
    )
    new_id = (await db.execute(stmt)).scalar_one_or_none()
    return None if new_id is None else await db.get(NotificationLog, new_id)


async def recipients_for(db: AsyncSession, kind: str) -> list[TelegramRecipient]:
    """Every active chat subscribed to `kind` (centre and branches)."""
    rows = (
        await db.execute(select(TelegramRecipient).where(TelegramRecipient.is_active.is_(True)))
    ).scalars()
    return [r for r in rows if kind in (r.subscriptions or [])]


def for_branch(
    people: list[TelegramRecipient], branch_id: uuid.UUID | None
) -> list[TelegramRecipient]:
    """The centre's chats plus (when given) that branch's chats."""
    return [r for r in people if r.branch_id is None or r.branch_id == branch_id]


async def branch_names(db: AsyncSession) -> dict[uuid.UUID, str]:
    rows = await db.execute(
        select(Branch.id, Branch.name).where(Branch.is_active.is_(True)).order_by(Branch.name)
    )
    return {bid: name for bid, name in rows.all()}


async def deliver_once(
    db: AsyncSession,
    tg: Telegram,
    kind: str,
    key: str,
    groups: list[tuple[list[TelegramRecipient], str | None]],
    data: dict[str, Any] | None = None,
) -> bool:
    """Like notify_people, but different chats may get different texts (centre / branch)."""
    ready = [(p, t) for p, t in groups if p and t]
    if not ready:
        return False
    row = await _claim(db, kind, key, ready[0][1].splitlines()[0], data)
    if row is None:
        return False
    row.recipients = sum(len(p) for p, _ in ready)
    row.delivered = 0
    for people, text in ready:
        row.delivered += await deliver(db, tg, people, text)
    await db.flush()
    return True


async def deliver(
    db: AsyncSession, tg: Telegram, people: list[TelegramRecipient], text: str
) -> int:
    sent = 0
    for r in people:
        try:
            await tg.send(r.chat_id, text)
            sent += 1
            r.last_error = None
        except TelegramError as e:
            r.last_error = str(e)[:300]
            if e.blocked:
                r.is_active = False  # the chat blocked the bot or no longer exists
            log.warning("telegram send failed for recipient %s: %s", r.id, e)
    return sent


async def notify(
    db: AsyncSession,
    tg: Telegram,
    kind: str,
    key: str,
    text: str,
    data: dict[str, Any] | None = None,
    audience: str | None = None,
    branch_id: uuid.UUID | None = None,
) -> bool:
    """Send `text` once for (kind, key) to the centre's chats subscribed to `audience` (default:
    kind) and, when `branch_id` is given, to that branch's subscribed chats."""
    people = for_branch(await recipients_for(db, audience or kind), branch_id)
    return await notify_people(db, tg, kind, key, text, people, data)


async def notify_people(
    db: AsyncSession,
    tg: Telegram,
    kind: str,
    key: str,
    text: str,
    people: list[TelegramRecipient],
    data: dict[str, Any] | None = None,
) -> bool:
    """Send `text` once for (kind, key) to exactly these chats."""
    if not people:
        return False
    row = await _claim(db, kind, key, text.splitlines()[0], data)
    if row is None:
        return False
    row.recipients = len(people)
    row.delivered = await deliver(db, tg, people, text)
    await db.flush()
    return True


# --- periodic checks --------------------------------------------------------------------------


async def today_results(
    db: AsyncSession, today: date, branch_id: uuid.UUID | None = None
) -> tuple[list[tuple[Employee, engine.DayResult]], int]:
    stmt = select(Employee).where(
        Employee.deleted_at.is_(None),
        Employee.status.in_([EmployeeStatus.ACTIVE, EmployeeStatus.ON_LEAVE]),
    )
    if branch_id is not None:
        stmt = stmt.where(Employee.branch_id == branch_id)
    emps = list((await db.execute(stmt)).scalars())
    active = [e for e in emps if e.status is EmployeeStatus.ACTIVE]
    on_leave = len(emps) - len(active)
    return await att.compute_range(db, active, today, today), on_leave


async def check_daily(db: AsyncSession, tg: Telegram, s: NotifySettings, now: datetime) -> bool:
    local = now.astimezone(s.tz)
    subs = await recipients_for(db, "daily")
    if (local.hour, local.minute) < s.daily_time or not subs:
        return False
    today = local.date()
    key = today.isoformat()
    if await db.scalar(
        select(NotificationLog.id).where(
            NotificationLog.kind == "daily", NotificationLog.key == key
        )
    ):
        return False
    names = await branch_names(db)
    groups: list[tuple[list[TelegramRecipient], str | None]] = []
    if len(names) <= 1:  # a single branch: everyone gets the detailed message
        results, on_leave = await today_results(db, today)
        groups.append((subs, daily_summary(today, results, on_leave, s.tz)))
    else:
        per_branch = []
        for bid, name in names.items():
            results, on_leave = await today_results(db, today, bid)
            per_branch.append((name, results))
            mine = [r for r in subs if r.branch_id == bid]
            groups.append((mine, daily_summary(today, results, on_leave, s.tz, name)))
        centre = [r for r in subs if r.branch_id is None]
        groups.insert(0, (centre, branches_summary(today, per_branch)))
    if not any(t for _, t in groups):  # nobody works today: remember it, send nothing
        await _claim(db, "daily", key, "skipped: nobody scheduled")
        return False
    return await deliver_once(db, tg, "daily", key, groups)


async def check_late(db: AsyncSession, tg: Telegram, s: NotifySettings, now: datetime) -> int:
    subs = await recipients_for(db, "late")
    if not subs:
        return 0
    today = now.astimezone(s.tz).date()
    results, _ = await today_results(db, today)
    names = await branch_names(db)
    sent = 0
    for e, r in results:
        if r.first_in is None or r.late_minutes <= 0 or now - r.first_in > LATE_WINDOW:
            continue
        text = late_message(e, r, s.tz)
        if len(names) > 1 and e.branch_id in names:
            text += f"\n🏢 {esc(names[e.branch_id])}"
        people = for_branch(subs, e.branch_id)
        if await notify_people(db, tg, "late", f"{today}:{e.id}", text, people):
            sent += 1
    return sent


async def check_reviews(db: AsyncSession, tg: Telegram, s: NotifySettings, now: datetime) -> bool:
    subs = await recipients_for(db, "reviews")
    if not subs:
        return False
    last = (
        await db.execute(
            select(NotificationLog)
            .where(NotificationLog.kind == "reviews")
            .order_by(NotificationLog.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if last is not None and now - last.created_at < s.review_interval:
        return False
    seen = int((last.data or {}).get("max_id", 0)) if last else 0
    pending = AttendanceEvent.review_status == "PENDING"
    new, max_id = (
        await db.execute(
            select(func.count(), func.max(AttendanceEvent.id)).where(
                pending, AttendanceEvent.id > seen
            )
        )
    ).one()
    if not new:
        return False
    total = await db.scalar(select(func.count()).where(pending)) or 0
    groups: list[tuple[list[TelegramRecipient], str | None]] = [
        ([r for r in subs if r.branch_id is None], reviews_message(int(new), int(total)))
    ]
    # Each branch hears about its own cameras' faces only.
    by_branch = (
        await db.execute(
            select(
                Camera.branch_id,
                func.count().filter(AttendanceEvent.id > seen),
                func.count(),
            )
            .join(Camera, Camera.id == AttendanceEvent.camera_id)
            .where(pending)
            .group_by(Camera.branch_id)
        )
    ).all()
    for bid, b_new, b_total in by_branch:
        mine = [r for r in subs if r.branch_id == bid]
        if b_new and mine:
            groups.append((mine, reviews_message(int(b_new), int(b_total))))
    return await deliver_once(
        db, tg, "reviews", f"{now:%Y-%m-%dT%H:%M}:{max_id}", groups, {"max_id": int(max_id)}
    )


async def check_cameras(db: AsyncSession, tg: Telegram, s: NotifySettings, now: datetime) -> int:
    """One alert per outage (keyed by the last frame time) and one when it recovers."""
    if not await recipients_for(db, "system"):
        return 0
    cams = (
        await db.execute(
            select(Camera).where(
                Camera.deleted_at.is_(None),
                Camera.is_enabled.is_(True),
                Camera.last_seen_at.is_not(None),
            )
        )
    ).scalars()
    sent = 0
    for c in cams:
        if c.last_seen_at is None:  # filtered in SQL; keeps the type checker informed
            continue
        down = now - c.last_seen_at > s.camera_offline
        last_down = (
            await db.execute(
                select(NotificationLog)
                .where(
                    NotificationLog.kind == "camera_down",
                    NotificationLog.key.like(f"{c.id}:%"),
                )
                .order_by(NotificationLog.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if down:
            key = f"{c.id}:{c.last_seen_at.isoformat()}"
            text = camera_down_message(c.name, c.last_seen_at, s.tz)
            if await notify(
                db, tg, "camera_down", key, text, audience="system", branch_id=c.branch_id
            ):
                sent += 1
        elif last_down is not None:
            # back online after an alert: one recovery message per outage
            text = camera_up_message(c.name)
            if await notify(
                db, tg, "camera_up", last_down.key, text, audience="system", branch_id=c.branch_id
            ):
                sent += 1
        if not down:
            # unreliable or too heavy (health check of the camera service): at most once a day
            health = (c.runtime or {}).get("health") or {}
            msg = (
                camera_health_message(c.name, health.get("issues") or [])
                if health.get("verdict") == "warn"
                else None
            )
            day = now.astimezone(s.tz).date().isoformat()
            if msg and await notify(
                db,
                tg,
                "camera_health",
                f"{c.id}:{day}",
                msg,
                audience="system",
                branch_id=c.branch_id,
            ):
                sent += 1
    return sent


async def check_director(db: AsyncSession, tg: Telegram, s: NotifySettings, now: datetime) -> int:
    """The director's report (centre chats only), at the daily summary time."""
    from app.services import director  # local import: director uses helpers of this module

    local = now.astimezone(s.tz)
    people = [r for r in await recipients_for(db, "director") if r.branch_id is None]
    if (local.hour, local.minute) < s.daily_time or not people:
        return 0
    today = local.date()
    jobs: list[tuple[str, date, date, str | None]] = [(f"day:{today}", today, today, None)]
    if today.weekday() == 0:
        start, end, week = director.last_week(today)
        jobs.append((f"week:{start}", start, end, week))
    if today.day == 1:
        start, end, month = director.last_month(today)
        jobs.append((f"month:{start:%Y-%m}", start, end, month))
    sent = 0
    for key, start, end, title in jobs:
        if await db.scalar(
            select(NotificationLog.id).where(
                NotificationLog.kind == "director", NotificationLog.key == key
            )
        ):
            continue
        p = await director.collect(db, start, end)
        text = director.daily_text(today, p) if title is None else director.period_text(title, p)
        if text is None:  # nobody was scheduled: remember it, send nothing
            await _claim(db, "director", key, "skipped: nobody scheduled")
            continue
        if await notify_people(db, tg, "director", key, text, people):
            sent += 1
    return sent


async def check_backup(db: AsyncSession, tg: Telegram, s: NotifySettings, now: datetime) -> bool:
    from app.api.v1.system import backup_check  # local import: avoids an import cycle

    c = backup_check()
    if c.status == "ok" or not c.detail:
        return False
    day = now.astimezone(s.tz).date().isoformat()
    return await notify(db, tg, "backup", day, backup_message(c.detail), audience="system")


async def check_self(db: AsyncSession, tg: Telegram, s: NotifySettings, now: datetime) -> int:
    """Messages to employees about their own punches / lateness (recent ones only)."""
    chats = [
        r
        for r in (
            await db.execute(
                select(TelegramRecipient).where(
                    TelegramRecipient.is_active.is_(True),
                    TelegramRecipient.employee_id.is_not(None),
                )
            )
        ).scalars()
        if any(k in (r.subscriptions or []) for k in SELF_SUBSCRIPTIONS)
    ]
    if not chats:
        return 0
    by_emp = {r.employee_id: r for r in chats}
    emps = {
        e.id: e
        for e in (
            await db.execute(
                select(Employee).where(
                    Employee.id.in_(list(by_emp)),
                    Employee.deleted_at.is_(None),
                    Employee.status == EmployeeStatus.ACTIVE,
                )
            )
        ).scalars()
    }
    if not emps:
        return 0
    today = now.astimezone(s.tz).date()
    results = {e.id: r for e, r in await att.compute_range(db, list(emps.values()), today, today)}
    sent = 0

    # every recorded punch of the last 30 minutes
    events = (
        await db.execute(
            select(AttendanceEvent)
            .where(
                AttendanceEvent.employee_id.in_(list(emps)),
                AttendanceEvent.counts_for_attendance.is_(True),
                AttendanceEvent.is_voided.is_(False),
                AttendanceEvent.event_time >= now - LATE_WINDOW,
                AttendanceEvent.event_time <= now + timedelta(minutes=1),
            )
            .order_by(AttendanceEvent.event_time, AttendanceEvent.id)
        )
    ).scalars()
    for ev in events:
        emp_id = ev.employee_id
        if emp_id is None or ev.direction is None:
            continue
        chat = by_emp[emp_id]
        if "self_punch" not in (chat.subscriptions or []):
            continue
        e, r = emps[emp_id], results.get(emp_id)
        late = r if r is not None and r.late_minutes > 0 and r.first_in == ev.event_time else None
        text = punch_message(e, ev.direction, ev.event_time, late, s.tz)
        if await notify_people(db, tg, "self_punch", str(ev.id), text, [chat]):
            sent += 1

    # lateness only (for chats that do not get every punch)
    for emp_id, e in emps.items():
        chat, r = by_emp[emp_id], results.get(emp_id)
        subs = chat.subscriptions or []
        if "self_late" not in subs or "self_punch" in subs or r is None:
            continue
        if r.first_in is None or r.late_minutes <= 0 or now - r.first_in > LATE_WINDOW:
            continue
        text = self_late_message(e, r, s.tz)
        if await notify_people(db, tg, "self_late", f"{today}:{emp_id}", text, [chat]):
            sent += 1
    return sent


async def run_checks(db: AsyncSession, tg: Telegram, now: datetime | None = None) -> dict[str, Any]:
    """The periodic job. Does nothing while notify.telegram_enabled is off."""
    now = now or datetime.now(UTC)
    s = await load_settings(db)
    if not s.enabled:
        return {"enabled": False}
    return {
        "enabled": True,
        "daily": await check_daily(db, tg, s, now),
        "director": await check_director(db, tg, s, now),
        "late": await check_late(db, tg, s, now),
        "self": await check_self(db, tg, s, now),
        "reviews": await check_reviews(db, tg, s, now),
        "cameras": await check_cameras(db, tg, s, now),
        "backup": await check_backup(db, tg, s, now),
    }


# --- linking a chat ---------------------------------------------------------------------------


@dataclass
class _Code:
    title: str
    user_id: Any
    expires: float
    employee_id: Any = None  # set: link this chat to the employee (self messages)
    branch_id: Any = None  # admin chat of this branch (None = the centre)


class LinkCodes:
    """One-time codes shown on the Settings page; the chat sends `/start <code>` to the bot.
    Kept in memory (they live 15 minutes; a restart simply needs a new code)."""

    def __init__(self) -> None:
        self._codes: dict[str, _Code] = {}
        self.created = asyncio.Event()  # wakes the update poller at once

    def create(
        self, title: str, user_id: Any, employee_id: Any = None, branch_id: Any = None
    ) -> tuple[str, float]:
        self._purge()
        code = secrets.token_urlsafe(9)
        expires = time.time() + LINK_CODE_TTL
        self._codes[code] = _Code(title, user_id, expires, employee_id, branch_id)
        self.created.set()
        return code, expires

    def take(self, code: str) -> _Code | None:
        self._purge()
        return self._codes.pop(code, None)

    def pending(self) -> bool:
        self._purge()
        return bool(self._codes)

    def _purge(self) -> None:
        now = time.time()
        for k in [k for k, v in self._codes.items() if v.expires < now]:
            del self._codes[k]


link_codes = LinkCodes()


async def handle_update(db: AsyncSession, tg: Telegram, update: dict[str, Any]) -> str | None:
    """Link a chat on `/start <code>` (returns the linked title); otherwise the attendance menu
    (app/services/bot_menu.py) answers buttons, /menu and typed dates."""
    from app.services import bot_menu  # local import: bot_menu uses this module

    if update.get("callback_query"):
        await bot_menu.handle_callback(db, tg, update["callback_query"])
        return None
    msg = update.get("message") or {}
    text = str(msg.get("text") or "").strip()
    chat = msg.get("chat") or {}
    chat_id = chat.get("id")
    if not isinstance(chat_id, int):
        return None
    parts = text.split(maxsplit=1)
    if not (text.startswith("/start") and len(parts) == 2):
        await bot_menu.handle_message(db, tg, chat_id, text)
        return None
    entry = link_codes.take(parts[1].strip())
    if entry is None:
        with contextlib.suppress(TelegramError):
            await tg.send(
                chat_id,
                "Bu bot AI Davomat tizimining bildirishnomalari uchun. Ulanish uchun "
                "administrator bergan havoladan foydalaning.",
            )
        return None
    username = (msg.get("from") or {}).get("username") or chat.get("username")
    if entry.employee_id is not None:
        await _link_employee(db, chat_id, username, entry)
        text = (
            f"✅ Salom, <b>{esc(entry.title)}</b>! Endi ishga kelishingiz, ketishingiz va "
            "kechikishlar haqidagi xabarlar shu yerga keladi. Boshqa xodimlar haqida "
            "maʼlumot yuborilmaydi."
        )
    else:
        await _link_admin(db, chat_id, username, entry)
        text = (
            f"✅ Ulandingiz: <b>{esc(entry.title)}</b>.\nBildirishnomalar shu yerga keladi. "
            "Qaysi xabarlar kelishini saytdagi Sozlamalar sahifasida tanlash mumkin.\n\n"
            "📋 Davomatni shu yerning oʻzida koʻrish uchun: /menu"
        )
    await db.flush()
    with contextlib.suppress(TelegramError):
        await tg.send(chat_id, text)
    return entry.title


async def _chat_row(db: AsyncSession, chat_id: int) -> TelegramRecipient | None:
    return (
        await db.execute(select(TelegramRecipient).where(TelegramRecipient.chat_id == chat_id))
    ).scalar_one_or_none()


async def _link_admin(db: AsyncSession, chat_id: int, username: str | None, entry: _Code) -> None:
    row = await _chat_row(db, chat_id)
    if row is None:
        db.add(
            TelegramRecipient(
                chat_id=chat_id,
                title=entry.title,
                username=username,
                created_by_user_id=entry.user_id,
                subscriptions=list(DEFAULT_SUBSCRIPTIONS),
                branch_id=entry.branch_id,
            )
        )
        return
    subs = list(row.subscriptions or [])
    if not any(k in subs for k in ADMIN_SUBSCRIPTIONS):  # an employee chat becomes admin too
        subs += DEFAULT_SUBSCRIPTIONS
    row.title, row.username, row.subscriptions = entry.title, username, subs
    row.branch_id = entry.branch_id
    row.is_active, row.last_error = True, None


async def unlink_employee(db: AsyncSession, row: TelegramRecipient) -> None:
    """Remove the employee link; delete the chat if nothing else is left."""
    subs = [k for k in (row.subscriptions or []) if k not in SELF_SUBSCRIPTIONS]
    if not subs:
        await db.delete(row)
    else:
        row.employee_id, row.subscriptions = None, subs
    await db.flush()


async def _link_employee(
    db: AsyncSession, chat_id: int, username: str | None, entry: _Code
) -> None:
    # one chat per employee: a new link replaces the old chat
    old = (
        await db.execute(
            select(TelegramRecipient).where(TelegramRecipient.employee_id == entry.employee_id)
        )
    ).scalar_one_or_none()
    if old is not None and old.chat_id != chat_id:
        await unlink_employee(db, old)
    row = await _chat_row(db, chat_id)
    if row is None:
        db.add(
            TelegramRecipient(
                chat_id=chat_id,
                title=entry.title,
                username=username,
                created_by_user_id=entry.user_id,
                employee_id=entry.employee_id,
                subscriptions=list(DEFAULT_SELF_SUBSCRIPTIONS),
            )
        )
        return
    subs = list(row.subscriptions or [])
    subs += [k for k in DEFAULT_SELF_SUBSCRIPTIONS if k not in subs]
    row.employee_id, row.username, row.subscriptions = entry.employee_id, username, subs
    row.is_active, row.last_error = True, None


# --- background worker ------------------------------------------------------------------------


class NotificationWorker:
    """Two asyncio tasks inside the API process: Telegram updates (long polling, only while a
    link code is waiting or every minute) and the periodic checks (every 30 s)."""

    CHECK_EVERY = 15.0  # punch confirmations reach the employee within ~15 s

    def __init__(self, tg: Telegram) -> None:
        self.tg = tg
        self._tasks: list[asyncio.Task[None]] = []
        self._offset: int | None = None
        self.last_error: str | None = None
        self.last_run: datetime | None = None

    def start(self) -> None:
        self._tasks = [
            asyncio.create_task(self._commands(), name="notify-commands"),
            asyncio.create_task(self._checks(), name="notify-checks"),
            asyncio.create_task(self._updates(), name="notify-updates"),
        ]

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await t

    async def _commands(self) -> None:
        """The bot's command list (the "Menu" button in Telegram). Best effort."""
        from app.services.bot_menu import COMMANDS

        with contextlib.suppress(TelegramError):
            await self.tg.set_commands(COMMANDS)

    async def _checks(self) -> None:
        from app.db.session import session_scope

        while True:
            try:
                async with session_scope() as db:
                    await run_checks(db, self.tg)
                self.last_run, self.last_error = datetime.now(UTC), None
            except Exception as e:  # keep the loop alive; the status endpoint shows the error
                self.last_error = f"{type(e).__name__}: {str(e)[:200]}"
                log.exception("notification checks failed")
            await asyncio.sleep(self.CHECK_EVERY)

    async def poll_updates_once(self, poll_s: int) -> None:
        from app.db.session import session_scope

        updates = await self.tg.updates(self._offset, poll_s)
        for u in updates:
            self._offset = int(u.get("update_id", 0)) + 1
            async with session_scope() as db:
                await handle_update(db, self.tg, u)

    async def _updates(self) -> None:
        """Long polling all the time: links and menu buttons are answered at once (an idle
        long poll is one cheap request every 25 s)."""
        while True:
            try:
                await self.poll_updates_once(25)
            except TelegramError as e:
                self.last_error = str(e)
                await asyncio.sleep(10)
            except Exception:
                log.exception("telegram update polling failed")
                await asyncio.sleep(10)


worker: NotificationWorker | None = None
