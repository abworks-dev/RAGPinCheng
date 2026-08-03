import { Menu, PanelRightOpen } from "lucide-react";
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
    <header className="z-sticky flex h-14 shrink-0 items-center border-b border-border bg-background/95 px-3 backdrop-blur md:px-4">
      <IconButton label="打开会话导航" onClick={onOpenConversations} className="lg:hidden">
        <Menu className="size-4" />
      </IconButton>
      <div className="min-w-0 flex-1 px-1">
        <h1 className="truncate text-sm font-semibold text-foreground">{title}</h1>
        <p className="truncate text-xs text-muted-foreground">
          {loading ? "正在加载会话" : `知识范围：${scopeLabel}`}
        </p>
      </div>
      {!sourceOpen && <button
        type="button"
        onClick={onToggleSources}
        className="inline-flex h-9 items-center gap-2 rounded-ui-md px-2.5 text-xs text-muted-foreground hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <PanelRightOpen className="size-4" />
        <span className="hidden sm:inline">来源</span>
        {sourceCount > 0 && (
          <span className="flex min-w-5 items-center justify-center rounded-full bg-ui-accent px-1.5 py-0.5 text-[11px] font-medium text-ui-accent-foreground">
            {sourceCount}
          </span>
        )}
      </button>}
    </header>
  );
}
