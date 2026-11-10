"use client";

import { ScanFace, X } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect } from "react";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/cn";
import { t } from "@/lib/i18n";
import { isActive, visibleNav } from "@/components/layout/nav";

const isDev = process.env.NODE_ENV === "development";

function Brand() {
  return (
    <div className="flex h-16 items-center gap-3 px-5">
      <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-brand-600 text-white shadow-sm">
        <ScanFace className="h-5 w-5" aria-hidden />
      </span>
      <div className="leading-tight">
        <p className="text-sm font-semibold text-white">{t.app.name}</p>
        <p className="text-xs text-navy-300">{t.app.tagline}</p>
      </div>
    </div>
  );
}

function Links({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = usePathname();
  const { can } = useAuth();
  return (
    <nav className="flex-1 space-y-6 overflow-y-auto px-3 py-4" aria-label="Asosiy menyu">
      {visibleNav(can, isDev).map((section) => (
        <div key={section.title}>
          <p className="px-3 pb-2 text-[11px] font-semibold tracking-wider text-navy-400 uppercase">
            {section.title}
          </p>
          <ul className="space-y-0.5">
            {section.items.map((item) => {
              const active = isActive(item, pathname);
              const Icon = item.icon;
              return (
                <li key={item.href}>
                  <Link
                    href={item.href}
                    onClick={onNavigate}
                    aria-current={active ? "page" : undefined}
                    className={cn(
                      "group flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors",
                      active
                        ? "bg-navy-800 text-white shadow-sm"
                        : "text-navy-200 hover:bg-navy-900 hover:text-white",
                    )}
                  >
                    <Icon
                      aria-hidden
                      className={cn("h-[18px] w-[18px]", active ? "text-brand-200" : "text-navy-400 group-hover:text-navy-200")}
                    />
                    {item.label}
                  </Link>
                </li>
              );
            })}
          </ul>
        </div>
      ))}
    </nav>
  );
}

/** Fixed sidebar on desktop (lg+), slide-in drawer on smaller screens. */
export function Sidebar({ mobileOpen, onClose }: { mobileOpen: boolean; onClose: () => void }) {
  useEffect(() => {
    if (!mobileOpen) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [mobileOpen, onClose]);

  return (
    <>
      <aside className="fixed inset-y-0 left-0 z-30 hidden w-64 flex-col bg-navy-950 lg:flex">
        <Brand />
        <Links />
        <p className="px-5 py-4 text-[11px] text-navy-500">v0.1.0</p>
      </aside>

      {mobileOpen ? (
        <div className="fixed inset-0 z-40 lg:hidden" role="dialog" aria-modal="true" aria-label={t.common.menu}>
          <div className="absolute inset-0 animate-fade-in bg-navy-950/60" onClick={onClose} aria-hidden />
          <aside className="relative flex h-full w-72 max-w-[85vw] animate-slide-in-right flex-col bg-navy-950 shadow-[var(--shadow-overlay)]">
            <div className="flex items-center justify-between pr-3">
              <Brand />
              <button
                type="button"
                onClick={onClose}
                aria-label={t.common.close}
                className="rounded-md p-2 text-navy-300 hover:bg-navy-900 hover:text-white"
              >
                <X className="h-5 w-5" />
              </button>
            </div>
            <Links onNavigate={onClose} />
          </aside>
        </div>
      ) : null}
    </>
  );
}
