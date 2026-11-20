"use client";

import { ScanFace } from "lucide-react";
import { useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { formatDate, formatTime } from "@/lib/format";
import { ROLE } from "@/lib/labels";
import type { Setting } from "@/lib/types";
import { useApiQuery } from "@/lib/use-api";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { DetailItem, RequirePermission } from "@/components/ui/controls";
import { PageHeader } from "@/components/ui/page-header";
import { DataTable } from "@/components/ui/table";
import { useToast } from "@/components/ui/toast";
import { TelegramSettings } from "@/components/telegram-settings";

const SWITCH = "recognition.attendance_enabled";

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
  const enabled = settings.data?.find((s) => s.key === SWITCH)?.value === true;
  return (
    <>
      {settings.data ? <RecognitionAttendanceSwitch enabled={enabled} onChanged={settings.reload} /> : null}
      {settings.data ? <ReviewSettings values={settings.data} onChanged={settings.reload} /> : null}
      <TelegramSettings />
      <Card>
        <CardHeader title="Tizim sozlamalari" description="Koʻpchiligi faqat oʻqish uchun. Sessiya va login cheklovlari server muhitida (.env) sozlanadi." />
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
    </>
  );
}

/** Master switch: confident recognitions create attendance events. Off by default (D22). */
function RecognitionAttendanceSwitch({ enabled, onChanged }: { enabled: boolean; onChanged: () => void }) {
  const { can } = useAuth();
  const toast = useToast();
  const [confirm, setConfirm] = useState(false);
  const writable = can("settings:write");

  const set = async (value: boolean) => {
    await api.request(`/settings/${SWITCH}`, { method: "PATCH", json: { value } });
    toast.success(value ? "Tanish davomatga ulandi" : "Tanish davomatdan uzildi");
    onChanged();
  };

  return (
    <Card>
      <CardHeader
        title={<span className="flex items-center gap-2"><ScanFace className="h-4 w-4 text-brand-600" aria-hidden /> Tanishni davomatga ulash</span>}
        description="Yoqilganda ishonchli tanilgan xodimlar uchun kirish/chiqish avtomatik yoziladi."
        actions={<Badge tone={enabled ? "success" : "neutral"}>{enabled ? "Yoqilgan" : "Oʻchirilgan"}</Badge>}
      />
      <CardBody className="space-y-3">
        <ul className="list-inside list-disc text-sm text-navy-800">
          <li>Faqat “Tanildi” natijasi yoziladi. “Noaniq” va “Roʻyxatda yoʻq” hech qachon davomatga tushmaydi.</li>
          <li>Faqat sifat testidan oʻtgan va tanish yoqilgan kameralar hisobga olinadi. Kamera vazifasi (Kirish/Chiqish) yoʻnalishni belgilaydi.</li>
          <li>Bir kamerada takroriy koʻrinishlar bitta yozuv boʻladi. Xatolarni Davomat sahifasida qoʻlda tuzatish mumkin.</li>
        </ul>
        {!enabled ? (
          <Alert tone="warning">
            Yoqishdan oldin tanishni bir necha xodim bilan sinab koʻring (AI tanish sahifasi): har kim toʻgʻri tanilyaptimi va
            bir-biri bilan adashtirilmayaptimi.
          </Alert>
        ) : null}
        {writable ? (
          enabled ? (
            <Button variant="secondary" onClick={() => void set(false).catch(() => toast.error("Oʻzgartirib boʻlmadi"))}>Oʻchirish</Button>
          ) : (
            <Button onClick={() => setConfirm(true)}>Yoqish</Button>
          )
        ) : (
          <p className="text-xs text-muted">Faqat bosh administrator oʻzgartira oladi.</p>
        )}
      </CardBody>
      <ConfirmDialog
        open={confirm}
        onClose={() => setConfirm(false)}
        title="Tanishni davomatga ulash"
        description="Shu paytdan boshlab ishonchli tanilgan xodimlarning kelishi va ketishi avtomatik yoziladi. Tanish yetarli darajada sinalganiga ishonchingiz komilmi?"
        confirmLabel="Ha, yoqish"
        onConfirm={async () => {
          await set(true);
        }}
      />
    </Card>
  );
}

const REVIEW_KEYS: { key: string; title: string; hint: string }[] = [
  {
    key: "recognition.review_enabled",
    title: "Noaniq yuzlarni koʻrib chiqishga saqlash",
    hint: "Tizim aniq taniy olmagan xodim yuzi (faqat yuz qismi) shifrlanib, AI tanish sahifasidagi Koʻrib chiqish roʻyxatiga tushadi.",
  },
  {
    key: "recognition.store_unknown_snapshots",
    title: "“Roʻyxatda yoʻq” yuzlarni ham saqlash",
    hint: "Diqqat: bunga mehmonlar va boshqa begona odamlar ham kiradi. Kerak boʻlmasa oʻchiring. Saqlash muddati: retention.unknown_snapshot_days.",
  },
];

function ReviewSettings({ values, onChanged }: { values: Setting[]; onChanged: () => void }) {
  const { can } = useAuth();
  const toast = useToast();
  const writable = can("settings:write");
  const [busy, setBusy] = useState<string | null>(null);
  const toggle = async (key: string, value: boolean) => {
    setBusy(key);
    try {
      await api.request(`/settings/${key}`, { method: "PATCH", json: { value } });
      onChanged();
    } catch {
      toast.error("Oʻzgartirib boʻlmadi");
    } finally {
      setBusy(null);
    }
  };
  return (
    <Card>
      <CardHeader title="Koʻrib chiqish roʻyxati" description="Rasmlar faqat qaror qabul qilinguncha saqlanadi va muddati oʻtgach oʻchadi." />
      <CardBody className="divide-y divide-line">
        {REVIEW_KEYS.map(({ key, title, hint }) => {
          const on = values.find((s) => s.key === key)?.value === true;
          return (
            <div key={key} className="flex items-start justify-between gap-4 py-3 first:pt-0 last:pb-0">
              <div>
                <p className="text-sm font-medium text-navy-900">{title}</p>
                <p className="text-xs text-muted">{hint}</p>
              </div>
              {writable ? (
                <Button size="sm" variant={on ? "secondary" : "primary"} loading={busy === key} onClick={() => void toggle(key, !on)}>
                  {on ? "Oʻchirish" : "Yoqish"}
                </Button>
              ) : (
                <Badge tone={on ? "success" : "neutral"}>{on ? "Yoqilgan" : "Oʻchirilgan"}</Badge>
              )}
            </div>
          );
        })}
      </CardBody>
    </Card>
  );
}
