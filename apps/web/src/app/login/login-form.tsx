"use client";

import { LogIn, ScanFace } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import { ApiError, NetworkError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { t } from "@/lib/i18n";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Field, FormError, Input, PasswordInput } from "@/components/ui/form";

/** Only same-app paths are allowed as ?next= targets (prevents open redirects). */
export function safeNext(next: string | null): string {
  if (!next || !next.startsWith("/") || next.startsWith("//") || next.startsWith("/\\")) return "/dashboard";
  return next;
}

export function loginErrorMessage(err: unknown): string {
  if (err instanceof NetworkError) return t.auth.network;
  if (err instanceof ApiError) {
    if (err.status === 401) return t.auth.invalid;
    if (err.status === 429) return t.auth.tooMany(Math.max(1, Math.ceil((err.retryAfter ?? 60) / 60)));
    if (err.status === 422) return t.auth.invalid;
  }
  return t.common.errorBody;
}

export function LoginForm() {
  const { status, login, endedReason } = useAuth();
  const router = useRouter();
  const params = useSearchParams();
  const next = safeNext(params.get("next"));

  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [touched, setTouched] = useState(false);

  useEffect(() => {
    if (status === "authenticated") router.replace(next);
  }, [status, next, router]);

  const usernameError = touched && !username.trim() ? "Foydalanuvchi nomini kiriting." : null;
  const passwordError = touched && !password ? "Parolni kiriting." : null;

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setTouched(true);
    if (!username.trim() || !password) return;
    setSubmitting(true);
    setError(null);
    try {
      await login(username.trim(), password);
      router.replace(next);
    } catch (err) {
      setError(loginErrorMessage(err));
      setPassword("");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="w-full max-w-sm">
      <div className="mb-8 flex items-center gap-3 lg:hidden">
        <span className="flex h-10 w-10 items-center justify-center rounded-lg bg-navy-900 text-white">
          <ScanFace className="h-5 w-5" aria-hidden />
        </span>
        <p className="text-lg font-semibold text-navy-900">{t.app.name}</p>
      </div>
      <h2 className="text-2xl font-semibold tracking-tight text-navy-900">{t.auth.title}</h2>
      <p className="mt-1 text-sm text-muted">{t.auth.subtitle}</p>

      {endedReason === "session_ended" && !error ? (
        <Alert tone="warning" className="mt-6">
          {t.auth.sessionEnded}
        </Alert>
      ) : null}

      <Card className="mt-6 p-6">
        <form onSubmit={onSubmit} noValidate className="space-y-5">
          <FormError>{error}</FormError>
          <Field label={t.auth.username} required error={usernameError}>
            <Input
              name="username"
              autoComplete="username"
              autoCapitalize="none"
              spellCheck={false}
              maxLength={64}
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoFocus
            />
          </Field>
          <Field label={t.auth.password} required error={passwordError}>
            <PasswordInput
              name="password"
              autoComplete="current-password"
              maxLength={128}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              showLabel={t.auth.showPassword}
              hideLabel={t.auth.hidePassword}
            />
          </Field>
          <Button
            type="submit"
            size="lg"
            className="w-full justify-center"
            loading={submitting}
            icon={<LogIn className="h-4 w-4" />}
          >
            {submitting ? t.auth.submitting : t.auth.submit}
          </Button>
        </form>
      </Card>
    </div>
  );
}
