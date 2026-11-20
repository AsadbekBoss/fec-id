import { describe, expect, it } from "vitest";
import { HEALTH_VERDICT, healthAdvice } from "./camera-health";

describe("camera health advice", () => {
  it("explains every finding the camera service produces", () => {
    const codes = ["offline", "unstable_connection", "frame_loss", "low_fps", "cpu_high", "h265", "resolution_high", "resolution_low", "fps_high"];
    for (const code of codes) {
      const a = healthAdvice({ code, severity: "warn" });
      expect(a.title).not.toBe(code);
      expect(a.fix.length).toBeGreaterThan(10);
    }
  });

  it("puts the measured numbers in the text", () => {
    expect(healthAdvice({ code: "frame_loss", severity: "warn", count: 40 }).title).toContain("40 ta");
    expect(healthAdvice({ code: "resolution_high", severity: "tip", width: 2560, height: 1440 }).title).toContain("2560×1440");
  });

  it("labels verdicts", () => {
    expect(HEALTH_VERDICT.warn.label).toBe("Diqqat");
  });
});
