"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { errorMessage, fieldErrors } from "@/lib/errors";
import type { Employee } from "@/lib/types";
import { DepartmentSelect, ScheduleSelect, useDepartments } from "@/components/entities";
import { Button } from "@/components/ui/button";
import { Field, FormError, Input, Select } from "@/components/ui/form";
import { Modal } from "@/components/ui/modal";
import { useToast } from "@/components/ui/toast";

type Values = {
  employee_code: string;
  last_name: string;
  first_name: string;
  middle_name: string;
  department_id: string;
  work_schedule_id: string;
  position_title: string;
  phone: string;
  email: string;
  hire_date: string;
  attendance_method: "FACE" | "MANUAL";
};

const NAME_RE = /^\p{L}[\p{L}\p{M} .ʻ'‘’`-]*$/u;

/** Client-side checks mirror the server rules (API_CONTRACTS §2.4). The server stays authoritative. */
export function validateEmployee(v: Values): Record<string, string> {
  const e: Record<string, string> = {};
  if (!/^[A-Za-z0-9_.-]{1,50}$/.test(v.employee_code.trim())) e.employee_code = "1–50 belgi: harf, raqam, _ . -";
  for (const f of ["last_name", "first_name"] as const) {
    if (!v[f].trim()) e[f] = "Majburiy maydon.";
    else if (!NAME_RE.test(v[f].trim())) e[f] = "Faqat harflar, boʻsh joy, defis, nuqta va ʻ.";
  }
  if (v.middle_name.trim() && !NAME_RE.test(v.middle_name.trim())) e.middle_name = "Faqat harflar, boʻsh joy, defis, nuqta va ʻ.";
  if (!v.department_id) e.department_id = "Boʻlimni tanlang.";
  if (v.phone.trim()) {
    const digits = v.phone.replace(/[\s().-]/g, "");
    if (!/^\+?\d{7,15}$/.test(digits)) e.phone = "7–15 ta raqam, masalan +998901234567.";
  }
  if (v.email.trim() && !/^[^@\s]+@[^@\s]+\.[^@\s]{2,}$/.test(v.email.trim())) e.email = "Email notoʻgʻri.";
  if (!v.hire_date) e.hire_date = "Ishga kirgan sanani kiriting.";
  return e;
}

function toPayload(v: Values) {
  const opt = (s: string) => (s.trim() ? s.trim() : null);
  return {
    employee_code: v.employee_code.trim(),
    last_name: v.last_name.trim(),
    first_name: v.first_name.trim(),
    middle_name: opt(v.middle_name),
    department_id: v.department_id,
    work_schedule_id: v.work_schedule_id || null,
    position_title: opt(v.position_title),
    phone: opt(v.phone),
    email: opt(v.email),
    hire_date: v.hire_date,
    attendance_method: v.attendance_method,
  };
}

const SERVER_FIELD_BY_CODE = {
  EMPLOYEE_CODE_EXISTS: "employee_code",
  EMPLOYEE_EMAIL_EXISTS: "email",
  EMPLOYEE_PHONE_EXISTS: "phone",
  ACTIVE_CONSENT_EXISTS: "attendance_method",
};

export function EmployeeFormModal({
  open,
  onClose,
  employee,
  onSaved,
}: {
  open: boolean;
  onClose: () => void;
  employee?: Employee;
  onSaved: (e: Employee) => void;
}) {
  const toast = useToast();
  const initial: Values = {
    employee_code: employee?.employee_code ?? "",
    last_name: employee?.last_name ?? "",
    first_name: employee?.first_name ?? "",
    middle_name: employee?.middle_name ?? "",
    department_id: employee?.department.id ?? "",
    work_schedule_id: employee?.work_schedule_id ?? "",
    position_title: employee?.position_title ?? "",
    phone: employee?.phone ?? "",
    email: employee?.email ?? "",
    hire_date: employee?.hire_date ?? "",
    attendance_method: employee?.attendance_method ?? "FACE",
  };
  const [v, setV] = useState<Values>(initial);
  const departments = useDepartments();
  /** The employee belongs to the branch of its department; its schedule must too. */
  const deptBranch = departments.data?.find((d) => d.id === v.department_id)?.branch_id ?? null;
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const set = <K extends keyof Values>(k: K) => (value: Values[K]) => setV((p) => ({ ...p, [k]: value }));

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    const local = validateEmployee(v);
    setErrors(local);
    setFormError(null);
    if (Object.keys(local).length) return;
    setBusy(true);
    try {
      const payload = toPayload(v);
      let saved: Employee;
      if (employee) {
        // PATCH only changed fields (partial update, API_CONTRACTS §2.4).
        const before = toPayload(initial) as Record<string, unknown>;
        const changes = Object.fromEntries(Object.entries(payload).filter(([k, val]) => before[k] !== val));
        if (Object.keys(changes).length === 0) {
          onClose();
          return;
        }
        saved = await api.request<Employee>(`/employees/${employee.id}`, { method: "PATCH", json: changes });
        toast.success("Oʻzgarishlar saqlandi");
      } else {
        saved = await api.request<Employee>("/employees", { method: "POST", json: payload });
        toast.success("Xodim qoʻshildi", saved.full_name);
      }
      onSaved(saved);
      onClose();
    } catch (err) {
      const fe = fieldErrors(err, SERVER_FIELD_BY_CODE);
      setErrors(fe);
      if (Object.keys(fe).length === 0) setFormError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      dismissible={!busy}
      size="lg"
      title={employee ? "Xodimni tahrirlash" : "Yangi xodim"}
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={busy}>
            Bekor qilish
          </Button>
          <Button type="submit" form="employee-form" loading={busy}>
            Saqlash
          </Button>
        </>
      }
    >
      <form id="employee-form" onSubmit={submit} noValidate className="space-y-5">
        <FormError>{formError}</FormError>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Xodim kodi (tabel raqami)" required error={errors.employee_code} hint="Katta harflarga oʻtkaziladi">
            <Input value={v.employee_code} maxLength={50} onChange={(e) => set("employee_code")(e.target.value)} />
          </Field>
          <Field label="Ishga kirgan sana" required error={errors.hire_date}>
            <Input type="date" value={v.hire_date} onChange={(e) => set("hire_date")(e.target.value)} />
          </Field>
          <Field label="Familiya" required error={errors.last_name}>
            <Input value={v.last_name} maxLength={100} onChange={(e) => set("last_name")(e.target.value)} />
          </Field>
          <Field label="Ism" required error={errors.first_name}>
            <Input value={v.first_name} maxLength={100} onChange={(e) => set("first_name")(e.target.value)} />
          </Field>
          <Field label="Otasining ismi" error={errors.middle_name}>
            <Input value={v.middle_name} maxLength={100} onChange={(e) => set("middle_name")(e.target.value)} />
          </Field>
          <Field label="Lavozim" error={errors.position_title}>
            <Input value={v.position_title} maxLength={150} onChange={(e) => set("position_title")(e.target.value)} />
          </Field>
          <Field label="Boʻlim" required error={errors.department_id}>
            <DepartmentSelect value={v.department_id} onChange={set("department_id")} />
          </Field>
          <Field label="Ish jadvali" error={errors.work_schedule_id} hint="Boʻsh boʻlsa, boʻlim jadvali qoʻllanadi">
            <ScheduleSelect value={v.work_schedule_id} onChange={set("work_schedule_id")} branchId={deptBranch} />
          </Field>
          <Field label="Telefon" error={errors.phone}>
            <Input type="tel" value={v.phone} placeholder="+998 90 123 45 67" onChange={(e) => set("phone")(e.target.value)} />
          </Field>
          <Field label="Email" error={errors.email}>
            <Input type="email" value={v.email} maxLength={320} onChange={(e) => set("email")(e.target.value)} />
          </Field>
          <Field
            label="Davomat usuli"
            error={errors.attendance_method}
            hint="Biometriyaga rozilik bermagan xodimlar uchun “Qoʻlda” tanlanadi"
            className="sm:col-span-2"
          >
            <Select value={v.attendance_method} onChange={(e) => set("attendance_method")(e.target.value as Values["attendance_method"])}>
              <option value="FACE">Yuz orqali (kamera)</option>
              <option value="MANUAL">Qoʻlda (biometriyasiz)</option>
            </Select>
          </Field>
        </div>
      </form>
    </Modal>
  );
}
