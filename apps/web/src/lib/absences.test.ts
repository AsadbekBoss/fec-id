import { describe, expect, it } from "vitest";
import { dmy, leaveDays, leaveState } from "@/lib/absences";

describe("leave helpers", () => {
  it("counts days inclusively", () => {
    expect(leaveDays("2026-11-02", "2026-11-02")).toBe(1);
    expect(leaveDays("2026-11-28", "2026-12-02")).toBe(5);
  });
  it("formats dates", () => {
    expect(dmy("2026-11-02")).toBe("02.11.2026");
  });
  it("tells whether a leave is current", () => {
    const l = { date_from: "2026-11-10", date_to: "2026-11-20" };
    expect(leaveState(l, "2026-11-05")).toBe("soon");
    expect(leaveState(l, "2026-11-20")).toBe("now");
    expect(leaveState(l, "2026-11-21")).toBe("past");
  });
});
