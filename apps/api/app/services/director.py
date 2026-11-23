"""The director's report (Phase 22): every branch ranked by attendance, and who is missing.

Sent to CENTRE chats only (branch_id NULL) subscribed to "director", at
notify.daily_summary_time:
* every day          - today's ranking + the names of those not here yet, per branch
* every Monday       - last week's ranking + the people absent / late most often
* the 1st of a month - last month's ranking, the same lists

Rating = share of scheduled working days on which the employee came (any first IN), higher is
better; ties: fewer late arrivals. Employees on leave, suspended or terminated are left out;
weekends and unscheduled days do not count. Names and numbers only, no photos.
"""

from __future__ import annotations

import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.attendance import engine
from app.attendance import service as att
from app.models import Branch, Employee
from app.models.enums import EmployeeStatus

_OFF = engine.OFF_STATUSES
MONTHS = [
    "yanvar", "fevral", "mart", "aprel", "may", "iyun",
    "iyul", "avgust", "sentabr", "oktabr", "noyabr", "dekabr",
]  # fmt: skip
NAMES_PER_BRANCH = 15
TOP_PEOPLE = 10


@dataclass
class BranchStat:
    id: uuid.UUID
    name: str
    scheduled: int = 0  # employee-days that were working days
    came: int = 0
    late: int = 0
    missing: list[Employee] = field(default_factory=list)  # one entry per missing day

    @property
    def rate(self) -> float:
        return 100.0 * self.came / self.scheduled if self.scheduled else 0.0

    @property
    def late_rate(self) -> float:
        return 100.0 * self.late / self.came if self.came else 0.0


@dataclass
class Period:
    branches: list[BranchStat]  # ranked, best first; branches without working days left out
    absent_days: Counter[uuid.UUID]
    late_days: Counter[uuid.UUID]
    people: dict[uuid.UUID, Employee]
    branch_of: dict[uuid.UUID, str]


async def collect(
    db: AsyncSession, start: date, end: date, branch_id: uuid.UUID | None = None
) -> Period:
    """Attendance of every active employee of every open branch (or one branch) in
    [start, end]."""
    stmt = select(Branch).where(Branch.is_active.is_(True))
    if branch_id is not None:
        stmt = select(Branch).where(Branch.id == branch_id)
    branches = {b.id: b.name for b in (await db.execute(stmt)).scalars()}
    emps = list(
        (
            await db.execute(
                select(Employee).where(
                    Employee.deleted_at.is_(None),
                    Employee.status == EmployeeStatus.ACTIVE,
                    Employee.branch_id.in_(list(branches)),
                )
            )
        ).scalars()
    )
    stats = {bid: BranchStat(bid, name) for bid, name in branches.items()}
    absent: Counter[uuid.UUID] = Counter()
    late: Counter[uuid.UUID] = Counter()
    for e, r in await att.compute_range(db, emps, start, end):
        if r.status in _OFF:
            continue
        s = stats[e.branch_id]
        s.scheduled += 1
        if r.first_in is not None:
            s.came += 1
            if r.late_minutes > 0:
                s.late += 1
                late[e.id] += 1
        else:  # ABSENT, or (today) PENDING = not here yet
            s.missing.append(e)
            absent[e.id] += 1
    ranked = sorted(
        (s for s in stats.values() if s.scheduled),
        key=lambda s: (-s.rate, s.late_rate, s.name),
    )
    return Period(
        ranked,
        absent,
        late,
        {e.id: e for e in emps},
        {e.id: branches[e.branch_id] for e in emps},
    )


# --- text ---------------------------------------------------------------------------------------


def _esc(s: object) -> str:
    from app.services.notifications import esc

    return esc(s)


def _name(e: Employee) -> str:
    return _esc(" ".join(p for p in (e.last_name, e.first_name) if p))


def _mark(rate: float) -> str:
    return "🟢" if rate >= 95 else "🟡" if rate >= 80 else "🔴"


def _place(i: int) -> str:
    return ("🥇", "🥈", "🥉")[i] if i < 3 else f"{i + 1}."


def _ranking(p: Period) -> list[str]:
    lines = []
    for i, s in enumerate(p.branches):
        lines.append(
            f"{_place(i)} {_mark(s.rate)} <b>{_esc(s.name)}</b> — {s.rate:.0f}% "
            f"({s.came}/{s.scheduled}), kechikish: {s.late}"
        )
    return lines


def _totals(p: Period) -> str:
    sched = sum(s.scheduled for s in p.branches)
    came = sum(s.came for s in p.branches)
    late = sum(s.late for s in p.branches)
    rate = 100.0 * came / sched if sched else 0.0
    return f"Jami: <b>{rate:.0f}%</b> ({came}/{sched}), kechikish: {late}"


def daily_text(day: date, p: Period, past: bool = False) -> str | None:
    """One day: ranking + who has not come, per branch (worst branch first). `past`: a finished
    day ("did not come" instead of "not here yet")."""
    if not p.branches:
        return None
    from app.services.notifications import WEEKDAYS

    lines = [
        f"🏆 <b>Direktor hisoboti — {day:%d.%m.%Y}, {WEEKDAYS[day.weekday()]}</b>",
        "Filiallar reytingi (kelganlar ulushi):",
        "",
        *_ranking(p),
        "",
        _totals(p),
    ]
    missing = [s for s in reversed(p.branches) if s.missing]
    if missing:
        lines += ["", "❌ <b>Kelmaganlar</b>" if past else "❌ <b>Hali kelmaganlar</b>"]
        for s in missing:
            names = sorted(_name(e) for e in s.missing)
            shown = ", ".join(names[:NAMES_PER_BRANCH])
            more = len(names) - NAMES_PER_BRANCH
            lines.append(
                f"🏢 <b>{_esc(s.name)}</b> ({len(names)}): {shown}"
                + (f" va yana {more} kishi" if more > 0 else "")
            )
    else:
        lines += ["", "👏 Barcha filiallarda hamma keldi."]
    return "\n".join(lines)


def _top(p: Period, counts: Counter[uuid.UUID], unit: str) -> list[str]:
    out = []
    for eid, n in counts.most_common(TOP_PEOPLE):
        e = p.people.get(eid)
        if e is not None:
            out.append(f"  • {_name(e)} ({_esc(p.branch_of[eid])}) — {n} {unit}")
    return out


def period_text(title: str, p: Period, single: bool = False) -> str | None:
    """A week or a month: ranking, totals, people absent / late most often. `single`: one
    branch (no ranking, just its numbers)."""
    if not p.branches:
        return None
    if single:
        s = p.branches[0]
        lines = [
            f"📈 <b>{_esc(s.name)} — {_esc(title)}</b>",
            "",
            f"{_mark(s.rate)} Davomat: <b>{s.rate:.0f}%</b> ({s.came}/{s.scheduled}), "
            f"kechikish: {s.late}",
        ]
    else:
        lines = [f"📈 <b>Direktor hisoboti — {_esc(title)}</b>", "Filiallar reytingi:", ""]
        lines += [*_ranking(p), "", _totals(p)]
    worst = p.branches[-1]
    if not single and len(p.branches) > 1 and worst.rate < 95:
        lines += ["", f"⚠️ Eng past davomat: <b>{_esc(worst.name)}</b> ({worst.rate:.0f}%)"]
    absent = _top(p, p.absent_days, "kun kelmagan")
    if absent:
        lines += ["", "❌ <b>Eng koʻp kelmaganlar</b>", *absent]
    late = _top(p, p.late_days, "marta kechikkan")
    if late:
        lines += ["", "⏰ <b>Eng koʻp kechikkanlar</b>", *late]
    return "\n".join(lines)


def last_week(today: date) -> tuple[date, date, str]:
    end = today - timedelta(days=today.weekday() + 1)  # last Sunday
    start = end - timedelta(days=6)
    return start, end, f"hafta {start:%d.%m} – {end:%d.%m.%Y}"


def last_month(today: date) -> tuple[date, date, str]:
    end = today.replace(day=1) - timedelta(days=1)
    start = end.replace(day=1)
    return start, end, f"{MONTHS[start.month - 1]} {start.year}"
