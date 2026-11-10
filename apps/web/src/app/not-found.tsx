import Link from "next/link";
import { t } from "@/lib/i18n";

export default function NotFound() {
  return (
    <div className="flex min-h-screen flex-col items-center justify-center px-4 text-center">
      <p className="text-sm font-semibold tracking-wider text-brand-600">404</p>
      <h1 className="mt-2 text-2xl font-semibold text-navy-900">{t.common.notFound}</h1>
      <Link
        href="/dashboard"
        className="mt-6 inline-flex h-10 items-center rounded-lg bg-navy-800 px-4 text-sm font-medium text-white hover:bg-navy-700"
      >
        {t.common.backHome}
      </Link>
    </div>
  );
}
