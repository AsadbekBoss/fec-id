"use client";

import { RefreshCw } from "lucide-react";
import { useEffect } from "react";
import { formatDate, formatTime } from "@/lib/format";
import { CHECK_STATUS, SERVICE_STATE, checkSummary, formatDuration, type SystemStatus } from "@/lib/system";
import { useApiQuery } from "@/lib/use-api";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardHeader } from "@/components/ui/card";
import { RequirePermission } from "@/components/ui/controls";
import { PageHeader } from "@/components/ui/page-header";
import { ErrorState, LoadingState } from "@/components/ui/states";
import { DataTable } from "@/components/ui/table";

const POLL_MS = 10_000;

export default function SystemPage() {
  return (
    <RequirePermission permission="branches:manage">
      <SystemHealth />
    </RequirePermission>
  );
}

const OVERALL = {
  ok: { tone: "success", title: "Hammasi ishlayapti" },
  warning: { tone: "warning", title: "Eʼtibor talab qiladigan holat bor" },
  error: { tone: "danger", title: "Tizimning bir qismi ishlamayapti" },
} as const;

function when(iso: string | null | undefined): string {
  return iso ? `${formatDate(iso)} ${formatTime(iso)}` : "—";
}

function SystemHealth() {
  const q = useApiQuery<SystemStatus>("/system/status");
  const { reload } = q;

  useEffect(() => {
    const id = setInterval(() => document.visibilityState === "visible" && reload(), POLL_MS);
    return () => clearInterval(id);
  }, [reload]);

  const s = q.data;
  const sup = s?.supervisor;

  return (
    <>
      <PageHeader
        title="Tizim holati"
        description="Baza, xizmatlar, zaxira va disk. Har 10 soniyada yangilanadi."
        actions={
          <Button variant="secondary" size="sm" icon={<RefreshCw className="h-4 w-4" />} onClick={reload} loading={q.loading && !!s}>
            Yangilash
          </Button>
        }
      />
      {q.error && !s ? <ErrorState error={q.error} onRetry={reload} /> : null}
      {!s && !q.error ? <LoadingState /> : null}
      {s ? (
        <>
          <Alert tone={OVERALL[s.status].tone} title={OVERALL[s.status].title} className="mb-6">
            Tekshirildi: {formatTime(s.checked_at)} · API {formatDuration(s.api_uptime_seconds)} dan beri ishlayapti · versiya {s.version}
          </Alert>

          <Card className="mb-6">
            <CardHeader title="Tekshiruvlar" description="Har bir qismning hozirgi holati" />
            <ul className="divide-y divide-line">
              {s.checks.map((c) => (
                <li key={c.name} className="flex flex-col gap-1 px-5 py-3 sm:flex-row sm:items-center sm:gap-4">
                  <span className="font-medium text-navy-900 sm:w-56">{c.title}</span>
                  <span className="sm:w-36">
                    <Badge tone={CHECK_STATUS[c.status].tone}>{CHECK_STATUS[c.status].label}</Badge>
                  </span>
                  <span className="min-w-0 flex-1 text-sm text-muted">
                    {[c.detail, checkSummary(c)].filter(Boolean).join(" · ") || "—"}
                    {c.name === "backup" && typeof c.info.last_at === "string" ? ` · ${when(c.info.last_at)}` : ""}
                  </span>
                  {c.latency_ms !== null ? <span className="text-xs tabular-nums text-muted">{Math.round(c.latency_ms)} ms</span> : null}
                </li>
              ))}
            </ul>
          </Card>

          <Card className="mb-6">
            <CardHeader
              title="Jarayonlar"
              description={
                sup?.running
                  ? `Avtomatik boshqaruv ishlayapti (${sup.mode === "dev" ? "ishlab chiqish" : "ish"} rejimi), ${when(sup.started_at)} dan beri. Toʻxtagan xizmat oʻzi qayta yoqiladi.`
                  : "Xizmatlar avtomatik boshqaruvsiz ishga tushirilgan"
              }
            />
            {sup?.running ? (
              <DataTable
                caption="Jarayonlar"
                rows={sup.services}
                rowKey={(p) => p.name}
                columns={[
                  { key: "t", header: "Xizmat", cell: (p) => <span className="font-medium text-navy-900">{p.title}</span> },
                  {
                    key: "s",
                    header: "Holat",
                    cell: (p) => {
                      const l = SERVICE_STATE[p.state] ?? { label: p.state, tone: "neutral" as const };
                      return <Badge tone={l.tone}>{l.label}</Badge>;
                    },
                  },
                  { key: "since", header: "Ishga tushgan", hideOnMobile: true, cell: (p) => when(p.started_at) },
                  { key: "r", header: "Qayta yoqilgan", align: "right", cell: (p) => <span className="tabular-nums">{p.restarts}</span> },
                  {
                    key: "e",
                    header: "Oxirgi xato",
                    hideOnMobile: true,
                    cell: (p) => (p.last_error ? <span className="text-sm text-danger-700">{p.last_error}</span> : <span className="text-muted">—</span>),
                  },
                  { key: "l", header: "Log", hideOnMobile: true, cell: (p) => <code className="font-mono text-xs">{p.log}</code> },
                ]}
              />
            ) : (
              <div className="p-5">
                <Alert tone="info" title="Bitta buyruq bilan ishga tushiring">
                  Terminalda <code className="font-mono">npm start</code> — hammasi bitta oynada, toʻxtagan xizmat oʻzi qayta yoqiladi.
                  Kompyuter yonganda oʻzi ishga tushishi uchun bir marta <code className="font-mono">npm run autostart</code>.
                </Alert>
              </div>
            )}
          </Card>
        </>
      ) : null}
    </>
  );
}
