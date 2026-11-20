/** Server reports (API_CONTRACTS §2.10): query building and response type. */

export type ReportKind = "timesheet" | "lateness" | "absence";

export type ReportJson = {
  kind: string;
  title: string;
  subtitle: string;
  columns: { key: string; label: string; align: "left" | "center" | "right" }[];
  rows: Record<string, string | number | null>[];
  footer: string[];
};

export type ReportParams = { kind: ReportKind; month: string; from: string; to: string; department: string };

export function reportQuery(p: ReportParams, format: "json" | "xlsx" | "pdf"): string {
  const q = new URLSearchParams({ format });
  if (p.kind === "timesheet") q.set("month", p.month);
  else {
    q.set("date_from", p.from);
    q.set("date_to", p.to);
  }
  if (p.department) q.set("department_id", p.department);
  return q.toString();
}
