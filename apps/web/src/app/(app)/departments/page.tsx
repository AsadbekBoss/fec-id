"use client";

import { BranchScopeNote } from "@/components/branch-note";
import { Pencil, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { errorMessage, fieldErrors } from "@/lib/errors";
import type { WorkSchedule } from "@/lib/types";
import { useApiQuery } from "@/lib/use-api";
import { DepartmentSelect, ScheduleSelect, useDepartments, type DeptOption } from "@/components/entities";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/confirm";
import { RequirePermission } from "@/components/ui/controls";
import { Field, FormError, Input } from "@/components/ui/form";
import { Modal } from "@/components/ui/modal";
import { PageHeader } from "@/components/ui/page-header";
import { EmptyState } from "@/components/ui/states";
import { DataTable } from "@/components/ui/table";
import { useToast } from "@/components/ui/toast";

export default function DepartmentsPage() {
  return (
    <RequirePermission permission="employees:read">
      <Departments />
    </RequirePermission>
  );
}

function Departments() {
  const { can } = useAuth();
  const toast = useToast();
  const depts = useDepartments();
  const schedules = useApiQuery<WorkSchedule[]>(can("attendance:read") ? "/work-schedules" : null);
  const [editing, setEditing] = useState<DeptOption | "new" | null>(null);
  const [deleting, setDeleting] = useState<DeptOption | null>(null);
  const writable = can("employees:write");
  const scheduleName = (id: string | null) => (id ? (schedules.data?.find((s) => s.id === id)?.name ?? "…") : null);

  return (
    <>
      <PageHeader
        title="Boʻlimlar"
        description="Tashkilot tuzilmasi. Boʻlim jadvali uning barcha ichki boʻlimlariga ham taʼsir qiladi."
        actions={writable ? <Button icon={<Plus className="h-4 w-4" />} onClick={() => setEditing("new")}>Yangi boʻlim</Button> : null}
      />
      <BranchScopeNote what="boʻlim (ota boʻlimsiz)" />
      <Card>
        <DataTable<DeptOption>
          caption="Boʻlimlar"
          loading={depts.loading}
          error={depts.error}
          onRetry={depts.reload}
          rows={depts.data ? depts.tree : undefined}
          rowKey={(d) => d.id}
          empty={<EmptyState title="Hali boʻlimlar yoʻq" body={writable ? "Birinchi boʻlimni qoʻshing." : undefined} />}
          columns={[
            {
              key: "name",
              header: "Nomi",
              cell: (d) => (
                <span style={{ paddingLeft: `${d.depth * 1.25}rem` }} className="font-medium text-navy-900">
                  {d.depth ? <span className="mr-1 text-muted">└</span> : null}
                  {d.name}
                </span>
              ),
            },
            { key: "code", header: "Kod", cell: (d) => d.code ?? "—", hideOnMobile: true },
            { key: "count", header: "Xodimlar", align: "right", cell: (d) => d.employee_count },
            { key: "sched", header: "Standart jadval", cell: (d) => scheduleName(d.work_schedule_id) ?? <span className="text-muted">ota boʻlimdan</span>, hideOnMobile: true },
            {
              key: "actions",
              header: "",
              align: "right",
              cell: (d) =>
                writable ? (
                  <div className="flex justify-end gap-1">
                    <Button variant="ghost" size="icon" aria-label={`${d.name}: tahrirlash`} onClick={() => setEditing(d)}>
                      <Pencil className="h-4 w-4" />
                    </Button>
                    <Button variant="ghost" size="icon" aria-label={`${d.name}: oʻchirish`} onClick={() => setDeleting(d)}>
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  </div>
                ) : null,
            },
          ]}
        />
      </Card>
      {editing ? (
        <DepartmentForm dept={editing === "new" ? null : editing} onClose={() => setEditing(null)} onSaved={depts.reload} canSchedule={can("attendance:read")} />
      ) : null}
      <ConfirmDialog
        open={deleting !== null}
        onClose={() => setDeleting(null)}
        title="Boʻlimni oʻchirish"
        description={deleting ? `“${deleting.name}” oʻchiriladi. Ichida xodim yoki ichki boʻlim boʻlsa, server rad etadi.` : undefined}
        confirmLabel="Oʻchirish"
        tone="danger"
        onConfirm={async () => {
          if (!deleting) return;
          await api.request(`/departments/${deleting.id}`, { method: "DELETE" });
          toast.success("Boʻlim oʻchirildi");
          depts.reload();
        }}
      />
    </>
  );
}

function DepartmentForm({ dept, onClose, onSaved, canSchedule }: { dept: DeptOption | null; onClose: () => void; onSaved: () => void; canSchedule: boolean }) {
  const toast = useToast();
  const [name, setName] = useState(dept?.name ?? "");
  const [code, setCode] = useState(dept?.code ?? "");
  const [parent, setParent] = useState(dept?.parent_id ?? "");
  const [schedule, setSchedule] = useState(dept?.work_schedule_id ?? "");
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    const local: Record<string, string> = {};
    if (!name.trim()) local.name = "Nomini kiriting.";
    if (code && !/^[A-Za-z0-9_.-]{1,50}$/.test(code)) local.code = "1–50 belgi: harf, raqam, _ . -";
    setErrors(local);
    setFormError(null);
    if (Object.keys(local).length) return;
    setBusy(true);
    try {
      if (dept) {
        const body: Record<string, unknown> = {};
        if (name.trim() !== dept.name) body.name = name.trim();
        if ((code || null) !== dept.code) body.code = code || null;
        if ((parent || null) !== dept.parent_id) body.parent_id = parent || null;
        if (canSchedule && (schedule || null) !== dept.work_schedule_id) body.work_schedule_id = schedule || null;
        if (Object.keys(body).length) await api.request(`/departments/${dept.id}`, { method: "PATCH", json: body });
      } else {
        await api.request("/departments", { method: "POST", json: { name: name.trim(), code: code || null, parent_id: parent || null } });
      }
      toast.success(dept ? "Boʻlim saqlandi" : "Boʻlim qoʻshildi");
      onSaved();
      onClose();
    } catch (err) {
      const fe = fieldErrors(err, { DEPARTMENT_EXISTS: "name" });
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
      title={dept ? "Boʻlimni tahrirlash" : "Yangi boʻlim"}
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={busy}>Bekor qilish</Button>
          <Button type="submit" form="dept-form" loading={busy}>Saqlash</Button>
        </>
      }
    >
      <form id="dept-form" onSubmit={submit} noValidate className="space-y-4">
        <FormError>{formError}</FormError>
        <Field label="Nomi" required error={errors.name}>
          <Input value={name} maxLength={200} onChange={(e) => setName(e.target.value)} autoFocus />
        </Field>
        <Field label="Kod" error={errors.code} hint="Ixtiyoriy, noyob">
          <Input value={code} maxLength={50} onChange={(e) => setCode(e.target.value)} />
        </Field>
        <Field label="Yuqori boʻlim" error={errors.parent_id}>
          <DepartmentSelect value={parent} onChange={setParent} allLabel="— (yuqori daraja)" exclude={dept?.id} />
        </Field>
        {dept && canSchedule ? (
          <Field label="Standart ish jadvali" error={errors.work_schedule_id} hint="Shaxsiy jadvali yoʻq xodimlarga qoʻllanadi">
            <ScheduleSelect value={schedule} onChange={setSchedule} emptyLabel="— (ota boʻlimdan meros)" branchId={dept?.branch_id ?? null} />
          </Field>
        ) : null}
      </form>
    </Modal>
  );
}
