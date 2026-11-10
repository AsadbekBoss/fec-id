"use client";

import {
  ArrowLeft,
  Download,
  FileSignature,
  History,
  Pencil,
  ShieldOff,
  Trash2,
  Upload,
  UserCheck,
  UserX,
} from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { Suspense, useRef, useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { errorMessage } from "@/lib/errors";
import { formatDate, formatMinutes, formatTime } from "@/lib/format";
import { BIOMETRIC_STATUS } from "@/lib/labels";
import { addDays, todayInOrg } from "@/lib/time";
import type { Consent, Day, Employee, Page } from "@/lib/types";
import { useApiQuery } from "@/lib/use-api";
import {
  AuthImage,
  BiometricBadge,
  DayStatusBadge,
  EmployeeStatusBadge,
} from "@/components/entities";
import { FaceTemplatesCard } from "@/components/face-templates";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/confirm";
import { DetailItem, RequirePermission } from "@/components/ui/controls";
import { Checkbox, Field, Input, Select } from "@/components/ui/form";
import { PageHeader } from "@/components/ui/page-header";
import { Skeleton } from "@/components/ui/spinner";
import { ErrorState, LoadingState } from "@/components/ui/states";
import { DataTable } from "@/components/ui/table";
import { useToast } from "@/components/ui/toast";
import { EmployeeFormModal } from "../employee-form";

const PHOTO_TYPES = ["image/jpeg", "image/png"];
const DOC_TYPES = ["application/pdf", "image/jpeg", "image/png"];

export default function EmployeeDetailPage() {
  // The dynamic [id] is only known at request time: Suspense lets Next prerender the shell.
  return (
    <RequirePermission permission="employees:read">
      <Suspense fallback={<LoadingState />}>
        <EmployeeDetail />
      </Suspense>
    </RequirePermission>
  );
}

function EmployeeDetail() {
  const { id } = useParams<{ id: string }>();
  const { can } = useAuth();
  const emp = useApiQuery<Employee>(`/employees/${id}`);
  const [editing, setEditing] = useState(false);

  if (emp.error) {
    return (
      <Card>
        <ErrorState error={emp.error} onRetry={emp.reload} />
      </Card>
    );
  }
  const e = emp.data;
  if (!e) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-48 w-full" />
      </div>
    );
  }

  return (
    <>
      <Link href="/employees" className="mb-3 inline-flex items-center gap-1 text-sm text-brand-600 hover:underline">
        <ArrowLeft className="h-4 w-4" /> Xodimlar roʻyxati
      </Link>
      <PageHeader
        title={e.full_name}
        description={
          <span className="flex flex-wrap items-center gap-2">
            <span className="font-mono">{e.employee_code}</span>
            <EmployeeStatusBadge status={e.status} />
            <BiometricBadge status={e.biometric.status} />
          </span>
        }
        actions={
          can("employees:write") ? (
            <>
              <Button variant="secondary" icon={<Pencil className="h-4 w-4" />} onClick={() => setEditing(true)}>
                Tahrirlash
              </Button>
              <StatusAction employee={e} onChanged={emp.reload} />
            </>
          ) : null
        }
      />

      <div className="grid gap-6 lg:grid-cols-3">
        <div className="space-y-6 lg:col-span-2">
          <Card>
            <CardHeader title="Profil" />
            <CardBody>
              <dl className="grid gap-5 sm:grid-cols-2">
                <DetailItem label="Boʻlim">{e.department.name}</DetailItem>
                <DetailItem label="Lavozim">{e.position_title}</DetailItem>
                <DetailItem label="Telefon">{e.phone}</DetailItem>
                <DetailItem label="Email">{e.email}</DetailItem>
                <DetailItem label="Ishga kirgan">{formatDate(`${e.hire_date}T12:00:00Z`)}</DetailItem>
                <DetailItem label="Ishdan boʻshagan">
                  {e.termination_date ? formatDate(`${e.termination_date}T12:00:00Z`) : "—"}
                </DetailItem>
                <DetailItem label="Davomat usuli">
                  {e.attendance_method === "FACE" ? "Yuz orqali (kamera)" : "Qoʻlda (biometriyasiz)"}
                </DetailItem>
                <DetailItem label="Ish jadvali">{e.work_schedule_id ? "Shaxsiy jadval" : "Boʻlim jadvali"}</DetailItem>
              </dl>
            </CardBody>
          </Card>
          {can("attendance:read") ? <RecentAttendance employeeId={e.id} /> : null}
        </div>

        <div className="space-y-6">
          <PhotoCard employee={e} onChanged={emp.reload} />
          {can("biometrics:read") ? <ConsentCard employee={e} onChanged={emp.reload} /> : null}
          {can("biometrics:read") && e.attendance_method === "FACE" && (e.biometric.can_enroll || e.biometric.face_templates > 0) ? (
            <FaceTemplatesCard employee={e} onChanged={emp.reload} />
          ) : null}
        </div>
      </div>

      {editing ? <EmployeeFormModal open employee={e} onClose={() => setEditing(false)} onSaved={emp.reload} /> : null}
    </>
  );
}

// --- status -----------------------------------------------------------------------------------

function StatusAction({ employee, onChanged }: { employee: Employee; onChanged: () => void }) {
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [kind, setKind] = useState<"SUSPENDED" | "TERMINATED">("SUSPENDED");
  const [termDate, setTermDate] = useState(todayInOrg());
  const active = employee.status === "ACTIVE";

  if (!active) {
    return (
      <>
        <Button icon={<UserCheck className="h-4 w-4" />} onClick={() => setOpen(true)}>
          Faollashtirish
        </Button>
        <ConfirmDialog
          open={open}
          onClose={() => setOpen(false)}
          title="Xodimni faollashtirish"
          description={`${employee.full_name} yana faol holatga oʻtadi.`}
          confirmLabel="Faollashtirish"
          reasonLabel="Sabab"
          onConfirm={async (reason) => {
            await api.request(`/employees/${employee.id}/activate`, { method: "POST", json: { reason } });
            toast.success("Xodim faollashtirildi");
            onChanged();
          }}
        />
      </>
    );
  }
  return (
    <>
      <Button variant="danger" icon={<UserX className="h-4 w-4" />} onClick={() => setOpen(true)}>
        Faolsizlantirish
      </Button>
      <ConfirmDialog
        open={open}
        onClose={() => setOpen(false)}
        title="Xodimni faolsizlantirish"
        description="Faol boʻlmagan xodimning kamera hodisalari davomatga hisoblanmaydi."
        confirmLabel="Tasdiqlash"
        tone="danger"
        reasonLabel="Sabab (jurnalga yoziladi)"
        onConfirm={async (reason) => {
          await api.request(`/employees/${employee.id}/deactivate`, {
            method: "POST",
            json: { status: kind, reason, ...(kind === "TERMINATED" ? { termination_date: termDate } : {}) },
          });
          toast.success(kind === "TERMINATED" ? "Xodim ishdan boʻshatildi" : "Xodim vaqtincha toʻxtatildi");
          onChanged();
        }}
      >
        <Field label="Turi">
          <Select value={kind} onChange={(e) => setKind(e.target.value as typeof kind)}>
            <option value="SUSPENDED">Vaqtincha toʻxtatish</option>
            <option value="TERMINATED">Ishdan boʻshatish</option>
          </Select>
        </Field>
        {kind === "TERMINATED" ? (
          <Field label="Ishdan boʻshagan sana" required>
            <Input type="date" value={termDate} min={employee.hire_date} onChange={(e) => setTermDate(e.target.value)} />
          </Field>
        ) : null}
      </ConfirmDialog>
    </>
  );
}

// --- photo ------------------------------------------------------------------------------------

function PhotoCard({ employee, onChanged }: { employee: Employee; onChanged: () => void }) {
  const { can } = useAuth();
  const toast = useToast();
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const path = `/employees/${employee.id}/photo`;

  const onFile = async (file: File | undefined) => {
    if (!file) return;
    if (!PHOTO_TYPES.includes(file.type)) return toast.error("Faqat JPEG yoki PNG rasm yuklang.");
    if (file.size > 5 * 1024 * 1024) return toast.error("Rasm 5 MB dan oshmasligi kerak.");
    setBusy(true);
    try {
      await api.upload(path, file);
      toast.success("Rasm yangilandi", "Metamaʼlumotlar (GPS va boshq.) olib tashlandi.");
      onChanged();
    } catch (err) {
      toast.error("Rasmni yuklab boʻlmadi", errorMessage(err));
    } finally {
      setBusy(false);
      if (input.current) input.current.value = "";
    }
  };

  return (
    <Card>
      <CardHeader title="Profil rasmi" description="Faqat tanib olish uchun. Yuzni tanishda ishlatilmaydi." />
      <CardBody className="space-y-4">
        <AuthImage
          path={employee.has_photo ? path : null}
          version={employee.photo_updated_at}
          alt={`${employee.full_name} rasmi`}
          className="mx-auto aspect-square w-full max-w-[220px] rounded-xl"
        />
        {can("employees:write") ? (
          <div className="flex flex-wrap justify-center gap-2">
            <input ref={input} type="file" accept="image/jpeg,image/png" className="hidden" onChange={(e) => onFile(e.target.files?.[0])} />
            <Button variant="secondary" size="sm" loading={busy} icon={<Upload className="h-4 w-4" />} onClick={() => input.current?.click()}>
              {employee.has_photo ? "Almashtirish" : "Yuklash"}
            </Button>
            {employee.has_photo ? (
              <Button variant="ghost" size="sm" icon={<Trash2 className="h-4 w-4" />} onClick={() => setConfirmDelete(true)}>
                Oʻchirish
              </Button>
            ) : null}
          </div>
        ) : null}
      </CardBody>
      <ConfirmDialog
        open={confirmDelete}
        onClose={() => setConfirmDelete(false)}
        title="Rasmni oʻchirish"
        description="Rasm fayli serverdan butunlay oʻchiriladi."
        confirmLabel="Oʻchirish"
        tone="danger"
        onConfirm={async () => {
          await api.request(path, { method: "DELETE" });
          toast.success("Rasm oʻchirildi");
          onChanged();
        }}
      />
    </Card>
  );
}

// --- biometric consent ------------------------------------------------------------------------

async function downloadBlob(path: string, filename: string) {
  const blob = await api.blob(path);
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function ConsentCard({ employee, onChanged }: { employee: Employee; onChanged: () => void }) {
  const { can } = useAuth();
  const toast = useToast();
  const consents = useApiQuery<Consent[]>(`/employees/${employee.id}/consents`);
  const version = useApiQuery<{ consent_text_version: string }>("/biometrics/consent-version");
  const [recording, setRecording] = useState(false);
  const [signed, setSigned] = useState(false);
  const [revoking, setRevoking] = useState<Consent | null>(null);
  const [uploading, setUploading] = useState(false);
  const docInput = useRef<HTMLInputElement>(null);
  const writable = can("biometrics:write");
  const active = consents.data?.find((c) => c.is_active) ?? null;
  const reload = () => {
    consents.reload();
    onChanged();
  };

  const uploadDoc = async (file: File | undefined) => {
    if (!file || !active) return;
    if (!DOC_TYPES.includes(file.type)) return toast.error("Faqat PDF, JPEG yoki PNG yuklang.");
    if (file.size > 10 * 1024 * 1024) return toast.error("Hujjat 10 MB dan oshmasligi kerak.");
    setUploading(true);
    try {
      await api.upload(`/employees/${employee.id}/consents/${active.id}/document`, file);
      toast.success("Imzolangan hujjat biriktirildi", "Endi yuzni roʻyxatga olish mumkin.");
      reload();
    } catch (err) {
      toast.error("Hujjatni yuklab boʻlmadi", errorMessage(err));
    } finally {
      setUploading(false);
      if (docInput.current) docInput.current.value = "";
    }
  };

  const bio = BIOMETRIC_STATUS[employee.biometric.status];

  return (
    <Card>
      <CardHeader title="Biometrik rozilik" description={bio.hint} />
      <CardBody className="space-y-4">
        {employee.attendance_method === "MANUAL" ? (
          <Alert tone="info">Xodim qoʻlda davomatda. Biometrik maʼlumot yigʻilmaydi.</Alert>
        ) : null}

        {consents.error ? <ErrorState error={consents.error} onRetry={consents.reload} /> : null}

        {active ? (
          <div className="rounded-lg border border-line bg-subtle p-3 text-sm">
            <p className="font-medium text-navy-900">Faol rozilik · {active.consent_text_version}</p>
            <p className="text-muted">Qayd etilgan: {formatDate(active.granted_at)} {formatTime(active.granted_at)}</p>
            <p className="mt-1">
              {active.has_document ? (
                <Badge tone="success">Imzolangan hujjat biriktirilgan</Badge>
              ) : (
                <Badge tone="warning">Imzolangan hujjat yoʻq</Badge>
              )}
            </p>
          </div>
        ) : null}

        {writable ? (
          <div className="flex flex-col gap-2">
            {!active && employee.attendance_method === "FACE" && employee.status !== "TERMINATED" ? (
              <Button icon={<FileSignature className="h-4 w-4" />} onClick={() => setRecording(true)}>
                Roziligini qayd etish
              </Button>
            ) : null}
            {active && !active.has_document ? (
              <>
                <input ref={docInput} type="file" accept="application/pdf,image/jpeg,image/png" className="hidden" onChange={(e) => uploadDoc(e.target.files?.[0])} />
                <Button loading={uploading} icon={<Upload className="h-4 w-4" />} onClick={() => docInput.current?.click()}>
                  Imzolangan hujjatni yuklash
                </Button>
              </>
            ) : null}
            {active ? (
              <Button variant="ghost" className="text-danger-700 hover:bg-danger-50" icon={<ShieldOff className="h-4 w-4" />} onClick={() => setRevoking(active)}>
                Rozilikni bekor qilish
              </Button>
            ) : null}
          </div>
        ) : null}


        {consents.data && consents.data.length > 0 ? (
          <div>
            <p className="mb-2 text-xs font-semibold tracking-wide text-muted uppercase">Tarix</p>
            <ul className="space-y-2 text-sm">
              {consents.data.map((c) => (
                <li key={c.id} className="flex items-start justify-between gap-2 rounded-lg border border-line p-2.5">
                  <div className="min-w-0">
                    <p className="font-medium">{formatDate(c.granted_at)} · {c.consent_text_version}</p>
                    {c.revoked_at ? (
                      <p className="text-xs text-danger-700">Bekor qilingan {formatDate(c.revoked_at)}: {c.revocation_reason}</p>
                    ) : (
                      <p className="text-xs text-success-700">Faol</p>
                    )}
                  </div>
                  {c.has_document ? (
                    <Button
                      variant="ghost"
                      size="icon"
                      aria-label="Hujjatni yuklab olish"
                      title="Yuklab olish (jurnalga yoziladi)"
                      onClick={() =>
                        downloadBlob(
                          `/employees/${employee.id}/consents/${c.id}/document`,
                          `rozilik-${employee.employee_code}-${c.granted_at.slice(0, 10)}.${c.document_content_type === "application/pdf" ? "pdf" : "jpg"}`,
                        ).catch((err) => toast.error("Yuklab boʻlmadi", errorMessage(err)))
                      }
                    >
                      <Download className="h-4 w-4" />
                    </Button>
                  ) : null}
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </CardBody>

      <ConfirmDialog
        open={recording}
        onClose={() => {
          setRecording(false);
          setSigned(false);
        }}
        title="Biometrik roziligini qayd etish"
        description={`Rozilik varaqasi versiyasi: ${version.data?.consent_text_version ?? "…"}`}
        confirmLabel="Qayd etish"
        confirmDisabled={!signed || !version.data}
        onConfirm={async () => {
          await api.request(`/employees/${employee.id}/consents`, {
            method: "POST",
            json: { consent_text_version: version.data?.consent_text_version, employee_signed: true },
          });
          toast.success("Rozilik qayd etildi", "Endi imzolangan hujjatni yuklang.");
          setSigned(false);
          reload();
        }}
      >
        <Alert tone="info">
          Xodim rozilik varaqasini <strong>shaxsan oʻzi imzolagan</strong> boʻlishi shart. Imzolangan nusxa keyingi
          qadamda yuklanadi. Usiz yuzni roʻyxatga olib boʻlmaydi.
        </Alert>
        <Checkbox label="Xodim varaqani shaxsan imzoladi" checked={signed} onChange={(e) => setSigned(e.target.checked)} />
      </ConfirmDialog>

      <ConfirmDialog
        open={revoking !== null}
        onClose={() => setRevoking(null)}
        title="Rozilikni bekor qilish"
        description="Xodimning barcha yuz namunalari darhol va qaytarib boʻlmaydigan tarzda oʻchiriladi."
        confirmLabel="Bekor qilish va oʻchirish"
        tone="danger"
        reasonLabel="Sabab"
        onConfirm={async (reason) => {
          if (!revoking) return;
          await api.request(`/employees/${employee.id}/consents/${revoking.id}/revoke`, { method: "POST", json: { reason } });
          toast.success("Rozilik bekor qilindi", "Yuz maʼlumotlari oʻchirildi.");
          reload();
        }}
      />
    </Card>
  );
}

// --- recent attendance ------------------------------------------------------------------------

function RecentAttendance({ employeeId }: { employeeId: string }) {
  const today = todayInOrg();
  const from = addDays(today, -13);
  const days = useApiQuery<Page<Day>>(`/attendance/days?employee_id=${employeeId}&date_from=${from}&date_to=${today}&page_size=31`);
  const rows = days.data ? [...days.data.items].reverse() : undefined;
  return (
    <Card>
      <CardHeader
        title="Soʻnggi 14 kun davomati"
        actions={
          <Link href={`/attendance/history?employee=${employeeId}`} className="text-sm text-brand-600 hover:underline">
            <History className="mr-1 inline h-4 w-4" aria-hidden />
            Toʻliq tarix
          </Link>
        }
      />
      <DataTable<Day>
        loading={days.loading}
        error={days.error}
        onRetry={days.reload}
        rows={rows}
        rowKey={(d) => d.work_date}
        columns={[
          { key: "date", header: "Sana", cell: (d) => formatDate(`${d.work_date}T12:00:00Z`) },
          { key: "status", header: "Holat", cell: (d) => <DayStatusBadge status={d.status} inProgress={d.in_progress} /> },
          { key: "in", header: "Keldi", cell: (d) => formatTime(d.arrival) },
          { key: "out", header: "Ketdi", cell: (d) => formatTime(d.departure), hideOnMobile: true },
          { key: "worked", header: "Ishladi", cell: (d) => (d.worked_minutes ? formatMinutes(d.worked_minutes) : "—"), hideOnMobile: true },
          { key: "late", header: "Kechikish", align: "right", cell: (d) => (d.late_minutes ? <Badge tone="warning">{d.late_minutes} daq</Badge> : "—") },
        ]}
      />
    </Card>
  );
}
