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
import {
  CITATION_EVENT,
  CITATION_HOVER_EVENT,
  dispatchCitation,
  toggleCitationSelection,
  type CitationDetail,
  type CitationHoverDetail,
  type CitationSelection,
} from "./citations";
import { getSelectedSourceCount } from "./sourceSelection";

export function ChatLayout() {
  const [categories, setCategories] = useState<string[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [conversationsLoading, setConversationsLoading] = useState(true);
  const [currentId, setCurrentId] = useState<string | null>(null);
  const [conversationDrawerOpen, setConversationDrawerOpen] = useState(false);
  const [sourceOpen, setSourceOpen] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [activeSourceMessageId, setActiveSourceMessageId] = useState<string | null>(null);
  const [activeCitation, setActiveCitation] = useState<CitationSelection>(null);

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
    const openSources = (event: Event) => {
      const detail = (event as CustomEvent<CitationDetail>).detail;
      if (!detail?.messageId) return;
      setActiveSourceMessageId(detail.messageId);
      setSourceOpen(true);
      const next = toggleCitationSelection(activeCitation, detail);
      setActiveCitation(next);
      window.dispatchEvent(
        new CustomEvent<CitationHoverDetail>(CITATION_HOVER_EVENT, {
          detail: {
            messageId: detail.messageId,
            sourceIndex: next?.sourceIndex ?? null,
          },
        }),
      );
    };
    window.addEventListener(CITATION_EVENT, openSources);
    return () => window.removeEventListener(CITATION_EVENT, openSources);
  }, [activeCitation]);

  const {
    messages,
    send,
    regenerate,
    editQuestion,
    viewAnswerVersion,
    viewQuestionVersion,
    sending,
    loading,
  } = useChat({
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
    setSourceOpen(false);
    setActiveSourceMessageId(null);
    setActiveCitation(null);
  }, []);

  const onNewChat = useCallback(() => {
    setCurrentId(null);
    setConversationDrawerOpen(false);
    setSourceOpen(false);
    setActiveSourceMessageId(null);
    setActiveCitation(null);
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
  const sourceCount = getSelectedSourceCount(messages, activeSourceMessageId);
  const scopeLabel = selected.length === 0 ? "全部企业知识" : selected.length === 1 ? selected[0] : `${selected.length} 个范围`;

  const clearCitationHighlight = useCallback(() => {
    if (activeCitation) {
      window.dispatchEvent(
        new CustomEvent<CitationHoverDetail>(CITATION_HOVER_EVENT, {
          detail: { messageId: activeCitation.messageId, sourceIndex: null },
        }),
      );
    }
    setActiveCitation(null);
  }, [activeCitation]);

  const closeSources = useCallback(() => {
    setSourceOpen(false);
    clearCitationHighlight();
  }, [clearCitationHighlight]);

  const toggleMessageSources = useCallback((messageId: string) => {
    if (sourceOpen && activeSourceMessageId === messageId) {
      closeSources();
      return;
    }
    setActiveSourceMessageId(messageId);
    dispatchCitation({ messageId, sourceIndex: 0 });
  }, [activeSourceMessageId, closeSources, sourceOpen]);

  const selectSource = useCallback((messageId: string, sourceIndex: number) => {
    setActiveSourceMessageId(messageId);
    setActiveCitation({ messageId, sourceIndex });
  }, []);

  const sidebar = (collapsed: boolean, onToggleCollapsed?: () => void) => (
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
      collapsed={collapsed}
      onToggleCollapsed={onToggleCollapsed}
    />
  );

  return (
    <PdfPreviewProvider>
      <div className="flex h-full min-w-0 overflow-hidden bg-background text-foreground">
        <div className="hidden h-full shrink-0 lg:block">
          {sidebar(sidebarCollapsed, () => setSidebarCollapsed((value) => !value))}
        </div>
        <main className="flex min-w-0 flex-1 flex-col">
          <ChatHeader
            title={currentConversation?.title || "品成 BIM 知识库"}
            scopeLabel={scopeLabel}
            loading={loading}
            sourceCount={sourceCount}
            sourceOpen={sourceOpen}
            onOpenConversations={() => setConversationDrawerOpen(true)}
            onToggleSources={() => {
              if (sourceOpen) closeSources();
              else setSourceOpen(true);
            }}
          />
          {messages.length === 0 ? (
            <div className="flex min-h-0 flex-1 flex-col justify-center gap-7 py-8">
              <MessageList messages={messages} conversationId={currentId} centeredEmpty />
              <Composer
                centered
                onSend={(t) => send(t, selected)}
                disabled={sending || loading}
                categories={categories}
                selected={selected}
                onToggleCategory={toggleCategory}
                onClearCategories={() => setSelected([])}
              />
            </div>
          ) : <>
          <MessageList
            messages={messages}
            conversationId={currentId}
            sourceOpen={sourceOpen}
            activeSourceMessageId={activeSourceMessageId}
            onToggleSources={toggleMessageSources}
            sending={sending}
            onEditQuestion={editQuestion}
            onViewQuestionVersion={viewQuestionVersion}
            onRegenerate={regenerate}
            onViewAnswerVersion={viewAnswerVersion}
          />
          <Composer
            onSend={(t) => send(t, selected)}
            disabled={sending || loading}
            categories={categories}
            selected={selected}
            onToggleCategory={toggleCategory}
            onClearCategories={() => setSelected([])}
          />
          </>}
        </main>
          <div className={`hidden h-full shrink-0 overflow-hidden transition-[width,opacity] duration-slow xl:block ${sourceOpen ? "w-[23rem] opacity-100" : "w-0 opacity-0"}`}>
            <div className="h-full w-[23rem]">
            <SourceWorkspace
              messages={messages}
              conversationId={currentId}
              selectedMessageId={activeSourceMessageId}
              highlightedSourceIndex={activeCitation?.messageId === activeSourceMessageId ? activeCitation.sourceIndex : null}
              onSelectedMessageChange={setActiveSourceMessageId}
              onSourceHighlightChange={selectSource}
            />
            </div>
          </div>
      </div>
      <Drawer open={conversationDrawerOpen} onClose={() => setConversationDrawerOpen(false)} title="会话导航">
        {sidebar(false)}
      </Drawer>
      <Drawer open={sourceOpen} onClose={closeSources} title="来源核验" side="right" className="xl:hidden">
        <SourceWorkspace
          messages={messages}
          conversationId={currentId}
          selectedMessageId={activeSourceMessageId}
          highlightedSourceIndex={activeCitation?.messageId === activeSourceMessageId ? activeCitation.sourceIndex : null}
          onSelectedMessageChange={setActiveSourceMessageId}
          onSourceHighlightChange={selectSource}
        />
      </Drawer>
      <PdfPreview />
    </PdfPreviewProvider>
  );
}
