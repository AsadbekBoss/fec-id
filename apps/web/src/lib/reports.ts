/** Client-side report building from GET /attendance/days results (pure, unit-tested). */
import type { Day } from "@/lib/types";

export type TimesheetRow = {
  employee_id: string;
  employee_code: string;
  full_name: string;
  scheduled_days: number;
  present_days: number;
  late_days: number;
  late_minutes: number;
  absent_days: number;
  incomplete_days: number;
  early_leave_minutes: number;
  worked_minutes: number;
  overtime_minutes: number;
};

/** One row per employee. PENDING days (not over yet) count as neither present nor absent. */
export function buildTimesheet(days: Day[]): TimesheetRow[] {
  const rows = new Map<string, TimesheetRow>();
  for (const d of days) {
    let r = rows.get(d.employee.id);
    if (!r) {
      r = {
        employee_id: d.employee.id,
        employee_code: d.employee.employee_code,
        full_name: d.employee.full_name,
        scheduled_days: 0,
        present_days: 0,
        late_days: 0,
        late_minutes: 0,
        absent_days: 0,
        incomplete_days: 0,
        early_leave_minutes: 0,
        worked_minutes: 0,
        overtime_minutes: 0,
      };
      rows.set(d.employee.id, r);
    }
    if (d.scheduled_minutes > 0 && d.status !== "PENDING") r.scheduled_days += 1;
    if (d.status === "PRESENT" || d.status === "LATE" || d.status === "INCOMPLETE") r.present_days += 1;
    if (d.status === "LATE" || d.late_minutes > 0) r.late_days += 1;
    if (d.status === "ABSENT") r.absent_days += 1;
    if (d.status === "INCOMPLETE") r.incomplete_days += 1;
    r.late_minutes += d.late_minutes;
    r.early_leave_minutes += d.early_leave_minutes;
    r.worked_minutes += d.worked_minutes;
    r.overtime_minutes += d.overtime_minutes;
  }
  return [...rows.values()].sort((a, b) => a.full_name.localeCompare(b.full_name, "uz"));
}

function cell(v: string | number): string {
  const s = String(v);
  // Neutralise spreadsheet formula injection (=, +, -, @ at the start of a text cell).
  const safe = typeof v === "string" && /^[=+\-@]/.test(s) ? `'${s}` : s;
  return /[";\n\r]/.test(safe) ? `"${safe.replace(/"/g, '""')}"` : safe;
}

/**
 * CSV for Excel: semicolon-separated (regional Excel defaults in UZ/RU) with a UTF-8 BOM,
 * so Cyrillic and Uzbek letters (ʻ) open correctly.
 */
export function timesheetCsv(rows: TimesheetRow[], period: string): string {
  const header = [
    "Kod", "Xodim", "Ish kunlari", "Kelgan kunlar", "Kechikkan kunlar", "Kechikish (daq)",
    "Kelmagan kunlar", "Chiqishsiz kunlar", "Erta ketish (daq)", "Ishlagan (soat)", "Qoʻshimcha (daq)",
  ];
  const lines = [
    `Davr;${cell(period)}`,
    header.map(cell).join(";"),
    ...rows.map((r) =>
      [
        r.employee_code, r.full_name, r.scheduled_days, r.present_days, r.late_days, r.late_minutes,
        r.absent_days, r.incomplete_days, r.early_leave_minutes, (r.worked_minutes / 60).toFixed(2).replace(".", ","),
        r.overtime_minutes,
      ]
        .map(cell)
        .join(";"),
    ),
  ];
  return `﻿${lines.join("\r\n")}\r\n`;
}
