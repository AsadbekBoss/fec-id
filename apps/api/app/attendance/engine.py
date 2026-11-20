"""Attendance rules: pure functions, no database, no clock (`now` is always a parameter).

Everything here is deterministic and unit-tested (tests/test_attendance_engine.py). The API
layer loads data, calls these functions, and stores the results.

Definitions
-----------
* Shift of work date D: starts at D + start_time (schedule time zone) and ends at
  D + end_time, or the next day if end_time <= start_time (night shift).
* Attendance window of D: [shift start - early_window, shift end + late_window]. Punches inside
  it belong to work date D. This is how a 22:00-06:00 shift's 05:58 OUT is assigned to the
  previous day.
* A day is "closed" once `now` is past its window end. Only then can it become ABSENT or
  INCOMPLETE.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo

ENGINE_VERSION = "1.0"
# One continuous IN→OUT pair at least this long gets the schedule's unpaid break deducted
# (the break is assumed taken without punching out). With several pairs, the gaps between them
# are the breaks, so nothing is deducted.
BREAK_DEDUCTION_MIN_SPAN = timedelta(hours=6)


class Direction(StrEnum):
    IN = "IN"
    OUT = "OUT"


class CameraMode(StrEnum):
    IN = "IN"
    OUT = "OUT"
    BOTH = "BOTH"
    NONE = "NONE"


class IgnoreReason(StrEnum):
    """Why a camera event was stored but does NOT count for attendance."""

    UNMATCHED = "UNMATCHED"  # unknown / ambiguous / spoof: no employee
    DEBOUNCED = "DEBOUNCED"  # same employee, same camera, within debounce seconds
    REPEATED_DETECTION = "REPEATED_DETECTION"  # BOTH-camera sighting too soon after IN
    DUPLICATE_IN = "DUPLICATE_IN"  # IN while already IN
    OUT_WITHOUT_IN = "OUT_WITHOUT_IN"  # OUT while not IN
    OUT_OF_SEQUENCE = "OUT_OF_SEQUENCE"  # late-arriving event would break the sequence
    MONITOR_ONLY = "MONITOR_ONLY"  # camera direction NONE / recognition disabled
    EMPLOYEE_INACTIVE = "EMPLOYEE_INACTIVE"  # not ACTIVE (suspended, terminated, on leave)
    NOT_BIOMETRIC = "NOT_BIOMETRIC"  # employee uses MANUAL attendance
    OUTSIDE_EMPLOYMENT = "OUTSIDE_EMPLOYMENT"  # before hire / after termination
    OTHER_DEPARTMENT = "OTHER_DEPARTMENT"  # recognised, but the camera serves other departments
    OTHER_BRANCH = "OTHER_BRANCH"  # recognised, but the employee belongs to another branch


class DayStatus(StrEnum):
    PRESENT = "PRESENT"
    LATE = "LATE"
    INCOMPLETE = "INCOMPLETE"
    ABSENT = "ABSENT"
    WEEKEND = "WEEKEND"
    NOT_SCHEDULED = "NOT_SCHEDULED"
    PENDING = "PENDING"  # working day not over yet and no IN so far (never persisted)


@dataclass(frozen=True)
class Schedule:
    id: object
    name: str
    tz: ZoneInfo
    start_time: time
    end_time: time
    working_days: frozenset[int]  # ISO 1=Mon..7=Sun
    break_minutes: int = 60
    grace_in_minutes: int = 5
    grace_out_minutes: int = 0
    early_window_minutes: int = 180
    late_window_minutes: int = 360
    overtime_enabled: bool = False

    def shift(self, d: date) -> tuple[datetime, datetime]:
        """Shift start/end in UTC (local wall times converted with DST rules)."""
        start = datetime.combine(d, self.start_time, tzinfo=self.tz)
        end_day = d + timedelta(days=1) if self.end_time <= self.start_time else d
        end = datetime.combine(end_day, self.end_time, tzinfo=self.tz)
        return utc(start), utc(end)

    def window(self, d: date) -> tuple[datetime, datetime]:
        start, end = self.shift(d)
        return (
            start - timedelta(minutes=self.early_window_minutes),
            end + timedelta(minutes=self.late_window_minutes),
        )

    def is_working_day(self, d: date) -> bool:
        return d.isoweekday() in self.working_days

    def shift_minutes(self) -> int:
        start, end = self.shift(date(2001, 1, 1))  # no DST in this reference date
        return int((end - start).total_seconds() // 60)


def utc(dt: datetime) -> datetime:
    """All arithmetic happens in UTC. Python subtracts two datetimes that share one ZoneInfo
    using *wall-clock* time, which is wrong across DST changes."""
    if dt.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return dt.astimezone(UTC)


@dataclass(frozen=True)
class Punch:
    time: datetime  # timezone-aware; normalised to UTC on creation
    direction: Direction
    manual: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "time", utc(self.time))


# --- work date ---------------------------------------------------------------------------------


def assign_work_date(t: datetime, schedule: Schedule | None, default_tz: ZoneInfo) -> date:
    """The work date a timestamp belongs to (see module docstring)."""
    t = utc(t)
    if schedule is None:
        return t.astimezone(default_tz).date()
    local_day = t.astimezone(schedule.tz).date()
    candidates = []
    for offset in (-1, 0, 1):
        d = local_day + timedelta(days=offset)
        w_start, w_end = schedule.window(d)
        if w_start <= t <= w_end:
            shift_start, _ = schedule.shift(d)
            candidates.append((abs((t - shift_start).total_seconds()), d))
    if not candidates:
        return local_day
    return min(candidates)[1]


def is_day_closed(d: date, schedule: Schedule | None, default_tz: ZoneInfo, now: datetime) -> bool:
    if schedule is None:
        end_of_day = datetime.combine(d + timedelta(days=1), time(0), tzinfo=default_tz)
        return utc(now) >= utc(end_of_day)
    return utc(now) > schedule.window(d)[1]


# --- sequence rules ----------------------------------------------------------------------------


def state_before(punches: list[Punch], t: datetime) -> Punch | None:
    """Last counted punch strictly before t (punches sorted by time)."""
    last = None
    for p in punches:
        if p.time < t:
            last = p
        else:
            break
    return last


def next_after(punches: list[Punch], t: datetime) -> Punch | None:
    for p in punches:
        if p.time > t:
            return p
    return None


def classify_camera_punch(
    mode: CameraMode,
    t: datetime,
    counted: list[Punch],
    min_presence: timedelta,
) -> tuple[Direction | None, IgnoreReason | None]:
    """Decide what a MATCHED camera sighting means, given the day's counted punches.

    Rules:
    * Fixed IN/OUT cameras give the direction, but the sequence must stay valid
      (IN, OUT, IN, OUT, ...).
    * BOTH cameras alternate. A sighting less than `min_presence` after the IN is a REPEATED
      DETECTION, never a departure (business rule 2).
    * A late-arriving event is only counted if it fits between its neighbours.
    """
    if mode is CameraMode.NONE:
        return None, IgnoreReason.MONITOR_ONLY
    t = utc(t)
    prev = state_before(counted, t)
    nxt = next_after(counted, t)
    if any(p.time == t for p in counted):
        return None, IgnoreReason.DEBOUNCED  # exact same instant already counted
    inside = prev is not None and prev.direction is Direction.IN

    if mode is CameraMode.BOTH:
        if prev is not None and prev.direction is Direction.IN:
            if t - prev.time < min_presence:
                return None, IgnoreReason.REPEATED_DETECTION
            direction = Direction.OUT
        else:
            direction = Direction.IN
    else:
        direction = Direction(mode.value)
        if direction is Direction.IN and inside:
            return None, IgnoreReason.DUPLICATE_IN
        if direction is Direction.OUT and not inside:
            return None, IgnoreReason.OUT_WITHOUT_IN

    if nxt is not None and nxt.direction is direction:
        return None, IgnoreReason.OUT_OF_SEQUENCE
    return direction, None


class SequenceError(ValueError):
    pass


def validate_sequence(punches: list[Punch]) -> None:
    """Counted punches of one work date must be strictly increasing in time and alternate,
    starting with IN. Ending with IN is allowed (still at work, or forgot to punch out)."""
    expected = Direction.IN
    previous: datetime | None = None
    for i, p in enumerate(punches):
        if previous is not None and p.time <= previous:
            raise SequenceError(f"Events {i} and {i + 1} have the same or decreasing time.")
        if p.direction is not expected:
            raise SequenceError(
                f"Event {i + 1} at {p.time.isoformat()} is {p.direction.value}, "
                f"expected {expected.value}."
            )
        expected = Direction.OUT if expected is Direction.IN else Direction.IN
        previous = p.time


# --- daily computation -------------------------------------------------------------------------


@dataclass
class DayResult:
    work_date: date
    status: DayStatus
    in_progress: bool = False
    schedule: Schedule | None = None
    scheduled_start: datetime | None = None
    scheduled_end: datetime | None = None
    scheduled_minutes: int = 0
    first_in: datetime | None = None
    last_out: datetime | None = None
    worked_minutes: int = 0
    late_minutes: int = 0
    early_leave_minutes: int = 0
    overtime_minutes: int = 0
    has_manual_events: bool = False
    punches: list[Punch] = field(default_factory=list)


def _minutes(delta: timedelta) -> int:
    return max(0, int(delta.total_seconds() // 60))


def compute_day(
    work_date: date,
    punches: list[Punch],
    schedule: Schedule | None,
    default_tz: ZoneInfo,
    now: datetime,
) -> DayResult:
    """Arrival, departure, worked time, lateness, early leave, overtime and status for one
    employee and one work date. `punches` = counted, non-voided events of that work date."""
    punches = sorted(punches, key=lambda p: p.time)
    closed = is_day_closed(work_date, schedule, default_tz, now)
    r = DayResult(
        work_date=work_date,
        status=DayStatus.PENDING,
        schedule=schedule,
        punches=punches,
        has_manual_events=any(p.manual for p in punches),
    )

    working_day = schedule is not None and schedule.is_working_day(work_date)
    if schedule is not None:
        r.scheduled_start, r.scheduled_end = schedule.shift(work_date)
        if working_day:
            r.scheduled_minutes = max(0, schedule.shift_minutes() - schedule.break_minutes)

    # Pair IN→OUT. Malformed sequences cannot be stored (validate_sequence on every write),
    # but stay defensive: ignore an OUT without a preceding IN.
    pairs: list[tuple[datetime, datetime]] = []
    open_in: datetime | None = None
    for p in punches:
        if p.direction is Direction.IN and open_in is None:
            open_in = p.time
        elif p.direction is Direction.OUT and open_in is not None:
            pairs.append((open_in, p.time))
            open_in = None

    ins = [p.time for p in punches if p.direction is Direction.IN]
    outs = [p.time for p in punches if p.direction is Direction.OUT]
    r.first_in = ins[0] if ins else None
    r.last_out = outs[-1] if outs and (r.first_in is None or outs[-1] > r.first_in) else None
    r.in_progress = open_in is not None and not closed

    worked = sum((b - a for a, b in pairs), timedelta())
    if schedule is not None and len(pairs) == 1 and worked >= BREAK_DEDUCTION_MIN_SPAN:
        worked -= timedelta(minutes=schedule.break_minutes)
    r.worked_minutes = _minutes(worked)

    if schedule is None:
        r.status = DayStatus.NOT_SCHEDULED if (punches or closed) else DayStatus.PENDING
        return r

    if working_day and r.first_in is not None and r.scheduled_start is not None:
        late = r.first_in - r.scheduled_start
        if late > timedelta(minutes=schedule.grace_in_minutes):
            r.late_minutes = _minutes(late)
    if working_day and r.last_out is not None and open_in is None and r.scheduled_end is not None:
        early = r.scheduled_end - r.last_out
        if early > timedelta(minutes=schedule.grace_out_minutes):
            r.early_leave_minutes = _minutes(early)
    if schedule.overtime_enabled:
        r.overtime_minutes = max(0, r.worked_minutes - r.scheduled_minutes)

    if not working_day:
        r.status = DayStatus.WEEKEND
    elif not punches:
        r.status = DayStatus.ABSENT if closed else DayStatus.PENDING
    elif open_in is not None and closed:
        r.status = DayStatus.INCOMPLETE
    elif r.late_minutes > 0:
        r.status = DayStatus.LATE
    else:
        r.status = DayStatus.PRESENT
    return r
