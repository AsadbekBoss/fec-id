export type TelegramRecipient = {
  id: string;
  title: string;
  username: string | null;
  is_active: boolean;
  subscriptions: string[];
  last_error: string | null;
  created_at: string;
  /** null = the centre (gets every branch's news). */
  branch_id?: string | null;
  employee_id: string | null;
};

const ADMIN_KEYS = ["daily", "late", "reviews", "system", "director"];
/** Chats shown on the Settings page: administrators (an employee chat appears only if it also
 * receives administrator messages). */
export function isAdminChat(r: TelegramRecipient): boolean {
  return r.employee_id === null || r.subscriptions.some((k) => ADMIN_KEYS.includes(k));
}

export type TelegramStatus = {
  configured: boolean;
  bot_username: string | null;
  bot_error: string | null;
  enabled: boolean;
  daily_summary_time: string;
  review_interval_minutes: number;
  camera_offline_minutes: number;
  worker_running: boolean;
  worker_last_run: string | null;
  worker_error: string | null;
  recipients: TelegramRecipient[];
  recent: { kind: string; created_at: string; recipients: number; delivered: number; summary: string | null }[];
};

export type TelegramLink = { code: string; link: string | null; bot_username: string | null; expires_at: string };

/** What a chat can subscribe to (same keys as the API). */
export const NOTIFY_KINDS = [
  { key: "daily", label: "Kunlik hisobot", hint: "Har kuni belgilangan vaqtda: kim keldi, kim kechikdi, kim hali kelmagan" },
  { key: "late", label: "Kechikish (darhol)", hint: "Xodim kechikib kelganda darhol xabar" },
  { key: "reviews", label: "Koʻrib chiqish", hint: "Noaniq yuzlar koʻrib chiqishni kutayotganda" },
  { key: "system", label: "Tizim / kamera", hint: "Kamera ishlamay qolsa yoki zaxira eskirsa" },
  {
    key: "director",
    label: "Direktor hisoboti",
    hint: "Barcha filiallar reytingi va kelmaganlar: har kuni; dushanba kuni o'tgan hafta, oyning 1-kuni o'tgan oy. Faqat markaz chatlari uchun",
  },
] as const;

/** Kinds a branch chat cannot receive (they cover every branch). */
export const CENTRE_ONLY_KINDS: readonly string[] = ["director"];

export const NOTIFY_LOG_KIND: Record<string, string> = {
  daily: "Kunlik",
  director: "Direktor",
  late: "Kechikish",
  reviews: "Koʻrib chiqish",
  camera_down: "Kamera",
  camera_up: "Kamera",
  backup: "Zaxira",
  self_punch: "Xodimga",
  self_late: "Xodimga",
};

export type EmployeeTelegram = {
  configured: boolean;
  enabled: boolean;
  bot_username: string | null;
  linked: boolean;
  username: string | null;
  is_active: boolean | null;
  subscriptions: string[];
  last_error: string | null;
  linked_at: string | null;
};

/** What an employee's own chat can receive (only about that employee). */
export const SELF_KINDS = [
  { key: "self_punch", label: "Kelish va ketish", hint: "Har safar kirish yoki chiqish qayd etilganda (kechiksa, necha daqiqa ekani ham)" },
  { key: "self_late", label: "Kechikish", hint: "Faqat kechikib kelganda" },
] as const;
