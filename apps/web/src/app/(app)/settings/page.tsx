"use client";

import { useAuth } from "@/lib/auth";
import { formatDate, formatTime } from "@/lib/format";
import { ROLE } from "@/lib/labels";
import type { Setting } from "@/lib/types";
import { useApiQuery } from "@/lib/use-api";
import { MissingApi } from "@/components/entities";
import { Badge } from "@/components/ui/badge";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { DetailItem, RequirePermission } from "@/components/ui/controls";
import { PageHeader } from "@/components/ui/page-header";
import { DataTable } from "@/components/ui/table";

const GROUPS: Record<string, string> = {
  recognition: "Yuzni tanish",
  attendance: "Davomat",
  retention: "Maʼlumotlarni saqlash muddati",
  security: "Xavfsizlik",
  biometric: "Biometriya",
};

function renderValue(v: unknown): string {
  if (Array.isArray(v)) return v.join(", ");
  if (typeof v === "boolean") return v ? "Ha" : "Yoʻq";
  if (v === null) return "—";
  return String(v);
}

export default function SettingsPage() {
  const { user, sessionExpiresAt, permissions } = useAuth();
  return (
    <>
      <PageHeader title="Sozlamalar" description="Tizim sozlamalari va hisobingiz" />
      <div className="grid gap-6 lg:grid-cols-3">
        <Card>
          <CardHeader title="Mening hisobim" />
          <CardBody>
            <dl className="space-y-4">
              <DetailItem label="Login">{user?.username}</DetailItem>
              <DetailItem label="Rol">{user ? <Badge tone={ROLE[user.role].tone}>{ROLE[user.role].label}</Badge> : null}</DetailItem>
              <DetailItem label="Sessiya tugashi">
                {sessionExpiresAt ? `${formatDate(sessionExpiresAt)} ${formatTime(sessionExpiresAt)}` : "—"}
              </DetailItem>
              <DetailItem label="Ruxsatlar">{[...permissions].sort().join(", ")}</DetailItem>
            </dl>
          </CardBody>
        </Card>
        <div className="space-y-6 lg:col-span-2">
          <RequirePermission permission="settings:read">
            <SystemSettings />
          </RequirePermission>
        </div>
      </div>
    </>
  );
}

function SystemSettings() {
  const settings = useApiQuery<Setting[]>("/settings");
  return (
    <>
      <Card>
        <CardHeader title="Tizim sozlamalari" description="Faqat oʻqish. Oʻzgartirish API hali mavjud emas." />
        <DataTable<Setting>
          caption="Tizim sozlamalari"
          loading={settings.loading}
          error={settings.error}
          onRetry={settings.reload}
          rows={settings.data}
          rowKey={(s) => s.key}
          columns={[
            {
              key: "k",
              header: "Sozlama",
              cell: (s) => (
                <div className="max-w-md whitespace-normal">
                  <p className="text-xs text-muted">{GROUPS[s.key.split(".")[0]] ?? s.key.split(".")[0]}</p>
                  <p className="font-mono text-xs text-navy-900">{s.key}</p>
                  {s.description ? <p className="mt-0.5 text-xs text-muted">{s.description}</p> : null}
                </div>
              ),
            },
            { key: "v", header: "Qiymat", cell: (s) => <span className="font-mono text-sm">{renderValue(s.value)}</span> },
          ]}
        />
      </Card>
      <MissingApi
        title="Sozlamalarni oʻzgartirish"
        phase="Sozlamalar bosqichi"
        contract="API_CONTRACTS §2.11"
        endpoints={["PATCH /api/v1/settings"]}
      >
        Hozircha qiymatlar faqat koʻrsatiladi. Ularni oʻzgartirish endpointi va tekshiruvlari hali yoʻq.
        Sessiya muddati va login cheklovlari esa server muhitida (.env) sozlanadi.
      </MissingApi>
    </>
  );
}
