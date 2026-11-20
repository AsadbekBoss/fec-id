import { describe, expect, it } from "vitest";
import { ApiError, NetworkError } from "@/lib/api";
import { errorMessage, fieldErrors } from "@/lib/errors";
import { buildTimesheet, timesheetCsv } from "@/lib/reports";
import { addDays, daysBetween, isoToOrgLocal, monthRange, orgLocalToIso } from "@/lib/time";
import type { Day, Department } from "@/lib/types";
import { passwordProblems, validateSchedule } from "@/lib/validation";
import { treeOrder } from "@/components/entities";
import { validateEmployee } from "@/app/(app)/employees/employee-form";

const day = (over: Partial<Day> & { id?: string; name?: string }): Day => ({
  employee: { id: over.id ?? "e1", employee_code: "E1", full_name: over.name ?? "Karimov Aziz", department_id: "d", has_photo: false, photo_updated_at: null },
  work_date: "2026-11-02",
  status: "PRESENT",
  in_progress: false,
  schedule: null,
  scheduled_start: null,
  scheduled_end: null,
  scheduled_minutes: 480,
  arrival: null,
  departure: null,
  worked_minutes: 480,
  late_minutes: 0,
  early_leave_minutes: 0,
  overtime_minutes: 0,
  has_manual_events: false,
  ...over,
});

describe("timesheet", () => {
  it("aggregates per employee and ignores PENDING days", () => {
    const rows = buildTimesheet([
      day({ status: "PRESENT", worked_minutes: 480 }),
      day({ status: "LATE", late_minutes: 20, worked_minutes: 460 }),
      day({ status: "ABSENT", worked_minutes: 0 }),
      day({ status: "INCOMPLETE", worked_minutes: 0 }),
      day({ status: "PENDING", worked_minutes: 0 }),
      day({ status: "WEEKEND", scheduled_minutes: 0, worked_minutes: 120, overtime_minutes: 120 }),
      day({ id: "e2", name: "Abdullayev Bek", status: "PRESENT" }),
    ]);
    expect(rows.map((r) => r.full_name)).toEqual(["Abdullayev Bek", "Karimov Aziz"]);
    const k = rows[1];
    expect(k.scheduled_days).toBe(4); // PENDING and WEEKEND excluded
    expect(k.present_days).toBe(3);
    expect(k.late_days).toBe(1);
    expect(k.late_minutes).toBe(20);
    expect(k.absent_days).toBe(1);
    expect(k.incomplete_days).toBe(1);
    expect(k.worked_minutes).toBe(480 + 460 + 120);
    expect(k.overtime_minutes).toBe(120);
  });

  it("CSV has BOM, semicolons, quoting and formula-injection protection", () => {
    const rows = buildTimesheet([day({ name: '=HYPERLINK("x");evil' })]);
    const csv = timesheetCsv(rows, "2026-11-01 — 2026-11-30");
    expect(csv.charCodeAt(0)).toBe(0xfeff);
    const line = csv.split("\r\n")[2];
    expect(line).toContain(`"'=HYPERLINK(""x"");evil"`);
    expect(line).toContain("8,00"); // hours with decimal comma
  });
});

describe("time helpers (Asia/Tashkent, UTC+5)", () => {
  it("converts wall clock <-> ISO", () => {
    expect(orgLocalToIso("2026-11-02T09:30")).toBe("2026-11-02T09:30:00+05:00");
    expect(isoToOrgLocal("2026-11-02T04:30:00Z")).toBe("2026-11-02T09:30");
    expect(isoToOrgLocal("2026-11-02T20:15:00Z")).toBe("2026-11-03T01:15");
  });
  it("date arithmetic", () => {
    expect(addDays("2026-12-31", 1)).toBe("2027-01-01");
    expect(daysBetween("2026-11-01", "2026-11-30")).toBe(29);
    expect(monthRange("2026-02")).toEqual({ from: "2026-02-01", to: "2026-02-28" });
    expect(monthRange("2028-02").to).toBe("2028-02-29");
  });
});

describe("validation mirrors server rules", () => {
  it("password policy", () => {
    expect(passwordProblems("short")).toContain("Kamida 12 ta belgi.");
    expect(passwordProblems("Password123!")).toContain("Juda oddiy parol.");
    expect(passwordProblems("aaaaaaaaaaaaaa")).toContain("Kamida 4 xil belgi boʻlsin.");
    expect(passwordProblems("john.smith-secret-2026", "john.smith")).toContain("Parolda foydalanuvchi nomi boʻlmasin.");
    expect(passwordProblems("togʻda qor yogʻdi 2026", "admin")).toEqual([]);
  });

  it("schedule form", () => {
    const ok = { name: "Ofis", start_time: "09:00", end_time: "18:00", working_days: [1, 2, 3, 4, 5],
      break_minutes: "60", grace_in_minutes: "5", grace_out_minutes: "0", overtime_enabled: false, is_active: true };
    expect(validateSchedule(ok)).toEqual({});
    expect(validateSchedule({ ...ok, end_time: "09:00" }).end_time).toBeTruthy();
    expect(validateSchedule({ ...ok, working_days: [] }).working_days).toBeTruthy();
    expect(validateSchedule({ ...ok, break_minutes: "600" }).break_minutes).toBeTruthy(); // longer than shift
    expect(validateSchedule({ ...ok, start_time: "22:00", end_time: "06:00", break_minutes: "60" })).toEqual({}); // night shift
    expect(validateSchedule({ ...ok, grace_in_minutes: "-1" }).grace_in_minutes).toBeTruthy();
  });

  it("employee form", () => {
    const ok = { employee_code: "E-1", last_name: "Qoʻchqorov", first_name: "Aziz", middle_name: "Baxtiyor oʻgʻli",
      department_id: "d", work_schedule_id: "", position_title: "", phone: "+998 (90) 123-45-67", email: "",
      hire_date: "2025-01-01", attendance_method: "FACE" as const };
    expect(validateEmployee(ok)).toEqual({});
    expect(validateEmployee({ ...ok, employee_code: "bad code" }).employee_code).toBeTruthy();
    expect(validateEmployee({ ...ok, last_name: "Ivan0v" }).last_name).toBeTruthy();
    expect(validateEmployee({ ...ok, first_name: "Сергей" })).toEqual({}); // Cyrillic allowed
    expect(validateEmployee({ ...ok, phone: "12-34" }).phone).toBeTruthy();
    expect(validateEmployee({ ...ok, department_id: "" }).department_id).toBeTruthy();
  });
});

describe("server error mapping", () => {
  it("maps 422 field errors and 409 codes onto fields", () => {
    const e422 = new ApiError(422, "VALIDATION_ERROR", "", [{ field: "phone", code: "value_error", message: "bad phone" }]);
    expect(fieldErrors(e422)).toEqual({ phone: "bad phone" });
    const e409 = new ApiError(409, "EMPLOYEE_CODE_EXISTS", "dup");
    expect(fieldErrors(e409, { EMPLOYEE_CODE_EXISTS: "employee_code" }).employee_code).toContain("band");
  });
  it("produces user messages", () => {
    expect(errorMessage(new NetworkError())).toContain("aloqa");
    expect(errorMessage(new ApiError(409, "LAST_SUPER_ADMIN", ""))).toContain("bosh administrator");
    expect(errorMessage(new ApiError(409, "INVALID_SEQUENCE", "Event 2 is IN, expected OUT."))).toContain("expected OUT");
  });
});

describe("department tree", () => {
  const dep = (id: string, name: string, parent: string | null): Department => ({
    id, branch_id: "b1", name, parent_id: parent, code: null, head_employee_id: null, work_schedule_id: null, employee_count: 0, created_at: "", updated_at: "",
  });
  it("orders parents before children with depth", () => {
    const t = treeOrder([dep("c", "IT", "a"), dep("a", "Markaz", null), dep("b", "Buxgalteriya", "a"), dep("x", "Orphan", "missing")]);
    expect(t.map((d) => `${d.depth}:${d.name}`)).toEqual(["0:Markaz", "1:Buxgalteriya", "1:IT", "0:Orphan"]);
    expect(t.find((d) => d.id === "c")?.path).toBe("Markaz / IT");
  });
});
