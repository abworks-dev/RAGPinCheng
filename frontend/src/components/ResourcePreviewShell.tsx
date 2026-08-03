import { X } from "./ui/icons";
import type { ReactNode } from "react";
import { IconButton } from "./ui/icon-button";

export function ResourcePreviewShell({
  open,
  title,
  subtitle,
  onClose,
  toolbar,
  children,
}: {
  open: boolean;
  title: string;
  subtitle?: string;
  onClose: () => void;
  toolbar?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className={open ? "fixed inset-0 z-modal" : "pointer-events-none fixed inset-0 z-modal invisible"}>
      <button type="button" aria-label="关闭资源预览" onClick={onClose} className="absolute inset-0 bg-slate-950/35" />
      <section role="dialog" aria-modal="true" aria-label={title} className="absolute inset-0 flex flex-col bg-card shadow-overlay md:left-auto md:w-[min(60rem,75vw)]">
        <header className="flex min-h-14 shrink-0 items-center gap-3 border-b border-border px-4">
          <div className="min-w-0 flex-1">
            <h2 className="truncate text-sm font-semibold text-foreground">{title}</h2>
            {subtitle && <p className="truncate text-[11px] text-muted-foreground">{subtitle}</p>}
          </div>
          {toolbar}
          <IconButton label="关闭预览" onClick={onClose}><X className="size-4" /></IconButton>
        </header>
        <div className="min-h-0 flex-1 overflow-hidden">{children}</div>
      </section>
    </div>
  );
}
