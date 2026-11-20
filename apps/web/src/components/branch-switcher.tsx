"use client";

import { Building } from "lucide-react";
import { useAuth } from "@/lib/auth";
import type { Branch } from "@/lib/types";
import { useApiQuery } from "@/lib/use-api";

/**
 * Super administrator: choose which branch the whole app shows ("Barcha filiallar" = every
 * branch). Branch users see their branch name instead. The server enforces the scope; the
 * choice only sets the X-Branch-Id header.
 */
export function BranchSwitcher() {
  const { allBranches, homeBranch, branchId, setBranchId } = useAuth();
  const q = useApiQuery<Branch[]>(allBranches ? "/branches" : null);

  if (!allBranches) {
    return homeBranch ? (
      <span className="hidden items-center gap-1.5 rounded-lg bg-navy-50 px-2.5 py-1.5 text-sm font-medium text-navy-900 sm:inline-flex">
        <Building className="h-4 w-4 text-muted" aria-hidden />
        {homeBranch.name}
      </span>
    ) : null;
  }

  const branches = q.data ?? [];
  // A single branch needs no switcher.
  if (branches.length <= 1 && !branchId) return null;

  return (
    <label className="flex items-center gap-1.5">
      <Building className="h-4 w-4 shrink-0 text-muted" aria-hidden />
      <span className="sr-only">Filial</span>
      <select
        value={branchId ?? ""}
        onChange={(e) => setBranchId(e.target.value || null)}
        className="h-9 max-w-[11rem] rounded-lg border border-line bg-surface px-2 text-sm font-medium text-navy-900 focus:border-brand-500 focus:outline-none sm:max-w-[16rem]"
      >
        <option value="">Barcha filiallar</option>
        {branches.map((b) => (
          <option key={b.id} value={b.id}>
            {b.name}
            {b.is_active ? "" : " (yopilgan)"}
          </option>
        ))}
      </select>
    </label>
  );
}
