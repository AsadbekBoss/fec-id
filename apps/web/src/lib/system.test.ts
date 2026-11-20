import { describe, expect, it } from "vitest";
import { checkSummary, formatBytes, formatDuration, type SystemCheck } from "./system";

const check = (name: string, info: Record<string, unknown>): SystemCheck => ({
  name, title: name, status: "ok", detail: null, latency_ms: null, info,
});

describe("system status helpers", () => {
  it("formats durations", () => {
    expect(formatDuration(42)).toBe("1 daqiqadan kam");
    expect(formatDuration(3700)).toBe("1 soat 1 daqiqa");
    expect(formatDuration(93784)).toBe("1 kun 2 soat");
    expect(formatDuration(86400)).toBe("1 kun");
  });

  it("formats sizes", () => {
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(1536)).toBe("1.5 KB");
    expect(formatBytes(5 * 1024 ** 2)).toBe("5.0 MB");
  });

  it("summarises checks", () => {
    expect(checkSummary(check("recognition", { templates: 6, employees: 1 }))).toBe("1 xodim, 6 namuna");
    expect(checkSummary(check("backup", { count: 3, last_size_bytes: 2048 }))).toBe("3 ta nusxa, oxirgisi 2.0 KB");
    expect(checkSummary(check("backup", {}))).toBeNull();
    expect(checkSummary(check("disk", { free_gb: 120.5, total_gb: 476.3 }))).toBe("120.5 GB boʻsh (476.3 GB dan)");
  });
});
