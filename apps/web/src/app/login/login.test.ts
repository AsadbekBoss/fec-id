import { describe, expect, it } from "vitest";
import { ApiError, NetworkError } from "@/lib/api";
import { loginErrorMessage, safeNext } from "@/app/login/login-form";
import { visibleNav } from "@/components/layout/nav";

describe("safeNext (open-redirect protection)", () => {
  it.each([
    [null, "/dashboard"],
    ["/employees", "/employees"],
    ["/attendance?date=2026-11-02", "/attendance?date=2026-11-02"],
    ["https://evil.example", "/dashboard"],
    ["//evil.example", "/dashboard"],
    ["/\\evil.example", "/dashboard"],
    ["javascript:alert(1)", "/dashboard"],
  ])("%s -> %s", (input, expected) => {
    expect(safeNext(input)).toBe(expected);
  });
});

describe("loginErrorMessage", () => {
  it("does not reveal whether the username exists", () => {
    expect(loginErrorMessage(new ApiError(401, "INVALID_CREDENTIALS", ""))).toBe(
      "Foydalanuvchi nomi yoki parol notoʻgʻri.",
    );
  });
  it("uses Retry-After for lockouts (rounded up to minutes)", () => {
    expect(loginErrorMessage(new ApiError(429, "TOO_MANY_ATTEMPTS", "", [], 61))).toContain("2 daqiqadan");
  });
  it("explains network failures", () => {
    expect(loginErrorMessage(new NetworkError())).toContain("Server bilan aloqa yoʻq");
  });
});

describe("visibleNav", () => {
  const items = (perms: string[], dev = false) =>
    visibleNav((p) => perms.includes(p), dev).flatMap((s) => s.items.map((i) => i.href));

  it("hides items the role cannot use", () => {
    const viewer = items(["attendance:read", "employees:read", "cameras:read", "reports:read"]);
    expect(viewer).toContain("/attendance");
    expect(viewer).not.toContain("/users");
    expect(viewer).not.toContain("/settings");
  });
  it("shows the design system only in development", () => {
    expect(items([], true)).toContain("/design-system");
    expect(items([], false)).not.toContain("/design-system");
  });
  it("always shows the dashboard", () => {
    expect(items([])).toEqual(["/dashboard"]);
  });
});
