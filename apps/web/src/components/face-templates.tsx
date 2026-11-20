"use client";

import { Camera as CameraIcon, ScanFace, Trash2, Upload } from "lucide-react";
import { useRef, useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { enrolmentError } from "@/lib/face-scan";
import { formatDate, formatTime } from "@/lib/format";
import type { Camera, Employee, FaceTemplate, Page } from "@/lib/types";
import { useApiQuery } from "@/lib/use-api";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/confirm";
import { Field, Select } from "@/components/ui/form";
import { Skeleton } from "@/components/ui/spinner";
import { useToast } from "@/components/ui/toast";
import { FaceScanModal } from "@/components/face-scan";

export { enrolmentError } from "@/lib/face-scan";

export function FaceTemplatesCard({ employee, onChanged }: { employee: Employee; onChanged: () => void }) {
  const { can } = useAuth();
  const toast = useToast();
  const writable = can("biometrics:write");
  const templates = useApiQuery<FaceTemplate[]>(`/employees/${employee.id}/face-templates`);
  const cameras = useApiQuery<Page<Camera>>(writable && can("cameras:read") ? "/cameras?page_size=200" : null);
  const [cameraId, setCameraId] = useState("");
  const [busy, setBusy] = useState<"upload" | null>(null);
  const [scanning, setScanning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<FaceTemplate | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const items = templates.data ?? [];
  const canEnroll = employee.biometric.can_enroll;

  const done = () => {
    toast.success("Yuz namunasi qoʻshildi");
    templates.reload();
    onChanged();
  };

  const upload = async (file: File) => {
    setBusy("upload");
    setError(null);
    try {
      await api.upload(`/employees/${employee.id}/face-templates`, file, "POST");
      done();
    } catch (err) {
      setError(enrolmentError(err));
    } finally {
      setBusy(null);
      if (fileRef.current) fileRef.current.value = "";
    }
  };

  const scanCamera = cameras.data?.items.find((c) => c.id === cameraId);

  return (
    <Card>
      <CardHeader
        title="Yuz namunalari"
        description="Yuzni tanish uchun. Rasm saqlanmaydi, faqat shifrlangan namuna saqlanadi."
      />
      <CardBody className="space-y-4">
        {error ? <Alert tone="danger">{error}</Alert> : null}
        {!canEnroll && items.length === 0 ? (
          <Alert tone="info">Yuz namunasi olish uchun hujjatlashtirilgan faol rozilik kerak.</Alert>
        ) : null}

        {templates.loading && !templates.data ? <Skeleton className="h-12 w-full" /> : null}
        {items.length > 0 ? (
          <ul className="divide-y divide-line rounded-lg border border-line">
            {items.map((t, i) => (
              <li key={t.id} className="flex items-center justify-between gap-3 px-3 py-2 text-sm">
                <span className="flex items-center gap-2">
                  <ScanFace className="h-4 w-4 text-brand-600" aria-hidden />
                  Namuna {i + 1}
                  <span className="text-xs text-muted">
                    {formatDate(t.created_at)} {formatTime(t.created_at)} · sifat {Math.round(t.quality_score * 100)}%
                  </span>
                </span>
                {writable ? (
                  <Button variant="ghost" size="icon" aria-label={`Namuna ${i + 1}: oʻchirish`} onClick={() => setDeleting(t)}>
                    <Trash2 className="h-4 w-4" />
                  </Button>
                ) : null}
              </li>
            ))}
          </ul>
        ) : templates.data ? (
          <p className="text-sm text-muted">Hali yuz namunasi yoʻq.</p>
        ) : null}

        {writable && canEnroll ? (
          <div className="space-y-3">
            <p className="text-xs text-muted">
              3–5 ta namuna tavsiya etiladi: yuz toʻgʻriga qaragan, yaxshi yoritilgan, rasmda faqat shu xodim.
            </p>
            <div className="flex flex-wrap items-end gap-3">
              <input
                ref={fileRef}
                type="file"
                accept="image/jpeg,image/png"
                className="hidden"
                onChange={(e) => {
                  const file = e.target.files?.[0];
                  if (file) void upload(file);
                }}
              />
              <Button
                variant="secondary"
                icon={<Upload className="h-4 w-4" />}
                loading={busy === "upload"}
                disabled={busy !== null}
                onClick={() => fileRef.current?.click()}
              >
                Rasm yuklash
              </Button>
              {cameras.data && cameras.data.items.length > 0 ? (
                <>
                  <Field label="Kamera" className="w-56">
                    <Select value={cameraId} onChange={(e) => setCameraId(e.target.value)}>
                      <option value="">Tanlang…</option>
                      {cameras.data.items.map((c) => (
                        <option key={c.id} value={c.id} disabled={!c.is_enabled}>
                          {c.name}{c.is_enabled ? "" : " (monitoring oʻchiq)"}
                        </option>
                      ))}
                    </Select>
                  </Field>
                  <Button
                    icon={<CameraIcon className="h-4 w-4" />}
                    disabled={busy !== null || !cameraId}
                    onClick={() => setScanning(true)}
                  >
                    Kamera bilan skanerlash
                  </Button>
                </>
              ) : null}
            </div>
            <p className="text-xs text-muted">
              Skanerlashda oyna ochiladi: toʻgʻriga, chapga, oʻngga qarash soʻraladi va har bir holatda namuna avtomatik olinadi.
            </p>
          </div>
        ) : null}
        {scanning && scanCamera ? (
          <FaceScanModal
            employeeId={employee.id}
            employeeName={employee.full_name}
            cameraId={scanCamera.id}
            cameraName={scanCamera.name}
            existing={items}
            onClose={() => setScanning(false)}
            onChanged={() => {
              templates.reload();
              onChanged();
            }}
          />
        ) : null}
      </CardBody>
      <ConfirmDialog
        open={deleting !== null}
        onClose={() => setDeleting(null)}
        title="Yuz namunasini oʻchirish"
        description="Namuna butunlay oʻchiriladi. Tanish xizmati uni ~30 soniya ichida unutadi."
        confirmLabel="Oʻchirish"
        tone="danger"
        onConfirm={async () => {
          if (!deleting) return;
          await api.request(`/employees/${employee.id}/face-templates/${deleting.id}`, { method: "DELETE" });
          toast.success("Namuna oʻchirildi");
          templates.reload();
          onChanged();
        }}
      />
    </Card>
  );
}
