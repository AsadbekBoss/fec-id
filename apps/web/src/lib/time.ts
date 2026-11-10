/**
 * Organisation-time helpers. Asia/Tashkent is UTC+05:00 with no DST (since 1992), so a fixed
 * offset is exact for wall-clock ↔ ISO conversions in forms.
 */
import { ORG_TIME_ZONE, todayInOrg } from "@/lib/format";

export const ORG_UTC_OFFSET = "+05:00";

/** "2026-11-08T09:30" (org wall clock from <input type="datetime-local">) → ISO with offset. */
export function orgLocalToIso(local: string): string {
  return `${local.length === 16 ? `${local}:00` : local}${ORG_UTC_OFFSET}`;
}

/** ISO instant → "YYYY-MM-DDTHH:mm" in org time, for datetime-local inputs. */
export function isoToOrgLocal(iso: string): string {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: ORG_TIME_ZONE,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).formatToParts(new Date(iso));
  const get = (t: string) => parts.find((p) => p.type === t)?.value ?? "00";
  const hour = get("hour") === "24" ? "00" : get("hour");
  return `${get("year")}-${get("month")}-${get("day")}T${hour}:${get("minute")}`;
}

export function addDays(isoDate: string, days: number): string {
  const d = new Date(`${isoDate}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

export function daysBetween(from: string, to: string): number {
  return Math.round((Date.parse(`${to}T00:00:00Z`) - Date.parse(`${from}T00:00:00Z`)) / 86_400_000);
}

/** First and last day of a "YYYY-MM" month. */
export function monthRange(month: string): { from: string; to: string } {
  const [y, m] = month.split("-").map(Number);
  const last = new Date(Date.UTC(y, m, 0)).getUTCDate();
  return { from: `${month}-01`, to: `${month}-${String(last).padStart(2, "0")}` };
}

export function currentMonth(): string {
  return todayInOrg().slice(0, 7);
}

export { todayInOrg };
