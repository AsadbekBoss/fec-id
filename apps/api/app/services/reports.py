"""Attendance reports (API_CONTRACTS §2.10): data + Excel / PDF rendering.

All numbers come from the attendance engine (`compute_range`, computed live), so a report always
matches the Attendance pages. Texts are Uzbek (the users' language).
"""

from __future__ import annotations

import io
import uuid
from calendar import monthrange
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.attendance import engine
from app.attendance import service as att
from app.models import Department, Employee

S = engine.DayStatus

STATUS_LABEL = {
    S.PRESENT: "Keldi",
    S.LATE: "Kechikdi",
    S.INCOMPLETE: "Chiqish yoʻq",
    S.ABSENT: "Kelmadi",
    S.WEEKEND: "Dam olish",
    S.NOT_SCHEDULED: "Jadvalsiz",
    S.PENDING: "Hali kelmagan",
    S.LEAVE: "Taʼtil / uzrli",
    S.HOLIDAY: "Bayram",
}
LEAVE_LABEL = {
    "VACATION": "Taʼtil",
    "SICK": "Kasallik",
    "TRIP": "Xizmat safari",
    "OTHER": "Uzrli sabab",
}
LEAVE_CODE = {"VACATION": "T", "SICK": "B", "TRIP": "S", "OTHER": "U"}
# One-letter codes for the monthly grid (+ legend in every export).
STATUS_CODE = {
    S.PRESENT: "+",
    S.LATE: "K",
    S.INCOMPLETE: "C",
    S.ABSENT: "Y",
    S.WEEKEND: "D",
    S.NOT_SCHEDULED: "J",
    S.PENDING: "·",
    S.LEAVE: "T",
    S.HOLIDAY: "H",
}
LEGEND = (
    "Belgilar: + keldi, K kechikdi, C chiqish yoʻq, Y kelmadi, D dam olish, J jadvalsiz, "
    "· hali kelmagan, H bayram, T taʼtil, B kasallik, S xizmat safari, U uzrli sabab"
)


def status_code(r: engine.DayResult) -> str:
    if r.status is S.LEAVE and r.leave_kind:
        return LEAVE_CODE.get(r.leave_kind, "T")
    return STATUS_CODE[r.status]


def status_label(r: engine.DayResult) -> str:
    if r.status is S.LEAVE and r.leave_kind:
        return LEAVE_LABEL.get(r.leave_kind, STATUS_LABEL[S.LEAVE])
    if r.status is S.HOLIDAY and r.holiday_name:
        return f"Bayram: {r.holiday_name}"
    return STATUS_LABEL[r.status]


WORK_STATUSES = {S.PRESENT, S.LATE, S.INCOMPLETE, S.ABSENT}
CAME = {S.PRESENT, S.LATE, S.INCOMPLETE}


def hm(minutes: int) -> str:
    return f"{minutes // 60}:{minutes % 60:02d}"


@dataclass
class Column:
    key: str
    label: str
    width: float = 12  # Excel character width; PDF scales proportionally
    align: str = "left"


@dataclass
class Report:
    kind: str
    title: str
    subtitle: str
    columns: list[Column]
    rows: list[dict[str, Any]]
    footer: list[str] = field(default_factory=list)
    # timesheet only: per-row day codes for colouring
    day_columns: list[str] = field(default_factory=list)
    details: Report | None = None  # second sheet (Excel)

    def as_json(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "title": self.title,
            "subtitle": self.subtitle,
            "columns": [{"key": c.key, "label": c.label, "align": c.align} for c in self.columns],
            "rows": self.rows,
            "footer": self.footer,
        }


# --- data -----------------------------------------------------------------------------------


async def _context(
    db: AsyncSession, emps: list[Employee], date_from: date, date_to: date
) -> tuple[list[tuple[Employee, engine.DayResult]], dict[uuid.UUID, str], ZoneInfo]:
    results = await att.compute_range(db, emps, date_from, date_to)
    depts = {d.id: d.name for d in (await db.execute(select(Department))).scalars()}
    tz = (await att.load_settings(db)).default_tz
    return results, depts, tz


def _local(dt: datetime | None, tz: ZoneInfo) -> str:
    return dt.astimezone(tz).strftime("%H:%M") if dt else ""


def _name(e: Employee) -> str:
    return f"{e.last_name} {e.first_name}"


def _scope(dept_name: str | None) -> str:
    if dept_name and dept_name.startswith("Filial: "):
        return dept_name
    return f"Boʻlim: {dept_name}" if dept_name else "Barcha boʻlimlar"


async def timesheet(
    db: AsyncSession, emps: list[Employee], month: date, dept_name: str | None
) -> Report:
    first = month.replace(day=1)
    last = first.replace(day=monthrange(first.year, first.month)[1])
    results, depts, tz = await _context(db, emps, first, last)
    by_emp: dict[uuid.UUID, dict[date, engine.DayResult]] = {}
    for e, r in results:
        by_emp.setdefault(e.id, {})[r.work_date] = r

    days = [first + timedelta(days=i) for i in range((last - first).days + 1)]
    day_keys = [f"d{d.day}" for d in days]
    cols = [
        Column("code", "Kod", 9),
        Column("name", "Xodim", 26),
        Column("department", "Boʻlim", 16),
        *[Column(k, str(d.day), 3.2, "center") for k, d in zip(day_keys, days, strict=True)],
        Column("work_days", "Ish kuni", 7, "right"),
        Column("came", "Keldi", 6, "right"),
        Column("late_days", "Kech.", 6, "right"),
        Column("absent_days", "Kelmadi", 7, "right"),
        Column("leave_days", "Uzrli", 6, "right"),
        Column("worked", "Ishladi (s:d)", 10, "right"),
        Column("late_minutes", "Kech. (daq)", 11, "right"),
        Column("early_minutes", "Erta ket. (daq)", 13, "right"),
    ]
    rows: list[dict[str, Any]] = []
    detail_rows: list[dict[str, Any]] = []
    for e in emps:
        mine = by_emp.get(e.id)
        if not mine:
            continue  # not employed during this month
        row: dict[str, Any] = {
            "code": e.employee_code,
            "name": _name(e),
            "department": depts.get(e.department_id, ""),
        }
        worked = late = early = 0
        counts = {"work_days": 0, "came": 0, "late_days": 0, "absent_days": 0, "leave_days": 0}
        for d, k in zip(days, day_keys, strict=True):
            day = mine.get(d)
            row[k] = status_code(day) if day else ""
            if day is None:
                continue
            r = day
            worked += r.worked_minutes
            late += r.late_minutes
            early += r.early_leave_minutes
            counts["work_days"] += r.status in WORK_STATUSES
            counts["came"] += r.status in CAME
            counts["late_days"] += r.status is S.LATE
            counts["absent_days"] += r.status is S.ABSENT
            counts["leave_days"] += r.status is S.LEAVE
            if r.status not in (S.WEEKEND,) or r.first_in:
                detail_rows.append(
                    {
                        "date": d.isoformat(),
                        "code": e.employee_code,
                        "name": _name(e),
                        "status": status_label(r),
                        "in": _local(r.first_in, tz),
                        "out": _local(r.last_out, tz),
                        "worked": hm(r.worked_minutes),
                        "late": r.late_minutes or "",
                        "early": r.early_leave_minutes or "",
                    }
                )
        row.update(counts, worked=hm(worked), late_minutes=late, early_minutes=early)
        rows.append(row)

    title = f"Davomat tabeli — {first:%Y-%m}"
    details = Report(
        "timesheet-days",
        "Kunlar",
        title,
        [
            Column("date", "Sana", 11),
            Column("code", "Kod", 9),
            Column("name", "Xodim", 26),
            Column("status", "Holat", 14),
            Column("in", "Keldi", 8, "center"),
            Column("out", "Ketdi", 8, "center"),
            Column("worked", "Ishladi", 8, "right"),
            Column("late", "Kech. (daq)", 9, "right"),
            Column("early", "Erta ket. (daq)", 10, "right"),
        ],
        sorted(detail_rows, key=lambda r: (r["date"], r["name"])),
    )
    return Report(
        "timesheet",
        title,
        _scope(dept_name),
        cols,
        rows,
        footer=[LEGEND, f"Xodimlar: {len(rows)}"],
        day_columns=day_keys,
        details=details,
    )


async def lateness(
    db: AsyncSession, emps: list[Employee], date_from: date, date_to: date, dept_name: str | None
) -> Report:
    results, depts, tz = await _context(db, emps, date_from, date_to)
    rows: list[dict[str, Any]] = [
        {
            "date": r.work_date.isoformat(),
            "code": e.employee_code,
            "name": _name(e),
            "department": depts.get(e.department_id, ""),
            "scheduled": _local(r.scheduled_start, tz),
            "arrived": _local(r.first_in, tz),
            "late_minutes": r.late_minutes,
        }
        for e, r in results
        if r.status is S.LATE or r.late_minutes > 0
    ]
    rows.sort(key=lambda r: (r["date"], r["name"]))
    per_person = {str(row["name"]) for row in rows}
    total = sum(int(row["late_minutes"]) for row in rows)
    return Report(
        "lateness",
        f"Kechikishlar — {date_from.isoformat()} … {date_to.isoformat()}",
        _scope(dept_name),
        [
            Column("date", "Sana", 11),
            Column("code", "Kod", 9),
            Column("name", "Xodim", 26),
            Column("department", "Boʻlim", 18),
            Column("scheduled", "Ish boshlanishi", 12, "center"),
            Column("arrived", "Keldi", 9, "center"),
            Column("late_minutes", "Kechikish (daq)", 13, "right"),
        ],
        rows,
        footer=[
            f"Kechikishlar soni: {len(rows)}, jami {hm(total)} (soat:daqiqa), "
            f"kechikkan xodimlar: {len(per_person)}"
        ],
    )


async def absence(
    db: AsyncSession, emps: list[Employee], date_from: date, date_to: date, dept_name: str | None
) -> Report:
    results, depts, _ = await _context(db, emps, date_from, date_to)
    rows = [
        {
            "date": r.work_date.isoformat(),
            "code": e.employee_code,
            "name": _name(e),
            "department": depts.get(e.department_id, ""),
            "schedule": (
                f"{r.schedule.start_time:%H:%M}–{r.schedule.end_time:%H:%M}" if r.schedule else ""
            ),
        }
        for e, r in results
        if r.status is S.ABSENT
    ]
    rows.sort(key=lambda r: (r["date"], r["name"]))
    return Report(
        "absence",
        f"Kelmaganlar — {date_from.isoformat()} … {date_to.isoformat()}",
        _scope(dept_name),
        [
            Column("date", "Sana", 11),
            Column("code", "Kod", 9),
            Column("name", "Xodim", 26),
            Column("department", "Boʻlim", 18),
            Column("schedule", "Ish vaqti", 12, "center"),
        ],
        rows,
        footer=[f"Kelmagan kunlar: {len(rows)}, xodimlar: {len({r['name'] for r in rows})}"],
    )


# --- Excel ----------------------------------------------------------------------------------

FILLS = {"+": "DCFCE7", "K": "FEF3C7", "C": "FEF3C7", "Y": "FEE2E2", "D": "F1F5F9", "J": "F1F5F9"}


def _sheet(ws: Any, rep: Report) -> None:
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    ws.append([rep.title])
    ws["A1"].font = Font(bold=True, size=13)
    ws.append([rep.subtitle])
    ws.append([])
    ws.append([c.label for c in rep.columns])
    header_row = ws.max_row
    thin = Side(style="thin", color="CBD5E1")
    for i, c in enumerate(rep.columns, start=1):
        cell = ws.cell(row=header_row, column=i)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1E293B")
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        ws.column_dimensions[get_column_letter(i)].width = c.width
    day_cols = set(rep.day_columns)
    for row in rep.rows:
        ws.append([row.get(c.key, "") for c in rep.columns])
        r = ws.max_row
        for i, c in enumerate(rep.columns, start=1):
            cell = ws.cell(row=r, column=i)
            cell.border = Border(left=thin, right=thin, top=thin, bottom=thin)
            cell.alignment = Alignment(horizontal=c.align)
            if c.key in day_cols and (fill := FILLS.get(str(cell.value))):
                cell.fill = PatternFill("solid", fgColor=fill)
    ws.freeze_panes = ws.cell(row=header_row + 1, column=3 if day_cols else 1)
    ws.append([])
    for line in rep.footer:
        ws.append([line])
    ws.append([f"Yaratildi: {datetime.now():%Y-%m-%d %H:%M}"])


def to_xlsx(rep: Report) -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Tabel" if rep.kind == "timesheet" else rep.title.split(" ")[0][:30]
    _sheet(ws, rep)
    if rep.details is not None:
        _sheet(wb.create_sheet(rep.details.title[:30]), rep.details)
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


# --- PDF ------------------------------------------------------------------------------------

_FONT: tuple[str, str] | None = None
FONT_CANDIDATES = [
    ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf"),
    (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ),
    ("/usr/share/fonts/dejavu/DejaVuSans.ttf", "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf"),
]


def _fonts() -> tuple[str, str]:
    """A TTF with Latin Extended (Uzbek letters); Helvetica as last resort."""
    global _FONT
    if _FONT is None:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont

        _FONT = ("Helvetica", "Helvetica-Bold")
        for regular, bold in FONT_CANDIDATES:
            if Path(regular).is_file() and Path(bold).is_file():
                pdfmetrics.registerFont(TTFont("Body", regular))
                pdfmetrics.registerFont(TTFont("Body-Bold", bold))
                _FONT = ("Body", "Body-Bold")
                break
    return _FONT


def _pdf_text(s: Any) -> str:
    # U+02BB/U+02BC (Uzbek oʻ, gʻ, ʼ) are missing from many fonts: use typographic quotes.
    return str(s).replace("ʻ", "‘").replace("ʼ", "’")


def to_pdf(rep: Report) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    regular, bold = _fonts()
    page = landscape(A4)
    out = io.BytesIO()
    doc = SimpleDocTemplate(
        out,
        pagesize=page,
        leftMargin=10 * mm,
        rightMargin=10 * mm,
        topMargin=10 * mm,
        bottomMargin=10 * mm,
        title=_pdf_text(rep.title),
    )
    h1 = ParagraphStyle("h1", fontName=bold, fontSize=13, leading=16)
    small = ParagraphStyle("s", fontName=regular, fontSize=8, leading=10)
    grid = bool(rep.day_columns)
    size = 6 if grid else 8
    data = [[_pdf_text(c.label) for c in rep.columns]]
    data += [[_pdf_text(r.get(c.key, "")) for c in rep.columns] for r in rep.rows]
    usable = page[0] - 20 * mm
    total_w = sum(c.width for c in rep.columns)
    widths = [usable * c.width / total_w for c in rep.columns]
    style = [
        ("FONT", (0, 0), (-1, 0), bold, size),
        ("FONT", (0, 1), (-1, -1), regular, size),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1E293B")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#CBD5E1")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 1.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5),
        ("LEFTPADDING", (0, 0), (-1, -1), 2),
        ("RIGHTPADDING", (0, 0), (-1, -1), 2),
    ]
    for i, c in enumerate(rep.columns):
        if c.align != "left":
            style.append(("ALIGN", (i, 0), (i, -1), "CENTER" if c.align == "center" else "RIGHT"))
    if grid:
        keys = [c.key for c in rep.columns]
        for ri, r in enumerate(rep.rows, start=1):
            for k in rep.day_columns:
                if fill := FILLS.get(str(r.get(k, ""))):
                    ci = keys.index(k)
                    style.append(("BACKGROUND", (ci, ri), (ci, ri), colors.HexColor("#" + fill)))
    table = Table(data, colWidths=widths, repeatRows=1)
    table.setStyle(TableStyle(style))
    story = [
        Paragraph(_pdf_text(rep.title), h1),
        Paragraph(_pdf_text(rep.subtitle), small),
        Spacer(1, 4 * mm),
        table,
        Spacer(1, 3 * mm),
    ]
    story += [Paragraph(_pdf_text(line), small) for line in rep.footer]
    story.append(Paragraph(f"Yaratildi: {datetime.now():%Y-%m-%d %H:%M}", small))
    if not rep.rows:
        story.insert(4, Paragraph("Maʼlumot yoʻq.".replace("ʼ", "’"), small))
    doc.build(story)
    return out.getvalue()
