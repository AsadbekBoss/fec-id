import { Loader2 } from "lucide-react";
import { cn } from "@/lib/cn";

const sizes = { sm: "h-4 w-4", md: "h-5 w-5", lg: "h-8 w-8" } as const;

export function Spinner({
  size = "md",
  className,
  label,
}: {
  size?: keyof typeof sizes;
  className?: string;
  /** Screen-reader text. Omit when the spinner sits inside a labelled control. */
  label?: string;
}) {
  return (
    <>
      <Loader2 aria-hidden className={cn("animate-spin text-navy-500", sizes[size], className)} />
      {label ? <span className="sr-only">{label}</span> : null}
    </>
  );
}

/** Grey placeholder block for skeleton loading states. */
export function Skeleton({ className }: { className?: string }) {
  return <div aria-hidden className={cn("animate-pulse rounded-md bg-navy-100/70", className)} />;
}
