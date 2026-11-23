"use client";

import { Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { api } from "@/lib/api";
import { dmy, type Holiday } from "@/lib/absences";
import { useAuth } from "@/lib/auth";
import { errorMessage } from "@/lib/errors";
import { todayInOrg } from "@/lib/format";
import type { Branch } from "@/lib/types";
import { useApiQuery } from "@/lib/use-api";
import { LeavesCard } from "@/components/leaves";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardHeader } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/confirm";
import { RequirePermission, Tabs } from "@/components/ui/controls";
import { Field, Input, Select } from "@/components/ui/form";
import { Modal } from "@/components/ui/modal";
import { PageHeader } from "@/components/ui/page-header";
import { DataTable } from "@/components/ui/table";
import { useToast } from "@/components/ui/toast";

const WEEKDAYS = ["Yakshanba", "Dushanba", "Seshanba", "Chorshanba", "Payshanba", "Juma", "Shanba"];

export default function LeavesPage() {
  const [tab, setTab] = useState<"leaves" | "holidays">("leaves");
  return (
    <RequirePermission permission="attendance:read">
      <PageHeader
        title="Taʼtil va bayramlar"
        description="Uzrli kunlar “kelmadi” deb hisoblanmaydi: davomat, reyting, hisobotlar va Telegram shunga qarab toʻgʻrilanadi."
      />
      <Tabs
        value={tab}
        onChange={setTab}
        tabs={[
          { value: "leaves", label: "Taʼtil va kasallik" },
          { value: "holidays", label: "Bayram kunlari" },
        ]}
      />
      {tab === "leaves" ? <LeavesCard /> : <Holidays />}
    </RequirePermission>
  );
}

function Holidays() {
  const { can, allBranches, branchId } = useAuth();
  const toast = useToast();
  const thisYear = Number(todayInOrg().slice(0, 4));
  const [year, setYear] = useState(thisYear);
  const q = useApiQuery<Holiday[]>(`/holidays?year=${year}`);
  const branches = useApiQuery<Branch[]>("/branches");
  const [adding, setAdding] = useState(false);
  const [removing, setRemoving] = useState<Holiday | null>(null);
  const writable = can("attendance:write");
  const centre = allBranches && !branchId;
  const scopeName = (id: string | null) =>
    id === null ? "Barcha filiallar" : (branches.data?.find((b) => b.id === id)?.name ?? "Filial");

  return (
    <Card>
      <CardHeader
        title="Bayram va dam olish kunlari"
        description={
          centre
            ? "Bu yerda qoʻshilgan kun barcha filiallar uchun dam olish boʻladi. Bitta filial uchun yuqorida filialni tanlang."
            : "Bu yerda qoʻshilgan kun faqat shu filial uchun. Barcha filiallar uchun kunlarni markaz belgilaydi."
        }
        actions={
          <div className="flex gap-2">
            <Select value={String(year)} onChange={(e) => setYear(Number(e.target.value))} aria-label="Yil">
              {[thisYear - 1, thisYear, thisYear + 1].map((y) => (
                <option key={y} value={y}>
                  {y}
                </option>
              ))}
            </Select>
            {writable ? (
              <Button size="sm" icon={<Plus className="h-4 w-4" />} onClick={() => setAdding(true)}>
                Qoʻshish
              </Button>
            ) : null}
          </div>
        }
      />
      <DataTable<Holiday>
        rows={q.data}
        loading={q.loading}
        error={q.error}
        onRetry={q.reload}
        rowKey={(h) => h.id}
        empty={<p className="px-4 py-6 text-center text-sm text-muted">{year} yil uchun bayram kunlari kiritilmagan.</p>}
        columns={[
          {
            key: "day",
            header: "Sana",
            cell: (h) => (
              <span className="tabular-nums">
                {dmy(h.day)} <span className="text-xs text-muted">{WEEKDAYS[new Date(`${h.day}T00:00:00Z`).getUTCDay()]}</span>
              </span>
            ),
          },
          { key: "name", header: "Nomi", cell: (h) => <span className="font-medium text-navy-900">{h.name}</span> },
          {
            key: "scope",
            header: "Kimlar uchun",
            hideOnMobile: true,
            cell: (h) => <Badge tone={h.branch_id ? "neutral" : "brand"}>{scopeName(h.branch_id)}</Badge>,
          },
          {
            key: "del",
            header: "",
            align: "right",
            cell: (h) =>
              writable && h.editable ? (
                <Button size="icon" variant="ghost" aria-label={`${h.name}: oʻchirish`} onClick={() => setRemoving(h)}>
                  <Trash2 className="h-4 w-4" />
                </Button>
              ) : null,
          },
        ]}
      />
      {adding ? (
        <HolidayForm
          scope={centre ? "Barcha filiallar" : scopeName(branchId)}
          onClose={() => setAdding(false)}
          onSaved={() => {
            setAdding(false);
            q.reload();
          }}
        />
      ) : null}
      <ConfirmDialog
        open={removing !== null}
        onClose={() => setRemoving(null)}
        title={`“${removing?.name}” ni oʻchirish`}
        description="Bu kun yana oddiy ish kuni sifatida hisoblanadi."
        confirmLabel="Oʻchirish"
        tone="danger"
        onConfirm={async () => {
          if (!removing) return;
          await api.request(`/holidays/${removing.id}`, { method: "DELETE" });
          toast.success("Oʻchirildi");
          setRemoving(null);
          q.reload();
        }}
      />
    </Card>
  );
}

function HolidayForm({ scope, onClose, onSaved }: { scope: string; onClose: () => void; onSaved: () => void }) {
  const toast = useToast();
  const [day, setDay] = useState(todayInOrg());
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.request("/holidays", { method: "POST", json: { day, name: name.trim() } });
      toast.success("Bayram qoʻshildi");
      onSaved();
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
      title="Bayram / dam olish kuni"
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Bekor qilish
          </Button>
          <Button loading={busy} disabled={!day || !name.trim()} onClick={() => void save()}>
            Saqlash
          </Button>
        </>
      }
    >
      {error ? (
        <Alert tone="danger" className="mb-3">
          {error}
        </Alert>
      ) : null}
      <Alert className="mb-4">
        Kimlar uchun: <b>{scope}</b>
      </Alert>
      <div className="space-y-4">
        <Field label="Sana" required>
          <Input type="date" value={day} onChange={(e) => setDay(e.target.value)} />
        </Field>
        <Field label="Nomi" required hint="Masalan: Mustaqillik kuni, Ramazon hayiti">
          <Input value={name} maxLength={120} autoFocus onChange={(e) => setName(e.target.value)} />
        </Field>
      </div>
    </Modal>
  );
}
