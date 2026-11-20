"use client";

import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { formatDate, formatMinutes, formatTime } from "@/lib/format";
import { DAY_STATUS, DECISION, IGNORE_REASON, SOURCE } from "@/lib/labels";
import { addDays, daysBetween, todayInOrg } from "@/lib/time";
import type { AttendanceEvent, Day, Employee, Page } from "@/lib/types";
import { useApiQuery } from "@/lib/use-api";
import { useEmployeeNames } from "@/lib/use-employee-names";
import { DayDetailModal } from "@/components/attendance/day-detail";
import { DayStatusBadge, DepartmentSelect, EmployeeAvatar, EmployeePicker, type PickedEmployee } from "@/components/entities";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { FilterBar, RequirePermission, Tabs } from "@/components/ui/controls";
import { Checkbox, Field, Input, Select } from "@/components/ui/form";
import { PageHeader } from "@/components/ui/page-header";
import { EmptyState, LoadingState } from "@/components/ui/states";
import { DataTable, Pagination } from "@/components/ui/table";

const MAX_DAYS = 31;

export default function HistoryPage() {
  return (
    <RequirePermission permission="attendance:read">
      <Suspense fallback={<LoadingState />}>
        <History />
      </Suspense>
    </RequirePermission>
  );
}

function History() {
  const params = useSearchParams();
  const [tab, setTab] = useState<"days" | "events">("days");
  const [employee, setEmployee] = useState<PickedEmployee | null>(null);
  const preselect = params.get("employee");

  // Deep link from an employee profile: /attendance/history?employee=<id>
  useEffect(() => {
    if (!preselect) return;
    api
      .get<Employee>(`/employees/${preselect}`)
      .then((e) => setEmployee({ id: e.id, full_name: e.full_name, employee_code: e.employee_code }))
      .catch(() => undefined);
  }, [preselect]);

  return (
    <>
      <PageHeader title="Davomat tarixi" description="Davr boʻyicha kunlik natijalar va barcha hodisalar jurnali" />
      <Tabs
        value={tab}
        onChange={setTab}
        tabs={[
          { value: "days", label: "Kunlar" },
          { value: "events", label: "Hodisalar jurnali" },
        ]}
      />
      {tab === "days" ? <DaysTab employee={employee} setEmployee={setEmployee} /> : <EventsTab employee={employee} setEmployee={setEmployee} />}
    </>
  );
}

type EmpProps = { employee: PickedEmployee | null; setEmployee: (e: PickedEmployee | null) => void };

function DaysTab({ employee, setEmployee }: EmpProps) {
  const today = todayInOrg();
  const [from, setFrom] = useState(addDays(today, -6));
  const [to, setTo] = useState(today);
  const [dept, setDept] = useState("");
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(1);
  const [open, setOpen] = useState<Day | null>(null);
  const PAGE = 50;

  const span = daysBetween(from, to) + 1;
  const rangeError = !from || !to ? "Sanalarni kiriting." : span < 1 ? "Boshlanish sanasi tugashdan keyin boʻlmasin." : span > MAX_DAYS ? `Bir soʻrovda koʻpi bilan ${MAX_DAYS} kun.` : null;

  const q = new URLSearchParams({ date_from: from, date_to: to, page: String(page), page_size: String(PAGE) });
  if (employee) q.set("employee_id", employee.id);
  if (dept) q.set("department_id", dept);
  if (status) q.set("status", status);
  const days = useApiQuery<Page<Day>>(rangeError ? null : `/attendance/days?${q}`);
  const reset = <T,>(s: (v: T) => void) => (v: T) => {
    s(v);
    setPage(1);
  };

  return (
    <Card>
      <FilterBar>
        <Field label="Dan" className="sm:w-40">
          <Input type="date" value={from} max={to} onChange={(e) => reset(setFrom)(e.target.value)} />
        </Field>
        <Field label="Gacha" className="sm:w-40">
          <Input type="date" value={to} max={today} onChange={(e) => reset(setTo)(e.target.value)} />
        </Field>
        <Field label="Xodim" className="sm:w-72">
          <EmployeePicker value={employee} onChange={(e) => { setEmployee(e); setPage(1); }} />
        </Field>
        <Field label="Boʻlim" className="sm:w-52">
          <DepartmentSelect value={dept} onChange={reset(setDept)} allLabel="Barcha boʻlimlar" />
        </Field>
        <Field label="Holat" className="sm:w-44">
          <Select value={status} onChange={(e) => reset(setStatus)(e.target.value)}>
            <option value="">Barchasi</option>
            {Object.entries(DAY_STATUS).map(([k, s]) => (
              <option key={k} value={k}>{s.label}</option>
            ))}
          </Select>
        </Field>
      </FilterBar>
      {rangeError ? (
        <Alert tone="warning" className="m-4">{rangeError}</Alert>
      ) : (
        <>
          <DataTable<Day>
            caption="Kunlik davomat tarixi"
            loading={days.loading}
            error={days.error}
            onRetry={days.reload}
            rows={days.data?.items}
            rowKey={(d) => `${d.employee.id}:${d.work_date}`}
            onRowClick={setOpen}
            empty={<EmptyState title="Maʼlumot topilmadi" />}
            columns={[
              { key: "date", header: "Sana", cell: (d) => formatDate(`${d.work_date}T12:00:00Z`) },
              {
                key: "emp",
                header: "Xodim",
                cell: (d) => (
                  <span className="flex items-center gap-2">
                    <EmployeeAvatar employee={d.employee} className="h-8 w-8" />
                    <span className="font-medium">{d.employee.full_name}</span>
                  </span>
                ),
              },
              { key: "status", header: "Holat", cell: (d) => <DayStatusBadge status={d.status} inProgress={d.in_progress} /> },
              { key: "in", header: "Keldi", cell: (d) => formatTime(d.arrival), hideOnMobile: true },
              { key: "out", header: "Ketdi", cell: (d) => formatTime(d.departure), hideOnMobile: true },
              { key: "worked", header: "Ishladi", cell: (d) => (d.worked_minutes ? formatMinutes(d.worked_minutes) : "—"), hideOnMobile: true },
              { key: "late", header: "Kechikish", align: "right", cell: (d) => (d.late_minutes ? <Badge tone="warning">{d.late_minutes} daq</Badge> : "—") },
            ]}
          />
          {days.data && days.data.total > 0 ? <Pagination page={page} pageSize={PAGE} total={days.data.total} onPage={setPage} /> : null}
        </>
      )}
      {open ? <DayDetailModal employeeId={open.employee.id} workDate={open.work_date} onClose={() => setOpen(null)} onChanged={days.reload} /> : null}
    </Card>
  );
}

function EventsTab({ employee, setEmployee }: EmpProps) {
  const today = todayInOrg();
  const [from, setFrom] = useState(addDays(today, -6));
  const [to, setTo] = useState(today);
  const [dept, setDept] = useState("");
  const [counted, setCounted] = useState("");
  const [source, setSource] = useState("");
  const [includeVoided, setIncludeVoided] = useState(true);
  const [page, setPage] = useState(1);
  const PAGE = 50;

  const q = new URLSearchParams({ page: String(page), page_size: String(PAGE), include_voided: String(includeVoided) });
  if (from) q.set("work_date_from", from);
  if (to) q.set("work_date_to", to);
  if (employee) q.set("employee_id", employee.id);
  if (dept) q.set("department_id", dept);
  if (counted) q.set("counted", counted);
  if (source) q.set("source", source);
  const events = useApiQuery<Page<AttendanceEvent>>(`/attendance/events?${q}`);
  const names = useEmployeeNames((events.data?.items ?? []).map((e) => e.employee_id));
  const reset = <T,>(s: (v: T) => void) => (v: T) => {
    s(v);
    setPage(1);
  };

  return (
    <Card>
      <FilterBar>
        <Field label="Ish kuni (dan)" className="sm:w-40">
          <Input type="date" value={from} onChange={(e) => reset(setFrom)(e.target.value)} />
        </Field>
        <Field label="Ish kuni (gacha)" className="sm:w-40">
          <Input type="date" value={to} onChange={(e) => reset(setTo)(e.target.value)} />
        </Field>
        <Field label="Xodim" className="sm:w-72">
          <EmployeePicker value={employee} onChange={(e) => { setEmployee(e); setPage(1); }} />
        </Field>
        <Field label="Boʻlim" className="sm:w-52">
          <DepartmentSelect value={dept} onChange={reset(setDept)} allLabel="Barcha boʻlimlar" />
        </Field>
        <Field label="Hisoblanganmi" className="sm:w-40">
          <Select value={counted} onChange={(e) => reset(setCounted)(e.target.value)}>
            <option value="">Barchasi</option>
            <option value="true">Hisoblangan</option>
            <option value="false">Hisoblanmagan</option>
          </Select>
        </Field>
        <Field label="Manba" className="sm:w-36">
          <Select value={source} onChange={(e) => reset(setSource)(e.target.value)}>
            <option value="">Barchasi</option>
            <option value="CAMERA">Kamera</option>
            <option value="MANUAL">Qoʻlda</option>
          </Select>
        </Field>
        <Checkbox label="Bekor qilinganlar ham" checked={includeVoided} onChange={(e) => reset(setIncludeVoided)(e.target.checked)} className="pb-2" />
      </FilterBar>
      <DataTable<AttendanceEvent>
        caption="Hodisalar jurnali"
        loading={events.loading}
        error={events.error}
        onRetry={events.reload}
        rows={events.data?.items}
        rowKey={(e) => String(e.id)}
        empty={<EmptyState title="Hodisa topilmadi" />}
        columns={[
          { key: "time", header: "Vaqt", cell: (e) => `${formatDate(e.event_time)} ${formatTime(e.event_time)}` },
          { key: "emp", header: "Xodim", cell: (e) => (e.employee_id ? names[e.employee_id] : <span className="text-muted">Nomaʼlum shaxs</span>) },
          { key: "dir", header: "Yoʻnalish", cell: (e) => (e.direction === "IN" ? "Kirish" : e.direction === "OUT" ? "Chiqish" : "—"), hideOnMobile: true },
          {
            key: "src",
            header: "Manba",
            cell: (e) => (
              <span>
                {SOURCE[e.source]}
                {e.decision ? <span className="ml-1 text-xs text-muted">({DECISION[e.decision]?.label})</span> : null}
              </span>
            ),
            hideOnMobile: true,
          },
          {
            key: "res",
            header: "Natija",
            cell: (e) =>
              e.is_voided ? (
                <Badge tone="neutral">Bekor qilingan</Badge>
              ) : e.counts_for_attendance ? (
                <Badge tone="success">Hisoblandi</Badge>
              ) : (
                <Badge tone="warning">{IGNORE_REASON[e.ignore_reason ?? ""] ?? e.ignore_reason}</Badge>
              ),
          },
        ]}
      />
      {events.data && events.data.total > 0 ? <Pagination page={page} pageSize={PAGE} total={events.data.total} onPage={setPage} /> : null}
    </Card>
  );
}
