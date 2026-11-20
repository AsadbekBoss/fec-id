/** Human labels + badge tones for API enums (single place, used by every page). */
import type { BadgeTone } from "@/components/ui/badge";
import type {
  BiometricStatus,
  CameraDirection,
  CameraProvider,
  CameraQualityStatus,
  CameraStatus,
  DayStatus,
  EmployeeStatus,
  Role,
} from "@/lib/types";

type Label = { label: string; tone: BadgeTone };

export const EMPLOYEE_STATUS: Record<EmployeeStatus, Label> = {
  ACTIVE: { label: "Faol", tone: "success" },
  ON_LEAVE: { label: "Taʼtilda", tone: "brand" },
  SUSPENDED: { label: "Toʻxtatilgan", tone: "warning" },
  TERMINATED: { label: "Ishdan boʻshagan", tone: "neutral" },
};

export const DAY_STATUS: Record<DayStatus, Label> = {
  PRESENT: { label: "Keldi", tone: "success" },
  LATE: { label: "Kechikdi", tone: "warning" },
  INCOMPLETE: { label: "Chiqish yoʻq", tone: "warning" },
  ABSENT: { label: "Kelmadi", tone: "danger" },
  WEEKEND: { label: "Dam olish", tone: "neutral" },
  NOT_SCHEDULED: { label: "Jadvalsiz", tone: "neutral" },
  PENDING: { label: "Hali kelmagan", tone: "brand" },
};

export const BIOMETRIC_STATUS: Record<BiometricStatus, Label & { hint: string }> = {
  NOT_APPLICABLE: { label: "Qoʻlda davomat", tone: "neutral", hint: "Xodim biometriyadan foydalanmaydi." },
  NO_CONSENT: { label: "Rozilik yoʻq", tone: "neutral", hint: "Biometrik rozilik qayd etilmagan." },
  CONSENT_PENDING_DOCUMENT: { label: "Hujjat kutilmoqda", tone: "warning", hint: "Rozilik yozilgan, imzolangan hujjat yuklanmagan." },
  READY_FOR_ENROLLMENT: { label: "Roʻyxatga tayyor", tone: "brand", hint: "Hujjatlashtirilgan rozilik bor, yuz hali roʻyxatga olinmagan." },
  ENROLLED: { label: "Roʻyxatga olingan", tone: "success", hint: "Yuz namunalari mavjud." },
  CONSENT_REVOKED: { label: "Rozilik bekor qilingan", tone: "danger", hint: "Yuz maʼlumotlari oʻchirilgan." },
};

export const ROLE: Record<Role, Label> = {
  SUPER_ADMIN: { label: "Bosh administrator", tone: "brand" },
  ADMIN: { label: "Administrator", tone: "success" },
  VIEWER: { label: "Kuzatuvchi", tone: "neutral" },
};

/** Why a stored camera event did not count (engine.IgnoreReason). */
export const IGNORE_REASON: Record<string, string> = {
  UNMATCHED: "Tanilmagan shaxs",
  DEBOUNCED: "Takroriy kadr (60 s ichida)",
  REPEATED_DETECTION: "Qayta koʻrinish, chiqish emas",
  DUPLICATE_IN: "Allaqachon ichkarida",
  OUT_WITHOUT_IN: "Kirishsiz chiqish",
  OUT_OF_SEQUENCE: "Ketma-ketlikka mos emas",
  MONITOR_ONLY: "Faqat kuzatuv kamerasi",
  EMPLOYEE_INACTIVE: "Xodim faol emas",
  NOT_BIOMETRIC: "Xodim qoʻlda davomatda",
  OUTSIDE_EMPLOYMENT: "Ish davridan tashqari",
  OTHER_DEPARTMENT: "Boshqa boʻlim (bu kamera unga xizmat qilmaydi)",
};

export const DECISION: Record<string, Label> = {
  MATCHED: { label: "Tanildi", tone: "success" },
  UNKNOWN: { label: "Nomaʼlum", tone: "neutral" },
  AMBIGUOUS: { label: "Noaniq", tone: "warning" },
  SPOOF_SUSPECTED: { label: "Soxta urinish?", tone: "danger" },
};

export const SOURCE: Record<string, string> = { CAMERA: "Kamera", MANUAL: "Qoʻlda", IMPORT: "Import" };

export const WEEKDAYS = ["Du", "Se", "Ch", "Pa", "Ju", "Sh", "Ya"]; // ISO 1..7

// --- cameras ---
export const CAMERA_PROVIDER: Record<CameraProvider, { label: string; hint: string }> = {
  RTSP: { label: "IP kamera (RTSP)", hint: "Tarmoqdagi IP kamera" },
  USB: { label: "USB veb-kamera", hint: "Kamera xizmati ishlayotgan kompyuterga ulangan" },
  TEST: { label: "Test kamera", hint: "Uskunasiz ishlab chiqish uchun sunʼiy kadrlar" },
};

export const CAMERA_DIRECTION: Record<CameraDirection, { label: string; hint: string }> = {
  IN: { label: "Kirish", hint: "Kirish eshigi: kelishni qayd etadi" },
  OUT: { label: "Chiqish", hint: "Chiqish eshigi: ketishni qayd etadi" },
  BOTH: { label: "Kirish va chiqish", hint: "Bitta eshik, ikki yoʻnalish (minimal boʻlish vaqti qoidasi bilan)" },
  NONE: { label: "Faqat kuzatuv", hint: "Davomatga taʼsir qilmaydi" },
};

export const CAMERA_STATUS: Record<CameraStatus, Label> = {
  DRAFT: { label: "Qoralama", tone: "neutral" },
  ACTIVE: { label: "Ishlayapti", tone: "success" },
  DEGRADED: { label: "Beqaror", tone: "warning" },
  OFFLINE: { label: "Aloqa yoʻq", tone: "danger" },
  DISABLED: { label: "Oʻchirilgan", tone: "neutral" },
};

export const QUALITY_STATUS: Record<CameraQualityStatus, Label & { hint: string }> = {
  UNTESTED: { label: "Sinalmagan", tone: "neutral", hint: "Oqim yuzni tanish uchun hali tekshirilmagan." },
  PASSED: { label: "Sifat yetarli", tone: "success", hint: "Sinov paytida koʻrilgan yuzlar sifat talabiga javob berdi." },
  FAILED: { label: "Sifat yetarli emas", tone: "danger", hint: "Bu oqim hozirgi sozlamada yuzni tanish uchun yaroqsiz." },
  INCONCLUSIVE: { label: "Aniq emas", tone: "warning", hint: "Oqim ishlaydi, lekin yuz baholanmadi. Kamera oldida odam turgan holda qayta sinang." },
};

export const QUALITY_REASON: Record<string, string> = {
  no_frames: "Kadr olinmadi",
  low_resolution: "Ruxsat past (kamida 640×480)",
  low_fps: "Kadr tezligi past",
  frozen_stream: "Tasvir qotib qolgan",
  too_dark: "Juda qorongʻi",
  too_bright: "Juda yorugʻ",
  blurry: "Tasvir xira",
  faces_fail_quality_gate: "Yuzlar sifat talabidan oʻtmadi",
  no_face_assessed: "Yuz baholanmadi",
  face_too_small: "Yuz juda kichik (masofa / linza)",
  head_turned: "Bosh burilgan",
  head_tilted: "Bosh qiyshaygan",
  low_detection_score: "Yuz aniq aniqlanmadi",
  eyes_too_close_in_pixels: "Koʻzlar orasi juda kam piksel",
};

export const CAMERA_ERROR: Record<string, string> = {
  UNREACHABLE: "Kamera javob bermayapti (manzil, port yoki tarmoqni tekshiring)",
  OPEN_FAILED: "Kamera javob berdi, lekin oqim ochilmadi (yoʻl, login/parol yoki kodek)",
  DEVICE_NOT_FOUND: "USB kamera topilmadi yoki band",
  STREAM_LOST: "Oqim uzildi",
  CONFIG_INVALID: "Sozlama notoʻgʻri",
  MEDIA_NOT_FOUND: "Test media topilmadi",
  PROVIDER_UNAVAILABLE: "Bu turdagi kamera ushbu serverda qoʻllab-quvvatlanmaydi",
  BUSY: "Kamera xizmati band, keyinroq urinib koʻring",
};
