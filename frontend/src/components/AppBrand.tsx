import { cn } from "../lib/utils";

export function AppBrand({
  subtitle,
  collapsed = false,
  className,
  subtitleClassName,
}: {
  subtitle: string;
  collapsed?: boolean;
  className?: string;
  subtitleClassName?: string;
}) {
  return (
    <div className={cn("flex min-w-0 items-center gap-3", collapsed && "justify-center", className)}>
      <span
        className="flex h-9 w-9 shrink-0 items-center justify-center rounded-ui-lg bg-primary text-ui-sm font-semibold text-primary-foreground shadow-surface"
        aria-hidden="true"
      >
        品
      </span>
      {!collapsed && (
        <div className="min-w-0">
          <p className="truncate text-ui-sm font-semibold text-foreground sm:text-ui-base">品成 BIM 知识库</p>
          <p className={cn("truncate text-ui-xs text-muted-foreground", subtitleClassName)}>{subtitle}</p>
        </div>
      )}
    </div>
  );
}
