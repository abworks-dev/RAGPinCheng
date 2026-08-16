import { useEffect, useRef, type ReactNode } from "react";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "./ui/sheet";

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
  const returnFocusRef = useRef<HTMLElement | null>(null);
  const wasOpenRef = useRef(false);

  useEffect(() => {
    if (open && !wasOpenRef.current) {
      returnFocusRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    }
    wasOpenRef.current = open;
  }, [open]);

  return (
    <Sheet open={open} onOpenChange={(nextOpen) => { if (!nextOpen) onClose(); }}>
      <SheetContent
        closeLabel="关闭预览"
        onCloseAutoFocus={(event) => {
          event.preventDefault();
          returnFocusRef.current?.focus();
        }}
        className="gap-0 border-l-0 p-0 md:w-[min(60rem,75vw)] md:max-w-none"
      >
        <SheetHeader className="flex min-h-14 items-center gap-3 space-y-0 border-b border-border px-4 py-0 pr-16">
          {backAction}
          <div className="min-w-0 flex-1">
            <SheetTitle className="truncate text-sm font-semibold">{title}</SheetTitle>
            {subtitle
              ? <SheetDescription className="truncate text-[11px]">{subtitle}</SheetDescription>
              : <SheetDescription className="sr-only">资源预览</SheetDescription>}
          </div>
          {toolbar}
        </SheetHeader>
        <div className="min-h-0 flex-1 overflow-hidden">{children}</div>
      </SheetContent>
    </Sheet>
  );
}
