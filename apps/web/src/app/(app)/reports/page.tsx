"use client";

import { FileSpreadsheet, FileText, Play } from "lucide-react";
import { useState } from "react";
import { api } from "@/lib/api";
import { errorMessage } from "@/lib/errors";
import { reportQuery, type ReportJson, type ReportKind } from "@/lib/report-api";
import { currentMonth, todayInOrg } from "@/lib/time";
import { DepartmentSelect } from "@/components/entities";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardHeader } from "@/components/ui/card";
import { FilterBar, RequirePermission, Tabs } from "@/components/ui/controls";
import { Field, Input } from "@/components/ui/form";
import { PageHeader } from "@/components/ui/page-header";
import { EmptyState, ErrorState, LoadingState } from "@/components/ui/states";
import { useToast } from "@/components/ui/toast";

const TABS: { value: ReportKind; label: string }[] = [
  { value: "timesheet", label: "Oylik tabel" },
  { value: "lateness", label: "Kechikishlar" },
  { value: "absence", label: "Kelmaganlar" },
];

const CODE_STYLE: Record<string, string> = {
  "+": "bg-success-50 text-success-700",
  K: "bg-warning-50 text-warning-700",
  C: "bg-warning-50 text-warning-700",
  Y: "bg-danger-50 text-danger-700",
  D: "bg-navy-50 text-navy-400",
  J: "bg-navy-50 text-navy-400",
};

export default function ReportsPage() {
  return (
    <RequirePermission permission="reports:read">
      <Reports />
    </RequirePermission>
  );
}

function Reports() {
  const toast = useToast();
  const today = todayInOrg();
  const [kind, setKind] = useState<ReportKind>("timesheet");
  const [month, setMonth] = useState(currentMonth());
  const [from, setFrom] = useState(`${today.slice(0, 8)}01`);
  const [to, setTo] = useState(today);
  const [dept, setDept] = useState("");
  const [report, setReport] = useState<ReportJson | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [downloading, setDownloading] = useState<"xlsx" | "pdf" | null>(null);

  const params = { kind, month, from, to, department: dept };

  const show = async () => {
    setLoading(true);
    setError(null);
    try {
      setReport(await api.get<ReportJson>(`/reports/${kind}?${reportQuery(params, "json")}`));
    } catch (err) {
      setError(err);
      setReport(null);
    } finally {
      setLoading(false);
    }
  };

  const download = async (format: "xlsx" | "pdf") => {
    setDownloading(format);
    try {
      const blob = await api.blob(`/reports/${kind}?${reportQuery(params, format)}`);
      const name = kind === "timesheet" ? `tabel_${month}` : `${kind === "lateness" ? "kechikishlar" : "kelmaganlar"}_${from}_${to}`;
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${name}.${format}`;
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (err) {
      toast.error("Yuklab boʻlmadi", errorMessage(err));
    } finally {
      setDownloading(null);
    }
  };

  return (
    <>
      <PageHeader title="Hisobotlar" description="Oylik tabel, kechikishlar va kelmaganlar. Excel va PDF koʻrinishida yuklab olinadi." />
      <Tabs value={kind} onChange={(v) => { setKind(v); setReport(null); setError(null); }} tabs={TABS} />
      <Card>
        <FilterBar>
          {kind === "timesheet" ? (
            <Field label="Oy" className="sm:w-44">
              <Input type="month" value={month} max={today.slice(0, 7)} onChange={(e) => setMonth(e.target.value)} />
            </Field>
          ) : (
            <>
              <Field label="Sanadan" className="sm:w-40">
                <Input type="date" value={from} onChange={(e) => setFrom(e.target.value)} />
              </Field>
              <Field label="Sanagacha" className="sm:w-40">
                <Input type="date" value={to} onChange={(e) => setTo(e.target.value)} />
              </Field>
            </>
          )}
          <Field label="Boʻlim" className="sm:w-64">
            <DepartmentSelect value={dept} onChange={setDept} allLabel="Barcha boʻlimlar" />
          </Field>
          <div className="flex flex-wrap gap-2 sm:self-end">
            <Button icon={<Play className="h-4 w-4" />} loading={loading} onClick={() => void show()}>Koʻrsatish</Button>
            <Button variant="secondary" icon={<FileSpreadsheet className="h-4 w-4" />} loading={downloading === "xlsx"} disabled={downloading !== null} onClick={() => void download("xlsx")}>
              Excel
            </Button>
            <Button variant="secondary" icon={<FileText className="h-4 w-4" />} loading={downloading === "pdf"} disabled={downloading !== null} onClick={() => void download("pdf")}>
              PDF
            </Button>
          </div>
        </FilterBar>

        {error ? <div className="p-5"><ErrorState error={error} onRetry={() => void show()} /></div> : null}
        {loading ? <LoadingState label="Hisoblanmoqda…" /> : null}
        {!loading && !report && !error ? (
          <EmptyState title="Hisobotni tanlang" body="Davrni va boʻlimni tanlab “Koʻrsatish”ni bosing yoki darhol Excel/PDF yuklab oling." />
        ) : null}
        {report && !loading ? <ReportView report={report} /> : null}
      </Card>
      {kind === "timesheet" ? (
        <Alert tone="info" className="mt-6">
          Hisobotlar davomat sahifalaridagi bilan bir xil qoidalar asosida hisoblanadi. Qoʻlda kiritilgan tuzatishlar ham hisobga olinadi.
          Yuklab olingan har bir fayl audit jurnaliga yoziladi.
        </Alert>
      ) : null}
    </>
  );
}

function ReportView({ report }: { report: ReportJson }) {
  const dayCols = report.kind === "timesheet" ? report.columns.filter((c) => /^d\d+$/.test(c.key)).map((c) => c.key) : [];
  return (
    <div>
      <CardHeader title={report.title} description={report.subtitle} />
      {report.rows.length === 0 ? (
        <EmptyState title="Maʼlumot yoʻq" body="Tanlangan davrda bu hisobotga tushadigan yozuv topilmadi." />
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-sm">
            <caption className="sr-only">{report.title}</caption>
            <thead>
              <tr className="bg-navy-800 text-white">
                {report.columns.map((c) => (
                  <th key={c.key} scope="col" className={`px-2 py-2 text-xs font-semibold whitespace-nowrap ${c.align === "right" ? "text-right" : c.align === "center" ? "text-center" : "text-left"}`}>
                    {c.label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {report.rows.map((r, i) => (
                <tr key={i} className="border-b border-line hover:bg-navy-50/40">
                  {report.columns.map((c) => {
                    const v = r[c.key];
                    const isDay = dayCols.includes(c.key);
                    return (
                      <td
                        key={c.key}
                        className={`px-2 py-1.5 whitespace-nowrap tabular-nums ${isDay ? `text-center text-xs font-semibold ${CODE_STYLE[String(v)] ?? ""}` : ""} ${c.align === "right" ? "text-right" : c.align === "center" ? "text-center" : ""}`}
                      >
                        {v === null || v === undefined ? "" : String(v)}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {report.footer.length ? (
        <div className="space-y-1 border-t border-line px-5 py-3 text-xs text-muted">
          {report.footer.map((l) => <p key={l}>{l}</p>)}
        </div>
      ) : null}
    </div>
  );
}
