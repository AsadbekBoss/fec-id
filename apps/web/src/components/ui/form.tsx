"use client";

import { AlertCircle, Eye, EyeOff } from "lucide-react";
import { cloneElement, forwardRef, isValidElement, useId, useState } from "react";
import { cn } from "@/lib/cn";

const control =
  "block w-full rounded-lg border bg-surface px-3 text-sm text-ink placeholder:text-muted/70 shadow-sm transition-colors " +
  "focus:outline-none focus:ring-2 focus:ring-brand-500/30 focus:border-brand-500 " +
  "disabled:cursor-not-allowed disabled:bg-subtle disabled:text-muted";

function border(invalid?: boolean) {
  return invalid ? "border-danger-600 focus:border-danger-600 focus:ring-danger-600/20" : "border-line";
}

type ControlProps = { invalid?: boolean };

export const Input = forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement> & ControlProps>(
  function Input({ className, invalid, ...rest }, ref) {
    return <input ref={ref} aria-invalid={invalid || undefined} className={cn(control, "h-10", border(invalid), className)} {...rest} />;
  },
);

export const Select = forwardRef<HTMLSelectElement, React.SelectHTMLAttributes<HTMLSelectElement> & ControlProps>(
  function Select({ className, invalid, children, ...rest }, ref) {
    return (
      <select ref={ref} aria-invalid={invalid || undefined} className={cn(control, "h-10 pr-8", border(invalid), className)} {...rest}>
        {children}
      </select>
    );
  },
);

export const Textarea = forwardRef<HTMLTextAreaElement, React.TextareaHTMLAttributes<HTMLTextAreaElement> & ControlProps>(
  function Textarea({ className, invalid, ...rest }, ref) {
    return <textarea ref={ref} aria-invalid={invalid || undefined} className={cn(control, "min-h-24 py-2", border(invalid), className)} {...rest} />;
  },
);

export function Checkbox({ label, className, ...rest }: React.InputHTMLAttributes<HTMLInputElement> & { label: React.ReactNode }) {
  const id = useId();
  return (
    <label htmlFor={rest.id ?? id} className={cn("inline-flex items-center gap-2 text-sm text-ink", className)}>
      <input id={rest.id ?? id} type="checkbox" className="h-4 w-4 rounded border-line text-navy-800 focus:ring-brand-500" {...rest} />
      {label}
    </label>
  );
}

/**
 * Label + control + hint + error, wired with ids for screen readers (aria-describedby).
 * Pass exactly one control element as the child.
 */
export function Field({
  label,
  hint,
  error,
  required,
  children,
  className,
}: {
  label: string;
  hint?: string;
  error?: string | null;
  required?: boolean;
  children: React.ReactElement<Record<string, unknown>>;
  className?: string;
}) {
  const id = useId();
  const hintId = `${id}-hint`;
  const errorId = `${id}-error`;
  const describedBy = [hint ? hintId : null, error ? errorId : null].filter(Boolean).join(" ") || undefined;
  const child = isValidElement(children)
    ? cloneElement(children, {
        id: (children.props.id as string | undefined) ?? id,
        invalid: Boolean(error),
        "aria-describedby": describedBy,
        required,
      })
    : children;
  return (
    <div className={cn("space-y-1.5", className)}>
      <label htmlFor={(children.props.id as string | undefined) ?? id} className="block text-sm font-medium text-navy-900">
        {label}
        {required ? <span className="ml-0.5 text-danger-600" aria-hidden>*</span> : null}
      </label>
      {child}
      {hint && !error ? (
        <p id={hintId} className="text-xs text-muted">
          {hint}
        </p>
      ) : null}
      {error ? (
        <p id={errorId} className="flex items-center gap-1 text-xs font-medium text-danger-700" role="alert">
          <AlertCircle className="h-3.5 w-3.5" aria-hidden />
          {error}
        </p>
      ) : null}
    </div>
  );
}

/** Form-level error banner (e.g. server rejected the whole submission). */
export function FormError({ children }: { children?: React.ReactNode }) {
  if (!children) return null;
  return (
    <div role="alert" className="flex items-start gap-2 rounded-lg border border-danger-600/30 bg-danger-50 px-3 py-2.5 text-sm text-danger-700">
      <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
      <div>{children}</div>
    </div>
  );
}

/** Password input with a show/hide toggle. Works inside <Field> like any other control. */
export const PasswordInput = forwardRef<
  HTMLInputElement,
  Omit<React.InputHTMLAttributes<HTMLInputElement>, "type"> & ControlProps & { showLabel: string; hideLabel: string }
>(function PasswordInput({ showLabel, hideLabel, className, ...rest }, ref) {
  const [visible, setVisible] = useState(false);
  return (
    <div className="relative">
      <Input ref={ref} type={visible ? "text" : "password"} className={cn("pr-10", className)} {...rest} />
      <button
        type="button"
        onClick={() => setVisible((v) => !v)}
        aria-label={visible ? hideLabel : showLabel}
        aria-pressed={visible}
        className="absolute inset-y-0 right-0 flex w-10 items-center justify-center rounded-r-lg text-muted hover:text-ink"
      >
        {visible ? <EyeOff className="h-4 w-4" aria-hidden /> : <Eye className="h-4 w-4" aria-hidden />}
      </button>
    </div>
  );
});
