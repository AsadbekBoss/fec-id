"use client";

import { ChevronLeft, ChevronRight } from "lucide-react";
import { useState } from "react";
import { formatMinutes, formatTime } from "@/lib/format";
import { DAY_STATUS } from "@/lib/labels";
import { addDays, todayInOrg } from "@/lib/time";
import type { Board, Day } from "@/lib/types";
import { useApiQuery } from "@/lib/use-api";
import { DayDetailModal } from "@/components/attendance/day-detail";
import { DayStatusBadge, DepartmentSelect, EmployeeAvatar } from "@/components/entities";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, StatCard } from "@/components/ui/card";
import { FilterBar, RequirePermission } from "@/components/ui/controls";
import { Field, Input, Select } from "@/components/ui/form";
import { PageHeader } from "@/components/ui/page-header";
import { EmptyState } from "@/components/ui/states";
import { DataTable, Pagination } from "@/components/ui/table";

const PAGE_SIZE = 50;

export default function AttendancePage() {
  return (
    <RequirePermission permission="attendance:read">
      <Attendance />
    </RequirePermission>
  );
}

function Attendance() {
  const today = todayInOrg();
  const [date, setDate] = useState(today);
  const [dept, setDept] = useState("");
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(1);
  const [open, setOpen] = useState<Day | null>(null);

  const params = new URLSearchParams({ date, page: String(page), page_size: String(PAGE_SIZE) });
  if (dept) params.set("department_id", dept);
  if (status) params.set("status", status);
  const board = useApiQuery<Board>(`/attendance/board?${params}`);
  const n = (k: string) => board.data?.totals[k] ?? 0;
  const reset = <T,>(setter: (v: T) => void) => (v: T) => {
    setter(v);
    setPage(1);
  };

  return (
    <>
      <PageHeader title="Davomat" description="Kunlik holat. Qatorni bosib hodisalarni koʻring yoki tuzating." />
      <div className="mb-6 grid grid-cols-2 gap-4 md:grid-cols-3 xl:grid-cols-6">
        <StatCard label="Xodimlar" value={board.data ? n("EMPLOYEES") : "—"} />
        <StatCard label="Keldi" value={board.data ? n("PRESENT") : "—"} tone="success" />
        <StatCard label="Kechikdi" value={board.data ? n("LATE") : "—"} tone="warning" />
        <StatCard label="Chiqish yoʻq" value={board.data ? n("INCOMPLETE") : "—"} tone="warning" />
        <StatCard label="Kelmadi" value={board.data ? n("ABSENT") : "—"} tone="danger" />
        <StatCard label="Hozir ishda" value={board.data ? n("IN_PROGRESS") : "—"} tone="info" />
      </div>
      <Card>
        <FilterBar>
          <Field label="Sana" className="sm:w-56">
            <div className="flex gap-1">
              <Button variant="secondary" size="icon" aria-label="Oldingi kun" onClick={() => reset(setDate)(addDays(date, -1))}>
                <ChevronLeft className="h-4 w-4" />
              </Button>
              <Input type="date" value={date} max={today} onChange={(e) => e.target.value && reset(setDate)(e.target.value)} />
              <Button variant="secondary" size="icon" aria-label="Keyingi kun" disabled={date >= today} onClick={() => reset(setDate)(addDays(date, 1))}>
                <ChevronRight className="h-4 w-4" />
              </Button>
            </div>
          </Field>
          <Field label="Boʻlim" className="sm:w-56">
            <DepartmentSelect value={dept} onChange={reset(setDept)} allLabel="Barcha boʻlimlar" />
          </Field>
          <Field label="Holat" className="sm:w-48">
            <Select value={status} onChange={(e) => reset(setStatus)(e.target.value)}>
              <option value="">Barchasi</option>
              {Object.entries(DAY_STATUS).map(([k, s]) => (
                <option key={k} value={k}>{s.label}</option>
              ))}
            </Select>
          </Field>
          {date !== today ? (
            <Button variant="link" onClick={() => reset(setDate)(today)}>Bugun</Button>
          ) : null}
        </FilterBar>
        <DataTable<Day>
          caption="Kunlik davomat"
          loading={board.loading}
          error={board.error}
          onRetry={board.reload}
          rows={board.data?.items}
          rowKey={(d) => d.employee.id}
          onRowClick={setOpen}
          empty={<EmptyState title="Bu sana uchun maʼlumot yoʻq" body="Filtrlarni oʻzgartiring yoki boshqa sanani tanlang." />}
          columns={[
            {
              key: "emp",
              header: "Xodim",
              cell: (d) => (
                <div className="flex items-center gap-3">
                  <EmployeeAvatar employee={d.employee} className="h-10 w-10" />
                  <div className="min-w-0">
                    <p className="font-medium text-navy-900">{d.employee.full_name}</p>
                    <p className="font-mono text-xs text-muted">{d.employee.employee_code}</p>
                  </div>
                </div>
              ),
            },
            { key: "status", header: "Holat", cell: (d) => <DayStatusBadge status={d.status} inProgress={d.in_progress} /> },
            { key: "in", header: "Keldi", cell: (d) => formatTime(d.arrival) },
            { key: "out", header: "Ketdi", cell: (d) => formatTime(d.departure) },
            { key: "worked", header: "Ishladi", cell: (d) => (d.worked_minutes ? formatMinutes(d.worked_minutes) : "—"), hideOnMobile: true },
            { key: "late", header: "Kechikish", cell: (d) => (d.late_minutes ? <Badge tone="warning">{d.late_minutes} daq</Badge> : "—"), hideOnMobile: true },
            { key: "early", header: "Erta ketdi", cell: (d) => (d.early_leave_minutes ? <Badge tone="warning">{d.early_leave_minutes} daq</Badge> : "—"), hideOnMobile: true },
            { key: "manual", header: "", cell: (d) => (d.has_manual_events ? <Badge tone="brand">qoʻlda</Badge> : null), hideOnMobile: true },
          ]}
        />
        {board.data && board.data.total > 0 ? (
          <Pagination page={page} pageSize={PAGE_SIZE} total={board.data.total} onPage={setPage} />
        ) : null}
      </Card>
      {open ? (
        <DayDetailModal employeeId={open.employee.id} workDate={open.work_date} onClose={() => setOpen(null)} onChanged={board.reload} />
      ) : null}
    </>
  );
}
