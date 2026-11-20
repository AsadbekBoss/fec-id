"use client";

import { Copy, ExternalLink, Send, Unlink } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { errorMessage } from "@/lib/errors";
import { SELF_KINDS, type EmployeeTelegram, type TelegramLink } from "@/lib/telegram";
import type { Employee } from "@/lib/types";
import { useApiQuery } from "@/lib/use-api";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/confirm";
import { Checkbox } from "@/components/ui/form";
import { useToast } from "@/components/ui/toast";

/** The employee's own Telegram: they get messages about their own arrivals, departures and
 * lateness only. Linked with a one-time link that the employee opens and presses Start. */
export function EmployeeTelegramCard({ employee }: { employee: Employee }) {
  const { can } = useAuth();
  const toast = useToast();
  const writable = can("employees:write");
  const url = `/employees/${employee.id}/telegram`;
  const q = useApiQuery<EmployeeTelegram>(url);
  const { reload } = q;
  const [link, setLink] = useState<TelegramLink | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [unlinking, setUnlinking] = useState(false);
  const s = q.data;

  // While a link is open, wait for the employee to press Start (the server links it at once).
  useEffect(() => {
    if (!link || s?.linked) return;
    let alive = true;
    const wait = async () => {
      while (alive) {
        await new Promise((r) => setTimeout(r, 2000));
        if (!alive) return;
        try {
          const now = await api.get<EmployeeTelegram>(url);
          if (now.linked) {
            setLink(null);
            reload();
            toast.success("Telegram ulandi", `${employee.full_name} endi oʻz xabarlarini oladi`);
            return;
          }
        } catch {
          /* keep waiting */
        }
      }
    };
    void wait();
    return () => {
      alive = false;
    };
  }, [link, s?.linked, url, reload, toast, employee.full_name]);

  const run = async (key: string, fn: () => Promise<unknown>) => {
    setBusy(key);
    try {
      await fn();
      reload();
    } catch (err) {
      toast.error("Bajarilmadi", errorMessage(err));
    } finally {
      setBusy(null);
    }
  };

  const createLink = () =>
    run("link", async () => setLink(await api.request<TelegramLink>(`${url}/link`, { method: "POST", json: {} })));

  return (
    <Card>
      <CardHeader
        title="Telegram"
        description="Xodimning oʻziga: ishga kelgani, ketgani va kechikkani haqida xabar. Boshqalar haqida hech narsa yuborilmaydi."
        actions={s?.linked ? <Badge tone={s.is_active ? "success" : "warning"}>{s.is_active ? "Ulangan" : "Toʻxtatilgan"}</Badge> : null}
      />
      <CardBody className="space-y-3">
        {q.error ? <Alert tone="danger">{errorMessage(q.error)}</Alert> : null}
        {s && !s.configured ? (
          <Alert tone="info">Telegram bot hali sozlanmagan: Sozlamalar → Telegram bildirishnomalari.</Alert>
        ) : null}
        {s && s.configured && !s.enabled ? (
          <Alert tone="warning">Bildirishnomalar oʻchirilgan. Xabarlar yuborilishi uchun Sozlamalarda yoqing.</Alert>
        ) : null}

        {s?.linked ? (
          <>
            <p className="text-sm text-navy-900">
              {s.username ? <>Chat: <b>@{s.username}</b></> : "Chat ulangan"}
            </p>
            {s.last_error ? <p className="text-xs text-danger-700">{s.last_error}</p> : null}
            <div className="space-y-1">
              {SELF_KINDS.map((k) => (
                <Checkbox
                  key={k.key}
                  label={<span title={k.hint}>{k.label}</span>}
                  checked={s.subscriptions.includes(k.key)}
                  disabled={!writable || busy === "subs"}
                  onChange={(e) => {
                    const next = e.target.checked ? [...s.subscriptions, k.key] : s.subscriptions.filter((x) => x !== k.key);
                    void run("subs", () => api.request(url, { method: "PATCH", json: { subscriptions: next } }));
                  }}
                />
              ))}
            </div>
            {writable ? (
              <div className="flex flex-wrap gap-2">
                <Button size="sm" variant="secondary" loading={busy === "active"} onClick={() => void run("active", () => api.request(url, { method: "PATCH", json: { is_active: !s.is_active } }))}>
                  {s.is_active ? "Toʻxtatish" : "Yoqish"}
                </Button>
                <Button size="sm" variant="ghost" icon={<Unlink className="h-4 w-4" />} onClick={() => setUnlinking(true)}>
                  Uzish
                </Button>
              </div>
            ) : null}
          </>
        ) : s && s.configured && writable ? (
          link ? (
            <div className="space-y-3 text-sm">
              <ol className="list-decimal space-y-1 pl-5">
                <li>Havolani xodimga yuboring yoki uning telefonida oching.</li>
                <li>Xodim Telegramda <b>Start</b> ni bossin.</li>
                <li>Ulanishi bilan bu yerda “Ulangan” chiqadi.</li>
              </ol>
              <div className="flex flex-wrap gap-2">
                {link.link ? (
                  <a href={link.link} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 rounded-lg bg-brand-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-brand-700">
                    <ExternalLink className="h-4 w-4" aria-hidden /> Telegramda ochish
                  </a>
                ) : null}
                <Button
                  size="sm"
                  variant="secondary"
                  icon={<Copy className="h-4 w-4" />}
                  onClick={() => void navigator.clipboard.writeText(link.link ?? "").then(() => toast.success("Havola nusxalandi"))}
                >
                  Nusxalash
                </Button>
              </div>
              <p className="flex items-center gap-2 text-xs text-muted">
                <span className="h-2 w-2 animate-pulse rounded-full bg-brand-500" /> Kutilmoqda… Havola 15 daqiqa va faqat bir marta ishlaydi.
              </p>
            </div>
          ) : (
            <Button size="sm" icon={<Send className="h-4 w-4" />} loading={busy === "link"} onClick={() => void createLink()}>
              Telegram ulash
            </Button>
          )
        ) : s && !s.linked ? (
          <p className="text-sm text-muted">Ulanmagan.</p>
        ) : null}
      </CardBody>
      <ConfirmDialog
        open={unlinking}
        onClose={() => setUnlinking(false)}
        title="Telegramni uzish"
        description="Bu xodimga boshqa xabar yuborilmaydi. Qayta ulash uchun yangi havola kerak boʻladi."
        confirmLabel="Uzish"
        tone="danger"
        onConfirm={async () => {
          await api.request(url, { method: "DELETE" });
          setUnlinking(false);
          reload();
        }}
      />
    </Card>
  );
}
