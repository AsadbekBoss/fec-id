"use client";

import { Check, ChevronsUpDown, ImageOff, X } from "lucide-react";
import { useEffect, useId, useMemo, useRef, useState } from "react";
import { api } from "@/lib/api";
import { cn } from "@/lib/cn";
import { BIOMETRIC_STATUS, DAY_STATUS, EMPLOYEE_STATUS } from "@/lib/labels";
import type { BiometricStatus, DayStatus, Department, Employee, EmployeeStatus, Page, WorkSchedule } from "@/lib/types";
import { useApiQuery } from "@/lib/use-api";
import { Badge } from "@/components/ui/badge";
import { Select } from "@/components/ui/form";
import { Skeleton } from "@/components/ui/spinner";
import { DevPlaceholder } from "@/components/ui/states";

// --- badges -----------------------------------------------------------------------------------

export function EmployeeStatusBadge({ status }: { status: EmployeeStatus }) {
  const s = EMPLOYEE_STATUS[status];
  return <Badge tone={s.tone}>{s.label}</Badge>;
}

export function DayStatusBadge({ status, inProgress }: { status: DayStatus; inProgress?: boolean }) {
  const s = DAY_STATUS[status];
  return (
    <Badge tone={s.tone}>
      {s.label}
      {inProgress ? " · ishda" : ""}
    </Badge>
  );
}

export function BiometricBadge({ status }: { status: BiometricStatus }) {
  const s = BIOMETRIC_STATUS[status];
  return (
    <Badge tone={s.tone}>
      <span title={s.hint}>{s.label}</span>
    </Badge>
  );
}

// --- departments ------------------------------------------------------------------------------

export type DeptOption = Department & { depth: number; path: string };

/** Flatten the department list into tree order with depth (for indented selects / tables). */
export function treeOrder(depts: Department[]): DeptOption[] {
  const children = new Map<string | null, Department[]>();
  for (const d of depts) {
    const key = d.parent_id && depts.some((p) => p.id === d.parent_id) ? d.parent_id : null;
    children.set(key, [...(children.get(key) ?? []), d]);
  }
  const out: DeptOption[] = [];
  const walk = (parent: string | null, depth: number, prefix: string) => {
    for (const d of (children.get(parent) ?? []).sort((a, b) => a.name.localeCompare(b.name, "uz"))) {
      const path = prefix ? `${prefix} / ${d.name}` : d.name;
      out.push({ ...d, depth, path });
      if (depth < 20) walk(d.id, depth + 1, path);
    }
  };
  walk(null, 0, "");
  return out;
}

export function useDepartments() {
  const q = useApiQuery<Department[]>("/departments");
  const tree = useMemo(() => treeOrder(q.data ?? []), [q.data]);
  return { ...q, tree };
}

export function DepartmentSelect({
  value,
  onChange,
  allLabel,
  exclude,
  ...rest
}: {
  value: string;
  onChange: (v: string) => void;
  allLabel?: string;
  exclude?: string;
} & Omit<React.SelectHTMLAttributes<HTMLSelectElement>, "value" | "onChange">) {
  const { tree, loading } = useDepartments();
  return (
    <Select value={value} onChange={(e) => onChange(e.target.value)} disabled={loading || rest.disabled} {...rest}>
      <option value="">{allLabel ?? "Tanlang…"}</option>
      {tree
        .filter((d) => d.id !== exclude)
        .map((d) => (
          <option key={d.id} value={d.id}>
            {"  ".repeat(d.depth)}
            {d.name}
          </option>
        ))}
    </Select>
  );
}

export function ScheduleSelect({
  value,
  onChange,
  emptyLabel = "Jadvalsiz (boʻlim jadvali)",
  ...rest
}: {
  value: string;
  onChange: (v: string) => void;
  emptyLabel?: string;
} & Omit<React.SelectHTMLAttributes<HTMLSelectElement>, "value" | "onChange">) {
  const { data, loading } = useApiQuery<WorkSchedule[]>("/work-schedules");
  return (
    <Select value={value} onChange={(e) => onChange(e.target.value)} disabled={loading || rest.disabled} {...rest}>
      <option value="">{emptyLabel}</option>
      {(data ?? []).map((s) => (
        <option key={s.id} value={s.id}>
          {s.name} ({s.start_time.slice(0, 5)}–{s.end_time.slice(0, 5)}){s.is_active ? "" : " · nofaol"}
        </option>
      ))}
    </Select>
  );
}

// --- employee picker (async combobox) ---------------------------------------------------------

export type PickedEmployee = { id: string; full_name: string; employee_code: string };

export function EmployeePicker({
  value,
  onChange,
  placeholder = "Xodimni qidiring…",
  id,
}: {
  value: PickedEmployee | null;
  onChange: (e: PickedEmployee | null) => void;
  placeholder?: string;
  id?: string;
}) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [items, setItems] = useState<Employee[]>([]);
  const [loading, setLoading] = useState(false);
  const [active, setActive] = useState(0);
  const listId = useId();
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    let alive = true;
    const timer = setTimeout(() => {
      setLoading(true);
      api
        .get<Page<Employee>>(`/employees?page_size=10${q ? `&q=${encodeURIComponent(q)}` : ""}`)
        .then((p) => alive && (setItems(p.items), setActive(0)))
        .catch(() => alive && setItems([]))
        .finally(() => alive && setLoading(false));
    }, 250);
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [q, open]);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => box.current && !box.current.contains(e.target as Node) && setOpen(false);
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  const choose = (e: Employee) => {
    onChange({ id: e.id, full_name: e.full_name, employee_code: e.employee_code });
    setOpen(false);
    setQ("");
  };

  if (value && !open) {
    return (
      <div className="flex h-10 items-center justify-between rounded-lg border border-line bg-surface px-3 text-sm shadow-sm">
        <span className="truncate">
          {value.full_name} <span className="font-mono text-xs text-muted">{value.employee_code}</span>
        </span>
        <button type="button" aria-label="Tanlovni bekor qilish" onClick={() => onChange(null)} className="rounded p-1 text-muted hover:text-ink">
          <X className="h-4 w-4" />
        </button>
      </div>
    );
  }

  return (
    <div ref={box} className="relative">
      <input
        id={id}
        role="combobox"
        aria-expanded={open}
        aria-controls={listId}
        aria-autocomplete="list"
        value={q}
        placeholder={placeholder}
        onFocus={() => setOpen(true)}
        onChange={(e) => {
          setQ(e.target.value);
          setOpen(true);
        }}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") setActive((a) => Math.min(a + 1, items.length - 1));
          else if (e.key === "ArrowUp") setActive((a) => Math.max(a - 1, 0));
          else if (e.key === "Enter" && items[active]) {
            e.preventDefault();
            choose(items[active]);
          } else if (e.key === "Escape") setOpen(false);
        }}
        className="h-10 w-full rounded-lg border border-line bg-surface pr-9 pl-3 text-sm shadow-sm focus:border-brand-500 focus:ring-2 focus:ring-brand-500/30 focus:outline-none"
      />
      <ChevronsUpDown className="pointer-events-none absolute top-1/2 right-3 h-4 w-4 -translate-y-1/2 text-muted" aria-hidden />
      {open ? (
        <ul id={listId} role="listbox" className="absolute z-30 mt-1 max-h-64 w-full overflow-auto rounded-lg border border-line bg-surface py-1 shadow-[var(--shadow-overlay)]">
          {loading ? (
            <li className="px-3 py-2 text-sm text-muted">Qidirilmoqda…</li>
          ) : items.length === 0 ? (
            <li className="px-3 py-2 text-sm text-muted">Topilmadi</li>
          ) : (
            items.map((e, i) => (
              <li
                key={e.id}
                role="option"
                aria-selected={i === active}
                onMouseDown={(ev) => {
                  ev.preventDefault();
                  choose(e);
                }}
                onMouseEnter={() => setActive(i)}
                className={cn("flex cursor-pointer items-center justify-between px-3 py-2 text-sm", i === active && "bg-navy-50")}
              >
                <span>
                  {e.full_name} <span className="font-mono text-xs text-muted">{e.employee_code}</span>
                </span>
                {value?.id === e.id ? <Check className="h-4 w-4 text-brand-600" /> : null}
              </li>
            ))
          )}
        </ul>
      ) : null}
    </div>
  );
}

// --- authenticated image ----------------------------------------------------------------------

/**
 * Loads an image through the API client (Bearer token) and shows it via an object URL.
 * A plain <img src> cannot send the in-memory access token.
 */
export function AuthImage({
  path,
  alt,
  className,
  version,
}: {
  path: string | null;
  alt: string;
  className?: string;
  /** Change to force a reload (e.g. photo_updated_at). */
  version?: string | null;
}) {
  const [url, setUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (!path) return;
    let objectUrl: string | null = null;
    let alive = true;
    api
      .blob(path)
      .then((b) => {
        if (!alive) return;
        objectUrl = URL.createObjectURL(b);
        setUrl(objectUrl);
        setFailed(false);
      })
      .catch(() => alive && setFailed(true));
    return () => {
      alive = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [path, version]);

  if (!path || failed) {
    return (
      <div className={cn("flex items-center justify-center bg-navy-50 text-navy-300", className)}>
        <ImageOff className="h-8 w-8" aria-hidden />
        <span className="sr-only">{alt}</span>
      </div>
    );
  }
  if (!url) return <Skeleton className={className} />;
  // eslint-disable-next-line @next/next/no-img-element -- blob: URL from an authenticated fetch
  return <img src={url} alt={alt} className={cn("object-cover", className)} />;
}

export function Initials({ name, className }: { name: string; className?: string }) {
  const letters = name
    .split(" ")
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0])
    .join("")
    .toUpperCase();
  return (
    <span className={cn("flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-navy-100 text-xs font-semibold text-navy-700", className)}>
      {letters}
    </span>
  );
}

// --- missing backend dependency ---------------------------------------------------------------

/** Documents that a feature waits for backend endpoints that do not exist yet. */
export function MissingApi({
  title,
  phase,
  endpoints,
  contract,
  children,
}: {
  title: string;
  phase: string;
  endpoints: string[];
  contract: string;
  children?: React.ReactNode;
}) {
  return (
    <DevPlaceholder title={title} phase={phase}>
      {children ? <p>{children}</p> : null}
      <p className="mt-2 font-medium text-navy-800">Kutilayotgan server API ({contract}, hali amalga oshirilmagan):</p>
      <ul className="mt-1 space-y-0.5 font-mono text-xs text-navy-700">
        {endpoints.map((e) => (
          <li key={e}>{e}</li>
        ))}
      </ul>
    </DevPlaceholder>
  );
}
