/**
 * Guided face enrolment ("Yuzni skanerlash"): the steps, and the per-frame check that tells the
 * person what to do. The server re-checks the head pose on the frame it actually enrols
 * (POSE_MISMATCH), so this is guidance only.
 */
import { ApiError } from "@/lib/api";
import { errorMessage } from "@/lib/errors";
import { QUALITY_REASON } from "@/lib/labels";

export type ScanFaceInfo = {
  /** [x, y, w, h] as fractions of the frame. */
  box: number[];
  yaw_deg: number | null;
  roll_deg: number | null;
  passed: boolean;
  reasons: string[];
  face_width_px: number;
};

/** `preview` is null when the page asked for analysis only (it shows the MJPEG stream). */
export type ScanFrame = { preview: string | null; faces: number; face: ScanFaceInfo | null };

export type ScanStep = {
  key: "front" | "left" | "right" | "smile";
  title: string;
  pose: "frontal" | "turned";
  /** For turned steps: "first" = either side (remembered), "opposite" = the other side. */
  side?: "first" | "opposite";
};

export const SCAN_STEPS: ScanStep[] = [
  { key: "front", title: "Toʻgʻriga, kameraga qarang", pose: "frontal" },
  { key: "left", title: "Boshingizni biroz chapga buring", pose: "turned", side: "first" },
  { key: "right", title: "Endi biroz oʻngga buring", pose: "turned", side: "opposite" },
  { key: "smile", title: "Yana toʻgʻriga qarang va jilmaying", pose: "frontal" },
];

/** Same limits as the API (FRONTAL_MAX_YAW, TURNED_MIN_YAW); the quality gate rejects > 30. */
export const FRONTAL_MAX_YAW = 10;
export const TURNED_MIN_YAW = 12;
export const TURNED_MAX_YAW = 27;
/** A capture needs the right pose held this long, over at least HOLD_FRAMES frames. */
export const HOLD_MS = 900;
export const HOLD_FRAMES = 3;

/** Progress 0..1 of holding the pose (null start = not holding). */
export function holdProgress(start: number | null, now: number): number {
  return start === null ? 0 : Math.min(1, (now - start) / HOLD_MS);
}

export type FrameCheck = { ok: boolean; message: string };

/** Sign the server must see for this step (undefined = any side). */
export function requiredSign(step: ScanStep, firstSign: 1 | -1 | null): 1 | -1 | undefined {
  if (step.pose !== "turned" || step.side !== "opposite" || firstSign === null) return undefined;
  return firstSign === 1 ? -1 : 1;
}

export function checkFrame(frame: ScanFrame, step: ScanStep, firstSign: 1 | -1 | null): FrameCheck {
  const f = frame.face;
  if (frame.faces === 0 || !f) return { ok: false, message: "Yuz koʻrinmayapti. Kameraga qarang." };
  if (frame.faces > 1) return { ok: false, message: "Kadrda faqat bitta odam boʻlsin." };
  const yaw = f.yaw_deg;
  const abs = yaw === null ? null : Math.abs(yaw);

  if (step.pose === "turned" && abs !== null) {
    // Turning is the point of this step: judge the angle before the generic quality reasons.
    if (abs < TURNED_MIN_YAW) return { ok: false, message: "Yana biroz buriling" };
    if (abs > TURNED_MAX_YAW || f.reasons.includes("head_turned")) return { ok: false, message: "Biroz kamroq buriling" };
    const sign = requiredSign(step, firstSign);
    if (sign !== undefined && (yaw! > 0 ? 1 : -1) !== sign) return { ok: false, message: "Boshqa tomonga buriling" };
  }
  if (!f.passed) {
    const r = f.reasons[0];
    if (r === "face_too_small" || r === "eyes_too_close_in_pixels") return { ok: false, message: "Kameraga yaqinroq keling" };
    return { ok: false, message: (r && QUALITY_REASON[r]) || "Yuz sifati yetarli emas" };
  }
  if (abs === null) return { ok: false, message: "Yuz holatini aniqlab boʻlmadi" };
  if (step.pose === "frontal" && abs > FRONTAL_MAX_YAW) return { ok: false, message: "Toʻgʻriga qarang" };
  return { ok: true, message: "Juda yaxshi, qimirlamang…" };
}

const FACE_REASON: Record<string, string> = {
  no_face: "Rasmda yuz topilmadi.",
  multiple_faces: "Rasmda bir nechta yuz bor. Faqat shu xodim boʻlsin.",
  low_quality: "Yuz sifati yetarli emas.",
  invalid_image: "Rasmni oʻqib boʻlmadi.",
};

/** Uzbek message for an enrolment error, including the quality reasons from the service. */
export function enrolmentError(err: unknown): string {
  if (err instanceof ApiError && err.code === "FACE_NOT_ACCEPTED") {
    const e = err.errors[0];
    const reasons = (e?.message ?? "")
      .split(",")
      .filter((r) => r && r !== e?.code)
      .map((r) => QUALITY_REASON[r] ?? r);
    return [FACE_REASON[e?.code ?? ""] ?? "Yuz qabul qilinmadi.", reasons.join(", ")].filter(Boolean).join(" ");
  }
  return errorMessage(err);
}
