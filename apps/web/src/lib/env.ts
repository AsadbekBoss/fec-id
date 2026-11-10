/** Public (browser-visible) configuration. Never put secrets in NEXT_PUBLIC_* variables. */
export const env = {
  apiBaseUrl: process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000",
} as const;
