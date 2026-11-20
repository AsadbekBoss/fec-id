"use client";

import { BranchScopeNote } from "@/components/branch-note";
import { Moon, Pencil, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { errorMessage, fieldErrors } from "@/lib/errors";
import { WEEKDAYS } from "@/lib/labels";
import type { WorkSchedule } from "@/lib/types";
import { useApiQuery } from "@/lib/use-api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/confirm";
import { RequirePermission } from "@/components/ui/controls";
import { Checkbox, Field, FormError, Input } from "@/components/ui/form";
import { Modal } from "@/components/ui/modal";
import { PageHeader } from "@/components/ui/page-header";
import { EmptyState } from "@/components/ui/states";
import { DataTable } from "@/components/ui/table";
import { useToast } from "@/components/ui/toast";
import { type ScheduleFormValues as Form, validateSchedule } from "@/lib/validation";

export default function SchedulesPage() {
  return (
    <RequirePermission permission="attendance:read">
      <Schedules />
    </RequirePermission>
  );
}

function Schedules() {
  const { can } = useAuth();
  const toast = useToast();
  const list = useApiQuery<WorkSchedule[]>("/work-schedules");
  const [editing, setEditing] = useState<WorkSchedule | "new" | null>(null);
  const [deleting, setDeleting] = useState<WorkSchedule | null>(null);
  const writable = can("attendance:write");

  return (
    <>
      <PageHeader
        title="Ish jadvallari"
        description="Ish vaqti, ish kunlari, tushlik va imtiyoz vaqti. Kechikish va kelmaganlik shu asosda hisoblanadi."
        actions={writable ? <Button icon={<Plus className="h-4 w-4" />} onClick={() => setEditing("new")}>Yangi jadval</Button> : null}
      />
      <BranchScopeNote what="jadval" />
      <Card>
        <DataTable<WorkSchedule>
          caption="Ish jadvallari"
          loading={list.loading}
          error={list.error}
          onRetry={list.reload}
          rows={list.data}
          rowKey={(s) => s.id}
          empty={<EmptyState title="Hali jadval yoʻq" />}
          columns={[
            { key: "name", header: "Nomi", cell: (s) => <span className="font-medium text-navy-900">{s.name}</span> },
            {
              key: "time",
              header: "Vaqt",
              cell: (s) => (
                <span className="inline-flex items-center gap-1 tabular-nums">
                  {s.start_time.slice(0, 5)}–{s.end_time.slice(0, 5)}
                  {s.crosses_midnight ? <Moon className="h-3.5 w-3.5 text-navy-400" aria-label="tungi smena" /> : null}
                </span>
              ),
            },
            { key: "days", header: "Ish kunlari", cell: (s) => s.working_days.map((d) => WEEKDAYS[d - 1]).join(", "), hideOnMobile: true },
            { key: "break", header: "Tushlik", cell: (s) => `${s.break_minutes} daq`, hideOnMobile: true },
            { key: "grace", header: "Imtiyoz", cell: (s) => `${s.grace_in_minutes} daq`, hideOnMobile: true },
            { key: "state", header: "Holat", cell: (s) => (s.is_active ? <Badge tone="success">Faol</Badge> : <Badge>Nofaol</Badge>) },
            {
              key: "actions",
              header: "",
              align: "right",
              cell: (s) =>
                writable ? (
                  <div className="flex justify-end gap-1">
                    <Button variant="ghost" size="icon" aria-label={`${s.name}: tahrirlash`} onClick={() => setEditing(s)}>
                      <Pencil className="h-4 w-4" />
                    </Button>
                    <Button variant="ghost" size="icon" aria-label={`${s.name}: oʻchirish`} onClick={() => setDeleting(s)}>
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  </div>
                ) : null,
            },
          ]}
        />
      </Card>
      {editing ? <ScheduleForm schedule={editing === "new" ? null : editing} onClose={() => setEditing(null)} onSaved={list.reload} /> : null}
      <ConfirmDialog
        open={deleting !== null}
        onClose={() => setDeleting(null)}
        title="Jadvalni oʻchirish"
        description="Jadval xodim yoki boʻlimga biriktirilgan boʻlsa, server rad etadi. Bunda uni “nofaol” qiling."
        confirmLabel="Oʻchirish"
        tone="danger"
        onConfirm={async () => {
          if (!deleting) return;
          await api.request(`/work-schedules/${deleting.id}`, { method: "DELETE" });
          toast.success("Jadval oʻchirildi");
          list.reload();
        }}
      />
    </>
  );
}

function ScheduleForm({ schedule, onClose, onSaved }: { schedule: WorkSchedule | null; onClose: () => void; onSaved: () => void }) {
  const toast = useToast();
  const [f, setF] = useState<Form>({
    name: schedule?.name ?? "",
    start_time: schedule?.start_time.slice(0, 5) ?? "09:00",
    end_time: schedule?.end_time.slice(0, 5) ?? "18:00",
    working_days: schedule?.working_days ?? [1, 2, 3, 4, 5],
    break_minutes: String(schedule?.break_minutes ?? 60),
    grace_in_minutes: String(schedule?.grace_in_minutes ?? 5),
    grace_out_minutes: String(schedule?.grace_out_minutes ?? 0),
    overtime_enabled: schedule?.overtime_enabled ?? false,
    is_active: schedule?.is_active ?? true,
  });
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const set = <K extends keyof Form>(k: K, v: Form[K]) => setF((p) => ({ ...p, [k]: v }));
  const nightShift = f.start_time && f.end_time && f.end_time <= f.start_time;

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    const local = validateSchedule(f);
    setErrors(local);
    setFormError(null);
    if (Object.keys(local).length) return;
    setBusy(true);
    const body = {
      name: f.name.trim(),
      start_time: f.start_time,
      end_time: f.end_time,
      working_days: f.working_days,
      break_minutes: Number(f.break_minutes),
      grace_in_minutes: Number(f.grace_in_minutes),
      grace_out_minutes: Number(f.grace_out_minutes),
      overtime_enabled: f.overtime_enabled,
      ...(schedule ? { is_active: f.is_active } : {}),
    };
    try {
      if (schedule) await api.request(`/work-schedules/${schedule.id}`, { method: "PATCH", json: body });
      else await api.request("/work-schedules", { method: "POST", json: body });
      toast.success(schedule ? "Jadval saqlandi" : "Jadval yaratildi");
      onSaved();
      onClose();
    } catch (err) {
      const fe = fieldErrors(err, { SCHEDULE_EXISTS: "name" });
      setErrors(fe);
      if (!Object.keys(fe).length) setFormError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      open
      onClose={onClose}
      dismissible={!busy}
      title={schedule ? "Jadvalni tahrirlash" : "Yangi ish jadvali"}
      description="Vaqtlar Toshkent vaqti (Asia/Tashkent)."
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={busy}>Bekor qilish</Button>
          <Button type="submit" form="schedule-form" loading={busy}>Saqlash</Button>
        </>
      }
    >
      <form id="schedule-form" onSubmit={submit} noValidate className="space-y-4">
        <FormError>{formError}</FormError>
        <Field label="Nomi" required error={errors.name}>
          <Input value={f.name} maxLength={120} onChange={(e) => set("name", e.target.value)} autoFocus />
        </Field>
        <div className="grid grid-cols-2 gap-4">
          <Field label="Boshlanish" required error={errors.start_time}>
            <Input type="time" value={f.start_time} onChange={(e) => set("start_time", e.target.value)} />
          </Field>
          <Field label="Tugash" required error={errors.end_time} hint={nightShift ? "Tungi smena: ertasi kuni tugaydi" : undefined}>
            <Input type="time" value={f.end_time} onChange={(e) => set("end_time", e.target.value)} />
          </Field>
        </div>
        <fieldset>
          <legend className="mb-2 text-sm font-medium text-navy-900">Ish kunlari</legend>
          <div className="flex flex-wrap gap-3">
            {WEEKDAYS.map((label, i) => (
              <Checkbox
                key={label}
                label={label}
                checked={f.working_days.includes(i + 1)}
                onChange={(e) =>
                  set("working_days", e.target.checked ? [...f.working_days, i + 1].sort() : f.working_days.filter((d) => d !== i + 1))
                }
              />
            ))}
          </div>
          {errors.working_days ? <p className="mt-1 text-xs font-medium text-danger-700" role="alert">{errors.working_days}</p> : null}
        </fieldset>
        <div className="grid grid-cols-3 gap-4">
          <Field label="Tushlik (daq)" error={errors.break_minutes}>
            <Input inputMode="numeric" value={f.break_minutes} onChange={(e) => set("break_minutes", e.target.value)} />
          </Field>
          <Field label="Kelish imtiyozi" error={errors.grace_in_minutes}>
            <Input inputMode="numeric" value={f.grace_in_minutes} onChange={(e) => set("grace_in_minutes", e.target.value)} />
          </Field>
          <Field label="Ketish imtiyozi" error={errors.grace_out_minutes}>
            <Input inputMode="numeric" value={f.grace_out_minutes} onChange={(e) => set("grace_out_minutes", e.target.value)} />
          </Field>
        </div>
        <Checkbox label="Qoʻshimcha ish vaqtini hisoblash" checked={f.overtime_enabled} onChange={(e) => set("overtime_enabled", e.target.checked)} />
        {schedule ? <Checkbox label="Faol" checked={f.is_active} onChange={(e) => set("is_active", e.target.checked)} /> : null}
      </form>
    </Modal>
  );
}
