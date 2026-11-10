"use client";

import { Camera as CameraIcon, Gauge, Image as ImageIcon, Pencil, Plug, Plus, Power, RefreshCw, ScanFace, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { canEnableRecognition, cameraBody, cameraPatch, emptyForm, formFromCamera, statusView, validateCameraForm, type CameraForm } from "@/lib/cameras";
import { errorMessage, fieldErrors } from "@/lib/errors";
import { formatDate, formatTime } from "@/lib/format";
import { CAMERA_DIRECTION, CAMERA_ERROR, CAMERA_PROVIDER, QUALITY_REASON, QUALITY_STATUS } from "@/lib/labels";
import type { Camera, CameraDirection, CameraProvider, Page, ProbeResult, QualityReport, TestSimulate } from "@/lib/types";
import { useApiQuery } from "@/lib/use-api";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/confirm";
import { DetailItem, FilterBar, RequirePermission } from "@/components/ui/controls";
import { Checkbox, Field, FormError, Input, PasswordInput, Select } from "@/components/ui/form";
import { Modal } from "@/components/ui/modal";
import { PageHeader } from "@/components/ui/page-header";
import { Skeleton } from "@/components/ui/spinner";
import { EmptyState } from "@/components/ui/states";
import { DataTable } from "@/components/ui/table";
import { useToast } from "@/components/ui/toast";

const POLL_MS = 10_000;

export default function CamerasPage() {
  return (
    <RequirePermission permission="cameras:read">
      <Cameras />
    </RequirePermission>
  );
}

function Cameras() {
  const { can } = useAuth();
  const writable = can("cameras:write");
  const [provider, setProvider] = useState("");
  const [direction, setDirection] = useState("");
  const params = new URLSearchParams({ page_size: "200" });
  if (provider) params.set("provider", provider);
  if (direction) params.set("direction", direction);
  const list = useApiQuery<Page<Camera>>(`/cameras?${params}`);
  const [openId, setOpenId] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const { reload } = list;

  // Status comes from the camera service every few seconds: refresh while the tab is visible.
  useEffect(() => {
    const id = setInterval(() => {
      if (document.visibilityState === "visible") reload();
    }, POLL_MS);
    return () => clearInterval(id);
  }, [reload]);

  const open = list.data?.items.find((c) => c.id === openId) ?? null;

  return (
    <>
      <PageHeader
        title="Kameralar"
        description="Kirish va chiqish kameralari, ulanish holati va oqim sifati"
        actions={writable ? <Button icon={<Plus className="h-4 w-4" />} onClick={() => setCreating(true)}>Yangi kamera</Button> : null}
      />
      <Alert tone="info" className="mb-6" title="Qanday ishlaydi">
        Video doimiy yozib olinmaydi: kadrlar kamera xizmatida (mahalliy) faqat xotirada qayta ishlanadi, suratlar
        soʻralganda olinadi va saqlanmaydi. Kamera parollari brauzerga hech qachon yuborilmaydi. Oqim yuzni tanish
        uchun yaroqli deb hisoblanmaydi, toki <b>sifat testi</b> oʻtmaguncha. Yuzni tanish hali davomatga ulanmagan.
      </Alert>
      <Card>
        <FilterBar>
          <Field label="Turi" className="sm:w-56">
            <Select value={provider} onChange={(e) => setProvider(e.target.value)}>
              <option value="">Barchasi</option>
              {Object.entries(CAMERA_PROVIDER).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
            </Select>
          </Field>
          <Field label="Yoʻnalish" className="sm:w-56">
            <Select value={direction} onChange={(e) => setDirection(e.target.value)}>
              <option value="">Barchasi</option>
              {Object.entries(CAMERA_DIRECTION).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
            </Select>
          </Field>
        </FilterBar>
        <DataTable<Camera>
          caption="Kameralar"
          loading={list.loading && !list.data}
          error={list.error}
          onRetry={list.reload}
          rows={list.data?.items}
          rowKey={(c) => c.id}
          empty={<EmptyState title="Hali kamera yoʻq" body={writable ? "Uskunasiz boshlash uchun “Test kamera” qoʻshing." : undefined} />}
          columns={[
            {
              key: "name",
              header: "Kamera",
              cell: (c) => (
                <button type="button" className="text-left" onClick={() => setOpenId(c.id)}>
                  <span className="block font-medium text-navy-900 hover:underline">{c.name}</span>
                  <span className="block text-xs text-muted">{c.location ?? CAMERA_PROVIDER[c.provider].label}</span>
                </button>
              ),
            },
            { key: "dir", header: "Yoʻnalish", cell: (c) => CAMERA_DIRECTION[c.direction].label },
            { key: "provider", header: "Turi", cell: (c) => CAMERA_PROVIDER[c.provider].label, hideOnMobile: true },
            {
              key: "status",
              header: "Holat",
              cell: (c) => {
                const v = statusView(c);
                const fps = c.monitoring?.runtime?.measured_fps;
                return (
                  <span title={v.hint ?? c.last_error ?? undefined}>
                    <Badge tone={v.tone}>{v.label}</Badge>
                    {fps && !c.monitoring?.stale ? <span className="ml-2 text-xs text-muted tabular-nums">{fps} kadr/s</span> : null}
                  </span>
                );
              },
            },
            {
              key: "quality",
              header: "Oqim sifati",
              cell: (c) => <Badge tone={QUALITY_STATUS[c.quality_status].tone}>{QUALITY_STATUS[c.quality_status].label}</Badge>,
              hideOnMobile: true,
            },
            {
              key: "actions",
              header: "",
              align: "right",
              cell: (c) => <Button variant="ghost" size="sm" onClick={() => setOpenId(c.id)}>Batafsil</Button>,
            },
          ]}
        />
      </Card>
      {creating ? <CameraFormModal camera={null} onClose={() => setCreating(false)} onSaved={(c) => { list.reload(); setOpenId(c.id); }} /> : null}
      {open ? <CameraDetails camera={open} writable={writable} onClose={() => setOpenId(null)} onChanged={list.reload} /> : null}
    </>
  );
}

// --- details --------------------------------------------------------------------------------

function CameraDetails({ camera: c, writable, onClose, onChanged }: { camera: Camera; writable: boolean; onClose: () => void; onChanged: () => void }) {
  const toast = useToast();
  const [busy, setBusy] = useState<string | null>(null);
  const [probe, setProbe] = useState<ProbeResult | null>(null);
  const [snapshotUrl, setSnapshotUrl] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const rt = c.monitoring?.runtime ?? null;
  const status = statusView(c);
  const q = QUALITY_STATUS[c.quality_status];

  useEffect(() => () => {
    if (snapshotUrl) URL.revokeObjectURL(snapshotUrl);
  }, [snapshotUrl]);

  const run = async (name: string, fn: () => Promise<void>) => {
    setBusy(name);
    setError(null);
    try {
      await fn();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(null);
    }
  };

  const post = (path: string, json?: unknown) => api.request<Camera>(`/cameras/${c.id}${path}`, { method: "POST", json });

  return (
    <>
      <Modal
        open
        size="lg"
        onClose={onClose}
        dismissible={busy === null}
        title={c.name}
        description={`${CAMERA_PROVIDER[c.provider].label} · ${CAMERA_DIRECTION[c.direction].label}`}
        footer={<Button variant="secondary" onClick={onClose} disabled={busy !== null}>Yopish</Button>}
      >
        <div className="space-y-6">
          <FormError>{error}</FormError>
          <section>
            <h3 className="mb-3 text-sm font-semibold text-navy-900">Ulanish va holat</h3>
            <dl className="grid grid-cols-1 gap-4 sm:grid-cols-3">
              <DetailItem label="Holat"><Badge tone={status.tone}>{status.label}</Badge>{status.hint ? <p className="mt-1 text-xs text-muted">{status.hint}</p> : null}</DetailItem>
              <DetailItem label="Manba">
                {c.provider === "RTSP" ? <code className="text-xs break-all">{c.stream_url_redacted}</code> : c.provider === "USB" ? `Qurilma ${c.device}` : `Sunʼiy (${c.settings.test?.simulate && c.settings.test.simulate !== "none" ? `simulyatsiya: ${c.settings.test.simulate}` : "normal"})`}
              </DetailItem>
              <DetailItem label="Login/parol">{c.provider === "RTSP" ? (c.has_credentials ? "Saqlangan (shifrlangan)" : "Yoʻq") : "—"}</DetailItem>
              <DetailItem label="Kadr tezligi">{rt?.measured_fps ? `${rt.measured_fps} kadr/s` : "—"}</DetailItem>
              <DetailItem label="Ruxsat">{rt?.width ? `${rt.width}×${rt.height}` : "—"}</DetailItem>
              <DetailItem label="Oxirgi kadr">{c.last_seen_at ? `${formatDate(c.last_seen_at)} ${formatTime(c.last_seen_at)}` : "—"}</DetailItem>
              <DetailItem label="Qayta ulanishlar">{rt?.reconnects ?? "—"}</DetailItem>
              <DetailItem label="Oʻqish xatolari">{rt?.read_failures ?? "—"}</DetailItem>
              <DetailItem label="Xato">{c.last_error ? <span className="text-danger-700">{(rt?.error_code && CAMERA_ERROR[rt.error_code]) || c.last_error}</span> : "—"}</DetailItem>
            </dl>
          </section>

          {writable ? (
            <section className="flex flex-wrap gap-2">
              <Button variant="secondary" icon={<Pencil className="h-4 w-4" />} onClick={() => setEditing(true)} disabled={busy !== null}>Tahrirlash</Button>
              <Button
                variant="secondary"
                icon={<Power className="h-4 w-4" />}
                loading={busy === "power"}
                disabled={busy !== null}
                onClick={() => run("power", async () => {
                  await post(c.is_enabled ? "/disable" : "/enable");
                  toast.success(c.is_enabled ? "Kamera oʻchirildi" : "Kamera yoqildi: kamera xizmati ulanmoqda");
                  onChanged();
                })}
              >
                {c.is_enabled ? "Monitoringni oʻchirish" : "Monitoringni yoqish"}
              </Button>
              <Button
                variant="secondary"
                icon={<Plug className="h-4 w-4" />}
                loading={busy === "probe"}
                disabled={busy !== null}
                onClick={() => run("probe", async () => setProbe(await api.request<ProbeResult>(`/cameras/${c.id}/test`, { method: "POST" })))}
              >
                Ulanishni tekshirish
              </Button>
              <Button
                variant="secondary"
                icon={<ImageIcon className="h-4 w-4" />}
                loading={busy === "snap"}
                disabled={busy !== null}
                onClick={() => run("snap", async () => {
                  const blob = await api.blob(`/cameras/${c.id}/snapshot`);
                  setSnapshotUrl(URL.createObjectURL(blob));
                })}
              >
                Kadr olish
              </Button>
              <Button variant="ghost" icon={<Trash2 className="h-4 w-4" />} onClick={() => setDeleting(true)} disabled={busy !== null}>Oʻchirish</Button>
            </section>
          ) : null}

          {probe ? (
            <Alert tone={probe.ok ? "success" : "danger"} title={probe.ok ? "Ulanish ishlayapti" : "Ulanib boʻlmadi"}>
              {probe.ok
                ? `${probe.width}×${probe.height}${probe.reported_fps ? `, ${probe.reported_fps} kadr/s` : ""} (${probe.backend}). Bu faqat ulanish tekshiruvi, sifat testi emas.`
                : (probe.error_code && CAMERA_ERROR[probe.error_code]) || probe.error}
            </Alert>
          ) : null}

          {busy === "snap" ? <Skeleton className="aspect-video w-full" /> : null}
          {snapshotUrl && busy !== "snap" ? (
            <figure>
              {/* eslint-disable-next-line @next/next/no-img-element -- blob: URL from an authenticated fetch */}
              <img src={snapshotUrl} alt={`${c.name}: joriy kadr`} className="w-full rounded-lg border border-line" />
              <figcaption className="mt-1 text-xs text-muted">Joriy kadr. Saqlanmaydi; koʻrish audit jurnaliga yoziladi.</figcaption>
            </figure>
          ) : null}

          <section>
            <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-navy-900">
              <Gauge className="h-4 w-4" aria-hidden /> Oqim sifati (yuzni tanish uchun)
            </h3>
            <div className="flex flex-wrap items-center gap-3">
              <Badge tone={q.tone}>{q.label}</Badge>
              <span className="text-sm text-muted">{q.hint}</span>
            </div>
            {c.quality_report ? <QualityDetails report={c.quality_report} testedAt={c.quality_tested_at} /> : null}
            {writable ? (
              <div className="mt-4 flex flex-wrap gap-2">
                <Button
                  icon={<RefreshCw className="h-4 w-4" />}
                  loading={busy === "quality"}
                  disabled={busy !== null}
                  onClick={() => run("quality", async () => {
                    const updated = await post("/quality-test", { duration_seconds: 5 });
                    toast.success(`Sifat testi: ${QUALITY_STATUS[updated.quality_status].label}`);
                    onChanged();
                  })}
                >
                  {busy === "quality" ? "Sinalmoqda (~5 s)…" : "Sifat testini oʻtkazish"}
                </Button>
                <Button
                  variant="secondary"
                  icon={<ScanFace className="h-4 w-4" />}
                  loading={busy === "recog"}
                  disabled={busy !== null || (!c.recognition_enabled && !canEnableRecognition(c))}
                  onClick={() => run("recog", async () => {
                    await api.request(`/cameras/${c.id}`, { method: "PATCH", json: { recognition_enabled: !c.recognition_enabled } });
                    onChanged();
                  })}
                >
                  {c.recognition_enabled ? "Yuzni tanishni oʻchirish" : "Yuzni tanishga ruxsat berish"}
                </Button>
              </div>
            ) : null}
            {!canEnableRecognition(c) ? (
              <p className="mt-2 text-xs text-muted">Yuzni tanishga ruxsat faqat sifat testi “Sifat yetarli” boʻlganda beriladi.</p>
            ) : null}
          </section>
        </div>
      </Modal>
      {editing ? <CameraFormModal camera={c} onClose={() => setEditing(false)} onSaved={onChanged} /> : null}
      <ConfirmDialog
        open={deleting}
        onClose={() => setDeleting(false)}
        title="Kamerani oʻchirish"
        description={`“${c.name}” oʻchiriladi va monitoring toʻxtaydi. Mavjud davomat yozuvlari saqlanadi.`}
        confirmLabel="Oʻchirish"
        tone="danger"
        onConfirm={async () => {
          await api.request(`/cameras/${c.id}`, { method: "DELETE" });
          toast.success("Kamera oʻchirildi");
          onClose();
          onChanged();
        }}
      />
    </>
  );
}

function QualityDetails({ report: r, testedAt }: { report: QualityReport; testedAt: string | null }) {
  const s = r.stream;
  return (
    <div className="mt-3 space-y-3 rounded-lg border border-line bg-navy-50/40 p-4 text-sm">
      {r.reasons.length ? (
        <ul className="list-inside list-disc text-navy-800">
          {r.reasons.map((x) => <li key={x}>{QUALITY_REASON[x] ?? x}</li>)}
        </ul>
      ) : null}
      <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <DetailItem label="Ruxsat">{s.width ? `${s.width}×${s.height}` : "—"}</DetailItem>
        <DetailItem label="Kadr/s">{s.measured_fps ?? "—"}</DetailItem>
        <DetailItem label="Yorugʻlik">{s.brightness_mean ?? "—"}</DetailItem>
        <DetailItem label="Aniqlik">{s.sharpness_mean ?? "—"}</DetailItem>
        {r.faces ? (
          <>
            <DetailItem label="Yuzlar">{`${r.faces.passed}/${r.faces.faces} oʻtdi`}</DetailItem>
            <DetailItem label="Yuz kengligi (median)">{r.faces.median_face_width_px ? `${r.faces.median_face_width_px} px` : "—"}</DetailItem>
            <DetailItem label="Talab">{`≥ ${r.faces.min_face_px_target} px`}</DetailItem>
            <DetailItem label="Rad sabablari">
              {Object.entries(r.faces.rejection_reasons).map(([k, n]) => `${QUALITY_REASON[k] ?? k}: ${n}`).join(", ") || "—"}
            </DetailItem>
          </>
        ) : null}
      </dl>
      <p className="text-xs text-muted">
        {r.note} {r.recognition_service === "not_configured" ? "(Yuz tahlili uchun tanish xizmati ulanmagan.)" : null}
        {testedAt ? ` Sinov: ${formatDate(testedAt)} ${formatTime(testedAt)}.` : null}
      </p>
    </div>
  );
}

// --- create / edit --------------------------------------------------------------------------

function CameraFormModal({ camera, onClose, onSaved }: { camera: Camera | null; onClose: () => void; onSaved: (c: Camera) => void }) {
  const toast = useToast();
  const [f, setF] = useState<CameraForm>(() => (camera ? formFromCamera(camera) : emptyForm()));
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const set = <K extends keyof CameraForm>(k: K, v: CameraForm[K]) => setF((prev) => ({ ...prev, [k]: v }));

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    const local = validateCameraForm(f);
    setErrors(local);
    setFormError(null);
    if (Object.keys(local).length) return;
    setBusy(true);
    try {
      let saved: Camera;
      if (camera) {
        const patch = cameraPatch(f, camera);
        saved = Object.keys(patch).length ? await api.request<Camera>(`/cameras/${camera.id}`, { method: "PATCH", json: patch }) : camera;
        if (saved.quality_status === "UNTESTED" && camera.quality_status !== "UNTESTED") {
          toast.info("Oqim oʻzgardi: sifat testini qayta oʻtkazing.");
        }
      } else {
        saved = await api.request<Camera>("/cameras", { method: "POST", json: cameraBody(f) });
      }
      toast.success(camera ? "Kamera saqlandi" : "Kamera qoʻshildi (monitoring hali oʻchiq)");
      onSaved(saved);
      onClose();
    } catch (err) {
      const fe = fieldErrors(err, { CAMERA_EXISTS: "name" });
      setErrors(fe);
      if (!Object.keys(fe).length) setFormError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      open
      size="lg"
      onClose={onClose}
      dismissible={!busy}
      title={camera ? "Kamerani tahrirlash" : "Yangi kamera"}
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={busy}>Bekor qilish</Button>
          <Button type="submit" form="camera-form" loading={busy}>Saqlash</Button>
        </>
      }
    >
      <form id="camera-form" onSubmit={submit} noValidate className="space-y-4">
        <FormError>{formError}</FormError>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Field label="Nomi" required error={errors.name}>
            <Input value={f.name} maxLength={120} onChange={(e) => set("name", e.target.value)} autoFocus />
          </Field>
          <Field label="Joylashuv" error={errors.location}>
            <Input value={f.location} maxLength={200} onChange={(e) => set("location", e.target.value)} placeholder="Masalan: 1-qavat, asosiy eshik" />
          </Field>
          <Field label="Kamera turi" required hint={CAMERA_PROVIDER[f.provider].hint}>
            <Select value={f.provider} onChange={(e) => set("provider", e.target.value as CameraProvider)}>
              {Object.entries(CAMERA_PROVIDER).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
            </Select>
          </Field>
          <Field label="Vazifasi (yoʻnalish)" required hint={CAMERA_DIRECTION[f.direction].hint}>
            <Select value={f.direction} onChange={(e) => set("direction", e.target.value as CameraDirection)}>
              {Object.entries(CAMERA_DIRECTION).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
            </Select>
          </Field>
        </div>

        {f.provider === "RTSP" ? (
          <fieldset className="grid grid-cols-1 gap-4 rounded-lg border border-line p-4 sm:grid-cols-2">
            <legend className="px-1 text-sm font-medium text-navy-900">IP kamera</legend>
            <Field label="Manzil (IP yoki xost)" required error={errors.host}>
              <Input value={f.host} onChange={(e) => set("host", e.target.value)} placeholder="192.168.1.64" inputMode="url" />
            </Field>
            <Field label="RTSP port" required error={errors.rtsp_port}>
              <Input value={f.rtsp_port} onChange={(e) => set("rtsp_port", e.target.value)} inputMode="numeric" />
            </Field>
            <Field label="Asosiy oqim yoʻli" required error={errors.main_stream_path} hint="Login/parolsiz, masalan /Streams/Channels/101">
              <Input value={f.main_stream_path} onChange={(e) => set("main_stream_path", e.target.value)} />
            </Field>
            <Field label="Qoʻshimcha oqim yoʻli" error={errors.sub_stream_path}>
              <Input value={f.sub_stream_path} onChange={(e) => set("sub_stream_path", e.target.value)} />
            </Field>
            <Field label="Login" error={errors.username} hint={camera?.has_credentials ? "Boʻsh qoldirilsa, saqlangan login/parol oʻzgarmaydi" : undefined}>
              <Input value={f.username} onChange={(e) => set("username", e.target.value)} autoComplete="off" />
            </Field>
            <Field label="Parol" error={errors.password} hint="Shifrlab saqlanadi va hech qachon qaytarib koʻrsatilmaydi">
              <PasswordInput value={f.password} onChange={(e) => set("password", e.target.value)} autoComplete="new-password" showLabel="Koʻrsatish" hideLabel="Yashirish" />
            </Field>
            <Field label="Transport">
              <Select value={f.transport} onChange={(e) => set("transport", e.target.value as "tcp" | "udp")}>
                <option value="tcp">TCP (tavsiya)</option>
                <option value="udp">UDP</option>
              </Select>
            </Field>
            {camera?.has_credentials ? (
              <div className="flex items-end">
                <Checkbox label="Saqlangan login/parolni oʻchirish" checked={f.clear_credentials} onChange={(e) => set("clear_credentials", e.target.checked)} />
              </div>
            ) : null}
          </fieldset>
        ) : null}

        {f.provider === "USB" ? (
          <Field label="Qurilma" required error={errors.device} hint="0 = birinchi veb-kamera (Linux: /dev/video0). Kamera xizmati ishlayotgan kompyuterda.">
            <Input value={f.device} onChange={(e) => set("device", e.target.value)} />
          </Field>
        ) : null}

        {f.provider === "TEST" ? (
          <fieldset className="grid grid-cols-1 gap-4 rounded-lg border border-line p-4 sm:grid-cols-3">
            <legend className="px-1 text-sm font-medium text-navy-900">Test kamera</legend>
            <Field label="Manba">
              <Select value={f.test_source} onChange={(e) => set("test_source", e.target.value as "pattern" | "media")}>
                <option value="pattern">Sunʼiy tasvir</option>
                <option value="media">Fayl / papka (TEST_MEDIA_DIR)</option>
              </Select>
            </Field>
            {f.test_source === "media" ? (
              <Field label="Media nomi" required error={errors.test_media}>
                <Input value={f.test_media} onChange={(e) => set("test_media", e.target.value)} placeholder="entrance.mp4" />
              </Field>
            ) : null}
            <Field label="Nosozlik simulyatsiyasi" hint="Qayta ulanishni sinash uchun">
              <Select value={f.test_simulate} onChange={(e) => set("test_simulate", e.target.value as TestSimulate)}>
                <option value="none">Yoʻq</option>
                <option value="flaky">Beqaror (kadrlar yoʻqoladi)</option>
                <option value="disconnect">Uziladi (~5 s dan keyin)</option>
                <option value="offline">Aloqa yoʻq</option>
                <option value="freeze">Tasvir qotadi</option>
              </Select>
            </Field>
            <Field label="Kenglik" error={errors.test_width}><Input value={f.test_width} onChange={(e) => set("test_width", e.target.value)} inputMode="numeric" /></Field>
            <Field label="Balandlik" error={errors.test_height}><Input value={f.test_height} onChange={(e) => set("test_height", e.target.value)} inputMode="numeric" /></Field>
            <Field label="Kadr/s" error={errors.test_fps}><Input value={f.test_fps} onChange={(e) => set("test_fps", e.target.value)} inputMode="numeric" /></Field>
          </fieldset>
        ) : null}

        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Field label="Qayta ishlash tezligi (kadr/s)" error={errors.sample_fps} hint="Oqimdan qancha kadr tahlil qilinadi">
            <Input value={f.sample_fps} onChange={(e) => set("sample_fps", e.target.value)} inputMode="numeric" />
          </Field>
          <Field label="Kerakli yuz kengligi (px)" error={errors.min_face_px} hint="Sifat testi shu qiymat bilan tekshiradi">
            <Input value={f.min_face_px} onChange={(e) => set("min_face_px", e.target.value)} inputMode="numeric" />
          </Field>
        </div>
        <p className="flex items-center gap-2 text-xs text-muted">
          <CameraIcon className="h-4 w-4" aria-hidden />
          Saqlangandan keyin monitoring oʻchiq boʻladi. Uni kamera tafsilotlaridan yoqing.
        </p>
      </form>
    </Modal>
  );
}
