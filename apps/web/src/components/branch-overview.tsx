"use client";

import { ArrowRight } from "lucide-react";
import { useAuth } from "@/lib/auth";
import { todayInOrg } from "@/lib/format";
import type { BranchOverview } from "@/lib/types";
import { useApiQuery } from "@/lib/use-api";
import { Badge } from "@/components/ui/badge";
import { Card, CardHeader } from "@/components/ui/card";
import { ErrorState } from "@/components/ui/states";
import { DataTable } from "@/components/ui/table";

type Row = BranchOverview["branches"][number];

const n = (t: Record<string, number>, ...keys: string[]) => keys.reduce((s, k) => s + (t[k] ?? 0), 0);

/** Today's attendance of every branch (super administrator). A row opens that branch. */
export function BranchOverviewCard({ title = "Filiallar boʻyicha bugun" }: { title?: string }) {
  const { setBranchId } = useAuth();
  const today = todayInOrg();
  const q = useApiQuery<BranchOverview>(`/branches/overview?date=${today}`);

  return (
    <Card className="mb-6">
      <CardHeader
        title={title}
        description="Qatorni bossangiz, butun sayt shu filialni koʻrsatadi (yuqoridagi tanlagich bilan qaytasiz)."
      />
      {q.error ? (
        <ErrorState error={q.error} onRetry={q.reload} className="m-4" />
      ) : (
        <DataTable<Row>
          rows={q.data?.branches}
          loading={q.loading}
          rowKey={(r) => r.branch.id}
          onRowClick={(r) => setBranchId(r.branch.id)}
          empty="Filiallar yoʻq"
          columns={[
            {
              key: "name",
              header: "Filial",
              cell: (r) => (
                <span className="flex items-center gap-2 font-medium text-navy-900">
                  {r.branch.name}
                  {!r.branch.is_active ? <Badge tone="warning">Yopilgan</Badge> : null}
                </span>
              ),
            },
            { key: "emp", header: "Xodimlar", cell: (r) => r.totals.EMPLOYEES ?? 0 },
            {
              key: "came",
              header: "Keldi",
              cell: (r) => (
                <span className="font-medium text-success-700">{n(r.totals, "PRESENT", "LATE", "INCOMPLETE")}</span>
              ),
            },
            {
              key: "late",
              header: "Kechikdi",
              cell: (r) => <span className="text-warning-700">{n(r.totals, "LATE")}</span>,
            },
            {
              key: "absent",
              header: "Kelmadi",
              hideOnMobile: true,
              cell: (r) => <span className="text-danger-700">{n(r.totals, "ABSENT")}</span>,
            },
            {
              key: "pending",
              header: "Kutilmoqda",
              hideOnMobile: true,
              cell: (r) => n(r.totals, "PENDING"),
            },
            {
              key: "go",
              header: "",
              cell: () => <ArrowRight className="h-4 w-4 text-muted" aria-hidden />,
            },
          ]}
        />
      )}
    </Card>
  );
}
