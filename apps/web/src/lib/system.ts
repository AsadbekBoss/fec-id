import type { BadgeTone } from "@/components/ui/badge";

export type CheckStatus = "ok" | "warning" | "error" | "not_configured";

export type SystemCheck = {
  name: string;
  title: string;
  status: CheckStatus;
  detail: string | null;
  latency_ms: number | null;
  info: Record<string, unknown>;
};

export type SupervisedService = {
  name: string;
  title: string;
  port: number;
  state: string;
  started_at: string | null;
  restarts: number;
  last_exit_code: number | null;
  last_exit_at: string | null;
  last_error: string | null;
  log: string;
};

export type SystemStatus = {
  status: "ok" | "warning" | "error";
  checked_at: string;
  version: string;
  environment: string;
  api_uptime_seconds: number;
  checks: SystemCheck[];
  supervisor: {
    running: boolean;
    mode: string | null;
    started_at: string | null;
    updated_at: string | null;
    services: SupervisedService[];
  } | null;
};

export const CHECK_STATUS: Record<CheckStatus, { label: string; tone: BadgeTone }> = {
  ok: { label: "Ishlayapti", tone: "success" },
  warning: { label: "Diqqat", tone: "warning" },
  error: { label: "Ishlamayapti", tone: "danger" },
  not_configured: { label: "Sozlanmagan", tone: "neutral" },
};

export const SERVICE_STATE: Record<string, { label: string; tone: BadgeTone }> = {
  running: { label: "Ishlayapti", tone: "success" },
  starting: { label: "Ishga tushmoqda", tone: "brand" },
  unhealthy: { label: "Javob bermayapti", tone: "warning" },
  crashed: { label: "Toʻxtadi, qayta yoqilmoqda", tone: "danger" },
  stopped: { label: "Toʻxtatilgan", tone: "neutral" },
  not_installed: { label: "Oʻrnatilmagan", tone: "neutral" },
};

/** 93784 -> "1 kun 2 soat", 3700 -> "1 soat 1 daqiqa", 42 -> "1 daqiqadan kam". */
export function formatDuration(seconds: number): string {
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (d > 0) return h > 0 ? `${d} kun ${h} soat` : `${d} kun`;
  if (h > 0) return m > 0 ? `${h} soat ${m} daqiqa` : `${h} soat`;
  return m > 0 ? `${m} daqiqa` : "1 daqiqadan kam";
}

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 ** 2) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 ** 3) return `${(n / 1024 ** 2).toFixed(1)} MB`;
  return `${(n / 1024 ** 3).toFixed(1)} GB`;
}

/** Short human summary of a check's extra info (never shows secrets: the API sends none). */
export function checkSummary(c: SystemCheck): string | null {
  const i = c.info;
  switch (c.name) {
    case "api":
      return `Versiya ${String(i.version ?? "—")}`;
    case "database":
      return i.migration ? `Migratsiya ${String(i.migration)}` : null;
    case "recognition":
      return i.templates !== undefined ? `${String(i.employees ?? 0)} xodim, ${String(i.templates)} namuna` : null;
    case "camera":
      return i.cameras !== undefined ? `${String(i.cameras)} ta kamera kuzatilmoqda` : null;
    case "backup":
      return typeof i.last_size_bytes === "number" ? `${String(i.count)} ta nusxa, oxirgisi ${formatBytes(i.last_size_bytes)}` : null;
    case "disk":
      return `${String(i.free_gb)} GB boʻsh (${String(i.total_gb)} GB dan)`;
    default:
      return null;
  }
}
