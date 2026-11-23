/** Holidays and employee leave (Phase 24). */

export type LeaveKind = "VACATION" | "SICK" | "TRIP" | "OTHER";

export type Leave = {
  id: string;
  employee_id: string;
  employee_name: string;
  employee_code: string;
  kind: LeaveKind;
  date_from: string;
  date_to: string;
  note: string | null;
};

export type Holiday = {
  id: string;
  day: string;
  name: string;
  /** null = every branch. */
  branch_id: string | null;
  editable: boolean;
};

export const LEAVE_KIND: Record<LeaveKind, { label: string; icon: string }> = {
  VACATION: { label: "Taʼtil", icon: "🏖" },
  SICK: { label: "Kasallik", icon: "🤒" },
  TRIP: { label: "Xizmat safari", icon: "✈️" },
  OTHER: { label: "Boshqa uzrli sabab", icon: "📄" },
};

/** Inclusive day count of a leave (dates are YYYY-MM-DD). */
export function leaveDays(from: string, to: string): number {
  const ms = Date.parse(`${to}T00:00:00Z`) - Date.parse(`${from}T00:00:00Z`);
  return Math.round(ms / 86_400_000) + 1;
}

/** "02.11.2026" from "2026-11-02". */
export function dmy(iso: string): string {
  const [y, m, d] = iso.split("-");
  return `${d}.${m}.${y}`;
}

/** Active today, upcoming or past (dates are YYYY-MM-DD, compared as strings). */
export function leaveState(l: Pick<Leave, "date_from" | "date_to">, today: string): "now" | "soon" | "past" {
  if (l.date_to < today) return "past";
  return l.date_from <= today ? "now" : "soon";
}
