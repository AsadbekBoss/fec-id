"use client";

import { Building, ChevronDown, LogOut, Menu, ShieldCheck } from "lucide-react";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { useAuth } from "@/lib/auth";
import { formatDate, formatTime } from "@/lib/format";
import { t } from "@/lib/i18n";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { findNavItem } from "@/components/layout/nav";
import { BranchSwitcher } from "@/components/branch-switcher";

function UserMenu() {
  const { user, logout, sessionExpiresAt, homeBranch } = useAuth();
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  if (!user) return null;
  const initials = user.username.slice(0, 2).toUpperCase();

  const doLogout = async () => {
    setBusy(true);
    try {
      await logout();
    } finally {
      router.replace("/login");
    }
  };

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="menu"
        aria-expanded={open}
        className="flex items-center gap-2 rounded-lg py-1.5 pr-2 pl-1.5 hover:bg-navy-50"
      >
        <span className="flex h-8 w-8 items-center justify-center rounded-full bg-navy-800 text-xs font-semibold text-white">
          {initials}
        </span>
        <span className="hidden text-left leading-tight sm:block">
          <span className="block text-sm font-medium text-navy-900">{user.username}</span>
          <span className="block text-xs text-muted">{t.roles[user.role] ?? user.role}</span>
        </span>
        <ChevronDown className="h-4 w-4 text-muted" aria-hidden />
      </button>
      {open ? (
        <div
          role="menu"
          className="absolute right-0 z-40 mt-2 w-64 animate-fade-in rounded-xl border border-line bg-surface p-2 shadow-[var(--shadow-overlay)]"
        >
          <div className="border-b border-line px-3 pt-1 pb-3">
            <p className="text-sm font-semibold text-navy-900">{user.username}</p>
            <div className="mt-1">
              <Badge tone="brand">
                <ShieldCheck className="h-3 w-3" aria-hidden />
                {t.roles[user.role] ?? user.role}
              </Badge>
            </div>
            {homeBranch ? (
              <p className="mt-2 flex items-center gap-1 text-xs text-muted">
                <Building className="h-3 w-3" aria-hidden /> {homeBranch.name}
              </p>
            ) : null}
            {sessionExpiresAt ? (
              <p className="mt-2 text-xs text-muted">
                Sessiya muddati: {formatDate(sessionExpiresAt)} {formatTime(sessionExpiresAt)}
              </p>
            ) : null}
          </div>
          <Button
            role="menuitem"
            variant="ghost"
            className="mt-1 w-full justify-start text-danger-700 hover:bg-danger-50"
            icon={<LogOut className="h-4 w-4" />}
            loading={busy}
            onClick={doLogout}
          >
            {t.auth.logout}
          </Button>
        </div>
      ) : null}
    </div>
  );
}

export function Topbar({ onMenu }: { onMenu: () => void }) {
  const pathname = usePathname();
  const current = findNavItem(pathname);
  return (
    <header className="sticky top-0 z-20 flex h-16 items-center gap-3 border-b border-line bg-surface/95 px-4 backdrop-blur sm:px-6">
      <Button variant="ghost" size="icon" className="lg:hidden" aria-label={t.common.menu} onClick={onMenu}>
        <Menu className="h-5 w-5" />
      </Button>
      <p className="truncate text-sm font-medium text-muted">{current?.label ?? t.app.name}</p>
      <div className="ml-auto flex items-center gap-2">
        <BranchSwitcher />
        <UserMenu />
      </div>
    </header>
  );
}
