import { useEffect, type ReactNode } from "react";
import { X } from "lucide-react";
import { cn } from "../../lib/utils";
import { IconButton } from "./icon-button";

export function Drawer({
  open,
  onClose,
  title,
  children,
  side = "left",
  className,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
  side?: "left" | "right";
  className?: string;
}) {
  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [open, onClose]);

  return (
    <div className={cn("fixed inset-0 z-overlay lg:hidden", open ? "" : "pointer-events-none")}>
      <button
        type="button"
        aria-label="关闭抽屉"
        onClick={onClose}
        className={cn(
          "absolute inset-0 bg-slate-950/30 transition-opacity duration-normal",
          open ? "opacity-100" : "opacity-0",
        )}
      />
      <section
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className={cn(
          "absolute inset-y-0 flex w-[min(22rem,90vw)] flex-col bg-card shadow-overlay transition-transform duration-normal",
          side === "left" ? "left-0" : "right-0",
          open ? "translate-x-0" : side === "left" ? "-translate-x-full" : "translate-x-full",
          className,
        )}
      >
        <div className="flex h-14 shrink-0 items-center justify-between border-b border-border px-4">
          <h2 className="text-sm font-semibold text-foreground">{title}</h2>
          <IconButton label="关闭" onClick={onClose}>
            <X className="size-4" />
          </IconButton>
        </div>
        <div className="min-h-0 flex-1">{children}</div>
      </section>
    </div>
  );
}
