import type { Conversation } from "../types";
import { useState } from "react";
import { AppBrand } from "./AppBrand";
import { ConversationList } from "./ConversationList";
import { UserMenu } from "./UserMenu";
import { PanelLeftClose, Plus } from "lucide-react";
import { IconButton } from "./ui/icon-button";
import { useAutoHideScrollbar } from "../hooks/useAutoHideScrollbar";
import { ThemeMenu } from "./ThemeMenu";

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
  const conversationScroll = useAutoHideScrollbar<HTMLDivElement>();
  const [conversationScrolledFromTop, setConversationScrolledFromTop] = useState(false);
  return (
    <aside className={`flex h-full shrink-0 flex-col overflow-hidden bg-sidebar text-sidebar-foreground transition-[width] duration-normal ${collapsed ? "w-16" : "w-[17rem]"}`}>
      <div className="px-3 py-3">
        <div className="mb-3 flex h-9 items-center justify-between gap-2">
          {collapsed && onToggleCollapsed ? (
            <button
              type="button"
              title="展开会话侧栏"
              aria-label="展开会话侧栏"
              onClick={onToggleCollapsed}
              className="flex size-9 items-center justify-start rounded-ui-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <AppBrand subtitle="知识问答工作台" collapsed />
            </button>
          ) : (
            <>
              <AppBrand subtitle="知识问答工作台" />
              {onToggleCollapsed && (
                <IconButton label="收起会话侧栏" onClick={onToggleCollapsed}>
                  <PanelLeftClose className="size-4" />
                </IconButton>
              )}
            </>
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

      <div className={`scroll-fade-bottom relative min-h-0 flex-1 ${conversationScrolledFromTop ? "scroll-fade-sidebar-start" : ""}`}>
        <div
          ref={conversationScroll.ref}
          {...conversationScroll.interactionProps}
          onScroll={(event) => {
            conversationScroll.interactionProps.onScroll();
            setConversationScrolledFromTop(event.currentTarget.scrollTop > 0);
          }}
          className={`h-full overflow-y-auto py-3 ${conversationScroll.className} ${collapsed ? "px-1" : "px-2"}`}
        >
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
      </div>

      <div className={`space-y-1 py-2 ${collapsed ? "px-3" : "px-2"}`}>
        <ThemeMenu collapsed={collapsed} />
        <UserMenu collapsed={collapsed} />
      </div>
    </aside>
  );
}
