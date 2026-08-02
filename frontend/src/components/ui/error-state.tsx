import { type ReactNode } from "react";
import { cn } from "../../lib/utils";

export function ErrorState({
  title = "加载失败",
  description,
  action,
  className,
}: {
  title?: string;
  description?: string;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("rounded-ui-xl border border-destructive/30 bg-destructive/10 px-6 py-8 text-center", className)} role="alert">
      <h3 className="text-ui-base font-medium text-destructive">{title}</h3>
      {description && <p className="mt-1 text-ui-sm text-destructive/80">{description}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}
