"use client";

import { Download, Plus, Save, Trash2 } from "lucide-react";
import { notFound } from "next/navigation";
import { useState } from "react";
import { ApiError, NetworkError } from "@/lib/api";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader, StatCard } from "@/components/ui/card";
import { Checkbox, Field, FormError, Input, PasswordInput, Select, Textarea } from "@/components/ui/form";
import { Modal } from "@/components/ui/modal";
import { PageHeader } from "@/components/ui/page-header";
import { DevPlaceholder, EmptyState, ErrorState, LoadingState, SkeletonRows } from "@/components/ui/states";
import { DataTable, Pagination } from "@/components/ui/table";
import { useToast } from "@/components/ui/toast";

/** Living style guide (development builds only). Sample data below is illustrative, not real. */
type Row = { id: string; name: string; dept: string; status: "PRESENT" | "LATE" | "ABSENT" };
const SAMPLE: Row[] = [
  { id: "1", name: "Namuna Xodim A", dept: "Namuna boʻlim", status: "PRESENT" },
  { id: "2", name: "Namuna Xodim B", dept: "Namuna boʻlim", status: "LATE" },
  { id: "3", name: "Namuna Xodim C", dept: "Namuna boʻlim", status: "ABSENT" },
];
const statusTone = { PRESENT: "success", LATE: "warning", ABSENT: "danger" } as const;

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <Card>
      <CardHeader title={title} />
      <CardBody>{children}</CardBody>
    </Card>
  );
}

export default function DesignSystemPage() {
  if (process.env.NODE_ENV !== "development") notFound();
  return <DesignSystem />;
}

function DesignSystem() {
  const toast = useToast();
  const [modal, setModal] = useState(false);
  const [page, setPage] = useState(1);

  return (
    <>
      <PageHeader title="Dizayn tizimi" description="Faqat ishlab chiqish rejimida. Barcha komponentlar va holatlar bir joyda." />
      <Alert tone="warning" className="mb-6" title="Namuna maʼlumotlar">
        Bu sahifadagi jadval va raqamlar faqat koʻrinishni namoyish qiladi, haqiqiy maʼlumot emas.
      </Alert>
      <div className="grid gap-6 lg:grid-cols-2">
        <Section title="Tugmalar">
          <div className="flex flex-wrap gap-2">
            <Button icon={<Plus className="h-4 w-4" />}>Asosiy</Button>
            <Button variant="secondary" icon={<Download className="h-4 w-4" />}>Ikkinchi darajali</Button>
            <Button variant="ghost">Shaffof</Button>
            <Button variant="danger" icon={<Trash2 className="h-4 w-4" />}>Oʻchirish</Button>
            <Button variant="link">Havola</Button>
            <Button loading>Saqlanmoqda</Button>
            <Button disabled>Faol emas</Button>
            <Button size="sm">Kichik</Button>
            <Button size="lg">Katta</Button>
          </div>
        </Section>

        <Section title="Belgilar va xabarlar">
          <div className="flex flex-wrap gap-2">
            <Badge>Neytral</Badge>
            <Badge tone="brand">Brend</Badge>
            <Badge tone="success">Keldi</Badge>
            <Badge tone="warning">Kechikdi</Badge>
            <Badge tone="danger">Kelmadi</Badge>
          </div>
          <div className="mt-4 space-y-3">
            <Alert tone="info">Maʼlumot xabari.</Alert>
            <Alert tone="success">Muvaffaqiyatli saqlandi.</Alert>
            <Alert tone="danger" title="Xatolik">Saqlab boʻlmadi.</Alert>
          </div>
        </Section>

        <Section title="Bildirishnomalar (toast)">
          <div className="flex flex-wrap gap-2">
            <Button variant="secondary" onClick={() => toast.success("Saqlandi", "Oʻzgarishlar qabul qilindi.")}>Muvaffaqiyat</Button>
            <Button variant="secondary" onClick={() => toast.info("Maʼlumot")}>Maʼlumot</Button>
            <Button variant="secondary" onClick={() => toast.warning("Diqqat", "Tekshirib chiqing.")}>Ogohlantirish</Button>
            <Button variant="secondary" onClick={() => toast.error("Xatolik", "Xatolar yopilguncha turadi.")}>Xatolik</Button>
          </div>
        </Section>

        <Section title="Modal oyna">
          <Button onClick={() => setModal(true)}>Modalni ochish</Button>
          <Modal
            open={modal}
            onClose={() => setModal(false)}
            title="Namuna modal"
            description="Esc, fon ustiga bosish yoki × tugmasi bilan yopiladi."
            footer={
              <>
                <Button variant="secondary" onClick={() => setModal(false)}>Bekor qilish</Button>
                <Button icon={<Save className="h-4 w-4" />} onClick={() => setModal(false)}>Saqlash</Button>
              </>
            }
          >
            <Field label="Izoh" hint="Ixtiyoriy">
              <Textarea placeholder="Matn kiriting" />
            </Field>
          </Modal>
        </Section>

        <Section title="Forma elementlari">
          <form className="space-y-4" onSubmit={(e) => e.preventDefault()}>
            <FormError>Formani yuborishda xatolik (namuna).</FormError>
            <Field label="Toʻliq ism" required hint="Lotin yoki kirill harflarida">
              <Input placeholder="Familiya Ism" />
            </Field>
            <Field label="Telefon" error="Telefon raqami notoʻgʻri.">
              <Input defaultValue="12-34" />
            </Field>
            <Field label="Parol">
              <PasswordInput showLabel="Koʻrsatish" hideLabel="Yashirish" />
            </Field>
            <Field label="Boʻlim">
              <Select defaultValue="">
                <option value="" disabled>Tanlang…</option>
                <option>Namuna boʻlim</option>
              </Select>
            </Field>
            <Checkbox label="Faol" defaultChecked />
          </form>
        </Section>

        <Section title="Statistika kartalari">
          <div className="grid gap-4 sm:grid-cols-2">
            <StatCard label="Namuna" value={128} hint="izoh matni" />
            <StatCard label="Namuna" value="—" tone="warning" />
          </div>
        </Section>
      </div>

      <div className="mt-6 grid gap-6">
        <Card>
          <CardHeader title="Jadval" description="Ustunlar telefonda yashirinadi, gorizontal aylantiriladi" />
          <DataTable<Row>
            rows={SAMPLE}
            rowKey={(r) => r.id}
            columns={[
              { key: "name", header: "Xodim", cell: (r) => <span className="font-medium">{r.name}</span> },
              { key: "dept", header: "Boʻlim", cell: (r) => r.dept, hideOnMobile: true },
              { key: "status", header: "Holat", cell: (r) => <Badge tone={statusTone[r.status]}>{r.status}</Badge> },
            ]}
          />
          <Pagination page={page} pageSize={3} total={9} onPage={setPage} />
        </Card>

        <div className="grid gap-6 lg:grid-cols-2">
          <Card><CardHeader title="Yuklanish" /><LoadingState /></Card>
          <Card><CardHeader title="Skelet" /><SkeletonRows rows={3} /></Card>
          <Card><CardHeader title="Boʻsh holat" /><EmptyState body="Hali hech narsa qoʻshilmagan." /></Card>
          <Card><CardHeader title="Tarmoq xatosi" /><ErrorState error={new NetworkError()} onRetry={() => undefined} /></Card>
          <Card><CardHeader title="Ruxsat yoʻq (403)" /><ErrorState error={new ApiError(403, "FORBIDDEN", "")} /></Card>
          <DevPlaceholder title="Ishlab chiqish belgisi" phase="namuna">Backend tayyor boʻlmagan joylar shunday belgilanadi.</DevPlaceholder>
        </div>
      </div>
    </>
  );
}
