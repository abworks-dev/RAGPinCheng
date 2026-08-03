import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import { useChat } from "../hooks/useChat";
import type { Conversation } from "../types";
import { Composer } from "./Composer";
import { MessageList } from "./MessageList";
import { PdfPreview } from "./PdfPreview";
import { PdfPreviewProvider } from "../hooks/usePdfPreview";
import { Sidebar } from "./Sidebar";
import { ChatHeader } from "./ChatHeader";
import { SourceWorkspace } from "./SourceWorkspace";
import { Drawer } from "./ui/drawer";
import { CITATION_EVENT } from "./citations";

export function ChatLayout() {
  const [categories, setCategories] = useState<string[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [conversationsLoading, setConversationsLoading] = useState(true);
  const [currentId, setCurrentId] = useState<string | null>(null);
  const [conversationDrawerOpen, setConversationDrawerOpen] = useState(false);
  const [sourceOpen, setSourceOpen] = useState(() => window.matchMedia("(min-width: 1280px)").matches);

  const refreshConversations = useCallback(async () => {
    try {
      const { conversations: list } = await api.listConversations();
      setConversations(list);
      return list;
    } catch {
      return [] as Conversation[];
    } finally {
      setConversationsLoading(false);
    }
  }, []);

  useEffect(() => {
    api.categories().then((r) => setCategories(r.categories)).catch(() => {});
    refreshConversations();
  }, [refreshConversations]);

  useEffect(() => {
    const openSources = () => setSourceOpen(true);
    window.addEventListener(CITATION_EVENT, openSources);
    return () => window.removeEventListener(CITATION_EVENT, openSources);
  }, []);

  const { messages, send, sending, loading } = useChat({
    conversationId: currentId,
    onConversationCreated: (id) => {
      setCurrentId(id);
      refreshConversations();
    },
    onConversationUpdated: () => {
      refreshConversations();
    },
  });

  const onSelectConversation = useCallback((id: string) => {
    setCurrentId(id);
    setConversationDrawerOpen(false);
  }, []);

  const onNewChat = useCallback(() => {
    setCurrentId(null);
    setConversationDrawerOpen(false);
  }, []);

  const onDeleteConversation = useCallback(
    async (id: string) => {
      try {
        await api.deleteConversation(id);
      } catch (e) {
        console.error(e);
      }
      if (id === currentId) setCurrentId(null);
      refreshConversations();
    },
    [currentId, refreshConversations],
  );

  function toggleCategory(c: string) {
    setSelected((prev) =>
      prev.includes(c) ? prev.filter((x) => x !== c) : [...prev, c],
    );
  }

  const currentConversation = conversations.find((conversation) => conversation.id === currentId);
  const sourceCount = [...messages].reverse().find((message) => message.sources?.length)?.sources?.length || 0;
  const scopeLabel = selected.length === 0 ? "全部企业知识" : selected.length === 1 ? selected[0] : `${selected.length} 个范围`;

  const sidebar = (
    <Sidebar
      conversations={conversations}
      conversationsLoading={conversationsLoading}
      currentConversationId={currentId}
      onSelectConversation={onSelectConversation}
      onDeleteConversation={onDeleteConversation}
      categories={categories}
      selected={selected}
      onToggle={toggleCategory}
      onClearCategories={() => setSelected([])}
      onNewChat={onNewChat}
    />
  );

  return (
    <PdfPreviewProvider>
      <div className="flex h-full min-w-0 overflow-hidden bg-background text-foreground">
        <div className="hidden h-full shrink-0 lg:block">{sidebar}</div>
        <main className="flex min-w-0 flex-1 flex-col">
          <ChatHeader
            title={currentConversation?.title || "品成 BIM 知识库"}
            scopeLabel={scopeLabel}
            loading={loading}
            sourceCount={sourceCount}
            sourceOpen={sourceOpen}
            onOpenConversations={() => setConversationDrawerOpen(true)}
            onToggleSources={() => setSourceOpen((value) => !value)}
          />
          <MessageList messages={messages} conversationId={currentId} />
          <Composer
            onSend={(t) => send(t, selected)}
            disabled={sending || loading}
            categories={categories}
            selected={selected}
            onToggleCategory={toggleCategory}
            onClearCategories={() => setSelected([])}
          />
        </main>
        {sourceOpen && (
          <div className="hidden h-full w-[21.5rem] shrink-0 border-l border-border xl:block">
            <SourceWorkspace messages={messages} conversationId={currentId} onClose={() => setSourceOpen(false)} />
          </div>
        )}
      </div>
      <Drawer open={conversationDrawerOpen} onClose={() => setConversationDrawerOpen(false)} title="会话导航">
        {sidebar}
      </Drawer>
      <Drawer open={sourceOpen} onClose={() => setSourceOpen(false)} title="来源核验" side="right" className="xl:hidden">
        <SourceWorkspace messages={messages} conversationId={currentId} />
      </Drawer>
      <PdfPreview />
    </PdfPreviewProvider>
  );
}
