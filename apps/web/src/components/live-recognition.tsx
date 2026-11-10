"use client";

import { Radio } from "lucide-react";
import { useEffect, useState } from "react";
import { formatTime } from "@/lib/format";
import { DECISION, QUALITY_REASON } from "@/lib/labels";
import type { LiveObservation, LiveResponse } from "@/lib/types";
import { useApiQuery } from "@/lib/use-api";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Card, CardHeader } from "@/components/ui/card";
import { FilterBar } from "@/components/ui/controls";
import { Field, Select } from "@/components/ui/form";
import { EmptyState, ErrorState } from "@/components/ui/states";
import { DataTable } from "@/components/ui/table";

const POLL_MS = 2000;
const LOW_QUALITY = { label: "Sifat past", tone: "neutral" as const };

const RECOGNITION_STATE: Record<string, string> = {
  active: "ishlayapti",
  starting: "ishga tushmoqda",
  waiting_for_frames: "kadr kutilmoqda",
  error: "xato",
};

/** What the recognition service sees right now. Observation only: nothing is written to attendance. */
export function LiveRecognition() {
  const [cameraId, setCameraId] = useState("");
  const q = useApiQuery<LiveResponse>(`/recognition/live${cameraId ? `?camera_id=${cameraId}` : ""}`);
  const { reload } = q;

  useEffect(() => {
    const id = setInterval(() => {
      if (document.visibilityState === "visible") reload();
    }, POLL_MS);
    return () => clearInterval(id);
  }, [reload]);

  const cams = q.data?.cameras ?? [];
  const rows = [...(q.data?.observations ?? [])].sort((a, b) => b.seq - a.seq).slice(0, 50);

  return (
    <Card className="mb-6">
      <CardHeader
        title={
          <span className="flex items-center gap-2">
            <Radio className="h-4 w-4 text-danger-600" aria-hidden /> Jonli tanish
          </span>
        }
        description="Har 2 soniyada yangilanadi. Suratlar saqlanmaydi. Davomatga yozilmaydi."
      />
      {q.error ? <div className="p-5"><ErrorState error={q.error} onRetry={q.reload} /></div> : null}
      {q.data && cams.length === 0 ? (
        <div className="p-5">
          <Alert tone="info" title="Hech bir kamerada tanish yoqilmagan">
            Kameralar sahifasida kamerani tanlang: monitoringni yoqing, sifat testidan oʻting va “Yuzni tanishga ruxsat berish”
            tugmasini bosing. Tanish xizmati (<code className="font-mono">npm run rec:dev</code>) ham ishlab turishi kerak.
          </Alert>
        </div>
      ) : null}
      {cams.length > 0 ? (
        <FilterBar>
          <Field label="Kamera" className="sm:w-64">
            <Select value={cameraId} onChange={(e) => setCameraId(e.target.value)}>
              <option value="">Barchasi</option>
              {cams.map((c) => <option key={c.camera_id} value={c.camera_id}>{c.name}</option>)}
            </Select>
          </Field>
          <div className="flex flex-wrap gap-3 text-xs text-muted sm:self-end sm:pb-2">
            {cams.map((c) => (
              <span key={c.camera_id} title={c.last_error ?? undefined}>
                {c.name}: {RECOGNITION_STATE[c.recognition] ?? c.recognition}
                {c.latency_ms ? ` · ${Math.round(c.latency_ms)} ms` : ""}
              </span>
            ))}
          </div>
        </FilterBar>
      ) : null}
      {cams.length > 0 ? (
        <DataTable<LiveObservation>
          caption="Jonli tanish natijalari"
          loading={q.loading && !q.data}
          rows={rows}
          rowKey={(o) => String(o.seq)}
          empty={<EmptyState title="Hozircha yuz koʻrinmadi" body="Kamera oldida turing." />}
          columns={[
            { key: "t", header: "Vaqt", cell: (o) => formatTime(o.last_seen) },
            {
              key: "d",
              header: "Natija",
              cell: (o) => {
                const l = o.decision === "LOW_QUALITY" ? LOW_QUALITY : DECISION[o.decision];
                return <Badge tone={l.tone}>{l.label}</Badge>;
              },
            },
            {
              key: "who",
              header: "Kim",
              cell: (o) =>
                o.employee ? (
                  <span className="font-medium text-navy-900">{o.employee.full_name} <span className="text-xs text-muted">{o.employee.employee_code}</span></span>
                ) : o.decision === "AMBIGUOUS" ? (
                  <span className="text-muted">Aniq emas: avtomatik aniqlanmaydi</span>
                ) : o.decision === "LOW_QUALITY" ? (
                  <span className="text-muted">{o.quality_reasons.map((r) => QUALITY_REASON[r] ?? r).join(", ") || "—"}</span>
                ) : (
                  <span className="text-muted">Roʻyxatda yoʻq</span>
                ),
            },
            {
              key: "s",
              header: "Oʻxshashlik",
              align: "right",
              hideOnMobile: true,
              cell: (o) => (o.score !== null && o.decision !== "LOW_QUALITY" ? <span className="tabular-nums">{o.score.toFixed(2)}</span> : "—"),
            },
            { key: "n", header: "Marta", align: "right", hideOnMobile: true, cell: (o) => o.count },
            {
              key: "cam",
              header: "Kamera",
              hideOnMobile: true,
              cell: (o) => cams.find((c) => c.camera_id === o.camera_id)?.name ?? "—",
            },
          ]}
        />
      ) : null}
    </Card>
  );
}
