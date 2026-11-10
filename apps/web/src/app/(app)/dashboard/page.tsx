"use client";

import { AlarmClock, CalendarCheck, Hourglass, RotateCw, UserX, Users } from "lucide-react";
import Link from "next/link";
import { useAuth } from "@/lib/auth";
import { statusView } from "@/lib/cameras";
import type { Camera, Page } from "@/lib/types";
import { formatMinutes, formatTime, todayInOrg } from "@/lib/format";
import { t } from "@/lib/i18n";
import { useApiQuery } from "@/lib/use-api";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardHeader, StatCard } from "@/components/ui/card";
import { PageHeader } from "@/components/ui/page-header";
import { Skeleton } from "@/components/ui/spinner";
import { DevPlaceholder, ErrorState } from "@/components/ui/states";
import { DataTable } from "@/components/ui/table";

type DayItem = {
  employee: { id: string; employee_code: string; full_name: string };
  status: string;
  arrival: string | null;
  late_minutes: number;
};
type Board = { date: string; totals: Record<string, number>; items: DayItem[]; total: number };
type EmployeePage = { total: number };

function KpiGrid({ board, employees, loading }: { board?: Board; employees?: EmployeePage; loading: boolean }) {
  const n = (k: string) => board?.totals[k] ?? 0;
  const value = (v: number | undefined) => (loading && v === undefined ? <Skeleton className="h-8 w-16" /> : (v ?? "—"));
  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-5">
      <StatCard label={t.dashboard.employees} value={value(employees?.total)} icon={<Users className="h-5 w-5" />} />
      <StatCard
        label={t.dashboard.present}
        value={value(board ? n("PRESENT") + n("LATE") + n("INCOMPLETE") : undefined)}
        tone="success"
        icon={<CalendarCheck className="h-5 w-5" />}
        hint={board ? `${n("IN_PROGRESS")} nafari hozir ishda` : undefined}
      />
      <StatCard label={t.dashboard.late} value={value(board ? n("LATE") : undefined)} tone="warning" icon={<AlarmClock className="h-5 w-5" />} />
      <StatCard label={t.dashboard.pending} value={value(board ? n("PENDING") : undefined)} tone="info" icon={<Hourglass className="h-5 w-5" />} />
      <StatCard label={t.dashboard.absent} value={value(board ? n("ABSENT") : undefined)} tone="danger" icon={<UserX className="h-5 w-5" />} />
    </div>
  );
}

export default function DashboardPage() {
  const { can, user } = useAuth();
  const today = todayInOrg();
  const canAttendance = can("attendance:read");
  const board = useApiQuery<Board>(canAttendance ? `/attendance/board?date=${today}&page_size=500` : null);
  const employees = useApiQuery<EmployeePage>(can("employees:read") ? "/employees?page_size=1" : null);
  const late = (board.data?.items ?? []).filter((i) => i.status === "LATE");

  return (
    <>
      <PageHeader
        title={`Xush kelibsiz, ${user?.username ?? ""}`}
        description={`${t.dashboard.today}: ${today}`}
        actions={
          <Button
            variant="secondary"
            icon={<RotateCw className="h-4 w-4" />}
            loading={board.loading || employees.loading}
            onClick={() => {
              board.reload();
              employees.reload();
            }}
          >
            Yangilash
          </Button>
        }
      />

      {!canAttendance ? <Alert className="mb-6">{t.dashboard.noAccess}</Alert> : null}

      {board.error ? (
        <Card className="mb-6">
          <ErrorState error={board.error} onRetry={board.reload} />
        </Card>
      ) : (
        <div className="mb-6">
          <KpiGrid board={board.data} employees={employees.data} loading={board.loading || employees.loading} />
        </div>
      )}

      <div className="grid gap-6 xl:grid-cols-3">
        <Card className="xl:col-span-2">
          <CardHeader title={t.dashboard.lateList} description="Imtiyoz vaqtidan keyin kelganlar" />
          <DataTable<DayItem>
            caption={t.dashboard.lateList}
            loading={board.loading}
            error={board.error}
            onRetry={board.reload}
            rows={board.data ? late : undefined}
            rowKey={(r) => r.employee.id}
            empty={<p className="px-5 py-10 text-center text-sm text-muted">{t.dashboard.noLate}</p>}
            columns={[
              { key: "code", header: "Kod", cell: (r) => <span className="font-mono text-xs">{r.employee.employee_code}</span>, hideOnMobile: true },
              { key: "name", header: "Xodim", cell: (r) => <span className="font-medium">{r.employee.full_name}</span> },
              { key: "arrival", header: "Keldi", cell: (r) => formatTime(r.arrival) },
              { key: "late", header: "Kechikish", align: "right", cell: (r) => <Badge tone="warning">{formatMinutes(r.late_minutes)}</Badge> },
            ]}
          />
        </Card>

        <div className="space-y-6">
          {can("cameras:read") ? <CameraStatusCard /> : null}
          <DevPlaceholder title="Soʻnggi yuz tanish hodisalari" phase="Davomatga ulash bosqichi">
            Tanish xizmati baholash bosqichida va davomatga hali ulanmagan. Natijalar ulanish tasdiqlangach shu yerda koʻrinadi.
          </DevPlaceholder>
        </div>
      </div>
    </>
  );
}

/** Live summary of camera monitoring (status reported by the camera service). */
function CameraStatusCard() {
  const q = useApiQuery<Page<Camera>>("/cameras?page_size=200");
  const cams = q.data?.items ?? [];
  const enabled = cams.filter((c) => c.is_enabled);
  const problems = enabled.filter((c) => c.monitoring?.stale || c.status === "OFFLINE" || c.status === "DEGRADED");
  return (
    <Card>
      <CardHeader title="Kameralar holati" actions={<Link href="/cameras" className="text-sm font-medium text-brand-700 hover:underline">Barchasi</Link>} />
      <div className="p-5">
        {q.error ? <ErrorState error={q.error} onRetry={q.reload} /> : null}
        {!q.data && !q.error ? <Skeleton className="h-16 w-full" /> : null}
        {q.data ? (
          enabled.length === 0 ? (
            <p className="text-sm text-muted">Monitoring yoqilgan kamera yoʻq.</p>
          ) : (
            <ul className="space-y-2">
              {enabled.slice(0, 6).map((c) => {
                const v = statusView(c);
                return (
                  <li key={c.id} className="flex items-center justify-between gap-3 text-sm">
                    <span className="truncate">{c.name}</span>
                    <Badge tone={v.tone}>{v.label}</Badge>
                  </li>
                );
              })}
              <li className="pt-1 text-xs text-muted">
                {problems.length ? `${problems.length} ta kamerada muammo` : "Hammasi ishlayapti"} · {enabled.length} ta yoqilgan
              </li>
            </ul>
          )
        ) : null}
      </div>
    </Card>
  );
}
