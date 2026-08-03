import type { Conversation } from "../types";
import { ConversationList } from "./ConversationList";
import { UserMenu } from "./UserMenu";
import { Plus } from "lucide-react";

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
}) {
  return (
    <aside className="flex h-full w-[17rem] shrink-0 flex-col border-r border-sidebar-border bg-sidebar text-sidebar-foreground">
      <div className="border-b border-sidebar-border px-3 py-3">
        <div className="mb-3 px-1 text-sm font-semibold">品成 BIM 知识库</div>
        <button
          type="button"
          onClick={onNewChat}
          className="inline-flex h-9 w-full items-center justify-center gap-2 rounded-ui-md bg-primary px-3 text-sm font-medium text-primary-foreground hover:opacity-90"
        >
          <Plus className="size-4" />
          新建对话
        </button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-2 py-3">
        <ConversationList
          conversations={conversations}
          currentId={currentConversationId}
          onSelect={onSelectConversation}
          onDelete={onDeleteConversation}
          loading={conversationsLoading}
        />

      </div>

      <div className="border-t border-sidebar-border px-2 py-2">
        <UserMenu />
      </div>
    </aside>
  );
}
