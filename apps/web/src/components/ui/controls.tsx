"use client";

import { Search, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/cn";
import { Card } from "@/components/ui/card";
import { ErrorState } from "@/components/ui/states";
import { ApiError } from "@/lib/api";

/** Search box that reports its value after the user pauses typing (debounced). */
export function SearchInput({
  value,
  onChange,
  placeholder = "Qidirish…",
  delay = 300,
  className,
  label = "Qidirish",
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  delay?: number;
  className?: string;
  label?: string;
}) {
  const [draft, setDraft] = useState(value);
  const first = useRef(true);

  useEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    const timer = setTimeout(() => onChange(draft.trim()), delay);
    return () => clearTimeout(timer);
  }, [draft, delay, onChange]);

  return (
    <div className={cn("relative", className)}>
      <Search className="pointer-events-none absolute top-1/2 left-3 h-4 w-4 -translate-y-1/2 text-muted" aria-hidden />
      <input
        type="search"
        aria-label={label}
        value={draft}
        maxLength={100}
        placeholder={placeholder}
        onChange={(e) => setDraft(e.target.value)}
        className="h-10 w-full rounded-lg border border-line bg-surface pr-9 pl-9 text-sm shadow-sm focus:border-brand-500 focus:ring-2 focus:ring-brand-500/30 focus:outline-none"
      />
      {draft ? (
        <button
          type="button"
          aria-label="Tozalash"
          onClick={() => setDraft("")}
          className="absolute top-1/2 right-2 -translate-y-1/2 rounded p-1 text-muted hover:text-ink"
        >
          <X className="h-4 w-4" />
        </button>
      ) : null}
    </div>
  );
}

/** Horizontal bar of filters that wraps on small screens. */
export function FilterBar({ children }: { children: React.ReactNode }) {
  return <div className="flex flex-col gap-3 border-b border-line p-4 sm:flex-row sm:flex-wrap sm:items-end">{children}</div>;
}

export function Tabs<T extends string>({
  value,
  onChange,
  tabs,
}: {
  value: T;
  onChange: (v: T) => void;
  tabs: { value: T; label: string }[];
}) {
  return (
    <div role="tablist" className="mb-4 flex gap-1 rounded-lg border border-line bg-surface p-1 shadow-sm sm:w-fit">
      {tabs.map((t) => (
        <button
          key={t.value}
          role="tab"
          type="button"
          aria-selected={value === t.value}
          onClick={() => onChange(t.value)}
          className={cn(
            "flex-1 rounded-md px-4 py-1.5 text-sm font-medium transition-colors sm:flex-none",
            value === t.value ? "bg-navy-800 text-white" : "text-navy-700 hover:bg-navy-50",
          )}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}

/** Shows the page only with the permission; otherwise a clear 403 panel (server enforces too). */
export function RequirePermission({ permission, children }: { permission: string; children: React.ReactNode }) {
  const { can } = useAuth();
  if (!can(permission)) {
    return (
      <Card>
        <ErrorState error={new ApiError(403, "FORBIDDEN", "")} />
      </Card>
    );
  }
  return <>{children}</>;
}

/** Small label/value pair for detail views. */
export function DetailItem({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <dt className="text-xs font-medium tracking-wide text-muted uppercase">{label}</dt>
      <dd className="mt-1 text-sm text-ink">{children ?? "—"}</dd>
    </div>
  );
}
