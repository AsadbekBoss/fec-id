"""Unit tests for attendance rules (pure functions, no database)."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.attendance import engine as e
from app.attendance.engine import CameraMode, DayStatus, Direction, IgnoreReason, Punch

TZ = ZoneInfo("Asia/Tashkent")  # UTC+5, no DST
MON = date(2026, 11, 2)  # Monday
SAT = date(2026, 11, 7)


def L(d: date, hh: int, mm: int = 0, tz: ZoneInfo = TZ) -> datetime:
    return datetime.combine(d, time(hh, mm), tzinfo=tz)


def sched(**kw: object) -> e.Schedule:
    base: dict[str, object] = {
        "id": "s1",
        "name": "5/2 09-18",
        "tz": TZ,
        "start_time": time(9),
        "end_time": time(18),
        "working_days": frozenset({1, 2, 3, 4, 5}),
    }
    return e.Schedule(**(base | kw))  # type: ignore[arg-type]


NIGHT = sched(name="night", start_time=time(22), end_time=time(6))
IN, OUT = Direction.IN, Direction.OUT


def P(t: datetime, d: Direction, manual: bool = False) -> Punch:
    return Punch(t, d, manual)


def day(
    punches: list[Punch], s: e.Schedule | None = None, d: date = MON, now: datetime | None = None
) -> e.DayResult:
    return e.compute_day(
        d, punches, s if s is not None else sched(), TZ, now or L(d + timedelta(days=2), 12)
    )


# --- work date assignment ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("t", "schedule", "expected"),
    [
        (L(MON, 8, 30), sched(), MON),
        (L(MON, 23, 30), sched(), MON),  # late window (18:00 + 6h) still Monday
        (L(MON, 5, 30), sched(), MON),  # outside every window -> local calendar date
        (L(MON + timedelta(days=1), 5, 58), NIGHT, MON),  # night shift OUT belongs to Monday
        (L(MON, 21, 30), NIGHT, MON),
        (L(MON + timedelta(days=1), 20, 0), NIGHT, MON + timedelta(days=1)),
        (
            datetime(2026, 11, 2, 20, 30, tzinfo=UTC),
            sched(),
            MON + timedelta(days=1),
        ),  # 01:30 local
    ],
)
def test_assign_work_date(t: datetime, schedule: e.Schedule, expected: date) -> None:
    assert e.assign_work_date(t, schedule, TZ) == expected


def test_assign_work_date_without_schedule_uses_default_tz() -> None:
    t = datetime(2026, 11, 2, 21, 0, tzinfo=UTC)  # 02:00 Tuesday in Tashkent
    assert e.assign_work_date(t, None, TZ) == MON + timedelta(days=1)


def test_naive_timestamps_rejected() -> None:
    with pytest.raises(ValueError):
        e.assign_work_date(datetime(2026, 11, 2, 9, 0), sched(), TZ)
    with pytest.raises(ValueError):
        Punch(datetime(2026, 11, 2, 9, 0), IN)


# --- camera classification --------------------------------------------------------------------

MIN_PRESENCE = timedelta(minutes=30)


def classify(mode: CameraMode, t: datetime, counted: list[Punch]):  # type: ignore[no-untyped-def]
    return e.classify_camera_punch(mode, t, counted, MIN_PRESENCE)


def test_both_camera_alternates() -> None:
    assert classify(CameraMode.BOTH, L(MON, 9), []) == (IN, None)
    assert classify(CameraMode.BOTH, L(MON, 18), [P(L(MON, 9), IN)]) == (OUT, None)
    assert classify(CameraMode.BOTH, L(MON, 19), [P(L(MON, 9), IN), P(L(MON, 18), OUT)]) == (
        IN,
        None,
    )


def test_repeated_detection_is_not_departure() -> None:
    """Business rule 2: seen again shortly after arriving != leaving."""
    assert classify(CameraMode.BOTH, L(MON, 9, 10), [P(L(MON, 9), IN)]) == (
        None,
        IgnoreReason.REPEATED_DETECTION,
    )
    assert (
        classify(CameraMode.BOTH, L(MON, 9, 29), [P(L(MON, 9), IN)])[1]
        is IgnoreReason.REPEATED_DETECTION
    )
    assert classify(CameraMode.BOTH, L(MON, 9, 30), [P(L(MON, 9), IN)]) == (OUT, None)


def test_fixed_cameras_enforce_sequence() -> None:
    inside = [P(L(MON, 9), IN)]
    assert classify(CameraMode.IN, L(MON, 10), inside) == (None, IgnoreReason.DUPLICATE_IN)
    assert classify(CameraMode.OUT, L(MON, 8), []) == (None, IgnoreReason.OUT_WITHOUT_IN)
    assert classify(CameraMode.OUT, L(MON, 9, 5), inside) == (
        OUT,
        None,
    )  # fixed OUT: no min presence
    assert classify(CameraMode.NONE, L(MON, 9), []) == (None, IgnoreReason.MONITOR_ONLY)


def test_late_arriving_events_must_fit_sequence() -> None:
    full = [P(L(MON, 9), IN), P(L(MON, 18), OUT)]
    assert classify(CameraMode.OUT, L(MON, 13), full) == (None, IgnoreReason.OUT_OF_SEQUENCE)
    assert classify(CameraMode.IN, L(MON, 8), full) == (None, IgnoreReason.OUT_OF_SEQUENCE)
    assert classify(CameraMode.IN, L(MON, 19), full) == (IN, None)
    assert classify(CameraMode.IN, L(MON, 9), full) == (
        None,
        IgnoreReason.DEBOUNCED,
    )  # same instant


# --- sequence validation ----------------------------------------------------------------------


def test_validate_sequence_accepts_valid() -> None:
    e.validate_sequence([])
    e.validate_sequence([P(L(MON, 9), IN)])
    e.validate_sequence([P(L(MON, 9), IN), P(L(MON, 13), OUT), P(L(MON, 14), IN)])


@pytest.mark.parametrize(
    "punches",
    [
        [P(L(MON, 9), OUT)],
        [P(L(MON, 9), IN), P(L(MON, 10), IN)],
        [P(L(MON, 9), IN), P(L(MON, 13), OUT), P(L(MON, 14), OUT)],
        [P(L(MON, 9), IN), P(L(MON, 9), OUT)],
        [P(L(MON, 10), IN), P(L(MON, 9), OUT)],
    ],
    ids=["starts-with-out", "double-in", "double-out", "same-time", "decreasing"],
)
def test_validate_sequence_rejects(punches: list[Punch]) -> None:
    with pytest.raises(e.SequenceError):
        e.validate_sequence(punches)


# --- daily computation ------------------------------------------------------------------------


def test_on_time_full_day() -> None:
    r = day([P(L(MON, 8, 58), IN), P(L(MON, 18, 5), OUT)])
    assert r.status is DayStatus.PRESENT
    assert r.first_in == L(MON, 8, 58) and r.last_out == L(MON, 18, 5)
    assert r.worked_minutes == 9 * 60 + 7 - 60  # single pair >= 6h: 60 min break deducted
    assert (r.late_minutes, r.early_leave_minutes, r.overtime_minutes) == (0, 0, 0)
    assert r.scheduled_minutes == 8 * 60
    assert r.scheduled_start == L(MON, 9) and r.scheduled_end == L(MON, 18)


@pytest.mark.parametrize(
    ("arrive", "late", "status"),
    [
        ((9, 0), 0, DayStatus.PRESENT),
        ((9, 5), 0, DayStatus.PRESENT),  # within 5 min grace
        ((9, 6), 6, DayStatus.LATE),  # beyond grace: counted from shift start
        ((10, 30), 90, DayStatus.LATE),
    ],
)
def test_late_arrival(arrive: tuple[int, int], late: int, status: DayStatus) -> None:
    r = day([P(L(MON, *arrive), IN), P(L(MON, 18), OUT)])
    assert r.late_minutes == late and r.status is status


@pytest.mark.parametrize(("leave", "early"), [((18, 0), 0), ((17, 59), 1), ((16, 30), 90)])
def test_early_departure(leave: tuple[int, int], early: int) -> None:
    assert day([P(L(MON, 9), IN), P(L(MON, *leave), OUT)]).early_leave_minutes == early


def test_grace_out() -> None:
    r = day([P(L(MON, 9), IN), P(L(MON, 17, 55), OUT)], sched(grace_out_minutes=10))
    assert r.early_leave_minutes == 0


def test_explicit_break_punches_not_double_deducted() -> None:
    r = day([P(L(MON, 9), IN), P(L(MON, 13), OUT), P(L(MON, 14), IN), P(L(MON, 18), OUT)])
    assert r.worked_minutes == 8 * 60 and r.status is DayStatus.PRESENT


def test_short_single_pair_no_break_deduction() -> None:
    assert day([P(L(MON, 9), IN), P(L(MON, 12), OUT)]).worked_minutes == 180


def test_absence_only_after_day_closes() -> None:
    assert day([]).status is DayStatus.ABSENT
    pending = day([], now=L(MON, 10))
    assert pending.status is DayStatus.PENDING
    # window ends 18:00 + 6h = 24:00 local
    assert day([], now=L(MON, 23, 59)).status is DayStatus.PENDING
    assert day([], now=L(MON + timedelta(days=1), 0, 1)).status is DayStatus.ABSENT


def test_incomplete_vs_in_progress() -> None:
    open_in = [P(L(MON, 9), IN)]
    live = day(open_in, now=L(MON, 12))
    assert live.status is DayStatus.PRESENT and live.in_progress and live.last_out is None
    assert live.early_leave_minutes == 0 and live.worked_minutes == 0
    closed = day(open_in)
    assert closed.status is DayStatus.INCOMPLETE and not closed.in_progress


def test_weekend_work_and_rest() -> None:
    worked = day([P(L(SAT, 10), IN), P(L(SAT, 14), OUT)], d=SAT)
    assert worked.status is DayStatus.WEEKEND and worked.worked_minutes == 240
    assert worked.late_minutes == 0 and worked.scheduled_minutes == 0
    assert day([], d=SAT).status is DayStatus.WEEKEND


def test_overtime() -> None:
    punches = [P(L(MON, 8), IN), P(L(MON, 20), OUT)]  # 12h - 1h break = 660
    assert day(punches, sched(overtime_enabled=True)).overtime_minutes == 660 - 480
    assert day(punches).overtime_minutes == 0
    weekend = day([P(L(SAT, 10), IN), P(L(SAT, 12), OUT)], sched(overtime_enabled=True), d=SAT)
    assert weekend.overtime_minutes == 120


def test_night_shift() -> None:
    r = day([P(L(MON, 21, 55), IN), P(L(MON + timedelta(days=1), 6, 2), OUT)], NIGHT)
    assert r.status is DayStatus.PRESENT
    assert r.worked_minutes == 8 * 60 + 7 - 60
    assert r.scheduled_end == L(MON + timedelta(days=1), 6)


def test_no_schedule() -> None:
    r = e.compute_day(MON, [P(L(MON, 9), IN), P(L(MON, 17), OUT)], None, TZ, L(MON, 20))
    assert r.status is DayStatus.NOT_SCHEDULED and r.worked_minutes == 480  # no break rule
    assert e.compute_day(MON, [], None, TZ, L(MON, 12)).status is DayStatus.PENDING


def test_manual_flag() -> None:
    assert day([P(L(MON, 9), IN, manual=True), P(L(MON, 18), OUT)]).has_manual_events
    assert not day([P(L(MON, 9), IN), P(L(MON, 18), OUT)]).has_manual_events


def test_dst_night_shift_counts_real_elapsed_time() -> None:
    """Berlin springs forward on 2026-03-29 (02:00 -> 03:00): the night is one hour shorter."""
    berlin = ZoneInfo("Europe/Berlin")
    s = sched(
        tz=berlin,
        start_time=time(22),
        end_time=time(6),
        working_days=frozenset(range(1, 8)),
        break_minutes=0,
    )
    d = date(2026, 3, 28)
    r = e.compute_day(
        d,
        [P(L(d, 22, tz=berlin), IN), P(L(d + timedelta(days=1), 6, tz=berlin), OUT)],
        s,
        berlin,
        L(d + timedelta(days=3), 12, tz=berlin),
    )
    assert r.worked_minutes == 7 * 60
    assert r.early_leave_minutes == 0 and r.late_minutes == 0


def test_mixed_timezones_compare_correctly() -> None:
    """Punches arrive in UTC from the DB, schedules are local: same instant, same result."""
    utc_in = datetime(2026, 11, 2, 4, 0, tzinfo=UTC)  # 09:00 Tashkent
    utc_out = datetime(2026, 11, 2, 13, 0, tzinfo=UTC)  # 18:00
    r = day([P(utc_in, IN), P(utc_out, OUT)])
    assert r.late_minutes == 0 and r.early_leave_minutes == 0 and r.status is DayStatus.PRESENT
