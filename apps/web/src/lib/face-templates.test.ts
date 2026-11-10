import { describe, expect, it } from "vitest";
import { ApiError } from "@/lib/api";
import { errorMessage } from "@/lib/errors";
import { enrolmentError } from "@/components/face-templates";

describe("enrolment error messages", () => {
  it("explains why a face was not accepted, with quality reasons", () => {
    const err = new ApiError(422, "FACE_NOT_ACCEPTED", "x", [{ field: "file", code: "low_quality", message: "face_too_small,blurry" }]);
    expect(enrolmentError(err)).toBe("Yuz sifati yetarli emas. Yuz juda kichik (masofa / linza), Tasvir xira");
    const none = new ApiError(422, "FACE_NOT_ACCEPTED", "x", [{ field: "file", code: "no_face", message: "no_face" }]);
    expect(enrolmentError(none)).toBe("Rasmda yuz topilmadi.");
  });

  it("keeps the server detail naming the other employee for duplicates", () => {
    const err = new ApiError(409, "FACE_MATCHES_OTHER_EMPLOYEE", "(E-7 Karimov Ali)");
    expect(enrolmentError(err)).toContain("(E-7 Karimov Ali)");
    expect(errorMessage(new ApiError(409, "CONSENT_REQUIRED", "x"))).toContain("rozilik");
  });
});
