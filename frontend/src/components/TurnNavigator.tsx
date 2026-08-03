import { useState } from "react";

export type TurnNavigationItem = {
  id: string;
  label: string;
};

export function TurnNavigator({
  turns,
  activeTurnId,
  onNavigate,
  className = "",
}: {
  turns: TurnNavigationItem[];
  activeTurnId: string | null;
  onNavigate: (turnId: string) => void;
  className?: string;
}) {
  const [expanded, setExpanded] = useState(false);

  if (turns.length < 2) return null;

  return (
    <nav
      aria-label="对话轮次快速导航"
      className={`absolute right-3 top-1/2 z-sticky -translate-y-1/2 ${className}`}
      onMouseEnter={() => setExpanded(true)}
      onMouseLeave={() => setExpanded(false)}
      onFocusCapture={() => setExpanded(true)}
      onBlurCapture={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setExpanded(false);
      }}
    >
      <div
        className={`overflow-hidden rounded-ui-md transition-[width,padding,background-color,border-color,box-shadow] duration-normal ${
          expanded
            ? "w-64 border border-border bg-popover/95 p-2 shadow-overlay backdrop-blur-sm"
            : "w-10 border border-transparent bg-transparent px-2 py-2.5 shadow-none"
        }`}
      >
        <ol className="space-y-1">
          {turns.map((turn) => {
            const active = turn.id === activeTurnId;
            return (
              <li key={turn.id}>
                <button
                  type="button"
                  aria-label={`跳转到问题：${turn.label}`}
                  aria-current={active ? "step" : undefined}
                  title={expanded ? undefined : turn.label}
                  onClick={() => onNavigate(turn.id)}
                  className={`group/turn flex h-7 w-full items-center rounded-ui-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${
                    expanded ? "gap-2 px-2" : "justify-end"
                  } ${active ? "bg-primary/10 text-primary" : "text-muted-foreground hover:bg-secondary hover:text-foreground"}`}
                >
                  {expanded && <span className="min-w-0 flex-1 truncate text-left text-xs">{turn.label}</span>}
                  <span
                    aria-hidden="true"
                    className={`h-0.5 shrink-0 rounded-full transition-[width,background-color] ${
                      active
                        ? "w-3 bg-primary"
                        : "w-2 bg-muted-foreground/45 group-hover/turn:w-3 group-hover/turn:bg-foreground/60"
                    }`}
                  />
                </button>
              </li>
            );
          })}
        </ol>
      </div>
    </nav>
  );
}
