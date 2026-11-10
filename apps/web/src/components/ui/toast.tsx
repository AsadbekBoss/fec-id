"use client";

import { AlertTriangle, CheckCircle2, Info, X, XCircle } from "lucide-react";
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { cn } from "@/lib/cn";
import { t } from "@/lib/i18n";

type Tone = "success" | "error" | "warning" | "info";
type Toast = { id: number; tone: Tone; title: string; body?: string };

type ToastApi = {
  notify: (tone: Tone, title: string, body?: string) => void;
  success: (title: string, body?: string) => void;
  error: (title: string, body?: string) => void;
  info: (title: string, body?: string) => void;
  warning: (title: string, body?: string) => void;
};

const ToastContext = createContext<ToastApi | null>(null);
const DURATION_MS = 5000;
const MAX_VISIBLE = 4;

const styles: Record<Tone, { icon: React.ReactNode; bar: string }> = {
  success: { icon: <CheckCircle2 className="h-5 w-5 text-success-600" />, bar: "bg-success-600" },
  error: { icon: <XCircle className="h-5 w-5 text-danger-600" />, bar: "bg-danger-600" },
  warning: { icon: <AlertTriangle className="h-5 w-5 text-warning-600" />, bar: "bg-warning-600" },
  info: { icon: <Info className="h-5 w-5 text-brand-600" />, bar: "bg-brand-600" },
};

/** App-wide notifications. Errors stay until closed; others auto-dismiss after 5 s. */
export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const seq = useRef(0);

  const dismiss = useCallback((id: number) => setToasts((all) => all.filter((x) => x.id !== id)), []);

  const notify = useCallback((tone: Tone, title: string, body?: string) => {
    const id = ++seq.current;
    setToasts((all) => [...all, { id, tone, title, body }].slice(-MAX_VISIBLE));
  }, []);

  const apiValue = useMemo<ToastApi>(
    () => ({
      notify,
      success: (a, b) => notify("success", a, b),
      error: (a, b) => notify("error", a, b),
      info: (a, b) => notify("info", a, b),
      warning: (a, b) => notify("warning", a, b),
    }),
    [notify],
  );

  return (
    <ToastContext.Provider value={apiValue}>
      {children}
      <div
        aria-live="polite"
        aria-relevant="additions"
        className="pointer-events-none fixed inset-x-0 bottom-0 z-[60] flex flex-col items-center gap-2 p-4 sm:inset-x-auto sm:right-0 sm:top-0 sm:bottom-auto sm:items-end"
      >
        {toasts.map((toast) => (
          <ToastItem key={toast.id} toast={toast} onDismiss={dismiss} />
        ))}
      </div>
    </ToastContext.Provider>
  );
}

function ToastItem({ toast, onDismiss }: { toast: Toast; onDismiss: (id: number) => void }) {
  // onDismiss is stable (useCallback), so the timer is not restarted by unrelated re-renders.
  useEffect(() => {
    if (toast.tone === "error") return; // errors need acknowledgement
    const timer = setTimeout(() => onDismiss(toast.id), DURATION_MS);
    return () => clearTimeout(timer);
  }, [toast.id, toast.tone, onDismiss]);
  const onClose = () => onDismiss(toast.id);

  const s = styles[toast.tone];
  return (
    <div
      role={toast.tone === "error" ? "alert" : "status"}
      className="pointer-events-auto relative flex w-full max-w-sm animate-slide-in-right overflow-hidden rounded-xl border border-line bg-surface shadow-[var(--shadow-overlay)]"
    >
      <span className={cn("w-1 shrink-0", s.bar)} aria-hidden />
      <div className="flex flex-1 items-start gap-3 p-4">
        <span aria-hidden>{s.icon}</span>
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold text-navy-900">{toast.title}</p>
          {toast.body ? <p className="mt-0.5 text-sm text-muted">{toast.body}</p> : null}
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label={t.common.close}
          className="rounded-md p-1 text-muted hover:bg-navy-50 hover:text-ink"
        >
          <X className="h-4 w-4" />
        </button>
      </div>
    </div>
  );
}

export function useToast(): ToastApi {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error("useToast must be used inside <ToastProvider>");
  return ctx;
}
