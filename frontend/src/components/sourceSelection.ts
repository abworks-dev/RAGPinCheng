import type { ChatMessage, Source } from "../types";

export type SourceSet = {
  messageId: string;
  sources: Source[];
  searchQuery?: string;
};

export function sourceSetsFromMessages(messages: ChatMessage[]): SourceSet[] {
  return messages
    .filter((message) => message.role === "assistant" && message.sources?.length)
    .map((message) => ({
      messageId: message.id,
      sources: message.sources || [],
      searchQuery: message.prep?.search_query || message.query,
    }));
}

export function selectedSourceSet(
  messages: ChatMessage[],
  selectedMessageId: string | null,
): SourceSet | undefined {
  const sets = sourceSetsFromMessages(messages);
  const latest = sets[sets.length - 1];
  return sets.find((set) => set.messageId === selectedMessageId) || latest;
}

export function getSelectedSourceCount(
  messages: ChatMessage[],
  selectedMessageId: string | null,
): number {
  return selectedSourceSet(messages, selectedMessageId)?.sources.length || 0;
}
