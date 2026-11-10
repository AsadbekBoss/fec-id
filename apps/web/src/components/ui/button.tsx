import { forwardRef } from "react";
import { cn } from "@/lib/cn";
import { Spinner } from "@/components/ui/spinner";

type Variant = "primary" | "secondary" | "ghost" | "danger" | "link";
type Size = "sm" | "md" | "lg" | "icon";

const variants: Record<Variant, string> = {
  primary:
    "bg-navy-800 text-white hover:bg-navy-700 active:bg-navy-900 shadow-sm disabled:bg-navy-300",
  secondary:
    "bg-surface text-ink border border-line hover:bg-subtle active:bg-navy-50 shadow-sm disabled:text-muted",
  ghost: "text-navy-700 hover:bg-navy-50 active:bg-navy-100 disabled:text-muted",
  danger: "bg-danger-600 text-white hover:bg-danger-700 shadow-sm disabled:bg-danger-600/50",
  link: "text-brand-600 hover:text-brand-700 hover:underline underline-offset-2 px-0",
};

const sizes: Record<Size, string> = {
  sm: "h-8 px-3 text-sm gap-1.5 rounded-md",
  md: "h-10 px-4 text-sm gap-2 rounded-lg",
  lg: "h-11 px-5 text-base gap-2 rounded-lg",
  icon: "h-9 w-9 rounded-lg justify-center",
};

export type ButtonProps = React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: Variant;
  size?: Size;
  loading?: boolean;
  /** Icon rendered before the label (e.g. a Lucide icon element). */
  icon?: React.ReactNode;
};

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = "primary", size = "md", loading = false, icon, className, children, disabled, type, ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type ?? "button"}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      className={cn(
        "inline-flex items-center font-medium transition-colors select-none",
        "disabled:cursor-not-allowed",
        variants[variant],
        sizes[size],
        className,
      )}
      {...rest}
    >
      {loading ? <Spinner size="sm" className="text-current" /> : icon}
      {children}
    </button>
  );
});
