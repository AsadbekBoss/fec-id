"use client";

import { useState } from "react";
import { formatDate, formatTime } from "@/lib/format";
import { DECISION, IGNORE_REASON } from "@/lib/labels";
import { addDays, todayInOrg } from "@/lib/time";
import type { AttendanceEvent, Page } from "@/lib/types";
import { useApiQuery } from "@/lib/use-api";
import { useEmployeeNames } from "@/lib/use-employee-names";
import { useAuth } from "@/lib/auth";
import { MissingApi } from "@/components/entities";
import { LiveRecognition } from "@/components/live-recognition";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Card, CardHeader } from "@/components/ui/card";
import { FilterBar, RequirePermission } from "@/components/ui/controls";
import { Field, Input, Select } from "@/components/ui/form";
import { PageHeader } from "@/components/ui/page-header";
import { EmptyState } from "@/components/ui/states";
import { DataTable, Pagination } from "@/components/ui/table";

const PAGE = 50;

export default function RecognitionPage() {
  return (
    <RequirePermission permission="attendance:read">
      <Recognition />
    </RequirePermission>
  );
}

function Recognition() {
  const { can } = useAuth();
  const today = todayInOrg();
  const [from, setFrom] = useState(addDays(today, -6));
  const [to, setTo] = useState(today);
  const [counted, setCounted] = useState("");
  const [page, setPage] = useState(1);

  const q = new URLSearchParams({ source: "CAMERA", page: String(page), page_size: String(PAGE), work_date_from: from, work_date_to: to });
  if (counted) q.set("counted", counted);
  const events = useApiQuery<Page<AttendanceEvent>>(`/attendance/events?${q}`);
  const names = useEmployeeNames((events.data?.items ?? []).map((e) => e.employee_id));

  return (
    <>
      <PageHeader title="AI tanish" description="Kamera oldidagi odamlarni jonli tanish va davomat hodisalari" />
      <Alert tone="warning" className="mb-6" title="Tanish hali davomatga ulanmagan">
        Jonli tanish faqat koʻrsatadi: davomat yozilmaydi. Aniq boʻlmagan holatlar hech qachon avtomatik shaxsga bogʻlanmaydi.
        Davomatga ulash tanish sifati sinovi (EVALUATION_PROTOCOL.md) natijasi tasdiqlangandan keyin qilinadi.
      </Alert>

      {can("biometrics:read") ? <LiveRecognition /> : null}

      <Card className="mb-6">
        <CardHeader title="Tanish hodisalari jurnali" description="Hisoblanmagan hodisalar sababi bilan koʻrsatiladi" />
        <FilterBar>
          <Field label="Ish kuni (dan)" className="sm:w-40">
            <Input type="date" value={from} onChange={(e) => { setFrom(e.target.value); setPage(1); }} />
          </Field>
          <Field label="Ish kuni (gacha)" className="sm:w-40">
            <Input type="date" value={to} onChange={(e) => { setTo(e.target.value); setPage(1); }} />
          </Field>
          <Field label="Natija" className="sm:w-48">
            <Select value={counted} onChange={(e) => { setCounted(e.target.value); setPage(1); }}>
              <option value="">Barchasi</option>
              <option value="true">Davomatga hisoblangan</option>
              <option value="false">Hisoblanmagan</option>
            </Select>
          </Field>
        </FilterBar>
        <DataTable<AttendanceEvent>
          caption="Tanish hodisalari"
          loading={events.loading}
          error={events.error}
          onRetry={events.reload}
          rows={events.data?.items}
          rowKey={(e) => String(e.id)}
          empty={<EmptyState title="Kamera hodisalari yoʻq" body="Kameralar ulangach yoki hodisa yuborilgach shu yerda koʻrinadi." />}
          columns={[
            { key: "t", header: "Vaqt", cell: (e) => `${formatDate(e.event_time)} ${formatTime(e.event_time)}` },
            {
              key: "d",
              header: "Qaror",
              cell: (e) => (e.decision ? <Badge tone={DECISION[e.decision].tone}>{DECISION[e.decision].label}</Badge> : "—"),
            },
            { key: "emp", header: "Xodim", cell: (e) => (e.employee_id ? names[e.employee_id] : <span className="text-muted">—</span>) },
            {
              key: "sim",
              header: "Oʻxshashlik",
              align: "right",
              cell: (e) => (e.similarity !== null ? <span className="tabular-nums">{e.similarity.toFixed(2)}</span> : "—"),
              hideOnMobile: true,
            },
            { key: "dir", header: "Yoʻnalish", cell: (e) => (e.direction === "IN" ? "Kirish" : e.direction === "OUT" ? "Chiqish" : "—"), hideOnMobile: true },
            {
              key: "r",
              header: "Davomatga taʼsiri",
              cell: (e) =>
                e.is_voided ? (
                  <Badge>Bekor qilingan</Badge>
                ) : e.counts_for_attendance ? (
                  <Badge tone="success">Hisoblandi</Badge>
                ) : (
                  <Badge tone="warning">{IGNORE_REASON[e.ignore_reason ?? ""] ?? e.ignore_reason}</Badge>
                ),
            },
          ]}
        />
        {events.data && events.data.total > 0 ? <Pagination page={page} pageSize={PAGE} total={events.data.total} onPage={setPage} /> : null}
      </Card>

      <MissingApi
        title="Tanilmagan yuzlarni koʻrib chiqish (saqlangan hodisalar)"
        phase="Davomatga ulash bosqichi"
        contract="API_CONTRACTS §2.7"
        endpoints={["GET /api/v1/recognition-events", "POST /api/v1/recognition-events/{id}/review"]}
      >
        Jonli tanish natijalari hozircha faqat xotirada turadi va saqlanmaydi. Saqlash va koʻrib chiqish davomatga ulash bilan birga qilinadi.
      </MissingApi>
    </>
  );
}
