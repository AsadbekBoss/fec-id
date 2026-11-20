import { describe, expect, it } from "vitest";
import { HOLD_MS, SCAN_STEPS, checkFrame, holdProgress, requiredSign, type ScanFrame } from "./face-scan";

const [front, left, right] = SCAN_STEPS;

const frame = (yaw: number | null, over: Partial<ScanFrame["face"] & object> = {}, faces = 1): ScanFrame => ({
  preview: "data:image/jpeg;base64,",
  faces,
  face: { box: [0.4, 0.2, 0.2, 0.3], yaw_deg: yaw, roll_deg: 0, passed: true, reasons: [], face_width_px: 160, ...over },
});

describe("guided face scan", () => {
  it("has four steps: straight, one side, the other side, straight again", () => {
    expect(SCAN_STEPS.map((s) => s.key)).toEqual(["front", "left", "right", "smile"]);
  });

  it("needs exactly one face", () => {
    expect(checkFrame({ preview: "", faces: 0, face: null }, front, null).message).toMatch(/koʻrinmayapti/);
    expect(checkFrame(frame(0, {}, 2), front, null).message).toMatch(/bitta odam/);
  });

  it("checks a straight look", () => {
    expect(checkFrame(frame(3), front, null).ok).toBe(true);
    expect(checkFrame(frame(18), front, null)).toEqual({ ok: false, message: "Toʻgʻriga qarang" });
  });

  it("checks turning, either side first, then the opposite side", () => {
    expect(checkFrame(frame(5), left, null).message).toBe("Yana biroz buriling");
    expect(checkFrame(frame(-16), left, null).ok).toBe(true);
    expect(checkFrame(frame(29), left, null).message).toBe("Biroz kamroq buriling");
    expect(requiredSign(right, -1)).toBe(1);
    expect(checkFrame(frame(-16), right, -1).message).toBe("Boshqa tomonga buriling");
    expect(checkFrame(frame(16), right, -1).ok).toBe(true);
  });

  it("explains quality problems", () => {
    expect(checkFrame(frame(0, { passed: false, reasons: ["face_too_small"] }), front, null).message).toBe("Kameraga yaqinroq keling");
    expect(checkFrame(frame(0, { passed: false, reasons: ["too_dark"] }), front, null).message).toBe("Juda qorongʻi");
    // a turned face fails the generic gate with head_turned: on a turn step say "less"
    expect(checkFrame(frame(20, { passed: false, reasons: ["head_turned"] }), left, null).message).toBe("Biroz kamroq buriling");
  });

  it("measures the hold in time, not frames", () => {
    expect(holdProgress(null, 5000)).toBe(0);
    expect(holdProgress(1000, 1000 + HOLD_MS / 2)).toBeCloseTo(0.5);
    expect(holdProgress(1000, 1000 + HOLD_MS * 3)).toBe(1);
  });
});
