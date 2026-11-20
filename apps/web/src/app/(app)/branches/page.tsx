"use client";

import { Building, Lock, LockOpen, Pencil, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { errorMessage } from "@/lib/errors";
import type { Branch } from "@/lib/types";
import { useApiQuery } from "@/lib/use-api";
import { BranchOverviewCard } from "@/components/branch-overview";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardHeader } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/confirm";
import { RequirePermission } from "@/components/ui/controls";
import { Field, Input } from "@/components/ui/form";
import { Modal } from "@/components/ui/modal";
import { PageHeader } from "@/components/ui/page-header";
import { DataTable } from "@/components/ui/table";
import { useToast } from "@/components/ui/toast";

export default function BranchesPage() {
  return (
    <RequirePermission permission="branches:manage">
      <Branches />
    </RequirePermission>
  );
}

function Branches() {
  const toast = useToast();
  const { branchId, setBranchId } = useAuth();
  const q = useApiQuery<Branch[]>("/branches");
  const [editing, setEditing] = useState<Branch | "new" | null>(null);
  const [closing, setClosing] = useState<Branch | null>(null);
  const [removing, setRemoving] = useState<Branch | null>(null);

  const setActive = async (b: Branch, active: boolean) => {
    await api.request(`/branches/${b.id}`, { method: "PATCH", json: { is_active: active } });
    toast.success(active ? "Filial qayta ochildi" : "Filial yopildi");
    q.reload();
  };

  return (
    <>
      <PageHeader
        title="Filiallar"
        description="Har bir filialning oʻz xodimlari, boʻlimlari, ish jadvallari va administratorlari bor. Filial administratori faqat oʻz filialini koʻradi."
        actions={
          <Button icon={<Plus className="h-4 w-4" />} onClick={() => setEditing("new")}>
            Filial ochish
          </Button>
        }
      />

      <Alert className="mb-6" title="Filialga administrator qanday beriladi?">
        <ol className="mt-1 list-decimal space-y-1 pl-5">
          <li>Shu yerda filial oching.</li>
          <li>
            <b>Administratorlar</b> sahifasida yangi foydalanuvchi yarating: roli <b>Administrator</b>, filiali shu
            filial.
          </li>
          <li>U kirganda faqat oʻz filialining xodimlari, davomati, hisobotlari va Telegram chatlarini koʻradi.</li>
        </ol>
      </Alert>

      <BranchOverviewCard />

      <Card>
        <CardHeader title="Roʻyxat" />
        <DataTable<Branch>
          rows={q.data}
          loading={q.loading}
          error={q.error}
          onRetry={q.reload}
          rowKey={(b) => b.id}
          columns={[
            {
              key: "name",
              header: "Nomi",
              cell: (b) => (
                <div>
                  <p className="flex flex-wrap items-center gap-2 font-medium text-navy-900">
                    <Building className="h-4 w-4 text-muted" aria-hidden />
                    {b.name}
                    {b.is_main ? <Badge tone="brand">Asosiy</Badge> : null}
                    {!b.is_active ? <Badge tone="warning">Yopilgan</Badge> : null}
                    {branchId === b.id ? <Badge tone="success">Hozir koʻrsatilmoqda</Badge> : null}
                  </p>
                  {b.location ? <p className="text-xs text-muted">{b.location}</p> : null}
                </div>
              ),
            },
            { key: "emp", header: "Xodimlar", cell: (b) => b.employee_count },
            { key: "users", header: "Administratorlar", hideOnMobile: true, cell: (b) => b.user_count },
            { key: "cams", header: "Kameralar", hideOnMobile: true, cell: (b) => b.camera_count },
            {
              key: "actions",
              header: "",
              align: "right",
              cell: (b) => (
                <div className="flex justify-end gap-1">
                  <Button size="sm" variant="secondary" onClick={() => setBranchId(b.id)} disabled={branchId === b.id}>
                    Koʻrish
                  </Button>
                  <Button size="icon" variant="ghost" aria-label={`${b.name}: tahrirlash`} onClick={() => setEditing(b)}>
                    <Pencil className="h-4 w-4" />
                  </Button>
                  {!b.is_main ? (
                    b.is_active ? (
                      <Button size="icon" variant="ghost" aria-label={`${b.name}: yopish`} onClick={() => setClosing(b)}>
                        <Lock className="h-4 w-4" />
                      </Button>
                    ) : (
                      <Button
                        size="icon"
                        variant="ghost"
                        aria-label={`${b.name}: qayta ochish`}
                        onClick={() => void setActive(b, true).catch((e) => toast.error("Bajarilmadi", errorMessage(e)))}
                      >
                        <LockOpen className="h-4 w-4" />
                      </Button>
                    )
                  ) : null}
                  {!b.is_main ? (
                    <Button size="icon" variant="ghost" aria-label={`${b.name}: oʻchirish`} onClick={() => setRemoving(b)}>
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  ) : null}
                </div>
              ),
            },
          ]}
        />
      </Card>

      {editing ? (
        <BranchForm
          branch={editing === "new" ? null : editing}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null);
            q.reload();
          }}
        />
      ) : null}

      <ConfirmDialog
        open={closing !== null}
        onClose={() => setClosing(null)}
        title={`“${closing?.name}” filialini yopish`}
        description="Filial administratorlari tizimga kira olmay qoladi. Xodimlar, davomat va hisobotlar oʻchirilmaydi; filialni istalgan vaqtda qayta ochish mumkin."
        confirmLabel="Yopish"
        tone="danger"
        onConfirm={async () => {
          if (closing) await setActive(closing, false);
          setClosing(null);
        }}
      />
      <ConfirmDialog
        open={removing !== null}
        onClose={() => setRemoving(null)}
        title={`“${removing?.name}” filialini oʻchirish`}
        description="Faqat boʻsh filialni oʻchirish mumkin (xodim, boʻlim, jadval, administrator va kamerasi yoʻq). Maʼlumoti bor filialni yoping."
        confirmLabel="Oʻchirish"
        tone="danger"
        onConfirm={async () => {
          if (!removing) return;
          await api.request(`/branches/${removing.id}`, { method: "DELETE" });
          if (branchId === removing.id) setBranchId(null);
          toast.success("Filial oʻchirildi");
          setRemoving(null);
          q.reload();
        }}
      />
    </>
  );
}

function BranchForm({ branch, onClose, onSaved }: { branch: Branch | null; onClose: () => void; onSaved: () => void }) {
  const toast = useToast();
  const [name, setName] = useState(branch?.name ?? "");
  const [location, setLocation] = useState(branch?.location ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      const json = { name: name.trim(), location: location.trim() || null };
      if (branch) await api.request(`/branches/${branch.id}`, { method: "PATCH", json });
      else await api.request("/branches", { method: "POST", json });
      toast.success(branch ? "Saqlandi" : "Filial ochildi");
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
      title={branch ? "Filialni tahrirlash" : "Yangi filial"}
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Bekor qilish
          </Button>
          <Button loading={busy} disabled={!name.trim()} onClick={() => void save()}>
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
      <div className="space-y-4">
        <Field label="Nomi" required hint="Masalan: Chilonzor tumani">
          <Input value={name} maxLength={200} autoFocus onChange={(e) => setName(e.target.value)} />
        </Field>
        <Field label="Manzili" hint="Ixtiyoriy">
          <Input value={location} maxLength={200} onChange={(e) => setLocation(e.target.value)} />
        </Field>
      </div>
    </Modal>
  );
}
