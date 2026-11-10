"use client";

import { Plus } from "lucide-react";
import { useRouter } from "next/navigation";
import { useCallback, useState } from "react";
import { useAuth } from "@/lib/auth";
import { formatDate } from "@/lib/format";
import { BIOMETRIC_STATUS, EMPLOYEE_STATUS } from "@/lib/labels";
import type { Employee, Page } from "@/lib/types";
import { useApiQuery } from "@/lib/use-api";
import { BiometricBadge, DepartmentSelect, EmployeeStatusBadge, Initials } from "@/components/entities";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { FilterBar, RequirePermission, SearchInput } from "@/components/ui/controls";
import { Field, Select } from "@/components/ui/form";
import { PageHeader } from "@/components/ui/page-header";
import { EmptyState } from "@/components/ui/states";
import { DataTable, Pagination } from "@/components/ui/table";
import { EmployeeFormModal } from "./employee-form";

const PAGE_SIZE = 20;

export default function EmployeesPage() {
  return (
    <RequirePermission permission="employees:read">
      <Employees />
    </RequirePermission>
  );
}

function Employees() {
  const { can } = useAuth();
  const router = useRouter();
  const [q, setQ] = useState("");
  const [dept, setDept] = useState("");
  const [status, setStatus] = useState("");
  const [method, setMethod] = useState("");
  const [bio, setBio] = useState("");
  const [sort, setSort] = useState("last_name");
  const [page, setPage] = useState(1);
  const [creating, setCreating] = useState(false);

  const params = new URLSearchParams({ page: String(page), page_size: String(PAGE_SIZE), sort });
  if (q) params.set("q", q);
  if (dept) params.set("department_id", dept);
  if (status) params.set("status", status);
  if (method) params.set("attendance_method", method);
  if (bio) params.set("biometric_status", bio);
  const list = useApiQuery<Page<Employee>>(`/employees?${params}`);

  const filter = <T,>(setter: (v: T) => void) => (v: T) => {
    setter(v);
    setPage(1);
  };
  const onSearch = useCallback((v: string) => {
    setQ(v);
    setPage(1);
  }, []);
  const filtered = Boolean(q || dept || status || method || bio);

  return (
    <>
      <PageHeader
        title="Xodimlar"
        description="Qidiruv, filtrlar va xodim profillari"
        actions={
          can("employees:write") ? (
            <Button icon={<Plus className="h-4 w-4" />} onClick={() => setCreating(true)}>
              Yangi xodim
            </Button>
          ) : null
        }
      />
      <Card>
        <FilterBar>
          <SearchInput value={q} onChange={onSearch} placeholder="Ism yoki kod boʻyicha…" className="sm:w-72" />
          <Field label="Boʻlim" className="sm:w-56">
            <DepartmentSelect value={dept} onChange={filter(setDept)} allLabel="Barcha boʻlimlar" />
          </Field>
          <Field label="Holat" className="sm:w-44">
            <Select value={status} onChange={(e) => filter(setStatus)(e.target.value)}>
              <option value="">Barchasi</option>
              {Object.entries(EMPLOYEE_STATUS).map(([k, s]) => (
                <option key={k} value={k}>{s.label}</option>
              ))}
            </Select>
          </Field>
          <Field label="Davomat usuli" className="sm:w-40">
            <Select value={method} onChange={(e) => filter(setMethod)(e.target.value)}>
              <option value="">Barchasi</option>
              <option value="FACE">Yuz orqali</option>
              <option value="MANUAL">Qoʻlda</option>
            </Select>
          </Field>
          <Field label="Biometrik holat" className="sm:w-52">
            <Select value={bio} onChange={(e) => filter(setBio)(e.target.value)}>
              <option value="">Barchasi</option>
              {Object.entries(BIOMETRIC_STATUS).map(([k, s]) => (
                <option key={k} value={k}>{s.label}</option>
              ))}
            </Select>
          </Field>
          <Field label="Saralash" className="sm:w-44">
            <Select value={sort} onChange={(e) => filter(setSort)(e.target.value)}>
              <option value="last_name">Familiya (A–Z)</option>
              <option value="-last_name">Familiya (Z–A)</option>
              <option value="employee_code">Kod</option>
              <option value="-hire_date">Yangi ishga kirganlar</option>
              <option value="-created_at">Yaqinda qoʻshilganlar</option>
            </Select>
          </Field>
        </FilterBar>
        <DataTable<Employee>
          caption="Xodimlar roʻyxati"
          loading={list.loading}
          error={list.error}
          onRetry={list.reload}
          rows={list.data?.items}
          rowKey={(e) => e.id}
          onRowClick={(e) => router.push(`/employees/${e.id}`)}
          empty={
            <EmptyState
              title={filtered ? "Mos xodim topilmadi" : "Hali xodimlar yoʻq"}
              body={filtered ? "Filtrlarni oʻzgartirib koʻring." : undefined}
            />
          }
          columns={[
            {
              key: "name",
              header: "Xodim",
              cell: (e) => (
                <div className="flex items-center gap-3">
                  <Initials name={e.full_name} />
                  <div className="min-w-0">
                    <p className="truncate font-medium text-navy-900">{e.full_name}</p>
                    <p className="font-mono text-xs text-muted">{e.employee_code}</p>
                  </div>
                </div>
              ),
            },
            { key: "dept", header: "Boʻlim", cell: (e) => e.department.name, hideOnMobile: true },
            { key: "pos", header: "Lavozim", cell: (e) => e.position_title ?? "—", hideOnMobile: true },
            { key: "hire", header: "Ishga kirgan", cell: (e) => formatDate(`${e.hire_date}T12:00:00Z`), hideOnMobile: true },
            { key: "status", header: "Holat", cell: (e) => <EmployeeStatusBadge status={e.status} /> },
            { key: "bio", header: "Biometriya", cell: (e) => <BiometricBadge status={e.biometric.status} />, hideOnMobile: true },
          ]}
        />
        {list.data && list.data.total > 0 ? (
          <Pagination page={page} pageSize={PAGE_SIZE} total={list.data.total} onPage={setPage} />
        ) : null}
      </Card>

      {creating ? (
        <EmployeeFormModal
          open
          onClose={() => setCreating(false)}
          onSaved={(e) => router.push(`/employees/${e.id}`)}
        />
      ) : null}
    </>
  );
}
