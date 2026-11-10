"use client";

import { Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { errorMessage } from "@/lib/errors";
import { formatDate, formatMinutes, formatTime } from "@/lib/format";
import { DECISION, IGNORE_REASON, SOURCE } from "@/lib/labels";
import { orgLocalToIso } from "@/lib/time";
import type { AttendanceEvent, DayDetail } from "@/lib/types";
import { useApiQuery } from "@/lib/use-api";
import { DayStatusBadge } from "@/components/entities";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { DetailItem } from "@/components/ui/controls";
import { Checkbox, Field, FormError, Input, Select, Textarea } from "@/components/ui/form";
import { Modal } from "@/components/ui/modal";
import { ErrorState, LoadingState } from "@/components/ui/states";
import { DataTable } from "@/components/ui/table";
import { useToast } from "@/components/ui/toast";

function EventState({ e }: { e: AttendanceEvent }) {
  if (e.is_voided) return <Badge tone="neutral">Bekor qilingan</Badge>;
  if (e.counts_for_attendance) return <Badge tone="success">Hisoblandi</Badge>;
  return <Badge tone="warning">{IGNORE_REASON[e.ignore_reason ?? ""] ?? e.ignore_reason}</Badge>;
}

type AddRow = { key: number; direction: "IN" | "OUT"; local: string };

export function DayDetailModal({
  employeeId,
  workDate,
  onClose,
  onChanged,
}: {
  employeeId: string;
  workDate: string;
  onClose: () => void;
  onChanged: () => void;
}) {
  const { can } = useAuth();
  const toast = useToast();
  const day = useApiQuery<DayDetail>(`/attendance/days/${employeeId}/${workDate}`);
  const [voids, setVoids] = useState<Set<number>>(new Set());
  const [adds, setAdds] = useState<AddRow[]>([]);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [touched, setTouched] = useState(false);
  const writable = can("attendance:write");

  const counted = (day.data?.events ?? []).filter((e) => e.counts_for_attendance && !e.is_voided);
  const hasOps = voids.size > 0 || adds.length > 0;
  const addErrors = adds.map((a) => (!a.local ? "Vaqtni kiriting" : null));
  const reasonError = touched && reason.trim().length < 3 ? "Sabab kamida 3 ta belgi." : null;

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setTouched(true);
    if (!hasOps || reason.trim().length < 3 || addErrors.some(Boolean)) return;
    setBusy(true);
    setError(null);
    try {
      await api.request("/attendance/corrections", {
        method: "POST",
        json: {
          employee_id: employeeId,
          work_date: workDate,
          reason: reason.trim(),
          operations: [
            ...[...voids].map((id) => ({ op: "void", event_id: id })),
            ...adds.map((a) => ({ op: "add", direction: a.direction, event_time: orgLocalToIso(a.local) })),
          ],
        },
      });
      toast.success("Tuzatish saqlandi", "Oʻzgarish jurnalga yozildi.");
      setVoids(new Set());
      setAdds([]);
      setReason("");
      setTouched(false);
      day.reload();
      onChanged();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  const d = day.data;
  return (
    <Modal
      open
      onClose={onClose}
      dismissible={!busy}
      size="lg"
      title={d ? d.employee.full_name : "Davomat kuni"}
      description={formatDate(`${workDate}T12:00:00Z`)}
      footer={
        writable && d ? (
          <>
            <Button variant="secondary" onClick={onClose} disabled={busy}>
              Yopish
            </Button>
            <Button type="submit" form="correction-form" loading={busy} disabled={!hasOps}>
              Tuzatishni saqlash
            </Button>
          </>
        ) : undefined
      }
    >
      {day.error ? <ErrorState error={day.error} onRetry={day.reload} /> : null}
      {!d && !day.error ? <LoadingState /> : null}
      {d ? (
        <div className="space-y-6">
          <dl className="grid grid-cols-2 gap-4 sm:grid-cols-4">
            <DetailItem label="Holat">
              <DayStatusBadge status={d.status} inProgress={d.in_progress} />
            </DetailItem>
            <DetailItem label="Jadval">
              {d.schedule ? `${d.schedule.name}` : "Jadvalsiz"}
              {d.scheduled_start ? (
                <span className="block text-xs text-muted">
                  {formatTime(d.scheduled_start)}–{formatTime(d.scheduled_end)}
                </span>
              ) : null}
            </DetailItem>
            <DetailItem label="Keldi / Ketdi">
              {formatTime(d.arrival)} / {formatTime(d.departure)}
            </DetailItem>
            <DetailItem label="Ishlagan">{d.worked_minutes ? formatMinutes(d.worked_minutes) : "—"}</DetailItem>
            <DetailItem label="Kechikish">{d.late_minutes ? `${d.late_minutes} daq` : "—"}</DetailItem>
            <DetailItem label="Erta ketish">{d.early_leave_minutes ? `${d.early_leave_minutes} daq` : "—"}</DetailItem>
            <DetailItem label="Qoʻshimcha">{d.overtime_minutes ? `${d.overtime_minutes} daq` : "—"}</DetailItem>
            <DetailItem label="Qoʻlda tuzatilgan">{d.has_manual_events ? "Ha" : "Yoʻq"}</DetailItem>
          </dl>

          <div>
            <h3 className="mb-2 text-sm font-semibold text-navy-900">Hodisalar ({d.events.length})</h3>
            <div className="overflow-hidden rounded-lg border border-line">
              <DataTable<AttendanceEvent>
                rows={d.events}
                rowKey={(e) => String(e.id)}
                empty={<p className="px-4 py-6 text-center text-sm text-muted">Bu kunda hodisa yoʻq.</p>}
                columns={[
                  { key: "t", header: "Vaqt", cell: (e) => formatTime(e.event_time) },
                  { key: "dir", header: "Yoʻnalish", cell: (e) => (e.direction === "IN" ? "Kirish" : e.direction === "OUT" ? "Chiqish" : "—") },
                  {
                    key: "src",
                    header: "Manba",
                    cell: (e) => (
                      <span>
                        {SOURCE[e.source]}
                        {e.decision ? <span className="ml-1 text-xs text-muted">({DECISION[e.decision]?.label})</span> : null}
                      </span>
                    ),
                    hideOnMobile: true,
                  },
                  { key: "state", header: "Natija", cell: (e) => <EventState e={e} /> },
                  {
                    key: "note",
                    header: "Izoh",
                    cell: (e) => <span className="block max-w-56 truncate text-xs text-muted" title={e.void_reason ?? e.note ?? ""}>{e.void_reason ?? e.note ?? ""}</span>,
                    hideOnMobile: true,
                  },
                ]}
              />
            </div>
          </div>

          {writable ? (
            <form id="correction-form" onSubmit={submit} noValidate className="space-y-4 rounded-lg border border-line bg-subtle p-4">
              <div>
                <h3 className="text-sm font-semibold text-navy-900">Qoʻlda tuzatish</h3>
                <p className="text-xs text-muted">
                  Hodisalar oʻchirilmaydi: bekor qilinadi va yangisi qoʻshiladi. Natija kirish → chiqish tartibida
                  boʻlishi shart, aks holda hech narsa oʻzgarmaydi.
                </p>
              </div>
              <FormError>{error}</FormError>
              {counted.length > 0 ? (
                <fieldset className="space-y-1.5">
                  <legend className="mb-1 text-sm font-medium text-navy-900">Bekor qilinadigan hodisalar</legend>
                  {counted.map((e) => (
                    <Checkbox
                      key={e.id}
                      label={`${formatTime(e.event_time)} · ${e.direction === "IN" ? "Kirish" : "Chiqish"} (${SOURCE[e.source]})`}
                      checked={voids.has(e.id)}
                      onChange={(ev) =>
                        setVoids((prev) => {
                          const next = new Set(prev);
                          if (ev.target.checked) next.add(e.id);
                          else next.delete(e.id);
                          return next;
                        })
                      }
                    />
                  ))}
                </fieldset>
              ) : null}
              <div className="space-y-2">
                <p className="text-sm font-medium text-navy-900">Qoʻshiladigan hodisalar</p>
                {adds.map((a, i) => (
                  <div key={a.key} className="flex flex-col gap-2 sm:flex-row sm:items-start">
                    <Select
                      aria-label="Yoʻnalish"
                      className="sm:w-32"
                      value={a.direction}
                      onChange={(e) => setAdds((p) => p.map((x) => (x.key === a.key ? { ...x, direction: e.target.value as "IN" | "OUT" } : x)))}
                    >
                      <option value="IN">Kirish</option>
                      <option value="OUT">Chiqish</option>
                    </Select>
                    <div className="flex-1">
                      <Input
                        aria-label="Vaqt (Toshkent)"
                        type="datetime-local"
                        value={a.local}
                        invalid={touched && Boolean(addErrors[i])}
                        onChange={(e) => setAdds((p) => p.map((x) => (x.key === a.key ? { ...x, local: e.target.value } : x)))}
                      />
                    </div>
                    <Button variant="ghost" size="icon" aria-label="Qatorni olib tashlash" onClick={() => setAdds((p) => p.filter((x) => x.key !== a.key))}>
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  </div>
                ))}
                {adds.length < 6 ? (
                  <Button
                    variant="secondary"
                    size="sm"
                    icon={<Plus className="h-4 w-4" />}
                    onClick={() => setAdds((p) => [...p, { key: Date.now(), direction: p.length % 2 === 0 ? "IN" : "OUT", local: `${workDate}T09:00` }])}
                  >
                    Hodisa qoʻshish
                  </Button>
                ) : null}
                <p className="text-xs text-muted">Vaqtlar Toshkent vaqti (UTC+5) boʻyicha.</p>
              </div>
              <Field label="Sabab (jurnalga yoziladi)" required error={reasonError}>
                <Textarea value={reason} maxLength={500} onChange={(e) => setReason(e.target.value)} placeholder="Masalan: kamera ishlamay qoldi" />
              </Field>
            </form>
          ) : null}
        </div>
      ) : null}
    </Modal>
  );
}
