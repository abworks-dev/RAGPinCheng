import { Menu, PanelRightClose, PanelRightOpen } from "lucide-react";
import { IconButton } from "./ui/icon-button";

export function ChatHeader({
  title,
  scopeLabel,
  loading,
  sourceCount,
  sourceOpen,
  onOpenConversations,
  onToggleSources,
}: {
  title: string;
  scopeLabel: string;
  loading: boolean;
  sourceCount: number;
  sourceOpen: boolean;
  onOpenConversations: () => void;
  onToggleSources: () => void;
}) {
  return (
    <header className="z-sticky flex min-h-[4.5rem] shrink-0 items-center gap-2 border-b border-border/70 bg-background/95 px-3 py-3 backdrop-blur sm:px-6">
      <IconButton label="打开会话导航" onClick={onOpenConversations} className="lg:hidden">
        <Menu className="size-4" />
      </IconButton>
      <div className="min-w-0 flex-1 px-1">
        <h1 className="truncate text-sm font-semibold tracking-tight text-foreground sm:text-base">{title}</h1>
        <p className="mt-1 truncate text-[11px] text-muted-foreground sm:text-xs">
          {loading ? "正在加载会话" : `知识范围：${scopeLabel}`}
        </p>
      </div>
      {sourceCount > 0 && (
        <button
          type="button"
          aria-label={sourceOpen ? "收起来源" : "展开来源"}
          title={sourceOpen ? "收起来源" : "展开来源"}
          aria-expanded={sourceOpen}
          onClick={onToggleSources}
          className={`${sourceOpen ? "hidden xl:inline-flex" : "inline-flex"} h-9 shrink-0 items-center justify-center gap-1.5 whitespace-nowrap rounded-ui-md border border-border bg-card px-2.5 text-xs font-medium text-foreground transition-colors hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring`}
        >
          {sourceOpen ? <PanelRightClose className="size-4 shrink-0" /> : <PanelRightOpen className="size-4 shrink-0" />}
          <span className="shrink-0">来源</span>
          <span className="flex min-w-5 shrink-0 items-center justify-center rounded-ui-sm bg-ui-accent px-1.5 py-0.5 text-[11px] font-medium text-ui-accent-foreground">
            {sourceCount}
          </span>
        </button>
      )}
    </header>
  );
}
