"use client";

import { Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { api } from "@/lib/api";
import { LEAVE_KIND, dmy, leaveDays, leaveState, type Leave, type LeaveKind } from "@/lib/absences";
import { useAuth } from "@/lib/auth";
import { errorMessage } from "@/lib/errors";
import { todayInOrg } from "@/lib/format";
import { useApiQuery } from "@/lib/use-api";
import { EmployeePicker, type PickedEmployee } from "@/components/entities";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardHeader } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/confirm";
import { Field, Input, Select } from "@/components/ui/form";
import { Modal } from "@/components/ui/modal";
import { DataTable } from "@/components/ui/table";
import { useToast } from "@/components/ui/toast";

const STATE = {
  now: { label: "Hozir", tone: "warning" },
  soon: { label: "Kelgusi", tone: "brand" },
  past: { label: "Oʻtgan", tone: "neutral" },
} as const;

/** Leave / sick days / trips: for one employee (employee page) or everyone visible. */
export function LeavesCard({ employee }: { employee?: { id: string; full_name: string; employee_code: string } }) {
  const { can } = useAuth();
  const toast = useToast();
  const today = todayInOrg();
  const since = new Date(Date.parse(`${today}T00:00:00Z`) - 60 * 86_400_000).toISOString().slice(0, 10);
  const path = employee ? `/leaves?employee_id=${employee.id}` : `/leaves?date_from=${since}`;
  const q = useApiQuery<Leave[]>(path);
  const [adding, setAdding] = useState(false);
  const [removing, setRemoving] = useState<Leave | null>(null);
  const writable = can("attendance:write");

  return (
    <Card>
      <CardHeader
        title="Taʼtil, kasallik, xizmat safari"
        description={
          employee
            ? "Bu kunlarda xodim “kelmadi” deb hisoblanmaydi."
            : "Oxirgi 60 kun va kelgusi. Bu kunlarda xodim “kelmadi” deb hisoblanmaydi, reyting va hisobotlarga taʼsir qilmaydi."
        }
        actions={
          writable ? (
            <Button size="sm" icon={<Plus className="h-4 w-4" />} onClick={() => setAdding(true)}>
              Qoʻshish
            </Button>
          ) : null
        }
      />
      <DataTable<Leave>
        rows={q.data}
        loading={q.loading}
        error={q.error}
        onRetry={q.reload}
        rowKey={(l) => l.id}
        empty={<p className="px-4 py-6 text-center text-sm text-muted">Yozuvlar yoʻq.</p>}
        columns={[
          ...(employee
            ? []
            : [
                {
                  key: "emp",
                  header: "Xodim",
                  cell: (l: Leave) => (
                    <div>
                      <p className="font-medium text-navy-900">{l.employee_name}</p>
                      <p className="text-xs text-muted">{l.employee_code}</p>
                    </div>
                  ),
                },
              ]),
          {
            key: "kind",
            header: "Turi",
            cell: (l) => (
              <span>
                {LEAVE_KIND[l.kind].icon} {LEAVE_KIND[l.kind].label}
              </span>
            ),
          },
          {
            key: "range",
            header: "Sana",
            cell: (l) => (
              <span className="tabular-nums">
                {dmy(l.date_from)}
                {l.date_to !== l.date_from ? ` – ${dmy(l.date_to)}` : ""}{" "}
                <span className="text-xs text-muted">({leaveDays(l.date_from, l.date_to)} kun)</span>
              </span>
            ),
          },
          {
            key: "state",
            header: "Holat",
            hideOnMobile: true,
            cell: (l) => {
              const s = STATE[leaveState(l, today)];
              return <Badge tone={s.tone}>{s.label}</Badge>;
            },
          },
          { key: "note", header: "Izoh", hideOnMobile: true, cell: (l) => l.note ?? "—" },
          {
            key: "del",
            header: "",
            align: "right",
            cell: (l) =>
              writable ? (
                <Button size="icon" variant="ghost" aria-label="Oʻchirish" onClick={() => setRemoving(l)}>
                  <Trash2 className="h-4 w-4" />
                </Button>
              ) : null,
          },
        ]}
      />
      {adding ? (
        <LeaveForm
          employee={employee ?? null}
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
        title="Yozuvni oʻchirish"
        description="Bu kunlar yana oddiy ish kuni sifatida hisoblanadi (kelmagan boʻlsa — “kelmadi”)."
        confirmLabel="Oʻchirish"
        tone="danger"
        onConfirm={async () => {
          if (!removing) return;
          await api.request(`/leaves/${removing.id}`, { method: "DELETE" });
          toast.success("Oʻchirildi");
          setRemoving(null);
          q.reload();
        }}
      />
    </Card>
  );
}

function LeaveForm({
  employee,
  onClose,
  onSaved,
}: {
  employee: { id: string; full_name: string; employee_code: string } | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const toast = useToast();
  const today = todayInOrg();
  const [picked, setPicked] = useState<PickedEmployee | null>(employee);
  const [kind, setKind] = useState<LeaveKind>("VACATION");
  const [from, setFrom] = useState(today);
  const [to, setTo] = useState(today);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const valid = picked !== null && from !== "" && to !== "" && to >= from;

  const save = async () => {
    if (!picked) return;
    setBusy(true);
    setError(null);
    try {
      await api.request("/leaves", {
        method: "POST",
        json: { employee_id: picked.id, kind, date_from: from, date_to: to, note: note.trim() || null },
      });
      toast.success("Saqlandi");
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
      title="Taʼtil / kasallik / safar qoʻshish"
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Bekor qilish
          </Button>
          <Button loading={busy} disabled={!valid} onClick={() => void save()}>
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
        {employee ? (
          <p className="text-sm">
            Xodim: <b>{employee.full_name}</b>
          </p>
        ) : (
          <Field label="Xodim" required>
            <EmployeePicker value={picked} onChange={setPicked} />
          </Field>
        )}
        <Field label="Turi" required>
          <Select value={kind} onChange={(e) => setKind(e.target.value as LeaveKind)}>
            {Object.entries(LEAVE_KIND).map(([k, v]) => (
              <option key={k} value={k}>
                {v.icon} {v.label}
              </option>
            ))}
          </Select>
        </Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Boshlanishi" required>
            <Input type="date" value={from} onChange={(e) => setFrom(e.target.value)} />
          </Field>
          <Field label="Tugashi" required error={to && from && to < from ? "Boshlanishidan oldin boʻlmasin" : null}>
            <Input type="date" value={to} min={from} onChange={(e) => setTo(e.target.value)} />
          </Field>
        </div>
        {valid ? <p className="text-xs text-muted">Jami: {leaveDays(from, to)} kun (dam olish kunlari ham kiradi, ular baribir hisoblanmaydi).</p> : null}
        <Field label="Izoh" hint="Ixtiyoriy, masalan buyruq raqami">
          <Input value={note} maxLength={300} onChange={(e) => setNote(e.target.value)} />
        </Field>
      </div>
    </Modal>
  );
}
