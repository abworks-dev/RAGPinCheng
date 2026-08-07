import { Check, ChevronDown, Layers3 } from "lucide-react";
import { useEffect, useRef, useState } from "react";

export function KnowledgeScopePicker({
  categories,
  selected,
  onToggle,
  onClear,
  compact = false,
}: {
  categories: string[];
  selected: string[];
  onToggle: (category: string) => void;
  onClear: () => void;
  compact?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const label = selected.length === 0 ? "全部企业知识" : selected.length === 1 ? selected[0] : `${selected.length} 个知识范围`;

  useEffect(() => {
    const onPointerDown = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onPointerDown);
    return () => document.removeEventListener("mousedown", onPointerDown);
  }, []);

  return (
    <div ref={rootRef} className="relative">
      <button
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
        className="inline-flex h-8 max-w-full items-center gap-2 rounded-ui-md px-2 text-xs text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <Layers3 className="size-3.5 shrink-0 text-citation" />
        <span className={compact ? "max-w-32 truncate" : "max-w-52 truncate"}>{label}</span>
        <ChevronDown className="size-3.5 shrink-0" />
      </button>
      {open && (
        <div
          role="menu"
          className="absolute bottom-full left-0 z-dropdown mb-2 w-64 overflow-hidden rounded-ui-lg border border-border bg-popover p-1.5 text-popover-foreground shadow-overlay"
        >
          <button
            type="button"
            onClick={onClear}
            className="flex w-full items-center gap-2 rounded-ui-md px-2.5 py-2 text-left text-sm hover:bg-secondary"
          >
            <span className="flex size-4 items-center justify-center">
              {selected.length === 0 && <Check className="size-4 text-primary" />}
            </span>
            全部企业知识
          </button>
          {categories.map((category) => {
            const checked = selected.includes(category);
            return (
              <button
                key={category}
                type="button"
                onClick={() => onToggle(category)}
                className="flex w-full items-center gap-2 rounded-ui-md px-2.5 py-2 text-left text-sm hover:bg-secondary"
              >
                <span className="flex size-4 items-center justify-center">
                  {checked && <Check className="size-4 text-primary" />}
                </span>
                <span className="truncate">{category}</span>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
