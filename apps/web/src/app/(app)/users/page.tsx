"use client";

import { KeyRound, LogOut, Pencil, Plus, Unlock } from "lucide-react";
import { useCallback, useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { errorMessage, fieldErrors } from "@/lib/errors";
import { formatDate, formatTime } from "@/lib/format";
import { ROLE } from "@/lib/labels";
import type { Branch, Page, Role, UserAdmin } from "@/lib/types";
import { useApiQuery } from "@/lib/use-api";
import { USERNAME_RE, passwordProblems } from "@/lib/validation";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/confirm";
import { FilterBar, RequirePermission, SearchInput } from "@/components/ui/controls";
import { Checkbox, Field, FormError, Input, PasswordInput, Select } from "@/components/ui/form";
import { Modal } from "@/components/ui/modal";
import { PageHeader } from "@/components/ui/page-header";
import { EmptyState } from "@/components/ui/states";
import { DataTable, Pagination } from "@/components/ui/table";
import { useToast } from "@/components/ui/toast";

const PAGE = 20;

export default function UsersPage() {
  return (
    <RequirePermission permission="users:read">
      <Users />
    </RequirePermission>
  );
}

function Users() {
  const { can, user: me } = useAuth();
  const toast = useToast();
  const [q, setQ] = useState("");
  const [role, setRole] = useState("");
  const [active, setActive] = useState("");
  const [page, setPage] = useState(1);
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<UserAdmin | null>(null);
  const [resetting, setResetting] = useState<UserAdmin | null>(null);
  const [signingOut, setSigningOut] = useState<UserAdmin | null>(null);
  const writable = can("users:write");

  const params = new URLSearchParams({ page: String(page), page_size: String(PAGE) });
  if (q) params.set("q", q);
  if (role) params.set("role", role);
  if (active) params.set("is_active", active);
  const list = useApiQuery<Page<UserAdmin>>(`/users?${params}`);
  const branches = useApiQuery<Branch[]>("/branches");
  const onSearch = useCallback((v: string) => {
    setQ(v);
    setPage(1);
  }, []);

  const unlock = async (u: UserAdmin) => {
    try {
      await api.request(`/users/${u.id}/unlock`, { method: "POST" });
      toast.success("Blok olib tashlandi", u.username);
      list.reload();
    } catch (err) {
      toast.error("Bajarib boʻlmadi", errorMessage(err));
    }
  };

  const locked = (u: UserAdmin) => Boolean(u.locked_until && Date.parse(u.locked_until) > Date.now());

  return (
    <>
      <PageHeader
        title="Administratorlar"
        description="Tizim foydalanuvchilari va rollari"
        actions={writable ? <Button icon={<Plus className="h-4 w-4" />} onClick={() => setCreating(true)}>Yangi foydalanuvchi</Button> : null}
      />
      {!writable ? <Alert className="mb-4">Sizda faqat koʻrish huquqi bor. Oʻzgartirish faqat bosh administrator uchun.</Alert> : null}
      <Card>
        <FilterBar>
          <SearchInput value={q} onChange={onSearch} placeholder="Login yoki email…" className="sm:w-72" />
          <Field label="Rol" className="sm:w-48">
            <Select value={role} onChange={(e) => { setRole(e.target.value); setPage(1); }}>
              <option value="">Barchasi</option>
              {Object.entries(ROLE).map(([k, r]) => <option key={k} value={k}>{r.label}</option>)}
            </Select>
          </Field>
          <Field label="Holat" className="sm:w-40">
            <Select value={active} onChange={(e) => { setActive(e.target.value); setPage(1); }}>
              <option value="">Barchasi</option>
              <option value="true">Faol</option>
              <option value="false">Faolsiz</option>
            </Select>
          </Field>
        </FilterBar>
        <DataTable<UserAdmin>
          caption="Foydalanuvchilar"
          loading={list.loading}
          error={list.error}
          onRetry={list.reload}
          rows={list.data?.items}
          rowKey={(u) => u.id}
          empty={<EmptyState title="Foydalanuvchi topilmadi" />}
          columns={[
            {
              key: "u",
              header: "Foydalanuvchi",
              cell: (u) => (
                <div>
                  <p className="font-medium text-navy-900">
                    {u.username} {u.id === me?.id ? <span className="text-xs text-muted">(siz)</span> : null}
                  </p>
                  <p className="text-xs text-muted">{u.email ?? "—"}</p>
                </div>
              ),
            },
            { key: "role", header: "Rol", cell: (u) => <Badge tone={ROLE[u.role].tone}>{ROLE[u.role].label}</Badge> },
            {
              key: "branch",
              header: "Filial",
              hideOnMobile: true,
              cell: (u) =>
                u.role === "SUPER_ADMIN" ? (
                  <span className="text-muted">Barcha filiallar</span>
                ) : (
                  (branches.data?.find((b) => b.id === u.branch_id)?.name ?? "…")
                ),
            },
            {
              key: "state",
              header: "Holat",
              cell: (u) =>
                !u.is_active ? <Badge>Faolsiz</Badge> : locked(u) ? <Badge tone="danger">Bloklangan</Badge> : <Badge tone="success">Faol</Badge>,
            },
            {
              key: "last",
              header: "Oxirgi kirish",
              cell: (u) => (u.last_login_at ? `${formatDate(u.last_login_at)} ${formatTime(u.last_login_at)}` : "—"),
              hideOnMobile: true,
            },
            {
              key: "a",
              header: "",
              align: "right",
              cell: (u) =>
                writable ? (
                  <div className="flex justify-end gap-1">
                    {locked(u) ? (
                      <Button variant="ghost" size="icon" aria-label={`${u.username}: blokdan chiqarish`} title="Blokdan chiqarish" onClick={() => unlock(u)}>
                        <Unlock className="h-4 w-4" />
                      </Button>
                    ) : null}
                    <Button variant="ghost" size="icon" aria-label={`${u.username}: tahrirlash`} title="Tahrirlash" onClick={() => setEditing(u)}>
                      <Pencil className="h-4 w-4" />
                    </Button>
                    <Button variant="ghost" size="icon" aria-label={`${u.username}: parolni tiklash`} title="Parolni tiklash" onClick={() => setResetting(u)}>
                      <KeyRound className="h-4 w-4" />
                    </Button>
                    <Button variant="ghost" size="icon" aria-label={`${u.username}: barcha sessiyalarni yopish`} title="Hamma joydan chiqarish" onClick={() => setSigningOut(u)}>
                      <LogOut className="h-4 w-4" />
                    </Button>
                  </div>
                ) : null,
            },
          ]}
        />
        {list.data && list.data.total > 0 ? <Pagination page={page} pageSize={PAGE} total={list.data.total} onPage={setPage} /> : null}
      </Card>

      {creating ? <UserForm onClose={() => setCreating(false)} onSaved={list.reload} /> : null}
      {editing ? <UserForm user={editing} onClose={() => setEditing(null)} onSaved={list.reload} /> : null}
      {resetting ? <ResetPassword user={resetting} onClose={() => setResetting(null)} onSaved={list.reload} /> : null}
      <ConfirmDialog
        open={signingOut !== null}
        onClose={() => setSigningOut(null)}
        title="Barcha sessiyalarni yopish"
        description={signingOut ? `${signingOut.username} barcha qurilmalardan darhol chiqariladi.` : undefined}
        confirmLabel="Chiqarish"
        tone="danger"
        onConfirm={async () => {
          if (!signingOut) return;
          await api.request(`/users/${signingOut.id}/sessions`, { method: "DELETE" });
          toast.success("Sessiyalar yopildi");
        }}
      />
    </>
  );
}

function UserForm({ user, onClose, onSaved }: { user?: UserAdmin; onClose: () => void; onSaved: () => void }) {
  const toast = useToast();
  const { branchId: shown, homeBranch } = useAuth();
  const branches = useApiQuery<Branch[]>("/branches");
  const [branch, setBranch] = useState(user?.branch_id ?? shown ?? homeBranch?.id ?? "");
  const [username, setUsername] = useState(user?.username ?? "");
  const [email, setEmail] = useState(user?.email ?? "");
  const [role, setRole] = useState<Role>(user?.role ?? "VIEWER");
  const [locale, setLocale] = useState(user?.locale ?? "uz");
  const [isActive, setIsActive] = useState(user?.is_active ?? true);
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    const local: Record<string, string> = {};
    if (!user && !USERNAME_RE.test(username.trim())) local.username = "3–64 belgi: harf, raqam, _ . @ -";
    if (email.trim() && !/^[^@\s]+@[^@\s]+$/.test(email.trim())) local.email = "Email notoʻgʻri.";
    if (!user) {
      const probs = passwordProblems(password, username);
      if (probs.length) local.password = probs.join(" ");
      if (password !== confirm) local.confirm = "Parollar mos emas.";
    }
    setErrors(local);
    setFormError(null);
    if (Object.keys(local).length) return;
    setBusy(true);
    try {
      if (user) {
        const body: Record<string, unknown> = {};
        if (role !== user.role) body.role = role;
        if (isActive !== user.is_active) body.is_active = isActive;
        if ((email.trim() || null) !== user.email) body.email = email.trim() || null;
        if (locale !== user.locale) body.locale = locale;
        if (branch && branch !== user.branch_id) body.branch_id = branch;
        if (Object.keys(body).length) await api.request(`/users/${user.id}`, { method: "PATCH", json: body });
      } else {
        await api.request("/users", {
          method: "POST",
          json: { username: username.trim(), password, role, email: email.trim() || null, locale, branch_id: branch || null },
        });
      }
      toast.success(user ? "Saqlandi" : "Foydalanuvchi yaratildi");
      onSaved();
      onClose();
    } catch (err) {
      const fe = fieldErrors(err, { USER_EXISTS: user ? "email" : "username", LAST_SUPER_ADMIN: "role" });
      setErrors(fe);
      if (!Object.keys(fe).length) setFormError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      open
      onClose={onClose}
      dismissible={!busy}
      title={user ? `Tahrirlash: ${user.username}` : "Yangi foydalanuvchi"}
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={busy}>Bekor qilish</Button>
          <Button type="submit" form="user-form" loading={busy}>Saqlash</Button>
        </>
      }
    >
      <form id="user-form" onSubmit={submit} noValidate className="space-y-4">
        <FormError>{formError}</FormError>
        {!user ? (
          <Field label="Login" required error={errors.username}>
            <Input value={username} autoComplete="off" maxLength={64} onChange={(e) => setUsername(e.target.value)} autoFocus />
          </Field>
        ) : null}
        <Field label="Rol" required error={errors.role} hint="Bosh administrator: hammasi. Administrator: kundalik ish. Kuzatuvchi: faqat koʻrish.">
          <Select value={role} onChange={(e) => setRole(e.target.value as Role)}>
            {Object.entries(ROLE).map(([k, r]) => <option key={k} value={k}>{r.label}</option>)}
          </Select>
        </Field>
        {role !== "SUPER_ADMIN" && (branches.data?.length ?? 0) > 1 ? (
          <Field label="Filial" required error={errors.branch_id} hint="Administrator va kuzatuvchi faqat shu filialning maʼlumotlarini koʻradi.">
            <Select value={branch} onChange={(e) => setBranch(e.target.value)}>
              {(branches.data ?? []).map((b) => (
                <option key={b.id} value={b.id}>
                  {b.name}
                </option>
              ))}
            </Select>
          </Field>
        ) : null}
        <Field label="Email" error={errors.email}>
          <Input type="email" value={email} maxLength={320} onChange={(e) => setEmail(e.target.value)} />
        </Field>
        <Field label="Interfeys tili">
          <Select value={locale} onChange={(e) => setLocale(e.target.value)}>
            <option value="uz">Oʻzbekcha</option>
            <option value="ru">Русский</option>
            <option value="en">English</option>
          </Select>
        </Field>
        {!user ? (
          <>
            <Field label="Parol" required error={errors.password} hint="Kamida 12 belgi. Iboralar yaxshi ishlaydi.">
              <PasswordInput value={password} autoComplete="new-password" maxLength={128} onChange={(e) => setPassword(e.target.value)} showLabel="Koʻrsatish" hideLabel="Yashirish" />
            </Field>
            <Field label="Parolni takrorlang" required error={errors.confirm}>
              <PasswordInput value={confirm} autoComplete="new-password" maxLength={128} onChange={(e) => setConfirm(e.target.value)} showLabel="Koʻrsatish" hideLabel="Yashirish" />
            </Field>
          </>
        ) : (
          <Checkbox label="Faol (oʻchirilsa, darhol tizimdan chiqariladi)" checked={isActive} onChange={(e) => setIsActive(e.target.checked)} />
        )}
      </form>
    </Modal>
  );
}

function ResetPassword({ user, onClose, onSaved }: { user: UserAdmin; onClose: () => void; onSaved: () => void }) {
  const toast = useToast();
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    const local: Record<string, string> = {};
    const probs = passwordProblems(password, user.username);
    if (probs.length) local.password = probs.join(" ");
    if (password !== confirm) local.confirm = "Parollar mos emas.";
    setErrors(local);
    if (Object.keys(local).length) return;
    setBusy(true);
    try {
      await api.request(`/users/${user.id}/password`, { method: "POST", json: { new_password: password } });
      toast.success("Parol oʻzgartirildi", `${user.username} barcha qurilmalardan chiqarildi.`);
      onSaved();
      onClose();
    } catch (err) {
      const fe = fieldErrors(err);
      if (fe.new_password) fe.password = fe.new_password;
      setErrors(fe);
      if (!fe.password) setFormError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      open
      onClose={onClose}
      dismissible={!busy}
      size="sm"
      title={`Parolni tiklash: ${user.username}`}
      description="Yangi parolni foydalanuvchiga xavfsiz yoʻl bilan bering. U barcha qurilmalardan chiqariladi."
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={busy}>Bekor qilish</Button>
          <Button type="submit" form="reset-form" loading={busy}>Oʻrnatish</Button>
        </>
      }
    >
      <form id="reset-form" onSubmit={submit} noValidate className="space-y-4">
        <FormError>{formError}</FormError>
        <Field label="Yangi parol" required error={errors.password}>
          <PasswordInput value={password} autoComplete="new-password" maxLength={128} onChange={(e) => setPassword(e.target.value)} showLabel="Koʻrsatish" hideLabel="Yashirish" />
        </Field>
        <Field label="Takrorlang" required error={errors.confirm}>
          <PasswordInput value={confirm} autoComplete="new-password" maxLength={128} onChange={(e) => setConfirm(e.target.value)} showLabel="Koʻrsatish" hideLabel="Yashirish" />
        </Field>
      </form>
    </Modal>
  );
}
