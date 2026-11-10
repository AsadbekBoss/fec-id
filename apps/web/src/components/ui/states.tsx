"use client";

import { AlertTriangle, Construction, Inbox, Lock, RotateCw, WifiOff } from "lucide-react";
import { ApiError, NetworkError } from "@/lib/api";
import { t } from "@/lib/i18n";
import { cn } from "@/lib/cn";
import { Button } from "@/components/ui/button";
import { Skeleton, Spinner } from "@/components/ui/spinner";

function Frame({
  icon,
  title,
  body,
  action,
  className,
  role,
}: {
  icon: React.ReactNode;
  title: string;
  body?: React.ReactNode;
  action?: React.ReactNode;
  className?: string;
  role?: "alert" | "status";
}) {
  return (
    <div role={role} className={cn("flex flex-col items-center justify-center px-6 py-12 text-center", className)}>
      <span className="flex h-12 w-12 items-center justify-center rounded-full bg-navy-50 text-navy-600">{icon}</span>
      <h3 className="mt-4 text-base font-semibold text-navy-900">{title}</h3>
      {body ? <p className="mt-1 max-w-md text-sm text-muted">{body}</p> : null}
      {action ? <div className="mt-5">{action}</div> : null}
    </div>
  );
}

export function LoadingState({ label = t.common.loading, className }: { label?: string; className?: string }) {
  return (
    <div role="status" className={cn("flex items-center justify-center gap-3 py-12 text-sm text-muted", className)}>
      <Spinner />
      <span>{label}</span>
    </div>
  );
}

export function SkeletonRows({ rows = 5, cols = 4 }: { rows?: number; cols?: number }) {
  return (
    <div className="space-y-3 p-5" aria-hidden>
      {Array.from({ length: rows }, (_, r) => (
        <div key={r} className="flex gap-4">
          {Array.from({ length: cols }, (_, c) => (
            <Skeleton key={c} className={cn("h-4", c === 0 ? "w-1/3" : "flex-1")} />
          ))}
        </div>
      ))}
    </div>
  );
}

export function EmptyState({ title = t.common.empty, body, action }: { title?: string; body?: string; action?: React.ReactNode }) {
  return <Frame icon={<Inbox className="h-6 w-6" />} title={title} body={body} action={action} />;
}

/** Turns any thrown value into a user-facing error panel with an optional retry. */
export function ErrorState({ error, onRetry, className }: { error: unknown; onRetry?: () => void; className?: string }) {
  let icon = <AlertTriangle className="h-6 w-6" />;
  let title: string = t.common.errorTitle;
  let body: string = t.common.errorBody;
  if (error instanceof NetworkError) {
    icon = <WifiOff className="h-6 w-6" />;
    body = t.auth.network;
  } else if (error instanceof ApiError) {
    if (error.status === 403) {
      icon = <Lock className="h-6 w-6" />;
      title = t.common.forbidden;
      body = "";
    } else {
      body = error.message || body;
    }
  }
  return (
    <Frame
      role="alert"
      className={className}
      icon={icon}
      title={title}
      body={body || undefined}
      action={
        onRetry ? (
          <Button variant="secondary" icon={<RotateCw className="h-4 w-4" />} onClick={onRetry}>
            {t.common.retry}
          </Button>
        ) : undefined
      }
    />
  );
}

/**
 * Clearly labelled development placeholder. Use ONLY where the backend or feature does not
 * exist yet. Never present it as working functionality.
 */
export function DevPlaceholder({ title, phase, children }: { title: string; phase: string; children?: React.ReactNode }) {
  return (
    <div className="rounded-xl border-2 border-dashed border-warning-600/40 bg-warning-50/60 p-6" data-placeholder="true">
      <div className="flex items-start gap-3">
        <Construction className="mt-0.5 h-5 w-5 shrink-0 text-warning-700" aria-hidden />
        <div>
          <p className="text-xs font-bold tracking-wider text-warning-700">
            {t.common.devPlaceholder} · {phase}
          </p>
          <h3 className="mt-1 text-base font-semibold text-navy-900">{title}</h3>
          {children ? <div className="mt-2 text-sm text-muted">{children}</div> : null}
        </div>
      </div>
    </div>
  );
}
