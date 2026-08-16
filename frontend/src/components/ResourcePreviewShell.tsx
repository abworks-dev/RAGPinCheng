import { X } from "lucide-react";
import { useEffect, useRef, type ReactNode } from "react";
import { IconButton } from "./ui/icon-button";

export function ResourcePreviewShell({
  open,
  title,
  subtitle,
  onClose,
  backAction,
  toolbar,
  children,
}: {
  open: boolean;
  title: string;
  subtitle?: string;
  onClose: () => void;
  backAction?: ReactNode;
  toolbar?: ReactNode;
  children: ReactNode;
}) {
  const panelRef = useRef<HTMLElement | null>(null);
  const returnFocusRef = useRef<HTMLElement | null>(null);
  const wasOpenRef = useRef(false);

  useEffect(() => {
    if (open && !wasOpenRef.current) {
      requestAnimationFrame(() => {
        returnFocusRef.current = document.activeElement instanceof HTMLElement
          ? document.activeElement
          : null;
        panelRef.current?.querySelector<HTMLButtonElement>('button[aria-label="关闭预览"]')?.focus();
      });
    } else if (!open && wasOpenRef.current) {
      requestAnimationFrame(() => returnFocusRef.current?.focus());
    }
    wasOpenRef.current = open;
  }, [open]);

  useEffect(() => {
    if (!open) return;
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
        return;
      }
      if (event.key !== "Tab" || !panelRef.current) return;
      const focusable = Array.from(panelRef.current.querySelectorAll<HTMLElement>(
        'button:not(:disabled), input:not(:disabled), select:not(:disabled), [tabindex]:not([tabindex="-1"])',
      ));
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [onClose, open]);

  return (
    <div
      aria-hidden={!open}
      className={`resource-preview-root fixed inset-0 z-modal ${open ? "resource-preview-open" : ""}`}
    >
      <button
        type="button"
        aria-label="关闭资源预览"
        onClick={onClose}
        className="resource-preview-backdrop absolute inset-0 bg-slate-950/35"
      />
      <section
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className="resource-preview-panel absolute inset-0 flex flex-col bg-card shadow-overlay md:left-auto md:w-[min(60rem,75vw)]"
      >
        <header className="flex min-h-14 shrink-0 items-center gap-3 border-b border-border px-4">
          {backAction}
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
