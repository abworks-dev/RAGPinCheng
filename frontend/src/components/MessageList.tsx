import { useEffect, useRef } from "react";
import type { ChatMessage } from "../types";
import { Message } from "./Message";

export function MessageList({
  messages,
  conversationId,
  centeredEmpty = false,
  sourceOpen = false,
  activeSourceMessageId = null,
  onToggleSources,
  sending = false,
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
  onRegenerate?: (messageId: string) => void;
  onViewAnswerVersion?: (messageId: string, versionIndex: number) => void;
}) {
  const bottomRef = useRef<HTMLDivElement | null>(null);
  // Auto-scroll on every render so streaming tokens stay in view.
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "end" });
  });

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

  return (
    <div className="min-h-0 flex-1 overflow-y-auto py-6">
      {(() => {
        let turn = 0;
        const latestAssistantId = [...messages].reverse().find((message) => message.role === "assistant")?.id;
        return messages.map((m) => {
          if (m.role === "user") turn += 1;
          return (
            <Message
              key={m.id}
              msg={m}
              conversationId={conversationId}
              turnIndex={m.role === "assistant" ? turn : turn}
              sourcesSelected={sourceOpen && activeSourceMessageId === m.id}
              onToggleSources={onToggleSources}
              canRegenerate={m.role === "assistant" && m.id === latestAssistantId && !sending && !m.streaming}
              onRegenerate={onRegenerate}
              onViewAnswerVersion={onViewAnswerVersion}
            />
          );
        });
      })()}
      <div ref={bottomRef} />
    </div>
  );
}
