import type { Metadata } from "next";
import { Suspense } from "react";
import { LoginForm } from "@/app/login/login-form";
import { LoadingState } from "@/components/ui/states";

export const metadata: Metadata = { title: "Kirish" };

// Public page: keep instant-navigation validation on (see next.config.ts).
export const instant = true;

export default function LoginPage() {
  return (
    <div className="grid min-h-screen lg:grid-cols-2">
      <section className="relative hidden flex-col justify-between overflow-hidden bg-navy-950 p-12 text-white lg:flex">
        <div
          aria-hidden
          className="pointer-events-none absolute -top-32 -right-32 h-96 w-96 rounded-full bg-brand-600/20 blur-3xl"
        />
        <div
          aria-hidden
          className="pointer-events-none absolute -bottom-40 -left-20 h-96 w-96 rounded-full bg-navy-700/40 blur-3xl"
        />
        <p className="relative text-sm font-semibold tracking-wide text-navy-300">AI DAVOMAT</p>
        <div className="relative max-w-md">
          <h1 className="text-3xl font-semibold leading-tight">Xodimlar davomatini ishonchli va shaffof boshqaring</h1>
          <p className="mt-4 text-navy-300">
            Yuzni tanish orqali avtomatik davomat, ish jadvallari, tuzatishlar jurnali va hisobotlar bitta tizimda.
          </p>
        </div>
        <p className="relative text-xs text-navy-400">Ichki korporativ tizim. Barcha harakatlar jurnalga yoziladi.</p>
      </section>
      <section className="flex items-center justify-center px-4 py-12 sm:px-8">
        <Suspense fallback={<LoadingState />}>
          <LoginForm />
        </Suspense>
      </section>
    </div>
  );
}
