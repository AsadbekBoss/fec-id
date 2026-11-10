/** Client-side form validation mirroring server rules (the server stays authoritative). */

export type ScheduleFormValues = {
  name: string;
  start_time: string;
  end_time: string;
  working_days: number[];
  break_minutes: string;
  grace_in_minutes: string;
  grace_out_minutes: string;
  overtime_enabled: boolean;
  is_active: boolean;
};

export function validateSchedule(f: ScheduleFormValues): Record<string, string> {
  const e: Record<string, string> = {};
  if (!f.name.trim()) e.name = "Nomini kiriting.";
  if (!f.start_time) e.start_time = "Boshlanish vaqti.";
  if (!f.end_time) e.end_time = "Tugash vaqti.";
  if (f.start_time && f.start_time === f.end_time) e.end_time = "Boshlanish va tugash bir xil boʻlmasin.";
  if (f.working_days.length === 0) e.working_days = "Kamida bitta ish kuni.";
  const num = (v: string, max: number) => /^\d+$/.test(v) && Number(v) <= max;
  if (!num(f.break_minutes, 720)) e.break_minutes = "0–720 daqiqa.";
  if (!num(f.grace_in_minutes, 240)) e.grace_in_minutes = "0–240 daqiqa.";
  if (!num(f.grace_out_minutes, 240)) e.grace_out_minutes = "0–240 daqiqa.";
  if (f.start_time && f.end_time && num(f.break_minutes, 720) && f.start_time !== f.end_time) {
    const m = (t: string) => Number(t.slice(0, 2)) * 60 + Number(t.slice(3, 5));
    const len = m(f.end_time) > m(f.start_time) ? m(f.end_time) - m(f.start_time) : m(f.end_time) + 1440 - m(f.start_time);
    if (Number(f.break_minutes) >= len) e.break_minutes = "Tushlik smenadan qisqa boʻlishi kerak.";
  }
  return e;
}

const COMMON = new Set([
  "password", "password1", "password123", "passw0rd", "qwerty", "qwerty123", "qwertyuiop", "123456",
  "12345678", "123456789", "1234567890", "111111", "000000", "abc123", "admin", "administrator",
  "letmein", "welcome", "iloveyou", "monkey", "dragon", "football", "parol", "parol123", "1q2w3e4r",
  "1q2w3e4r5t", "zaq12wsx", "changeme", "attendance",
]);

/** Mirrors app/auth/passwords.py policy_violations (NIST-style: length + blocklist). */
export function passwordProblems(password: string, username = ""): string[] {
  const p = password.normalize("NFKC");
  const out: string[] = [];
  if (p.length < 12) out.push("Kamida 12 ta belgi.");
  if (p.length > 128) out.push("Koʻpi bilan 128 ta belgi.");
  const lower = p.toLowerCase();
  if (COMMON.has(lower) || COMMON.has(lower.replace(/[0-9!]+$/, ""))) out.push("Juda oddiy parol.");
  if (new Set(p).size < 4) out.push("Kamida 4 xil belgi boʻlsin.");
  if (username.length >= 3 && lower.includes(username.toLowerCase())) out.push("Parolda foydalanuvchi nomi boʻlmasin.");
  return out;
}

export const USERNAME_RE = /^[A-Za-z0-9_.@-]{3,64}$/;
