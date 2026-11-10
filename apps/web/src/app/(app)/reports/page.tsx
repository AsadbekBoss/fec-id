"use client";

import { Download, Play } from "lucide-react";
import { useState } from "react";
import { api } from "@/lib/api";
import { formatMinutes } from "@/lib/format";
import { buildTimesheet, timesheetCsv, type TimesheetRow } from "@/lib/reports";
import { currentMonth, monthRange, todayInOrg } from "@/lib/time";
import type { Day, Page } from "@/lib/types";
import { DepartmentSelect, MissingApi } from "@/components/entities";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardHeader } from "@/components/ui/card";
import { FilterBar, RequirePermission } from "@/components/ui/controls";
import { Field, Input } from "@/components/ui/form";
import { PageHeader } from "@/components/ui/page-header";
import { EmptyState, ErrorState } from "@/components/ui/states";
import { DataTable } from "@/components/ui/table";

const PAGE_SIZE = 500;
const MAX_PAGES = 40; // 20 000 day-rows: ~650 employees × 31 days

export default function ReportsPage() {
  return (
    <RequirePermission permission="reports:read">
      <RequirePermission permission="attendance:read">
        <Reports />
      </RequirePermission>
    </RequirePermission>
  );
}

function Reports() {
  const [month, setMonth] = useState(currentMonth());
  const [dept, setDept] = useState("");
  const [rows, setRows] = useState<TimesheetRow[] | null>(null);
  const [period, setPeriod] = useState("");
  const [loading, setLoading] = useState(false);
  const [progress, setProgress] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [truncated, setTruncated] = useState(false);

  const run = async () => {
    const { from } = monthRange(month);
    let { to } = monthRange(month);
    const today = todayInOrg();
    if (to > today) to = today; // future days are not attendance yet
    setLoading(true);
    setError(null);
    setTruncated(false);
    try {
      const all: Day[] = [];
      for (let page = 1; page <= MAX_PAGES; page++) {
        const q = new URLSearchParams({ date_from: from, date_to: to, page: String(page), page_size: String(PAGE_SIZE) });
        if (dept) q.set("department_id", dept);
        const res = await api.get<Page<Day>>(`/attendance/days?${q}`);
        all.push(...res.items);
        setProgress(`${all.length} / ${res.total} kun yozuvi`);
        if (all.length >= res.total) break;
        if (page === MAX_PAGES) setTruncated(true);
      }
      setRows(buildTimesheet(all));
      setPeriod(`${from} — ${to}`);
    } catch (err) {
      setError(err);
    } finally {
      setLoading(false);
      setProgress("");
    }
  };

  const download = () => {
    if (!rows) return;
    const blob = new Blob([timesheetCsv(rows, period)], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `tabel-${month}.csv`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };

  return (
    <>
      <PageHeader title="Hisobotlar" description="Oylik tabel: davomat natijalari xodimlar boʻyicha jamlangan" />
      <Card className="mb-6">
        <CardHeader
          title="Oylik tabel"
          description="Joriy oy uchun bugungi kungacha hisoblanadi"
          actions={
            rows && rows.length > 0 ? (
              <Button variant="secondary" icon={<Download className="h-4 w-4" />} onClick={download}>
                CSV (Excel)
              </Button>
            ) : null
          }
        />
        <FilterBar>
          <Field label="Oy" className="sm:w-44">
            <Input type="month" value={month} max={currentMonth()} onChange={(e) => e.target.value && setMonth(e.target.value)} />
          </Field>
          <Field label="Boʻlim" className="sm:w-60">
            <DepartmentSelect value={dept} onChange={setDept} allLabel="Barcha boʻlimlar" />
          </Field>
          <Button icon={<Play className="h-4 w-4" />} loading={loading} onClick={run}>
            Hisobotni tuzish
          </Button>
          {progress ? <span className="pb-2 text-sm text-muted">{progress}</span> : null}
        </FilterBar>
        {truncated ? (
          <Alert tone="warning" className="m-4">
            Maʼlumot juda koʻp, hisobot toʻliq emas. Boʻlim boʻyicha filtrlang.
          </Alert>
        ) : null}
        {error ? (
          <ErrorState error={error} onRetry={run} />
        ) : rows === null ? (
          <EmptyState title="Hisobot hali tuzilmagan" body="Oy va boʻlimni tanlab “Hisobotni tuzish” tugmasini bosing." />
        ) : (
          <DataTable<TimesheetRow>
            caption={`Oylik tabel ${period}`}
            rows={rows}
            rowKey={(r) => r.employee_id}
            empty={<EmptyState title="Bu davr uchun maʼlumot yoʻq" />}
            columns={[
              {
                key: "n",
                header: "Xodim",
                cell: (r) => (
                  <div>
                    <p className="font-medium text-navy-900">{r.full_name}</p>
                    <p className="font-mono text-xs text-muted">{r.employee_code}</p>
                  </div>
                ),
              },
              { key: "s", header: "Ish kuni", align: "right", cell: (r) => r.scheduled_days },
              { key: "p", header: "Keldi", align: "right", cell: (r) => r.present_days },
              { key: "l", header: "Kechikdi", align: "right", cell: (r) => (r.late_days ? `${r.late_days} (${r.late_minutes} daq)` : "0") },
              { key: "a", header: "Kelmadi", align: "right", cell: (r) => r.absent_days },
              { key: "i", header: "Chiqishsiz", align: "right", cell: (r) => r.incomplete_days, hideOnMobile: true },
              { key: "w", header: "Ishladi", align: "right", cell: (r) => formatMinutes(r.worked_minutes), hideOnMobile: true },
              { key: "o", header: "Qoʻshimcha", align: "right", cell: (r) => (r.overtime_minutes ? `${r.overtime_minutes} daq` : "—"), hideOnMobile: true },
            ]}
          />
        )}
      </Card>
      <MissingApi
        title="Server tomonidagi hisobotlar va eksport"
        phase="Hisobotlar bosqichi"
        contract="API_CONTRACTS §2.10"
        endpoints={[
          "GET /api/v1/reports/daily | timesheet | lateness | absence | overtime",
          "POST /api/v1/reports/exports (XLSX / PDF)",
          "GET /api/v1/reports/exports/{id}/download",
        ]}
      >
        Yuqoridagi tabel mavjud <code className="font-mono">/attendance/days</code> API asosida brauzerda hisoblanadi.
        XLSX/PDF eksport, katta hajmlar va jurnalga yoziladigan rasmiy hisobotlar server endpointlarini talab qiladi.
      </MissingApi>
    </>
  );
}
