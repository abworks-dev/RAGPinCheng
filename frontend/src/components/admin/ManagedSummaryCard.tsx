import type { ReactNode } from "react";
import { Card, CardContent } from "../ui/card";

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
  const content = <CardContent className="relative p-4 pt-4">
    <span className="absolute inset-x-0 top-0 h-1 bg-primary/80" aria-hidden="true" />
    <span className={`flex items-center justify-between text-ui-xs font-medium text-muted-foreground ${active ? toneClasses[tone] : ""}`}>
      <span>{label}</span>
      <span className={toneClasses[tone]} aria-hidden="true">{icon}</span>
    </span>
    <span className="mt-2 block text-ui-xl font-semibold tabular-nums text-foreground">{value}</span>
  </CardContent>;
  return <Card className={`overflow-hidden shadow-surface ${active ? "border-primary ring-1 ring-primary/30" : ""}`}>
    {onClick ? <button type="button" className="block w-full text-left transition-colors hover:bg-surface-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" aria-pressed={active} onClick={onClick}>{content}</button> : content}
  </Card>;
}
