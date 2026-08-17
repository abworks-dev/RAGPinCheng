import type { HTMLAttributes } from "react";
import { cn } from "../../lib/utils";

type ProgressProps = Omit<HTMLAttributes<HTMLDivElement>, "aria-label"> & {
  label: string;
  value: number | null;
};

export function Progress({ className, label, value, ...props }: ProgressProps) {
  const determinate = value !== null && Number.isFinite(value);
  const safeValue = determinate ? Math.max(0, Math.min(100, value)) : null;

  return (
    <div
      className={cn("h-2 overflow-hidden rounded-full bg-surface-muted", className)}
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={safeValue === null ? undefined : Math.round(safeValue)}
      aria-valuetext={safeValue === null ? "处理中" : `${Math.round(safeValue)}%`}
      {...props}
    >
      <span
        className={cn(
          "block h-full rounded-full bg-primary transition-[width] duration-normal",
          safeValue === null && "w-full animate-pulse opacity-60",
        )}
        style={safeValue === null ? undefined : { width: `${safeValue}%` }}
      />
    </div>
  );
}
