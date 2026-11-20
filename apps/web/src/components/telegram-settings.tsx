"use client";

import { Copy, ExternalLink, Eye, Plus, Send, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { errorMessage } from "@/lib/errors";
import { formatDate, formatTime } from "@/lib/format";
import { CENTRE_ONLY_KINDS, NOTIFY_KINDS, NOTIFY_LOG_KIND, isAdminChat, type TelegramLink, type TelegramStatus } from "@/lib/telegram";
import type { Branch } from "@/lib/types";
import { useApiQuery } from "@/lib/use-api";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/confirm";
import { Checkbox, Field, Input } from "@/components/ui/form";
import { Modal } from "@/components/ui/modal";
import { useToast } from "@/components/ui/toast";

const BASE = "/notifications/telegram";

/** Telegram notifications: bot status, master switch, schedule, linked chats. */
export function TelegramSettings() {
  const { can, allBranches, branchId } = useAuth();
  const toast = useToast();
  /** Global switches (master switch, time, intervals): super administrator only. */
  const writable = can("settings:write");
  /** Linking / changing chats of the shown branch. */
  const chats = can("notifications:write");
  const q = useApiQuery<TelegramStatus>(BASE);
  const branches = useApiQuery<Branch[]>(allBranches ? "/branches" : null);
  const branchName = (id: string) => branches.data?.find((b) => b.id === id)?.name;
  /** Where a newly linked chat belongs: the shown branch, or the centre in "all branches". */
  const linkTarget = branchId ? (branchName(branchId) ?? "tanlangan filial") : null;
  const { reload } = q;
  const [busy, setBusy] = useState<string | null>(null);
  const [linking, setLinking] = useState(false);
  const [preview, setPreview] = useState<string | null | undefined>(undefined);
  const [removing, setRemoving] = useState<string | null>(null);
  const [timeDraft, setTimeDraft] = useState<string | null>(null); // null = not edited
  const s = q.data;
  const admins = s?.recipients.filter(isAdminChat) ?? [];
  const employeeChats = (s?.recipients.length ?? 0) - admins.length;
  const time = timeDraft ?? s?.daily_summary_time ?? "";

  const run = async (key: string, fn: () => Promise<unknown>, ok?: string) => {
    setBusy(key);
    try {
      await fn();
      if (ok) toast.success(ok);
      reload();
    } catch (err) {
      toast.error("Bajarilmadi", errorMessage(err));
    } finally {
      setBusy(null);
    }
  };
  const setting = (key: string, value: unknown) => api.request(`/settings/${key}`, { method: "PATCH", json: { value } });

  if (!s) {
    return (
      <Card>
        <CardHeader title="Telegram bildirishnomalari" />
        <CardBody>{q.error ? <Alert tone="danger">{errorMessage(q.error)}</Alert> : <p className="text-sm text-muted">Yuklanmoqda…</p>}</CardBody>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader
        title="Telegram bildirishnomalari"
        description="Kunlik hisobot, kechikishlar, koʻrib chiqish va kamera nosozliklari haqida xabarlar. Xabarlarda faqat ism va vaqt boʻladi, rasm yuborilmaydi."
        actions={
          s.configured ? (
            <Badge tone={s.enabled ? "success" : "neutral"}>{s.enabled ? "Yoqilgan" : "Oʻchirilgan"}</Badge>
          ) : (
            <Badge tone="warning">Bot sozlanmagan</Badge>
          )
        }
      />
      <CardBody className="space-y-5">
        {!s.configured ? (
          <Alert tone="info" title="Botni ulash (bir marta)">
            <ol className="mt-1 list-decimal space-y-1 pl-5">
              <li>Telegramda <b>@BotFather</b> ni oching → <code>/newbot</code> → botga nom bering.</li>
              <li>BotFather bergan <b>tokenni</b> nusxa oling.</li>
              <li>
                <code className="font-mono">apps/api/.env</code> fayliga qoʻshing: <code className="font-mono">TELEGRAM_BOT_TOKEN=…</code>
              </li>
              <li>Tizimni qayta ishga tushiring: <code className="font-mono">npm run stop</code>, keyin <code className="font-mono">npm start</code>.</li>
            </ol>
            <p className="mt-2">Token maxfiy: uni hech kimga yubormang va saytga kiritmang.</p>
          </Alert>
        ) : null}
        {s.bot_error ? <Alert tone="danger" title="Telegram bilan aloqa yoʻq">{s.bot_error}</Alert> : null}
        {s.worker_error ? <Alert tone="warning" title="Oxirgi xato">{s.worker_error}</Alert> : null}

        {s.configured ? (
          <>
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <p className="text-sm font-medium text-navy-900">
                  Bot: {s.bot_username ? <a className="text-brand-700 hover:underline" href={`https://t.me/${s.bot_username}`} target="_blank" rel="noreferrer">@{s.bot_username}</a> : "—"}
                </p>
                <p className="text-xs text-muted">Yoqilganda ism va vaqtlar Telegram serverlari orqali yuboriladi.</p>
              </div>
              {writable ? (
                <Button
                  size="sm"
                  variant={s.enabled ? "secondary" : "primary"}
                  loading={busy === "enabled"}
                  onClick={() => void run("enabled", () => setting("notify.telegram_enabled", !s.enabled), s.enabled ? "Oʻchirildi" : "Yoqildi")}
                >
                  {s.enabled ? "Oʻchirish" : "Yoqish"}
                </Button>
              ) : null}
            </div>

            <div className="grid gap-4 sm:grid-cols-3">
              <Field label="Kunlik hisobot vaqti">
                <div className="flex gap-2">
                  <Input type="time" value={time} disabled={!writable} onChange={(e) => setTimeDraft(e.target.value)} />
                  {writable && time && time !== s.daily_summary_time ? (
                    <Button size="sm" loading={busy === "time"} onClick={() => void run("time", () => setting("notify.daily_summary_time", time).then(() => setTimeDraft(null)), "Saqlandi")}>
                      Saqlash
                    </Button>
                  ) : null}
                </div>
              </Field>
              <NumberSetting
                label="Koʻrib chiqish eslatmasi (daq)"
                value={s.review_interval_minutes}
                min={5}
                max={1440}
                disabled={!writable}
                busy={busy === "review"}
                onSave={(v) => void run("review", () => setting("notify.review_interval_minutes", v), "Saqlandi")}
              />
              <NumberSetting
                label="Kamera ogohlantirishi (daq)"
                value={s.camera_offline_minutes}
                min={1}
                max={1440}
                disabled={!writable}
                busy={busy === "camera"}
                onSave={(v) => void run("camera", () => setting("notify.camera_offline_minutes", v), "Saqlandi")}
              />
            </div>

            <div>
              <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                <p className="text-sm font-semibold text-navy-900">Qabul qiluvchilar</p>
                <div className="flex flex-wrap gap-2">
                  <Button size="sm" variant="secondary" icon={<Eye className="h-4 w-4" />} onClick={() => void api.get<{ text: string | null }>(`${BASE}/daily-preview`).then((r) => setPreview(r.text)).catch((e) => toast.error("Bajarilmadi", errorMessage(e)))}>
                    Bugungi hisobot
                  </Button>
                  {allBranches ? (
                    <Button
                      size="sm"
                      variant="secondary"
                      icon={<Eye className="h-4 w-4" />}
                      onClick={() =>
                        void api
                          .get<{ text: string | null }>(`${BASE}/director-preview?period=day`)
                          .then((r) => setPreview(r.text))
                          .catch((e) => toast.error("Bajarilmadi", errorMessage(e)))
                      }
                    >
                      Direktor hisoboti
                    </Button>
                  ) : null}
                  {chats && admins.some((r) => r.is_active) ? (
                    <Button size="sm" variant="secondary" icon={<Send className="h-4 w-4" />} loading={busy === "test"} onClick={() => void run("test", () => api.request(`${BASE}/test`, { method: "POST", json: {} }), "Sinov xabari yuborildi")}>
                      Sinov xabari
                    </Button>
                  ) : null}
                  {chats ? (
                    <Button size="sm" icon={<Plus className="h-4 w-4" />} onClick={() => setLinking(true)}>
                      Qoʻshish
                    </Button>
                  ) : null}
                </div>
              </div>
              {admins.length === 0 ? (
                <p className="rounded-lg border border-dashed border-line px-4 py-6 text-center text-sm text-muted">
                  Hali hech kim ulanmagan. “Qoʻshish” tugmasini bosing.
                </p>
              ) : (
                <ul className="divide-y divide-line rounded-lg border border-line">
                  {admins.map((r) => (
                    <li key={r.id} className="space-y-2 px-3 py-3">
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <div className="min-w-0">
                          <p className="text-sm font-medium text-navy-900">
                            {r.title} {r.username ? <span className="text-xs text-muted">@{r.username}</span> : null}
                          </p>
                          {r.last_error ? <p className="text-xs text-danger-700">{r.last_error}</p> : null}
                        </div>
                        <div className="flex items-center gap-2">
                          {allBranches ? <Badge tone={r.branch_id ? "neutral" : "brand"}>{r.branch_id ? (branchName(r.branch_id) ?? "Filial") : "Markaz: barcha filiallar"}</Badge> : null}
                          {!r.is_active ? <Badge tone="warning">Toʻxtatilgan</Badge> : null}
                          {chats ? (
                            <>
                              <Button
                                size="sm"
                                variant="secondary"
                                loading={busy === `active-${r.id}`}
                                onClick={() => void run(`active-${r.id}`, () => api.request(`${BASE}/recipients/${r.id}`, { method: "PATCH", json: { is_active: !r.is_active } }))}
                              >
                                {r.is_active ? "Toʻxtatish" : "Yoqish"}
                              </Button>
                              <Button size="icon" variant="ghost" aria-label={`${r.title}: oʻchirish`} onClick={() => setRemoving(r.id)}>
                                <Trash2 className="h-4 w-4" />
                              </Button>
                            </>
                          ) : null}
                        </div>
                      </div>
                      <div className="flex flex-wrap gap-x-5 gap-y-1">
                        {NOTIFY_KINDS.filter((k) => !CENTRE_ONLY_KINDS.includes(k.key) || (allBranches && !r.branch_id)).map((k) => (
                          <Checkbox
                            key={k.key}
                            label={<span title={k.hint}>{k.label}</span>}
                            checked={r.subscriptions.includes(k.key)}
                            disabled={!chats || busy === `subs-${r.id}`}
                            onChange={(e) => {
                              const next = e.target.checked ? [...r.subscriptions, k.key] : r.subscriptions.filter((x) => x !== k.key);
                              void run(`subs-${r.id}`, () => api.request(`${BASE}/recipients/${r.id}`, { method: "PATCH", json: { subscriptions: next } }));
                            }}
                          />
                        ))}
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </div>

            <p className="text-xs text-muted">
              Xodimlarning oʻz chatlari: <b>{employeeChats}</b> ta. Xodimni ulash uchun uning sahifasida “Telegram ulash” tugmasini bosing.
            </p>
            <Alert tone="info">
              <b>Botning oʻzida menyu:</b> ulangan administrator chatida <code>/menu</code> yozing (yoki Telegramdagi “Menu” tugmasi).
              Markaz chati: bugungi, haftalik va oylik reyting, istalgan filial va istalgan sana. Filial chati: faqat oʻz filiali.
              Sanani yozib yuborsa ham boʻladi, masalan <code>05.11.2026</code>.
            </Alert>

            {s.recent.length > 0 ? (
              <details className="text-sm">
                <summary className="cursor-pointer text-muted">Oxirgi yuborilgan xabarlar</summary>
                <ul className="mt-2 space-y-1">
                  {s.recent.map((l, i) => (
                    <li key={i} className="flex gap-2 text-xs">
                      <span className="shrink-0 tabular-nums text-muted">{formatDate(l.created_at)} {formatTime(l.created_at)}</span>
                      <Badge>{NOTIFY_LOG_KIND[l.kind] ?? l.kind}</Badge>
                      <span className="min-w-0 truncate" title={l.summary ?? ""}>{stripTags(l.summary ?? "")}</span>
                      <span className="shrink-0 text-muted">{l.delivered}/{l.recipients}</span>
                    </li>
                  ))}
                </ul>
              </details>
            ) : null}
          </>
        ) : null}
      </CardBody>

      {linking ? (
        <LinkModal
          onClose={() => {
            setLinking(false);
            reload();
          }}
          known={s.recipients.length}
          target={allBranches ? (linkTarget ?? "Markaz (barcha filiallar haqida xabar oladi)") : null}
        />
      ) : null}
      {preview !== undefined ? (
        <Modal open onClose={() => setPreview(undefined)} title="Bugungi hisobot (yuborilmaydi)">
          {preview ? <pre className="whitespace-pre-wrap rounded-lg bg-subtle p-3 font-sans text-sm">{stripTags(preview)}</pre> : <p className="text-sm text-muted">Bugun hech kim ishlamaydi (dam olish kuni yoki jadval yoʻq): hisobot yuborilmaydi.</p>}
        </Modal>
      ) : null}
      <ConfirmDialog
        open={removing !== null}
        onClose={() => setRemoving(null)}
        title="Qabul qiluvchini oʻchirish"
        description="Bu chatga boshqa xabar yuborilmaydi. Qayta ulash uchun yangi havola kerak boʻladi."
        confirmLabel="Oʻchirish"
        tone="danger"
        onConfirm={async () => {
          await api.request(`${BASE}/recipients/${removing}`, { method: "DELETE" });
          setRemoving(null);
          reload();
        }}
      />
    </Card>
  );
}

function stripTags(html: string): string {
  return html.replace(/<[^>]+>/g, "").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&");
}

function NumberSetting({ label, value, min, max, disabled, busy, onSave }: { label: string; value: number; min: number; max: number; disabled: boolean; busy: boolean; onSave: (v: number) => void }) {
  const [draft, setDraft] = useState<string | null>(null); // null = not edited
  const v = draft ?? String(value);
  const n = Number(v);
  const valid = Number.isInteger(n) && n >= min && n <= max;
  return (
    <Field label={label}>
      <div className="flex gap-2">
        <Input type="number" min={min} max={max} value={v} disabled={disabled} onChange={(e) => setDraft(e.target.value)} />
        {!disabled && valid && n !== value ? (
          <Button size="sm" loading={busy} onClick={() => { onSave(n); setDraft(null); }}>Saqlash</Button>
        ) : null}
      </div>
    </Field>
  );
}

/** Create a one-time code; the person opens the link in Telegram and presses Start. */
function LinkModal({ onClose, known, target }: { onClose: () => void; known: number; target: string | null }) {
  const toast = useToast();
  const [title, setTitle] = useState("");
  const [link, setLink] = useState<TelegramLink | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [linked, setLinked] = useState(false);

  // Wait for the chat to appear (the server links it as soon as Start is pressed).
  useEffect(() => {
    if (!link || linked) return;
    let alive = true;
    const tick = async () => {
      while (alive) {
        try {
          const s = await api.get<TelegramStatus>(BASE);
          if (s.recipients.length > known) {
            setLinked(true);
            return;
          }
        } catch {
          /* keep waiting */
        }
        await new Promise((r) => setTimeout(r, 2000));
      }
    };
    void tick();
    return () => {
      alive = false;
    };
  }, [link, linked, known]);

  const create = async () => {
    setBusy(true);
    setError(null);
    try {
      setLink(await api.request<TelegramLink>(`${BASE}/link`, { method: "POST", json: { title: title.trim() } }));
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      open
      onClose={onClose}
      title="Telegram chatni ulash"
      description="Shaxsiy chat yoki guruhni ulash mumkin. Havola 15 daqiqa amal qiladi va faqat bir marta ishlaydi."
      footer={
        link ? (
          <Button onClick={onClose}>{linked ? "Tayyor" : "Yopish"}</Button>
        ) : (
          <>
            <Button variant="secondary" onClick={onClose}>Bekor qilish</Button>
            <Button loading={busy} disabled={!title.trim()} onClick={() => void create()}>Havola yaratish</Button>
          </>
        )
      }
    >
      {error ? <Alert tone="danger" className="mb-3">{error}</Alert> : null}
      {target && !link ? (
        <Alert tone="info" className="mb-3">
          Chat ulanadigan joy: <b>{target}</b>. Boshqa filial uchun yuqoridagi filial tanlagichdan uni tanlang.
        </Alert>
      ) : null}
      {!link ? (
        <Field label="Kim uchun? (masalan: Direktor, HR guruhi)">
          <Input value={title} maxLength={120} autoFocus onChange={(e) => setTitle(e.target.value)} />
        </Field>
      ) : linked ? (
        <Alert tone="success" title="Ulandi!">Endi bildirishnomalar shu chatga keladi. Qaysi xabarlar kelishini roʻyxatda tanlang.</Alert>
      ) : (
        <div className="space-y-3 text-sm">
          <ol className="list-decimal space-y-1 pl-5">
            <li>Quyidagi havolani <b>{title}</b> ning telefonida oching (yoki yuboring).</li>
            <li>Telegramda <b>Start</b> (Boshlash) tugmasini bosing.</li>
            <li>Bu oyna oʻzi “Ulandi” deb koʻrsatadi.</li>
          </ol>
          {link.link ? (
            <div className="flex flex-wrap gap-2">
              <a href={link.link} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 rounded-lg bg-brand-600 px-3 py-2 font-medium text-white hover:bg-brand-700">
                <ExternalLink className="h-4 w-4" aria-hidden /> Telegramda ochish
              </a>
              <Button
                variant="secondary"
                icon={<Copy className="h-4 w-4" />}
                onClick={() => void navigator.clipboard.writeText(link.link ?? "").then(() => toast.success("Nusxa olindi"))}
              >
                Havolani nusxalash
              </Button>
            </div>
          ) : null}
          <p className="text-xs text-muted">
            Guruh uchun: botni guruhga qoʻshing va guruhga yozing: <code className="font-mono">/start {link.code}</code>
          </p>
          <p className="flex items-center gap-2 text-xs text-muted">
            <span className="h-2 w-2 animate-pulse rounded-full bg-brand-500" /> Kutilmoqda…
          </p>
        </div>
      )}
    </Modal>
  );
}
