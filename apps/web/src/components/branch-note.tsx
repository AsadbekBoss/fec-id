"use client";

import { useAuth } from "@/lib/auth";
import type { Branch } from "@/lib/types";
import { useApiQuery } from "@/lib/use-api";
import { Alert } from "@/components/ui/alert";

/**
 * For the super administrator in "every branch" mode: new records of this page go to the main
 * branch unless a branch is chosen in the top bar. Nothing is shown with a single branch.
 */
export function BranchScopeNote({ what }: { what: string }) {
  const { allBranches, branchId } = useAuth();
  const q = useApiQuery<Branch[]>(allBranches && !branchId ? "/branches" : null);
  if (!allBranches || branchId || (q.data?.length ?? 0) <= 1) return null;
  const main = q.data?.find((b) => b.is_main)?.name ?? "asosiy";
  return (
    <Alert className="mb-4">
      Hozir <b>barcha filiallar</b> koʻrsatilmoqda. Yangi {what} <b>{main}</b> filialiga qoʻshiladi. Boshqa filial uchun
      yuqoridagi tanlagichdan uni tanlang.
    </Alert>
  );
}
