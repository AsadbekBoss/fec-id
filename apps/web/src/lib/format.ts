/** Display formatting. All dates are shown in the organisation time zone, never the browser's. */
export const ORG_TIME_ZONE = "Asia/Tashkent";

const timeFmt = new Intl.DateTimeFormat("uz-UZ", {
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
  timeZone: ORG_TIME_ZONE,
});

const dateFmt = new Intl.DateTimeFormat("uz-UZ", {
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  timeZone: ORG_TIME_ZONE,
});

export function formatTime(iso: string | null | undefined): string {
  return iso ? timeFmt.format(new Date(iso)) : "—";
}

export function formatDate(iso: string | null | undefined): string {
  return iso ? dateFmt.format(new Date(iso)) : "—";
}

/** "2026-11-08" for the current day in the organisation time zone. */
export function todayInOrg(now: Date = new Date()): string {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: ORG_TIME_ZONE,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(now);
  return parts; // en-CA formats as YYYY-MM-DD
}

export function formatMinutes(total: number): string {
  const h = Math.floor(total / 60);
  const m = total % 60;
  return h ? `${h} soat ${m} daq` : `${m} daq`;
}
