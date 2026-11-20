"""Telegram notifications: daily summary, late arrivals, review reminders, camera alerts,
linking chats, send-once guarantee, and the settings/API surface."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.attendance import service as att
from app.models import AttendanceEvent, Employee, SystemSetting, TelegramRecipient
from app.models.enums import EventSource, PunchDirection, RecognitionDecision, UserRole
from app.services import notifications as notif
from app.services.telegram import TelegramError, telegram_client
from tests import factories as f
from tests.api_helpers import bearer, make_user, token_for
from tests.conftest import requires_db

pytestmark = requires_db

TZ = ZoneInfo("Asia/Tashkent")
WED = date(2026, 11, 4)


def at(hh: int, mm: int = 0, d: date = WED) -> datetime:
    return datetime.combine(d, time(hh, mm), tzinfo=TZ).astimezone(UTC)


class FakeTelegram:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []
        self.blocked: set[int] = set()

        self.keyboards: list[Any] = []  # keyboard of each sent message
        self.edited: list[tuple[int, int, str, Any]] = []
        self.answers: list[tuple[str, str | None]] = []
        self.commands: list[tuple[str, str]] = []

    async def send(self, chat_id: int, html: str, keyboard: Any = None) -> None:
        if chat_id in self.blocked:
            raise TelegramError("Forbidden: bot was blocked by the user (chat)", blocked=True)
        self.sent.append((chat_id, html))
        self.keyboards.append(keyboard)

    async def edit(self, chat_id: int, message_id: int, html: str, keyboard: Any = None) -> None:
        self.edited.append((chat_id, message_id, html, keyboard))

    async def answer(self, callback_id: str, text: str | None = None) -> None:
        self.answers.append((callback_id, text))

    async def set_commands(self, commands: list[tuple[str, str]]) -> None:
        self.commands = commands

    async def updates(self, offset: int | None, poll_s: int) -> list[dict[str, Any]]:
        return []

    async def me(self) -> dict[str, Any]:
        return {"username": "davomat_test_bot"}


@pytest.fixture
def tg() -> FakeTelegram:
    return FakeTelegram()


@pytest.fixture(autouse=True)
def clock(monkeypatch: pytest.MonkeyPatch) -> dict[str, datetime]:
    now = {"t": at(10, 5)}
    monkeypatch.setattr(att, "now", lambda: now["t"])
    return now


async def _set(session: AsyncSession, key: str, value: Any) -> None:
    row = await session.get(SystemSetting, key)
    assert row is not None
    row.value = value
    await session.flush()


async def _recipient(session: AsyncSession, chat_id: int, subs: list[str]) -> TelegramRecipient:
    r = TelegramRecipient(chat_id=chat_id, title=f"Chat {chat_id}", subscriptions=subs)
    session.add(r)
    await session.flush()
    return r


async def _office(session: AsyncSession) -> dict[str, Any]:
    """Mon-Fri 09:00-18:00. Aliyev on time, Karimov late (09:40), Valiyev not here yet."""
    user = await f.user(session)
    ws = await f.schedule(session, name=f"Ofis {uuid.uuid4().hex[:6]}")
    dept = await f.department(session)
    dept.work_schedule_id = ws.id
    emps = {
        "on_time": await f.employee(session, dept=dept, last_name="Aliyev", first_name="Ali"),
        "late": await f.employee(session, dept=dept, last_name="Karimov", first_name="Bek"),
        "absent": await f.employee(session, dept=dept, last_name="Valiyev", first_name="Sardor"),
    }
    for key, t in (("on_time", at(8, 55)), ("late", at(9, 40))):
        session.add(
            AttendanceEvent(
                event_uid=uuid.uuid4(),
                event_time=t,
                source=EventSource.IMPORT,
                employee_id=emps[key].id,
                direction=PunchDirection.IN,
                work_date=WED,
                counts_for_attendance=True,
                created_by_user_id=user.id,
            )
        )
    await session.flush()
    return emps


async def _enable(session: AsyncSession) -> None:
    await _set(session, "notify.telegram_enabled", True)


async def test_nothing_happens_while_disabled(session: AsyncSession, tg: FakeTelegram) -> None:
    await _recipient(session, 1, ["daily", "late", "reviews", "system"])
    await _office(session)
    assert await notif.run_checks(session, tg, at(12)) == {"enabled": False}
    assert tg.sent == []


async def test_daily_summary_once_after_the_configured_time(
    session: AsyncSession, tg: FakeTelegram, clock: dict[str, datetime]
) -> None:
    await _enable(session)
    await _recipient(session, 7, ["daily"])
    await _office(session)
    clock["t"] = at(9, 50)
    await notif.run_checks(session, tg, at(9, 50))
    assert tg.sent == []  # before 10:00
    clock["t"] = at(10, 5)
    await notif.run_checks(session, tg, at(10, 5))
    assert len(tg.sent) == 1
    chat, text = tg.sent[0]
    assert chat == 7 and "04.11.2026, Chorshanba" in text
    assert "Keldi: <b>2</b> / 3" in text
    assert "Karimov Bek — 09:40 (+40 daq)" in text
    assert "Hali kelmagan: <b>1</b>" in text and "Valiyev Sardor" in text
    assert "Aliyev" not in text.split("Kechikdi")[1]  # on time: not in the late list
    await notif.run_checks(session, tg, at(11))
    assert len(tg.sent) == 1  # once per day


async def test_branches_get_their_own_daily_summary(
    session: AsyncSession, tg: FakeTelegram, clock: dict[str, datetime]
) -> None:
    await _enable(session)
    await _office(session)  # main branch: Karimov late, Valiyev absent
    other = await f.branch(session, name=f"Chilonzor {uuid.uuid4().hex[:6]}")
    ws = await f.schedule(session, name=f"Ofis {uuid.uuid4().hex[:6]}", branch_id=other.id)
    dept = await f.department(session, branch_id=other.id, work_schedule_id=ws.id)
    await f.employee(session, dept=dept, last_name="Toshmatov", first_name="Olim")
    centre = await _recipient(session, 7, ["daily"])
    mine = await _recipient(session, 8, ["daily"])
    mine.branch_id = other.id
    await session.flush()
    assert centre.branch_id is None
    clock["t"] = at(10, 5)
    await notif.run_checks(session, tg, at(10, 5))
    sent = dict(tg.sent)
    assert set(sent) == {7, 8}
    # the centre: one line per branch, no names
    assert "barcha filiallar" in sent[7] and other.name in sent[7] and "Markaz" in sent[7]
    assert "Karimov" not in sent[7]
    # the branch: its own people only
    assert other.name in sent[8] and "Toshmatov Olim" in sent[8]
    assert "Karimov" not in sent[8] and "Valiyev" not in sent[8]


async def test_no_daily_summary_on_a_day_off(
    session: AsyncSession, tg: FakeTelegram, clock: dict[str, datetime]
) -> None:
    await _enable(session)
    await _recipient(session, 7, ["daily"])
    await _office(session)
    sat = at(11, 0, WED + timedelta(days=3))
    clock["t"] = sat
    await notif.run_checks(session, tg, sat)
    assert tg.sent == []


async def test_late_arrival_message_once(
    session: AsyncSession, tg: FakeTelegram, clock: dict[str, datetime]
) -> None:
    await _enable(session)
    await _recipient(session, 8, ["late"])
    await _office(session)
    clock["t"] = at(9, 50)
    await notif.run_checks(session, tg, at(9, 50))
    await notif.run_checks(session, tg, at(9, 55))
    assert [t for _, t in tg.sent] == [
        "⏰ <b>Karimov Bek</b> kechikdi: 09:40 (ish 09:00 da boshlanadi, +40 daq)"
    ]


async def test_late_arrival_older_than_30_minutes_is_not_sent(
    session: AsyncSession, tg: FakeTelegram, clock: dict[str, datetime]
) -> None:
    await _enable(session)
    await _recipient(session, 8, ["late"])
    await _office(session)
    clock["t"] = at(11)  # switched on mid-day: no flood of old arrivals
    await notif.run_checks(session, tg, at(11))
    assert tg.sent == []


async def _review(session: AsyncSession, cam: Any, t: datetime) -> None:
    session.add(
        AttendanceEvent(
            event_uid=uuid.uuid4(),
            event_time=t,
            source=EventSource.CAMERA,
            camera_id=cam.id,
            decision=RecognitionDecision.AMBIGUOUS,
            model_id="sface@2021dec",
            counts_for_attendance=False,
            ignore_reason="UNMATCHED",
            review_status="PENDING",
        )
    )
    await session.flush()


async def test_review_reminders_are_throttled(session: AsyncSession, tg: FakeTelegram) -> None:
    await _enable(session)
    await _recipient(session, 9, ["reviews"])
    cam = await f.camera(session)
    await _review(session, cam, at(10))
    await _review(session, cam, at(10, 1))
    await notif.run_checks(session, tg, at(10, 2))
    assert "<b>2</b> ta yangi" in tg.sent[-1][1] and "jami: 2" in tg.sent[-1][1]
    await _review(session, cam, at(10, 3))
    await notif.run_checks(session, tg, datetime.now(UTC) + timedelta(minutes=5))
    assert len(tg.sent) == 1  # within the 15-minute interval
    await notif.run_checks(session, tg, datetime.now(UTC) + timedelta(minutes=16))
    assert len(tg.sent) == 2
    assert "<b>1</b> ta yangi" in tg.sent[-1][1] and "jami: 3" in tg.sent[-1][1]
    await notif.run_checks(session, tg, datetime.now(UTC) + timedelta(minutes=40))
    assert len(tg.sent) == 2  # nothing new


async def test_camera_outage_alert_and_recovery_once(
    session: AsyncSession, tg: FakeTelegram
) -> None:
    await _enable(session)
    await _recipient(session, 10, ["system"])
    cam = await f.camera(session, name="Kirish", is_enabled=True)
    now = datetime.now(UTC)
    cam.last_seen_at = now - timedelta(minutes=10)
    await session.flush()
    await notif.run_checks(session, tg, now)
    await notif.run_checks(session, tg, now + timedelta(minutes=1))
    downs = [t for _, t in tg.sent if "ishlamayapti" in t]
    assert len(downs) == 1 and "<b>Kirish</b>" in downs[0]
    cam.last_seen_at = now + timedelta(minutes=2)
    await session.flush()
    await notif.run_checks(session, tg, now + timedelta(minutes=2))
    await notif.run_checks(session, tg, now + timedelta(minutes=3))
    ups = [t for _, t in tg.sent if "qayta ishlayapti" in t]
    assert len(ups) == 1


async def test_blocked_chat_is_deactivated(session: AsyncSession, tg: FakeTelegram) -> None:
    r = await _recipient(session, 11, ["system"])
    tg.blocked.add(11)
    assert await notif.deliver(session, tg, [r], "x") == 0
    assert r.is_active is False and "blocked" in (r.last_error or "")


async def test_link_with_one_time_code(session: AsyncSession, tg: FakeTelegram) -> None:
    user = await f.user(session)
    code, _ = notif.link_codes.create("Direktor", user.id)
    upd = {"message": {"text": f"/start {code}", "chat": {"id": 555}, "from": {"username": "dir"}}}
    assert await notif.handle_update(session, tg, upd) == "Direktor"
    row = (
        await session.execute(select(TelegramRecipient).where(TelegramRecipient.chat_id == 555))
    ).scalar_one()
    assert row.title == "Direktor" and row.username == "dir"
    assert row.subscriptions == ["daily", "reviews", "system"]
    assert "Ulandingiz" in tg.sent[-1][1]
    # the code works once; unknown codes link nothing
    assert await notif.handle_update(session, tg, upd) is None
    stranger = {"message": {"text": "/start nope", "chat": {"id": 777}}}
    assert await notif.handle_update(session, tg, stranger) is None
    assert "administrator bergan havola" in tg.sent[-1][1]
    count = (await session.execute(select(TelegramRecipient))).scalars().all()
    assert len(count) == 1


# --- API ---------------------------------------------------------------------------------------

BASE = "/api/v1/notifications/telegram"


@pytest.fixture
def fake_bot(app: FastAPI, tg: FakeTelegram) -> FakeTelegram:
    app.dependency_overrides[telegram_client] = lambda: tg
    return tg


async def test_api_status_link_patch_test(
    api: httpx.AsyncClient, session: AsyncSession, fake_bot: FakeTelegram
) -> None:
    h = bearer(await token_for(api, await make_user(session, role=UserRole.SUPER_ADMIN)))
    st = (await api.get(BASE, headers=h)).json()
    assert st["configured"] is True and st["bot_username"] == "davomat_test_bot"
    assert st["enabled"] is False and st["daily_summary_time"] == "10:00"
    assert "token" not in str(st).lower()

    link = (await api.post(f"{BASE}/link", json={"title": "Direktor"}, headers=h)).json()
    assert link["link"] == f"https://t.me/davomat_test_bot?start={link['code']}"

    rid = (await _recipient(session, 42, ["daily"])).id  # objects expire after a 422 rollback
    bad = await api.patch(f"{BASE}/recipients/{rid}", json={"subscriptions": ["spam"]}, headers=h)
    assert bad.status_code == 422
    ok = await api.patch(
        f"{BASE}/recipients/{rid}", json={"subscriptions": ["system", "late"]}, headers=h
    )
    assert ok.status_code == 200 and ok.json()["subscriptions"] == ["late", "system"]

    sent = (await api.post(f"{BASE}/test", json={"recipient_id": str(rid)}, headers=h)).json()
    assert sent == {"recipients": 1, "delivered": 1, "errors": []}
    assert "Sinov xabari" in fake_bot.sent[-1][1]

    assert (await api.delete(f"{BASE}/recipients/{rid}", headers=h)).status_code == 204


async def test_api_settings_and_permissions(
    api: httpx.AsyncClient, session: AsyncSession, fake_bot: FakeTelegram
) -> None:
    sa = bearer(await token_for(api, await make_user(session, role=UserRole.SUPER_ADMIN)))
    bad = await api.patch(
        "/api/v1/settings/notify.daily_summary_time", json={"value": "25:00"}, headers=sa
    )
    assert bad.status_code == 422
    good = await api.patch(
        "/api/v1/settings/notify.daily_summary_time", json={"value": "09:30"}, headers=sa
    )
    assert good.status_code == 200
    admin = bearer(await token_for(api, await make_user(session, role=UserRole.ADMIN)))
    assert (await api.get(BASE, headers=admin)).status_code == 200  # notifications:read
    # a branch administrator links its branch's chats ...
    assert (await api.post(f"{BASE}/link", json={"title": "x"}, headers=admin)).status_code == 200
    # ... but the bot's global switches stay with the super administrator
    r = await api.patch(
        "/api/v1/settings/notify.telegram_enabled", json={"value": True}, headers=admin
    )
    assert r.status_code == 403
    viewer = bearer(await token_for(api, await make_user(session, role=UserRole.VIEWER)))
    assert (await api.get(BASE, headers=viewer)).status_code == 403


async def test_api_without_token(
    api: httpx.AsyncClient, session: AsyncSession, app: FastAPI
) -> None:
    app.dependency_overrides[telegram_client] = lambda: None
    h = bearer(await token_for(api, await make_user(session, role=UserRole.SUPER_ADMIN)))
    assert (await api.get(BASE, headers=h)).json()["configured"] is False
    r = await api.post(f"{BASE}/link", json={"title": "x"}, headers=h)
    assert r.status_code == 409 and r.json()["code"] == "TELEGRAM_NOT_CONFIGURED"


# --- employee's own chat -------------------------------------------------------------------------


async def _employee_chat(
    session: AsyncSession, chat_id: int, emp: Any, subs: list[str] | None = None
) -> TelegramRecipient:
    r = TelegramRecipient(
        chat_id=chat_id,
        title=emp.first_name,
        employee_id=emp.id,
        subscriptions=subs or ["self_punch", "self_late"],
    )
    session.add(r)
    await session.flush()
    return r


async def test_employee_gets_only_own_punches(
    session: AsyncSession, tg: FakeTelegram, clock: dict[str, datetime]
) -> None:
    await _enable(session)
    emps = await _office(session)
    await _employee_chat(session, 101, emps["late"])
    await _employee_chat(session, 102, emps["on_time"])
    await _employee_chat(session, 103, emps["absent"])
    clock["t"] = at(9, 41)
    await notif.run_checks(session, tg, at(9, 41))
    await notif.run_checks(session, tg, at(9, 42))  # no repeats
    assert tg.sent == [
        (
            101,
            "✅ Bek, ishga kelishingiz qayd etildi: <b>09:40</b>\n"
            "⏰ Ish 09:00 da boshlanadi — <b>40 daqiqa</b> kechikish.",
        )
    ]  # Aliyev's 08:55 punch is older than 30 min; Valiyev has no punch
    user = await f.user(session)
    session.add(
        AttendanceEvent(
            event_uid=uuid.uuid4(),
            event_time=at(9, 45),
            source=EventSource.IMPORT,
            employee_id=emps["on_time"].id,
            direction=PunchDirection.OUT,
            work_date=WED,
            counts_for_attendance=True,
            created_by_user_id=user.id,
        )
    )
    await session.flush()
    clock["t"] = at(9, 46)
    await notif.run_checks(session, tg, at(9, 46))
    assert tg.sent[-1] == (102, "👋 Ali, ishdan ketishingiz qayd etildi: <b>09:45</b>")
    assert len(tg.sent) == 2


async def test_employee_late_only_subscription(
    session: AsyncSession, tg: FakeTelegram, clock: dict[str, datetime]
) -> None:
    await _enable(session)
    emps = await _office(session)
    await _employee_chat(session, 101, emps["late"], ["self_late"])
    clock["t"] = at(9, 50)
    await notif.run_checks(session, tg, at(9, 50))
    await notif.run_checks(session, tg, at(9, 55))
    assert [t for _, t in tg.sent] == [
        "⏰ Bek, bugun ishga 09:40 da keldingiz. Ish 09:00 da boshlanadi — "
        "<b>40 daqiqa</b> kechikish qayd etildi."
    ]


async def test_employee_link_moves_and_merges(session: AsyncSession, tg: FakeTelegram) -> None:
    user = await f.user(session)
    emp = await f.employee(session, first_name="Bek")
    code, _ = notif.link_codes.create("Bek Karimov", user.id, employee_id=emp.id)
    upd = {"message": {"text": f"/start {code}", "chat": {"id": 201}}}
    assert await notif.handle_update(session, tg, upd) == "Bek Karimov"
    assert "Salom" in tg.sent[-1][1]
    # a new link from another phone moves the employee to the new chat
    code2, _ = notif.link_codes.create("Bek Karimov", user.id, employee_id=emp.id)
    await notif.handle_update(
        session, tg, {"message": {"text": f"/start {code2}", "chat": {"id": 202}}}
    )
    rows = (await session.execute(select(TelegramRecipient))).scalars().all()
    assert [(r.chat_id, r.employee_id) for r in rows] == [(202, emp.id)]
    # an administrator's chat that is also an employee keeps both roles
    admin_chat = await _recipient(session, 300, ["daily"])
    code3, _ = notif.link_codes.create("Bek Karimov", user.id, employee_id=emp.id)
    await notif.handle_update(
        session, tg, {"message": {"text": f"/start {code3}", "chat": {"id": 300}}}
    )
    await session.refresh(admin_chat)
    assert admin_chat.employee_id == emp.id
    assert admin_chat.subscriptions == ["daily", "self_punch", "self_late"]
    assert (await session.execute(select(TelegramRecipient))).scalars().all() == [admin_chat]


async def test_employee_telegram_api(
    api: httpx.AsyncClient, session: AsyncSession, fake_bot: FakeTelegram
) -> None:
    admin = bearer(await token_for(api, await make_user(session, role=UserRole.ADMIN)))
    emp_id = (await f.employee(session, first_name="Bek", last_name="Karimov")).id
    url = f"/api/v1/employees/{emp_id}/telegram"
    st = (await api.get(url, headers=admin)).json()
    assert st["linked"] is False and st["configured"] is True
    link = (await api.post(f"{url}/link", headers=admin)).json()
    assert link["link"].startswith("https://t.me/davomat_test_bot?start=")
    await _employee_chat(session, 401, await session.get(Employee, emp_id))
    st = (await api.get(url, headers=admin)).json()
    assert st["linked"] is True and st["subscriptions"] == ["self_punch", "self_late"]
    bad = await api.patch(url, json={"subscriptions": ["daily"]}, headers=admin)
    assert bad.status_code == 422  # employees never get admin messages from here
    ok = await api.patch(url, json={"subscriptions": ["self_late"]}, headers=admin)
    assert ok.json()["subscriptions"] == ["self_late"]
    assert (await api.delete(url, headers=admin)).status_code == 204
    assert (await api.get(url, headers=admin)).json()["linked"] is False
    viewer = bearer(await token_for(api, await make_user(session, role=UserRole.VIEWER)))
    assert (await api.post(f"{url}/link", headers=viewer)).status_code == 403


async def test_camera_health_warning_once_a_day(session: AsyncSession, tg: FakeTelegram) -> None:
    await _enable(session)
    await _recipient(session, 12, ["system"])
    cam = await f.camera(session, name="103", is_enabled=True)
    now = datetime.now(UTC)
    cam.last_seen_at = now
    cam.runtime = {
        "health": {
            "verdict": "warn",
            "issues": [
                {"code": "frame_loss", "severity": "warn", "count": 40},
                {"code": "h265", "severity": "warn"},
                {"code": "fps_high", "severity": "tip", "fps": 21},
            ],
        }
    }
    await session.flush()
    await notif.run_checks(session, tg, now)
    await notif.run_checks(session, tg, now + timedelta(minutes=1))
    msgs = [t for _, t in tg.sent if "muammo" in t]
    assert len(msgs) == 1
    assert "<b>103</b>" in msgs[0] and "40 ta / 5 daq" in msgs[0] and "H.264" in msgs[0]
    assert "kadr/s" not in msgs[0]  # tips are not alerts


# --- director's report (Phase 22) ---------------------------------------------------------------


async def _second_branch(session: AsyncSession) -> Any:
    """Branch "Yunusobod": Toshmatov came on time, Rahimov did not come."""
    other = await f.branch(session, name=f"Yunusobod {uuid.uuid4().hex[:6]}")
    ws = await f.schedule(session, name=f"Ofis {uuid.uuid4().hex[:6]}", branch_id=other.id)
    dept = await f.department(session, branch_id=other.id, work_schedule_id=ws.id)
    here = await f.employee(session, dept=dept, last_name="Toshmatov", first_name="Olim")
    await f.employee(session, dept=dept, last_name="Rahimov", first_name="Jasur")
    user = await f.user(session)
    session.add(
        AttendanceEvent(
            event_uid=uuid.uuid4(),
            event_time=at(8, 50),
            source=EventSource.IMPORT,
            employee_id=here.id,
            direction=PunchDirection.IN,
            work_date=WED,
            counts_for_attendance=True,
            created_by_user_id=user.id,
        )
    )
    await session.flush()
    return other


async def test_director_report_ranks_branches_daily(
    session: AsyncSession, tg: FakeTelegram, clock: dict[str, datetime]
) -> None:
    await _enable(session)
    await _office(session)  # Markaz: 2 of 3 came (Karimov late), Valiyev missing
    other = await _second_branch(session)  # 1 of 2 came, Rahimov missing
    director = await _recipient(session, 5, ["director"])
    branch_chat = await _recipient(session, 6, ["director"])
    branch_chat.branch_id = other.id  # a branch chat never gets the all-branch report
    await session.flush()
    clock["t"] = at(10, 5)
    await notif.run_checks(session, tg, at(10, 5))
    assert [c for c, _ in tg.sent] == [director.chat_id]
    text = tg.sent[0][1]
    assert "Direktor hisoboti — 04.11.2026" in text
    # Markaz 67% ranks above Yunusobod 50%
    assert text.index("Markaz") < text.index(other.name)
    assert "67% (2/3)" in text and "50% (1/2)" in text and "🥇" in text
    assert "Jami: <b>60%</b> (3/5)" in text
    assert "Valiyev Sardor" in text and "Rahimov Jasur" in text
    assert "Toshmatov" not in text.split("Hali kelmaganlar")[1]
    await notif.run_checks(session, tg, at(11))
    assert len(tg.sent) == 1  # once a day


async def test_director_weekly_report_on_monday(
    session: AsyncSession, tg: FakeTelegram, clock: dict[str, datetime]
) -> None:
    await _enable(session)
    await _office(session)
    await _second_branch(session)
    await _recipient(session, 5, ["director"])
    monday = date(2026, 11, 9)
    clock["t"] = at(10, 5, monday)
    await notif.run_checks(session, tg, at(10, 5, monday))
    texts = [t for _, t in tg.sent]
    assert len(texts) == 2  # today's + last week's
    week = next(t for t in texts if "hafta 02.11 – 08.11.2026" in t)
    assert "Eng koʻp kelmaganlar" in week and "Valiyev Sardor" in week
    assert "Eng koʻp kechikkanlar" in week and "Karimov Bek" in week


async def test_director_report_is_centre_only(
    api: httpx.AsyncClient, session: AsyncSession, fake_bot: FakeTelegram
) -> None:
    admin = await make_user(session, role=UserRole.ADMIN)
    r = await _recipient(session, 9, ["daily"])
    r.branch_id = admin.obj.branch_id
    rid = r.id
    await session.flush()
    h = bearer(await token_for(api, admin))
    res = await api.patch(
        f"{BASE}/recipients/{rid}", json={"subscriptions": ["daily", "director"]}, headers=h
    )
    assert res.status_code == 422
    assert (await api.get(f"{BASE}/director-preview", headers=h)).status_code == 403
    sa = bearer(await token_for(api, await make_user(session, role=UserRole.SUPER_ADMIN)))
    for period in ("day", "week", "month"):
        res = await api.get(f"{BASE}/director-preview", params={"period": period}, headers=sa)
        assert res.status_code == 200, res.text


# --- attendance menu inside the bot (Phase 23) ---------------------------------------------------


def _msg(chat_id: int, text: str) -> dict[str, Any]:
    return {"update_id": 1, "message": {"chat": {"id": chat_id}, "text": text}}


def _press(chat_id: int, data: str) -> dict[str, Any]:
    return {
        "update_id": 2,
        "callback_query": {
            "id": "cb1",
            "data": data,
            "message": {"chat": {"id": chat_id}, "message_id": 77},
        },
    }


def _buttons(keyboard: Any) -> list[str]:
    return [d for row in keyboard for _, d in row]


async def test_menu_is_for_linked_administrators_only(
    session: AsyncSession, tg: FakeTelegram
) -> None:
    await notif.handle_update(session, tg, _msg(404, "/menu"))
    assert "administrator bergan havoladan" in tg.sent[-1][1]  # a stranger learns nothing
    emp = await f.employee(session)
    session.add(
        TelegramRecipient(chat_id=405, title="X", employee_id=emp.id, subscriptions=["self_punch"])
    )
    await session.flush()
    await notif.handle_update(session, tg, _msg(405, "/menu"))
    assert "faqat administratorlar" in tg.sent[-1][1]
    await notif.handle_update(session, tg, _press(405, f"rd:{WED}"))
    assert tg.edited == [] and tg.answers[-1][1]  # refused


async def test_centre_menu_rankings_and_branches(session: AsyncSession, tg: FakeTelegram) -> None:
    await _office(session)
    other = await _second_branch(session)
    await _recipient(session, 5, ["daily"])
    await notif.handle_update(session, tg, _msg(5, "/menu"))
    assert "Davomat menyusi" in tg.sent[-1][1]
    buttons = _buttons(tg.keyboards[-1])
    assert "bl" in buttons and "dp" in buttons and any(b.startswith("rd:") for b in buttons)

    await notif.handle_update(session, tg, _press(5, f"rd:{WED}"))
    _, message_id, text, kb = tg.edited[-1]
    assert message_id == 77 and "04.11.2026" in text
    assert text.index("Markaz") < text.index(other.name)  # ranking
    assert "Kelmaganlar" in text and "Rahimov Jasur" in text  # a past day
    assert f"rd:{WED - timedelta(days=1)}" in _buttons(kb)  # previous day

    await notif.handle_update(session, tg, _press(5, "bl"))
    assert f"bd:{other.id}:" in " ".join(_buttons(tg.edited[-1][3]))
    await notif.handle_update(session, tg, _press(5, f"bd:{other.id}:{WED}"))
    text = tg.edited[-1][2]
    assert other.name in text and "Rahimov Jasur" in text and "Karimov" not in text

    await notif.handle_update(session, tg, _press(5, f"rw:{WED}"))
    assert "hafta 02.11 – 08.11.2026" in tg.edited[-1][2]
    await notif.handle_update(session, tg, _press(5, "rm:2026-11"))
    assert "noyabr 2026" in tg.edited[-1][2]
    # a typed date opens that day
    await notif.handle_update(session, tg, _msg(5, "04.11.2026"))
    assert "04.11.2026" in tg.sent[-1][1] and "Markaz" in tg.sent[-1][1]


async def test_branch_chat_sees_only_its_branch(session: AsyncSession, tg: FakeTelegram) -> None:
    await _office(session)
    other = await _second_branch(session)
    mine = await _recipient(session, 6, ["daily"])
    mine.branch_id = other.id
    await session.flush()
    main = (await session.execute(select(Employee.branch_id).limit(1))).scalar_one()
    await notif.handle_update(session, tg, _msg(6, "/menu"))
    buttons = _buttons(tg.keyboards[-1])
    assert "bl" not in buttons and not any(b.startswith("rd:") for b in buttons)
    for forged in ("bl", f"rd:{WED}", f"bd:{main}:{WED}", "rm:2026-11"):
        await notif.handle_update(session, tg, _press(6, forged))
        assert tg.answers[-1][1], forged  # refused
    assert tg.edited == []
    await notif.handle_update(session, tg, _press(6, f"bd:{other.id}:{WED}"))
    text = tg.edited[-1][2]
    assert "Toshmatov Olim" not in text.split("Kelmagan")[-1] and "Karimov" not in text
    await notif.handle_update(session, tg, _press(6, f"bm:{other.id}:2026-11"))
    assert other.name in tg.edited[-1][2] and "Davomat:" in tg.edited[-1][2]
