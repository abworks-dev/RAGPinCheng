import { cn } from "../../lib/utils";

export function LoadingState({ className, label = "加载中…", ...props }: React.HTMLAttributes<HTMLDivElement> & { label?: string }) {
  return (
    <div className={cn("flex items-center justify-center gap-2 text-ui-sm text-muted-foreground", className)} {...props}>
      <span className="h-4 w-4 animate-spin rounded-full border-2 border-border border-t-primary" aria-hidden="true" />
      <span>{label}</span>
    </div>
  );
}
