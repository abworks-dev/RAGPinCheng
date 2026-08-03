import type { Conversation } from "../types";
import { ConversationList } from "./ConversationList";
import { UserMenu } from "./UserMenu";
import { Building2, PanelLeftClose, PanelLeftOpen, Plus } from "lucide-react";
import { IconButton } from "./ui/icon-button";

export function Sidebar({
  conversations,
  conversationsLoading,
  currentConversationId,
  onSelectConversation,
  onDeleteConversation,
  categories: _categories,
  selected: _selected,
  onToggle: _onToggle,
  onClearCategories: _onClearCategories,
  onNewChat,
  collapsed = false,
  onToggleCollapsed,
}: {
  conversations: Conversation[];
  conversationsLoading: boolean;
  currentConversationId: string | null;
  onSelectConversation: (id: string) => void;
  onDeleteConversation: (id: string) => void;
  categories: string[];
  selected: string[];
  onToggle: (c: string) => void;
  onClearCategories: () => void;
  onNewChat: () => void;
  collapsed?: boolean;
  onToggleCollapsed?: () => void;
}) {
  return (
    <aside className={`flex h-full shrink-0 flex-col border-r border-sidebar-border bg-sidebar text-sidebar-foreground transition-[width] duration-normal ${collapsed ? "w-16" : "w-[17rem]"}`}>
      <div className="border-b border-sidebar-border px-3 py-3">
        <div className={`mb-3 flex h-9 items-center ${collapsed ? "justify-center" : "justify-between"}`}>
          <div className="flex min-w-0 items-center gap-2">
            <Building2 className="size-5 shrink-0 text-primary" />
            {!collapsed && <span className="truncate text-sm font-semibold">品成 BIM 知识库</span>}
          </div>
          {onToggleCollapsed && (
            <IconButton label={collapsed ? "展开会话侧栏" : "收起会话侧栏"} onClick={onToggleCollapsed}>
              {collapsed ? <PanelLeftOpen className="size-4" /> : <PanelLeftClose className="size-4" />}
            </IconButton>
          )}
        </div>
        <button
          type="button"
          onClick={onNewChat}
          title="新建对话"
          className={`inline-flex h-9 items-center justify-center rounded-ui-md bg-primary text-sm font-medium text-primary-foreground hover:opacity-90 ${collapsed ? "w-9" : "w-full gap-2 px-3"}`}
        >
          <Plus className="size-4" />
          {!collapsed && "新建对话"}
        </button>
      </div>

      <div className={`min-h-0 flex-1 overflow-y-auto py-3 ${collapsed ? "px-1" : "px-2"}`}>
        {!collapsed && (
        <ConversationList
          conversations={conversations}
          currentId={currentConversationId}
          onSelect={onSelectConversation}
          onDelete={onDeleteConversation}
          loading={conversationsLoading}
        />
        )}

      </div>

      <div className="border-t border-sidebar-border px-2 py-2">
        <UserMenu collapsed={collapsed} />
      </div>
    </aside>
  );
}
