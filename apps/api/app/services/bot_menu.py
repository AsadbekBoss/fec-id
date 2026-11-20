"""Attendance menu inside the Telegram bot (Phase 23).

Only chats linked by an administrator can use it (`telegram_recipients`, active, with an
administrator subscription). A centre chat (branch_id NULL) sees every branch: rankings for a
day / week / month, any branch's day, week or month, any past date. A branch chat sees its own
branch only; every request is checked again on each button press, so a forged button cannot
reach another branch. Strangers get the standard "use the link" answer and nothing else.

Buttons carry short callback data (<= 64 bytes):
    m                 main menu            dp            date picker
    rd:<date>         ranking, one day     bl            branch list
    rw:<monday>       ranking, one week    bd:<id>:<date>     one branch, one day
    rm:<yyyy-mm>      ranking, one month   bw:<id>:<monday>   one branch, one week
                                           bm:<id>:<yyyy-mm>  one branch, one month
A typed date (19.11.2026 or 19.11) opens that day.
"""

from __future__ import annotations

import contextlib
import re
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Branch
from app.models.notifications import ADMIN_SUBSCRIPTIONS, TelegramRecipient
from app.services import director
from app.services import notifications as notif
from app.services.telegram import Keyboard, Telegram, TelegramError

STRANGER = (
    "Bu bot AI Davomat tizimining bildirishnomalari uchun. Ulanish uchun administrator bergan "
    "havoladan foydalaning."
)
NO_MENU = "Menyu faqat administratorlar chatlari uchun. Bu chatga faqat oʻzingiz haqingizdagi xabarlar keladi."  # noqa: E501, RUF001
DATE_RE = re.compile(r"^(\d{1,2})[./-](\d{1,2})(?:[./-](\d{4}))?$")
COMMANDS = [("menu", "Davomat menyusi"), ("bugun", "Bugungi holat")]


# --- who may use it --------------------------------------------------------------------------


async def _admin_chat(db: AsyncSession, chat_id: int) -> TelegramRecipient | None:
    r = (
        await db.execute(select(TelegramRecipient).where(TelegramRecipient.chat_id == chat_id))
    ).scalar_one_or_none()
    if r is None or not r.is_active:
        return None
    is_admin = r.employee_id is None or any(k in ADMIN_SUBSCRIPTIONS for k in r.subscriptions)
    return r if is_admin else None


def _allowed(chat: TelegramRecipient, branch_id: uuid.UUID) -> bool:
    return chat.branch_id is None or chat.branch_id == branch_id


# --- dates -------------------------------------------------------------------------------------


def _week(d: date) -> tuple[date, date]:
    start = d - timedelta(days=d.weekday())
    return start, start + timedelta(days=6)


def _month(d: date) -> tuple[date, date]:
    start = d.replace(day=1)
    nxt = (start + timedelta(days=32)).replace(day=1)
    return start, nxt - timedelta(days=1)


def _parse_date(text: str, today: date) -> date | None:
    m = DATE_RE.match(text.strip())
    if not m:
        return None
    try:
        return date(int(m.group(3) or today.year), int(m.group(2)), int(m.group(1)))
    except ValueError:
        return None


# --- views (text + buttons) ---------------------------------------------------------------------

View = tuple[str, Keyboard]


def _home_row(chat: TelegramRecipient) -> list[tuple[str, str]]:
    row = [("🏠 Menyu", "m")]
    if chat.branch_id is None:
        row.insert(0, ("🏢 Filiallar", "bl"))
    return row


def main_menu(chat: TelegramRecipient, today: date) -> View:
    if chat.branch_id is None:
        return (
            "📋 <b>Davomat menyusi</b>\nBarcha filiallar. Tanlang yoki sanani yozing "
            "(masalan, <code>19.11.2026</code>).",
            [
                [("🏆 Bugungi reyting", f"rd:{today}")],
                [("📈 Haftalik reyting", f"rw:{_week(today)[0]}"),
                 ("🗓 Oylik reyting", f"rm:{today:%Y-%m}")],
                [("🏢 Filiallar", "bl"), ("📅 Sana tanlash", "dp")],
            ],
        )  # fmt: skip
    bid = chat.branch_id
    return (
        "📋 <b>Davomat menyusi</b>\nTanlang yoki sanani yozing (masalan, <code>19.11.2026</code>).",
        [
            [("📊 Bugun", f"bd:{bid}:{today}")],
            [("📈 Hafta", f"bw:{bid}:{_week(today)[0]}"), ("🗓 Oy", f"bm:{bid}:{today:%Y-%m}")],
            [("📅 Sana tanlash", "dp")],
        ],
    )


def date_picker(chat: TelegramRecipient, today: date) -> View:
    days = [today - timedelta(days=i) for i in range(7)]
    def target(d: date) -> str:
        return f"rd:{d}" if chat.branch_id is None else f"bd:{chat.branch_id}:{d}"

    rows: Keyboard = [
        [(f"{d:%d.%m}" + (" (bugun)" if d == today else ""), target(d)) for d in days[i : i + 3]]
        for i in range(0, 7, 3)
    ]
    rows.append([("🏠 Menyu", "m")])
    return (
        "📅 <b>Sanani tanlang</b>\nOxirgi 7 kun quyida. Boshqa sana uchun uni yozib yuboring: "
        "<code>05.11.2026</code>",
        rows,
    )


async def branch_list(db: AsyncSession, today: date) -> View:
    branches = (await db.execute(select(Branch).order_by(Branch.name))).scalars()
    rows: Keyboard = [
        [(("🏢 " if b.is_active else "🔒 ") + b.name[:40], f"bd:{b.id}:{today}")] for b in branches
    ]
    rows.append([("🏠 Menyu", "m")])
    return "🏢 <b>Filialni tanlang</b>", rows


def _nav(prev: str | None, nxt: str | None) -> list[tuple[str, str]]:
    row = []
    if prev:
        row.append(("◀️ Oldingi", prev))
    if nxt:
        row.append(("Keyingi ▶️", nxt))
    return row


async def ranking_day(db: AsyncSession, chat: TelegramRecipient, d: date, today: date) -> View:
    text = director.daily_text(d, await director.collect(db, d, d), past=d < today)
    kb: Keyboard = [
        _nav(f"rd:{d - timedelta(days=1)}", f"rd:{d + timedelta(days=1)}" if d < today else None),
        [("📈 Shu hafta", f"rw:{_week(d)[0]}"), ("🗓 Shu oy", f"rm:{d:%Y-%m}")],
        _home_row(chat),
    ]
    return text or f"📅 {d:%d.%m.%Y}: bu kuni hech kim ishlamaydi (dam olish kuni).", kb


async def ranking_period(
    db: AsyncSession, chat: TelegramRecipient, kind: str, start: date, today: date
) -> View:
    if kind == "w":
        start, end = _week(start)
        title = f"hafta {start:%d.%m} – {end:%d.%m.%Y}"  # noqa: RUF001
        prev, nxt = start - timedelta(days=7), start + timedelta(days=7)
        keys = (f"rw:{prev}", f"rw:{nxt}" if nxt <= today else None)
    else:
        start, end = _month(start)
        title = f"{director.MONTHS[start.month - 1]} {start.year}"
        prev = (start - timedelta(days=1)).replace(day=1)
        nxt = end + timedelta(days=1)
        keys = (f"rm:{prev:%Y-%m}", f"rm:{nxt:%Y-%m}" if nxt <= today else None)
    if end >= today:
        end, title = today, f"{title} (hozirgacha)"
    text = director.period_text(title, await director.collect(db, start, end))
    return text or f"📈 {title}: ish kunlari yoʻq.", [_nav(*keys), _home_row(chat)]  # noqa: RUF001


async def branch_day(
    db: AsyncSession, chat: TelegramRecipient, bid: uuid.UUID, d: date, today: date
) -> View:
    b = await db.get(Branch, bid)
    name = b.name if b else "Filial"
    s = await notif.load_settings(db)
    results, on_leave = await notif.today_results(db, d, bid)
    text = notif.daily_summary(d, results, on_leave, s.tz, name)
    if text and d < today:
        text = text.replace("Hali kelmagan", "Kelmagan")
    kb: Keyboard = [
        _nav(
            f"bd:{bid}:{d - timedelta(days=1)}",
            f"bd:{bid}:{d + timedelta(days=1)}" if d < today else None,
        ),
        [("📈 Hafta", f"bw:{bid}:{_week(d)[0]}"), ("🗓 Oy", f"bm:{bid}:{d:%Y-%m}")],
        _home_row(chat),
    ]
    return text or f"📅 {name}, {d:%d.%m.%Y}: bu kuni hech kim ishlamaydi.", kb


async def branch_period(
    db: AsyncSession, chat: TelegramRecipient, bid: uuid.UUID, kind: str, start: date, today: date
) -> View:
    if kind == "w":
        start, end = _week(start)
        title = f"hafta {start:%d.%m} – {end:%d.%m.%Y}"  # noqa: RUF001
        prev, nxt = start - timedelta(days=7), start + timedelta(days=7)
        keys = (f"bw:{bid}:{prev}", f"bw:{bid}:{nxt}" if nxt <= today else None)
    else:
        start, end = _month(start)
        title = f"{director.MONTHS[start.month - 1]} {start.year}"
        prev = (start - timedelta(days=1)).replace(day=1)
        nxt = end + timedelta(days=1)
        keys = (f"bm:{bid}:{prev:%Y-%m}", f"bm:{bid}:{nxt:%Y-%m}" if nxt <= today else None)
    if end >= today:
        end, title = today, f"{title} (hozirgacha)"
    p = await director.collect(db, start, end, bid)
    text = director.period_text(title, p, single=True)
    kb: Keyboard = [_nav(*keys), [("📊 Bugun", f"bd:{bid}:{today}")], _home_row(chat)]
    return text or f"📈 {title}: ish kunlari yoʻq.", kb  # noqa: RUF001


async def render(db: AsyncSession, chat: TelegramRecipient, data: str, today: date) -> View | None:
    """The view for a button. None = not allowed or malformed."""
    centre = chat.branch_id is None
    parts = data.split(":")
    try:
        if data == "m":
            return main_menu(chat, today)
        if data == "dp":
            return date_picker(chat, today)
        if data == "bl":
            return await branch_list(db, today) if centre else None
        if parts[0] in ("rd", "rw", "rm") and len(parts) == 2:
            if not centre:
                return None
            if parts[0] == "rm":
                start = date.fromisoformat(parts[1] + "-01")
                return await ranking_period(db, chat, "m", min(start, today), today)
            d = min(date.fromisoformat(parts[1]), today)
            if parts[0] == "rd":
                return await ranking_day(db, chat, d, today)
            return await ranking_period(db, chat, "w", d, today)
        if parts[0] in ("bd", "bw", "bm") and len(parts) == 3:
            bid = uuid.UUID(parts[1])
            if not _allowed(chat, bid):
                return None
            if parts[0] == "bm":
                start = date.fromisoformat(parts[2] + "-01")
                return await branch_period(db, chat, bid, "m", min(start, today), today)
            d = min(date.fromisoformat(parts[2]), today)
            if parts[0] == "bd":
                return await branch_day(db, chat, bid, d, today)
            return await branch_period(db, chat, bid, "w", d, today)
    except ValueError:
        return None
    return None


# --- update handling ------------------------------------------------------------------------------


async def _today(db: AsyncSession) -> date:
    s = await notif.load_settings(db)
    return datetime.now(UTC).astimezone(s.tz).date()


async def handle_message(db: AsyncSession, tg: Telegram, chat_id: int, text: str) -> bool:
    """/menu, /bugun, /start (already linked), "menyu" or a date. True when answered."""
    cmd = text.split("@", 1)[0].strip().lower()
    today = await _today(db)
    wants_menu = cmd in ("/menu", "/start", "menyu", "menu", "📋 menyu")
    when = _parse_date(text, today) if not cmd.startswith("/") else None
    if not (wants_menu or cmd == "/bugun" or when):
        return False
    chat = await _admin_chat(db, chat_id)
    if chat is None:
        linked = (
            await db.execute(
                select(TelegramRecipient.id).where(TelegramRecipient.chat_id == chat_id)
            )
        ).first()
        with contextlib.suppress(TelegramError):
            await tg.send(chat_id, NO_MENU if linked else STRANGER)
        return True
    if when is not None and when > today:
        view: View | None = ("📅 Kelajakdagi sana uchun maʼlumot yoʻq.", [[("🏠 Menyu", "m")]])  # noqa: RUF001
    elif when is not None or cmd == "/bugun":
        d = when or today
        data = f"rd:{d}" if chat.branch_id is None else f"bd:{chat.branch_id}:{d}"
        view = await render(db, chat, data, today)
    else:
        view = main_menu(chat, today)
    if view is not None:
        with contextlib.suppress(TelegramError):
            await tg.send(chat_id, view[0], view[1])
    return True


async def handle_callback(db: AsyncSession, tg: Telegram, cb: dict[str, Any]) -> None:
    """A button press: re-check the chat, then replace the message with the new view."""
    msg = cb.get("message") or {}
    chat_id = (msg.get("chat") or {}).get("id")
    message_id = msg.get("message_id")
    cb_id = str(cb.get("id") or "")
    if not isinstance(chat_id, int) or not isinstance(message_id, int):
        return
    chat = await _admin_chat(db, chat_id)
    view = None
    if chat is not None:
        view = await render(db, chat, str(cb.get("data") or ""), await _today(db))
    with contextlib.suppress(TelegramError):
        await tg.answer(cb_id, None if view else "Ruxsat yoʻq yoki eskirgan tugma")  # noqa: RUF001
    if view is not None:
        with contextlib.suppress(TelegramError):
            await tg.edit(chat_id, message_id, view[0], view[1])
