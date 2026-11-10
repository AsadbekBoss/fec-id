"use client";

import { Card } from "@/components/ui/card";
import { ErrorState } from "@/components/ui/states";

/** Error boundary for authenticated pages. Next.js 16 passes `retry` (re-fetch + re-render). */
export default function AppError({ error, retry }: { error: Error & { digest?: string }; retry: () => void }) {
  return (
    <Card>
      <ErrorState error={error} onRetry={retry} />
    </Card>
  );
}
