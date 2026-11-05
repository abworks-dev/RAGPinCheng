import type { ReactNode } from "react";
import { Card } from "../ui/card";

type SummaryTone = "primary" | "success" | "warning" | "destructive";

const toneClasses: Record<SummaryTone, string> = {
  primary: "text-primary",
  success: "text-success",
  warning: "text-warning",
  destructive: "text-destructive",
};

export function ManagedSummaryCard({
  label,
  value,
  icon,
  tone = "primary",
  onClick,
  active = false,
}: {
  label: string;
  value: number;
  icon: ReactNode;
  tone?: SummaryTone;
  onClick?: () => void;
  active?: boolean;
}) {
  // Headerless summaries own their spacing; CardContent intentionally removes top padding.
  const content = <span className="relative flex min-w-0 items-start gap-3 p-4">
    <span className={`mt-0.5 shrink-0 ${toneClasses[tone]}`} aria-hidden="true">{icon}</span>
    <span className="flex min-w-0 flex-col gap-2">
      <span className={`break-words text-ui-xs font-medium leading-5 text-muted-foreground ${active ? toneClasses[tone] : ""}`}>{label}</span>
      <span className="block text-ui-xl font-semibold leading-7 tabular-nums text-foreground">{value}</span>
    </span>
  </span>;
  return <Card data-active={active} className={`admin-summary overflow-hidden shadow-none ${active ? "border-primary bg-primary/5" : ""}`}>
    {onClick ? <button type="button" className="block w-full text-left transition-colors hover:bg-surface-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring" aria-pressed={active} onClick={onClick}>{content}</button> : content}
  </Card>;
}
