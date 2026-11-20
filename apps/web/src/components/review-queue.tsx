"use client";

import { Check, UserCheck, X } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { errorMessage } from "@/lib/errors";
import { formatDate, formatTime } from "@/lib/format";
import type { Page, ReviewItem } from "@/lib/types";
import { useApiQuery } from "@/lib/use-api";
import { AuthImage, EmployeePicker, type PickedEmployee } from "@/components/entities";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardHeader } from "@/components/ui/card";
import { Tabs } from "@/components/ui/controls";
import { Field, Select } from "@/components/ui/form";
import { EmptyState, ErrorState } from "@/components/ui/states";
import { Pagination } from "@/components/ui/table";
import { useToast } from "@/components/ui/toast";

const PAGE = 12;

/** Human decisions on uncertain recognitions. The system never suggests who it might be. */
export function ReviewQueue() {
  const [status, setStatus] = useState<"PENDING" | "ALL">("PENDING");
  const [page, setPage] = useState(1);
  const q = useApiQuery<Page<ReviewItem>>(`/recognition/reviews?status=${status}&page=${page}&page_size=${PAGE}`);
  const { reload } = q;

  useEffect(() => {
    const id = setInterval(() => document.visibilityState === "visible" && reload(), 15_000);
    return () => clearInterval(id);
  }, [reload]);

  const pending = status === "PENDING" ? q.data?.total : undefined;

  return (
    <Card className="mb-6">
      <CardHeader
        title={
          <span className="flex items-center gap-2">
            Koʻrib chiqish {pending ? <Badge tone="warning">{pending}</Badge> : null}
          </span>
        }
        description="Tizim aniq taniy olmagan yuzlar. Rasmga qarab xodimni tanlang yoki rad eting. Qarordan keyin rasm oʻchiriladi."
      />
      <div className="px-5 pt-4">
        <Tabs
          value={status}
          onChange={(v) => { setStatus(v); setPage(1); }}
          tabs={[{ value: "PENDING", label: "Kutilmoqda" }, { value: "ALL", label: "Hammasi" }]}
        />
      </div>
      {q.error ? <div className="p-5"><ErrorState error={q.error} onRetry={q.reload} /></div> : null}
      {q.data && q.data.items.length === 0 ? (
        <EmptyState
          title={status === "PENDING" ? "Koʻrib chiqiladigan yuz yoʻq" : "Hali yozuv yoʻq"}
          body="Davomat kaliti yoqilgan va kamerada tanish yoqilgan boʻlsa, noaniq yuzlar shu yerga tushadi."
        />
      ) : null}
      {q.data && q.data.items.length > 0 ? (
        <div className="grid gap-4 p-5 sm:grid-cols-2 xl:grid-cols-3">
          {q.data.items.map((r) => <ReviewCard key={r.id} item={r} onDone={reload} />)}
        </div>
      ) : null}
      {q.data && q.data.total > PAGE ? <Pagination page={page} pageSize={PAGE} total={q.data.total} onPage={setPage} /> : null}
    </Card>
  );
}

function ReviewCard({ item: r, onDone }: { item: ReviewItem; onDone: () => void }) {
  const { can } = useAuth();
  const toast = useToast();
  const [who, setWho] = useState<PickedEmployee | null>(null);
  const [direction, setDirection] = useState<"" | "IN" | "OUT">("");
  const [busy, setBusy] = useState<"assign" | "dismiss" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const needsDirection = r.camera_direction !== "IN" && r.camera_direction !== "OUT";
  const decidable = r.review_status === "PENDING" && can("attendance:write");

  const act = async (kind: "assign" | "dismiss") => {
    setBusy(kind);
    setError(null);
    try {
      if (kind === "assign") {
        await api.request(`/recognition/reviews/${r.id}/assign`, {
          method: "POST",
          json: { employee_id: who?.id, ...(needsDirection ? { direction } : {}) },
        });
        toast.success(`${who?.full_name}: davomatga yozildi`);
      } else {
        await api.request(`/recognition/reviews/${r.id}/dismiss`, { method: "POST", json: {} });
        toast.success("Rad etildi");
      }
      onDone();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="flex flex-col gap-3 rounded-lg border border-line p-3">
      <div className="flex gap-3">
        <AuthImage
          path={r.has_snapshot ? `/recognition/reviews/${r.id}/snapshot` : null}
          alt="Noaniq yuz"
          className="h-28 w-24 shrink-0 rounded-md"
        />
        <div className="min-w-0 space-y-1 text-sm">
          <p className="font-medium text-navy-900">{formatDate(r.event_time)} {formatTime(r.event_time)}</p>
          <p className="text-muted">{r.camera_name ?? "—"}</p>
          <Badge tone={r.decision === "AMBIGUOUS" ? "warning" : "neutral"}>
            {r.decision === "AMBIGUOUS" ? "Noaniq" : "Roʻyxatda yoʻq"}
          </Badge>
          {r.review_status === "ASSIGNED" ? (
            <p className="flex items-center gap-1 text-success-700"><UserCheck className="h-4 w-4" aria-hidden /> {r.employee?.full_name}</p>
          ) : r.review_status === "DISMISSED" ? (
            <p className="text-muted">{r.reviewed_by_user_id ? "Rad etilgan" : "Muddati oʻtgan"}</p>
          ) : null}
        </div>
      </div>
      {decidable ? (
        <div className="space-y-2">
          {error ? <Alert tone="danger">{error}</Alert> : null}
          <EmployeePicker value={who} onChange={setWho} placeholder="Bu kim? Xodimni qidiring…" />
          {needsDirection ? (
            <Field label="Yoʻnalish">
              <Select value={direction} onChange={(e) => setDirection(e.target.value as "" | "IN" | "OUT")}>
                <option value="">Tanlang…</option>
                <option value="IN">Kirish</option>
                <option value="OUT">Chiqish</option>
              </Select>
            </Field>
          ) : null}
          <div className="flex gap-2">
            <Button
              size="sm"
              icon={<Check className="h-4 w-4" />}
              loading={busy === "assign"}
              disabled={busy !== null || !who || (needsDirection && !direction)}
              onClick={() => void act("assign")}
            >
              Tasdiqlash
            </Button>
            <Button size="sm" variant="secondary" icon={<X className="h-4 w-4" />} loading={busy === "dismiss"} disabled={busy !== null} onClick={() => void act("dismiss")}>
              Rad etish
            </Button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
