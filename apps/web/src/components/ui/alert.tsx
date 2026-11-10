import { AlertTriangle, CheckCircle2, Info, XCircle } from "lucide-react";
import { cn } from "@/lib/cn";

const tones = {
  info: { box: "border-brand-200 bg-brand-50 text-navy-800", icon: <Info className="h-5 w-5 text-brand-600" /> },
  success: { box: "border-success-600/30 bg-success-50 text-success-700", icon: <CheckCircle2 className="h-5 w-5" /> },
  warning: { box: "border-warning-600/30 bg-warning-50 text-warning-700", icon: <AlertTriangle className="h-5 w-5" /> },
  danger: { box: "border-danger-600/30 bg-danger-50 text-danger-700", icon: <XCircle className="h-5 w-5" /> },
} as const;

/** Inline, persistent message inside a page (for transient messages use toasts). */
export function Alert({
  tone = "info",
  title,
  children,
  className,
}: {
  tone?: keyof typeof tones;
  title?: string;
  children?: React.ReactNode;
  className?: string;
}) {
  const s = tones[tone];
  return (
    <div role={tone === "danger" ? "alert" : "note"} className={cn("flex gap-3 rounded-lg border px-4 py-3 text-sm", s.box, className)}>
      <span className="mt-0.5 shrink-0" aria-hidden>
        {s.icon}
      </span>
      <div>
        {title ? <p className="font-semibold">{title}</p> : null}
        {children ? <div className={cn(title && "mt-0.5")}>{children}</div> : null}
      </div>
    </div>
  );
}
