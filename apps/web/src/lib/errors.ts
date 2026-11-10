import { ApiError, NetworkError } from "@/lib/api";

/** Server error codes (API_CONTRACTS §1.1) → Uzbek messages. */
const CODE_MESSAGES: Record<string, string> = {
  FORBIDDEN: "Bu amal uchun ruxsatingiz yoʻq.",
  NOT_FOUND: "Maʼlumot topilmadi.",
  USER_EXISTS: "Bunday foydalanuvchi nomi yoki email allaqachon mavjud.",
  LAST_SUPER_ADMIN: "Kamida bitta faol bosh administrator qolishi shart.",
  EMPLOYEE_CODE_EXISTS: "Bu xodim kodi allaqachon band.",
  EMPLOYEE_EMAIL_EXISTS: "Bu email boshqa xodimga tegishli.",
  EMPLOYEE_PHONE_EXISTS: "Bu telefon boshqa xodimga tegishli.",
  INVALID_STATUS_TRANSITION: "Xodim allaqachon shu holatda.",
  DEPARTMENT_EXISTS: "Bunday nomli (yoki kodli) boʻlim allaqachon bor.",
  DEPARTMENT_NOT_EMPTY: "Boʻlimda xodimlar yoki ichki boʻlimlar bor. Avval ularni koʻchiring.",
  CONSENT_ALREADY_ACTIVE: "Xodimning faol rozilik yozuvi allaqachon bor.",
  CONSENT_REVOKED: "Rozilik bekor qilingan.",
  DOCUMENT_ALREADY_ATTACHED: "Imzolangan hujjat dalil sifatida saqlangan, uni almashtirib boʻlmaydi.",
  EMPLOYEE_TERMINATED: "Ishdan boʻshagan xodim uchun biometrik amallar bajarilmaydi.",
  BIOMETRICS_NOT_APPLICABLE: "Xodim qoʻlda davomatda. Avval davomat usulini FACE ga oʻzgartiring.",
  ACTIVE_CONSENT_EXISTS: "Avval biometrik rozilikni bekor qiling.",
  INVALID_SEQUENCE: "Natijada kirish/chiqish ketma-ketligi buziladi.",
  SCHEDULE_EXISTS: "Bunday nomli jadval allaqachon bor.",
  SCHEDULE_IN_USE: "Jadval xodim yoki boʻlimlarga biriktirilgan.",
  PAYLOAD_TOO_LARGE: "Fayl hajmi juda katta.",
  INVALID_FILE: "Fayl turi yoki tarkibi yaroqsiz.",
  TOO_MANY_ATTEMPTS: "Juda koʻp urinish. Birozdan keyin qayta urining.",
  CONSENT_REQUIRED: "Avval biometrik rozilikni yozing va imzolangan hujjatni yuklang.",
  NOT_FACE_METHOD: "Xodim qoʻlda davomatda. Yuz namunasi olinmaydi.",
  TEMPLATE_LIMIT: "Yuz namunalari soni chegaraga yetdi. Avval eskisini oʻchiring.",
  FACE_MATCHES_OTHER_EMPLOYEE: "Bu yuz boshqa roʻyxatdan oʻtgan xodimga juda oʻxshaydi. Shaxsni tekshiring.",
  RECOGNITION_SERVICE_UNAVAILABLE: "Tanish xizmati ishlamayapti (npm run rec:dev).",
  CAMERA_SERVICE_UNAVAILABLE: "Kamera xizmati ishlamayapti (npm run cam:dev).",
  CAMERA_UNREACHABLE: "Kameradan kadr olinmadi.",
  CAMERA_EXISTS: "Bunday nomli kamera allaqachon bor.",
  QUALITY_NOT_PASSED: "Avval sifat testidan oʻting.",
};

/** One-line user message for any thrown error. */
export function errorMessage(err: unknown): string {
  if (err instanceof NetworkError) return "Server bilan aloqa yoʻq.";
  if (err instanceof ApiError) {
    if (CODE_MESSAGES[err.code]) {
      // INVALID_SEQUENCE carries a precise server explanation: keep it.
      // These carry a precise server explanation (sequence problem / which employee): keep it.
      const keepDetail = err.code === "INVALID_SEQUENCE" || err.code === "FACE_MATCHES_OTHER_EMPLOYEE";
      return keepDetail && err.message ? `${CODE_MESSAGES[err.code]} ${err.message}` : CODE_MESSAGES[err.code];
    }
    if (err.status === 422 && err.errors.length) return err.errors.map((e) => e.message).join(" ");
    return err.message || "Xatolik yuz berdi.";
  }
  return "Xatolik yuz berdi.";
}

/** Map a 422/409 response onto form fields: { field: message }. */
export function fieldErrors(err: unknown, fieldByCode: Record<string, string> = {}): Record<string, string> {
  const out: Record<string, string> = {};
  if (!(err instanceof ApiError)) return out;
  for (const e of err.errors) {
    const field = e.field.split(".").pop() ?? e.field;
    out[field] = out[field] ? `${out[field]} ${e.message}` : e.message;
  }
  const mapped = fieldByCode[err.code];
  if (mapped) out[mapped] = CODE_MESSAGES[err.code] ?? err.message;
  return out;
}
