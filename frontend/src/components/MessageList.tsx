import { ArrowDown } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ChatMessage } from "../types";
import { useAutoHideScrollbar } from "../hooks/useAutoHideScrollbar";
import { Message } from "./Message";
import { TurnNavigator, type TurnNavigationItem } from "./TurnNavigator";

type MessageTurn = TurnNavigationItem & {
  messages: ChatMessage[];
  turnIndex: number;
};

export function groupMessagesIntoTurns(messages: ChatMessage[]): MessageTurn[] {
  const turns: MessageTurn[] = [];

  messages.forEach((message) => {
    if (message.role === "user" || turns.length === 0) {
      turns.push({
        id: message.id,
        label: message.content.trim() || `第 ${turns.length + 1} 轮对话`,
        messages: [message],
        turnIndex: turns.length + 1,
      });
      return;
    }
    turns[turns.length - 1].messages.push(message);
  });

  return turns;
}

export function MessageList({
  messages,
  conversationId,
  centeredEmpty = false,
  sourceOpen = false,
  activeSourceMessageId = null,
  onToggleSources,
  sending = false,
  onEditQuestion,
  onViewQuestionVersion,
  onRegenerate,
  onViewAnswerVersion,
}: {
  messages: ChatMessage[];
  conversationId: string | null;
  centeredEmpty?: boolean;
  sourceOpen?: boolean;
  activeSourceMessageId?: string | null;
  onToggleSources?: (messageId: string) => void;
  sending?: boolean;
  onEditQuestion?: (messageId: string, content: string) => void;
  onViewQuestionVersion?: (messageId: string, versionIndex: number) => void;
  onRegenerate?: (messageId: string) => void;
  onViewAnswerVersion?: (messageId: string, versionIndex: number) => void;
}) {
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const turnRefs = useRef<Record<string, HTMLElement | null>>({});
  const previousConversationId = useRef<string | null>(conversationId);
  const previousMessageCount = useRef(0);
  const shouldFollowOutput = useRef(true);
  const scrollFrame = useRef<number | null>(null);
  const navigationTarget = useRef<string | null>(null);
  const navigationSettleTimer = useRef<number | null>(null);
  const [activeTurnId, setActiveTurnId] = useState<string | null>(null);
  const [showScrollToBottom, setShowScrollToBottom] = useState(false);
  const scrollbar = useAutoHideScrollbar<HTMLDivElement>();
  const turns = useMemo(() => groupMessagesIntoTurns(messages), [messages]);
  const lastMessage = messages[messages.length - 1];
  const streamSignature = `${lastMessage?.id || ""}:${lastMessage?.content.length || 0}:${lastMessage?.stage || ""}:${lastMessage?.streaming ? 1 : 0}`;

  const updateActiveTurn = useCallback(() => {
    const container = scrollbar.ref.current;
    if (!container || turns.length === 0) return;
    if (navigationTarget.current) {
      setActiveTurnId(navigationTarget.current);
      return;
    }
    const anchor = container.getBoundingClientRect().top + Math.min(120, container.clientHeight * 0.25);
    let active = turns[0].id;
    for (const turn of turns) {
      const element = turnRefs.current[turn.id];
      if (element && element.getBoundingClientRect().top <= anchor) active = turn.id;
    }
    setActiveTurnId(active);
  }, [scrollbar.ref, turns]);

  const scheduleNavigationSettle = useCallback(() => {
    if (navigationSettleTimer.current !== null) window.clearTimeout(navigationSettleTimer.current);
    navigationSettleTimer.current = window.setTimeout(() => {
      navigationTarget.current = null;
      navigationSettleTimer.current = null;
      updateActiveTurn();
    }, 160);
  }, [updateActiveTurn]);

  const handleScroll = useCallback(() => {
    scrollbar.interactionProps.onScroll();
    const container = scrollbar.ref.current;
    if (container) {
      const distanceFromBottom = container.scrollHeight - container.scrollTop - container.clientHeight;
      shouldFollowOutput.current = distanceFromBottom < 80;
      setShowScrollToBottom(distanceFromBottom > 96);
    }
    if (scrollFrame.current !== null) window.cancelAnimationFrame(scrollFrame.current);
    scrollFrame.current = window.requestAnimationFrame(updateActiveTurn);
    if (navigationTarget.current) scheduleNavigationSettle();
  }, [scheduleNavigationSettle, scrollbar.interactionProps, scrollbar.ref, updateActiveTurn]);

  const scrollToBottom = useCallback(() => {
    bottomRef.current?.scrollIntoView({ block: "end" });
    const latestTurn = turns[turns.length - 1];
    if (latestTurn) setActiveTurnId(latestTurn.id);
  }, [turns]);

  const smoothlyScrollToBottom = useCallback(() => {
    const container = scrollbar.ref.current;
    container?.scrollTo({ top: container.scrollHeight, behavior: "smooth" });
    shouldFollowOutput.current = true;
    setShowScrollToBottom(false);
    const latestTurn = turns[turns.length - 1];
    if (latestTurn) setActiveTurnId(latestTurn.id);
  }, [scrollbar.ref, turns]);

  useEffect(() => {
    const conversationChanged = previousConversationId.current !== conversationId;
    const messageAdded = messages.length > previousMessageCount.current;
    if (conversationChanged || messageAdded || (lastMessage?.streaming && shouldFollowOutput.current)) {
      scrollToBottom();
    }
    previousConversationId.current = conversationId;
    previousMessageCount.current = messages.length;
  }, [conversationId, lastMessage?.streaming, messages.length, scrollToBottom, streamSignature]);

  useEffect(() => {
    updateActiveTurn();
    return () => {
      if (scrollFrame.current !== null) window.cancelAnimationFrame(scrollFrame.current);
      if (navigationSettleTimer.current !== null) window.clearTimeout(navigationSettleTimer.current);
    };
  }, [turns, updateActiveTurn]);

  const navigateToTurn = useCallback((turnId: string) => {
    shouldFollowOutput.current = false;
    navigationTarget.current = turnId;
    setActiveTurnId(turnId);
    scheduleNavigationSettle();
    const reducedMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    turnRefs.current[turnId]?.scrollIntoView({
      behavior: reducedMotion ? "auto" : "smooth",
      block: "start",
    });
  }, [scheduleNavigationSettle]);

  if (messages.length === 0) {
    return (
      <div className={`flex items-center justify-center px-6 text-center ${centeredEmpty ? "flex-none" : "flex-1"}`}>
        <div className="max-w-lg">
          <h1 className="text-xl font-semibold text-foreground">从企业知识中找到可靠答案</h1>
          <p className="text-muted mt-2">
            统一管理公司多年积累的<strong>行业规范与标准</strong>、<strong>客户要求</strong>、
            <strong>内部标准</strong>、<strong>项目资料</strong>与<strong>培训视频</strong>，
            帮助员工快速查标准、查经验，并辅助新人培训。
          </p>
          <p className="text-muted mt-3 text-sm">
            试着问：
            <br />
            <code className="text-xs">Revit 建模交付时的命名规则是什么？</code>
            <br />
            <code className="text-xs">XX 客户对图层有哪些特殊要求？</code>
          </p>
        </div>
      </div>
    );
  }

  const latestAssistantId = [...messages]
    .reverse()
    .find((message) => message.role === "assistant")?.id;
  const latestUserId = [...messages]
    .reverse()
    .find((message) => message.role === "user")?.id;

  return (
    <div className="scroll-fade-content-start relative min-h-0 flex-1">
      <div
        ref={scrollbar.ref}
        data-message-scroll-container
        className={`h-full overflow-y-auto py-6 ${scrollbar.className}`}
        onScroll={handleScroll}
        onMouseEnter={scrollbar.interactionProps.onMouseEnter}
        onMouseMove={scrollbar.interactionProps.onMouseMove}
        onMouseLeave={scrollbar.interactionProps.onMouseLeave}
        onFocus={scrollbar.interactionProps.onFocus}
      >
        {turns.map((turn) => (
          <section
            key={turn.id}
            ref={(element) => { turnRefs.current[turn.id] = element; }}
            data-turn-id={turn.id}
            className="scroll-mt-6"
          >
            {turn.messages.map((message) => {
              const turnUserMessage = turn.messages.find((item) => item.role === "user");
              const activeQuestionVersion = turnUserMessage?.userVersions?.find((version) => version.isActive);
              const viewingActiveQuestion =
                !activeQuestionVersion
                || turnUserMessage?.viewedUserVersionIndex === activeQuestionVersion.versionIndex;
              return (
            <Message
              key={message.id}
              msg={message}
              conversationId={conversationId}
              turnIndex={turn.turnIndex}
              sourcesSelected={sourceOpen && activeSourceMessageId === message.id}
              onToggleSources={onToggleSources}
              canEdit={
                message.role === "user"
                && message.id === latestUserId
                && !sending
              }
              onEdit={onEditQuestion}
              onViewQuestionVersion={onViewQuestionVersion}
              canRegenerate={
                message.role === "assistant"
                && message.id === latestAssistantId
                && !sending
                && !message.streaming
                && viewingActiveQuestion
              }
              onRegenerate={onRegenerate}
              onViewAnswerVersion={onViewAnswerVersion}
            />
              );
            })}
          </section>
        ))}
        <div ref={bottomRef} aria-hidden="true" className="h-20 sm:h-24" data-message-bottom-spacer />
      </div>
      <TurnNavigator
        turns={turns}
        activeTurnId={activeTurnId}
        onNavigate={navigateToTurn}
        className={sourceOpen ? "hidden 2xl:block" : "hidden xl:block"}
      />
      {showScrollToBottom && (
        <button
          type="button"
          aria-label="回到底部"
          title="回到底部"
          onClick={smoothlyScrollToBottom}
          className="absolute bottom-3 left-1/2 z-10 inline-flex size-9 -translate-x-1/2 items-center justify-center rounded-full border border-border bg-card text-muted-foreground shadow-surface transition-colors hover:bg-accent hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <ArrowDown className="size-4" />
        </button>
      )}
    </div>
  );
}
