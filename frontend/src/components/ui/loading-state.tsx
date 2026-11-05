import { cn } from "../../lib/utils";

export function LoadingState({ className, label = "加载中…", ...props }: React.HTMLAttributes<HTMLDivElement> & { label?: string }) {
  return (
    <div role="status" aria-live="polite" className={cn("flex min-w-0 items-center justify-center gap-2.5 text-ui-sm text-muted-foreground", className)} {...props}>
      <span className="h-4 w-4 shrink-0 animate-spin rounded-full motion-reduce:animate-none border-2 border-border border-t-primary" aria-hidden="true" />
      <span>{label}</span>
    </div>
  );
}
