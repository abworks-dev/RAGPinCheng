import { Check, ChevronDown, Layers3 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { KnowledgeScope } from "../types";

export function KnowledgeScopePicker({
  scopes,
  selected,
  onToggle,
  onClear,
  compact = false,
}: {
  scopes: KnowledgeScope[];
  selected: string[];
  onToggle: (scopeId: string) => void;
  onClear: () => void;
  compact?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const visibleScopes = scopes.filter((scope) => scope.chat_filter_selectable && scope.chat_search_enabled);
  const selectedLabels = visibleScopes.filter((scope) => selected.includes(scope.id)).map((scope) => scope.display_name);
  const label = selected.length === 0 ? "全部企业知识" : selected.length === 1 ? selectedLabels[0] || "已选知识范围" : `${selected.length} 个知识范围`;

  useEffect(() => {
    const onPointerDown = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onPointerDown);
    return () => document.removeEventListener("mousedown", onPointerDown);
  }, []);

  return (
    <div ref={rootRef} className="relative">
      <button type="button" aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen((value) => !value)} className="inline-flex h-8 max-w-full items-center gap-2 rounded-ui-md px-2 text-xs text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
        <Layers3 className="size-3.5 shrink-0 text-citation" />
        <span className={compact ? "max-w-32 truncate" : "max-w-52 truncate"}>{label}</span>
        <ChevronDown className="size-3.5 shrink-0" />
      </button>
      {open && <div role="menu" className="absolute bottom-full left-0 z-dropdown mb-2 max-h-80 w-72 overflow-y-auto rounded-ui-lg border border-border bg-popover p-1.5 text-popover-foreground shadow-overlay">
        <button type="button" onClick={onClear} className="flex w-full items-center gap-2 rounded-ui-md px-2.5 py-2 text-left text-sm hover:bg-secondary">
          <span className="flex size-4 items-center justify-center">{selected.length === 0 && <Check className="size-4 text-primary" />}</span>
          全部企业知识
        </button>
        {visibleScopes.map((scope) => {
          const checked = selected.includes(scope.id);
          return <button key={scope.id} type="button" onClick={() => onToggle(scope.id)} className="flex w-full items-center gap-2 rounded-ui-md px-2.5 py-2 text-left text-sm hover:bg-secondary">
            <span className="flex size-4 shrink-0 items-center justify-center">{checked && <Check className="size-4 text-primary" />}</span>
            <span className="min-w-0 truncate" style={{ paddingLeft: `${Math.max(0, scope.level - 1) * 12}px` }}><span className="tabular-nums text-muted-foreground">{scope.display_code}</span> {scope.display_name}</span>
          </button>;
        })}
      </div>}
    </div>
  );
}
