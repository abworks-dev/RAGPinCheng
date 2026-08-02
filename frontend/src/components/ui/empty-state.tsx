import { type ReactNode } from "react";
import { cn } from "../../lib/utils";

export function EmptyState({
  title,
  description,
  action,
  className,
}: {
  title: string;
  description?: string;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex flex-col items-center justify-center rounded-ui-xl border border-dashed border-border bg-card px-6 py-10 text-center", className)}>
      <h3 className="text-ui-base font-medium text-foreground">{title}</h3>
      {description && <p className="mt-1 max-w-md text-ui-sm text-muted-foreground">{description}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}
