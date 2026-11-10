import { Suspense } from "react";
import { AppShell } from "@/components/layout/app-shell";
import { LoadingState } from "@/components/ui/states";

/**
 * Authenticated pages render only in the browser: the access token lives in memory, so the
 * server cannot prerender them. Opt this subtree out of Next.js instant-navigation /
 * static-shell validation. Public routes (/login, 404) keep validating.
 */
export const instant = false;

export default function AuthenticatedLayout({ children }: { children: React.ReactNode }) {
  // Suspense: on dynamic routes (e.g. /employees/[id]) the pathname used by the shell is only
  // known at request time.
  return (
    <Suspense fallback={<LoadingState />}>
      <AppShell>{children}</AppShell>
    </Suspense>
  );
}
