import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import { streamChat } from "../api/chatStream";
import type { ChatMessage } from "../types";

function newId() {
  return Math.random().toString(36).slice(2, 10);
}

/** Manages the active chat thread.
 *
 * The owning component drives `conversationId`. When it changes, we replay
 * the conversation's messages from the backend. A null id means "fresh
 * chat, not yet persisted" — the first `send()` call will create one on
 * the fly and notify the parent.
 */
export function useChat({
  conversationId,
  onConversationCreated,
  onConversationUpdated,
}: {
  conversationId: string | null;
  onConversationCreated?: (id: string) => void;
  onConversationUpdated?: (id: string) => void;
}) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [loading, setLoading] = useState(false);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  // When send() lazy-creates a conversation, it stashes the new id here so
  // the next [conversationId] effect run treats it as a no-op — otherwise
  // the effect would abort the streaming controller we just set and replace
  // the optimistic user/assistant messages with the (still-empty) DB read.
  const skipNextLoadRef = useRef<string | null>(null);

  // Reload messages whenever the active conversation changes.
  useEffect(() => {
    if (conversationId && skipNextLoadRef.current === conversationId) {
      skipNextLoadRef.current = null;
      return;
    }
    abortRef.current?.abort();
    setError(null);
    if (!conversationId) {
      setMessages([]);
      return;
    }
    let cancelled = false;
    setLoading(true);
    (async () => {
      try {
        const state = await api.getConversation(conversationId);
        if (cancelled) return;
        // Pair each assistant message with the immediately-preceding user
        // turn so the FeedbackBar has `query` to ship — otherwise resumed
        // conversations would send feedback with only `answer_text`.
        let lastUserContent: string | undefined;
        let lastUserVersionId: string | undefined;
        const replayed: ChatMessage[] = state.messages.map((m) => {
          if (m.role === "user") {
            lastUserContent = m.content;
            lastUserVersionId = m.user_versions?.find((version) => version.is_active)?.id != null
              ? String(m.user_versions.find((version) => version.is_active)!.id)
              : undefined;
          }
          const allAnswerVersions = m.answer_versions?.map((version) => ({
            id: String(version.id),
            versionIndex: version.version_index,
            content: version.content,
            sources: version.sources_for_ui || undefined,
            isActive: version.is_active,
            userVersionId: version.user_version_id != null ? String(version.user_version_id) : undefined,
          }));
          const visibleAnswerVersions = m.role === "assistant" && lastUserVersionId
            ? allAnswerVersions?.filter(
                (version) => version.userVersionId === lastUserVersionId,
              )
            : allAnswerVersions;
          return {
            id: m.id != null ? String(m.id) : newId(),
            role: m.role,
            content: m.content,
            sources: m.sources_for_ui || undefined,
            query: m.role === "assistant" ? lastUserContent : undefined,
            stage: "done",
            answerVersions: visibleAnswerVersions,
            allAnswerVersions,
            viewedVersionIndex: visibleAnswerVersions?.find((version) => version.isActive)?.versionIndex,
            userVersions: m.user_versions?.map((version) => ({
              id: String(version.id),
              versionIndex: version.version_index,
              content: version.content,
              createdAt: version.created_at,
              isActive: version.is_active,
            })),
            activeUserVersionId: lastUserVersionId,
            viewedUserVersionIndex: m.user_versions?.find((version) => version.is_active)?.version_index,
          };
        });
        setMessages(replayed);
      } catch (e: any) {
        if (!cancelled) setError(e?.message || String(e));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [conversationId]);

  const send = useCallback(
    async (query: string, categories?: string[] | null) => {
      const trimmed = query.trim();
      if (!trimmed || sending) return;

      // Lazy-create a conversation on the first message if there isn't one.
      let cid = conversationId;
      if (!cid) {
        try {
          const conv = await api.createConversation();
          cid = conv.id;
          // Mark the just-created id so the [conversationId] effect doesn't
          // run its abort+reload path when the parent calls setCurrentId.
          skipNextLoadRef.current = cid;
          onConversationCreated?.(cid);
        } catch (e: any) {
          setError(e?.message || String(e));
          return;
        }
      }

      setSending(true);
      abortRef.current?.abort();
      const ctrl = new AbortController();
      abortRef.current = ctrl;

      const userMsg: ChatMessage = { id: newId(), role: "user", content: trimmed };
      const assistantId = newId();
      const assistantMsg: ChatMessage = {
        id: assistantId,
        role: "assistant",
        content: "",
        query: trimmed,
        streaming: true,
        stage: "retrieving",
      };
      setMessages((prev) => [...prev, userMsg, assistantMsg]);

      let gotContent = false;
      let aborted = false;
      try {
        for await (const ev of streamChat(
          cid,
          { query: trimmed, categories: categories && categories.length ? categories : null },
          ctrl.signal,
        )) {
          if (ev.type === "prep") {
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantId
                  ? { ...m, prep: ev.data, sources: ev.data.used_sources, stage: "generating" }
                  : m,
              ),
            );
          } else if (ev.type === "token") {
            if (ev.data.text) gotContent = true;
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantId
                  ? { ...m, content: m.content + ev.data.text, stage: "streaming" }
                  : m,
              ),
            );
          } else if (ev.type === "done") {
            gotContent = true;
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantId
                  ? {
                      ...m,
                      id: ev.data.assistant_message_id != null
                        ? String(ev.data.assistant_message_id)
                        : m.id,
                      content: ev.data.answer_text || m.content,
                      sources: ev.data.sources,
                      done: ev.data,
                      streaming: false,
                      stage: "done",
                    }
                  : m,
              ),
            );
          } else if (ev.type === "error") {
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantId
                  ? { ...m, error: ev.data.message, streaming: false, stage: "done" }
                  : m,
              ),
            );
          }
        }
      } catch (e: any) {
        aborted = e?.name === "AbortError";
        const msg = aborted ? "（已中止）" : e?.message || String(e);
        setMessages((prev) =>
          prev.map((m) =>
            m.id === assistantId
              ? { ...m, error: msg, streaming: false, stage: "done" }
              : m,
          ),
        );
      } finally {
        setSending(false);
        setMessages((prev) =>
          prev.map((m) =>
            m.id === assistantId && m.streaming
              ? { ...m, streaming: false, stage: "done" }
              : m,
          ),
        );
        // If the user switched away before any assistant content arrived,
        // drop the lazily-created conversation so it doesn't clutter history.
        if (cid && aborted && !gotContent) {
          try {
            await api.deleteConversation(cid);
          } catch {
            // best-effort cleanup; ignore failures
          }
        }
        // Persisted (or deleted); let the sidebar refresh either way.
        if (cid) onConversationUpdated?.(cid);
      }
    },
    [sending, conversationId, onConversationCreated, onConversationUpdated],
  );

  const regenerate = useCallback(
    async (assistantMessageId: string) => {
      if (sending || !conversationId || !/^\d+$/.test(assistantMessageId)) return;
      const snapshot = messages.find((message) => message.id === assistantMessageId);
      if (!snapshot || snapshot.role !== "assistant") return;

      setSending(true);
      abortRef.current?.abort();
      const ctrl = new AbortController();
      abortRef.current = ctrl;
      setMessages((prev) =>
        prev.map((message) =>
          message.id === assistantMessageId
            ? {
                ...message,
                content: "",
                sources: undefined,
                error: undefined,
                streaming: true,
                stage: "retrieving",
              }
            : message,
        ),
      );

      let completed = false;
      try {
        for await (const ev of streamChat(
          conversationId,
          { regenerate_assistant_message_id: Number(assistantMessageId) },
          ctrl.signal,
        )) {
          if (ev.type === "prep") {
            setMessages((prev) =>
              prev.map((message) =>
                message.id === assistantMessageId
                  ? { ...message, prep: ev.data, sources: ev.data.used_sources, stage: "generating" }
                  : message,
              ),
            );
          } else if (ev.type === "token") {
            setMessages((prev) =>
              prev.map((message) =>
                message.id === assistantMessageId
                  ? { ...message, content: message.content + ev.data.text, stage: "streaming" }
                  : message,
              ),
            );
          } else if (ev.type === "done") {
            completed = true;
            setMessages((prev) =>
              prev.map((message) => {
                if (message.id !== assistantMessageId) return message;
                const oldVersions = message.answerVersions?.length
                  ? message.answerVersions
                  : [{
                      id: `${assistantMessageId}-original`,
                      versionIndex: 1,
                      content: snapshot.content,
                      sources: snapshot.sources,
                      isActive: true,
                    }];
                const nextIndex = Math.max(...oldVersions.map((version) => version.versionIndex)) + 1;
                const nextVersion = {
                  id: `${assistantMessageId}-pending-${nextIndex}`,
                  versionIndex: nextIndex,
                  content: ev.data.answer_text || message.content,
                  sources: ev.data.sources,
                  isActive: true,
                  userVersionId: snapshot.activeUserVersionId,
                };
                const updatedVisibleVersions = [
                  ...oldVersions.map((version) => ({ ...version, isActive: false })),
                  nextVersion,
                ];
                const visibleIds = new Set(oldVersions.map((version) => version.id));
                return {
                  ...message,
                  content: ev.data.answer_text || message.content,
                  sources: ev.data.sources,
                  done: ev.data,
                  streaming: false,
                  stage: "done",
                  answerVersions: updatedVisibleVersions,
                  allAnswerVersions: [
                    ...(snapshot.allAnswerVersions || []).filter((version) => !visibleIds.has(version.id)),
                    ...updatedVisibleVersions,
                  ],
                  viewedVersionIndex: nextIndex,
                };
              }),
            );
          } else if (ev.type === "error") {
            throw new Error(ev.data.message);
          }
        }
      } catch (e: any) {
        const message = e?.name === "AbortError" ? "重新生成已中止" : e?.message || String(e);
        setMessages((prev) =>
          prev.map((item) =>
            item.id === assistantMessageId
              ? { ...snapshot, error: message, streaming: false, stage: "done" }
              : item,
          ),
        );
      } finally {
        if (!completed) {
          setMessages((prev) =>
            prev.map((item) =>
              item.id === assistantMessageId && item.streaming
                ? { ...snapshot, streaming: false, stage: "done" }
                : item,
            ),
          );
        }
        setSending(false);
        onConversationUpdated?.(conversationId);
      }
    },
    [conversationId, messages, onConversationUpdated, sending],
  );

  const editQuestion = useCallback(
    async (userMessageId: string, editedContent: string) => {
      const query = editedContent.trim();
      if (sending || !conversationId || !query || !/^\d+$/.test(userMessageId)) return;
      const userIndex = messages.findIndex((message) => message.id === userMessageId);
      const userSnapshot = messages[userIndex];
      const answerSnapshot = messages[userIndex + 1];
      if (
        !userSnapshot ||
        userSnapshot.role !== "user" ||
        !answerSnapshot ||
        answerSnapshot.role !== "assistant" ||
        userSnapshot.content === query
      ) return;

      setSending(true);
      abortRef.current?.abort();
      const ctrl = new AbortController();
      abortRef.current = ctrl;
      setMessages((prev) =>
        prev.map((message) => {
          if (message.id === userMessageId) return { ...message, content: query };
          if (message.id === answerSnapshot.id) {
            return {
              ...message,
              content: "",
              sources: undefined,
              error: undefined,
              streaming: true,
              stage: "retrieving",
              query,
            };
          }
          return message;
        }),
      );

      let completed = false;
      try {
        for await (const ev of streamChat(
          conversationId,
          { query, edit_user_message_id: Number(userMessageId) },
          ctrl.signal,
        )) {
          if (ev.type === "prep") {
            setMessages((prev) =>
              prev.map((message) =>
                message.id === answerSnapshot.id
                  ? { ...message, prep: ev.data, sources: ev.data.used_sources, stage: "generating" }
                  : message,
              ),
            );
          } else if (ev.type === "token") {
            setMessages((prev) =>
              prev.map((message) =>
                message.id === answerSnapshot.id
                  ? { ...message, content: message.content + ev.data.text, stage: "streaming" }
                  : message,
              ),
            );
          } else if (ev.type === "done") {
            completed = true;
            const pendingUserVersionId = `${userMessageId}-pending-edit`;
            setMessages((prev) =>
              prev.map((message) => {
                if (message.id === userMessageId) {
                  const oldVersions = message.userVersions?.length
                    ? message.userVersions
                    : [{
                        id: `${userMessageId}-original`,
                        versionIndex: 1,
                        content: userSnapshot.content,
                        createdAt: Math.floor(Date.now() / 1000),
                        isActive: true,
                      }];
                  const nextIndex = Math.max(...oldVersions.map((version) => version.versionIndex)) + 1;
                  return {
                    ...message,
                    content: query,
                    userVersions: [
                      ...oldVersions.map((version) => ({ ...version, isActive: false })),
                      {
                        id: pendingUserVersionId,
                        versionIndex: nextIndex,
                        content: query,
                        createdAt: Math.floor(Date.now() / 1000),
                        isActive: true,
                      },
                    ],
                    activeUserVersionId: pendingUserVersionId,
                    viewedUserVersionIndex: nextIndex,
                  };
                }
                if (message.id === answerSnapshot.id) {
                  const nextAnswer = {
                    id: `${answerSnapshot.id}-pending-edit`,
                    versionIndex: Math.max(
                      0,
                      ...(answerSnapshot.allAnswerVersions || answerSnapshot.answerVersions || [])
                        .map((version) => version.versionIndex),
                    ) + 1,
                    content: ev.data.answer_text || message.content,
                    sources: ev.data.sources,
                    isActive: true,
                    userVersionId: pendingUserVersionId,
                  };
                  return {
                    ...message,
                    content: ev.data.answer_text || message.content,
                    sources: ev.data.sources,
                    done: ev.data,
                    streaming: false,
                    stage: "done",
                    query,
                    answerVersions: [nextAnswer],
                    allAnswerVersions: [
                      ...(answerSnapshot.allAnswerVersions || answerSnapshot.answerVersions || [])
                        .map((version) => ({ ...version, isActive: false })),
                      nextAnswer,
                    ],
                    viewedVersionIndex: nextAnswer.versionIndex,
                  };
                }
                return message;
              }),
            );
          } else if (ev.type === "error") {
            throw new Error(ev.data.message);
          }
        }
      } catch (e: any) {
        const editError = e?.name === "AbortError" ? "编辑已中止" : e?.message || String(e);
        setMessages((prev) =>
          prev.map((message) => {
            if (message.id === userMessageId) return userSnapshot;
            if (message.id === answerSnapshot.id) {
              return { ...answerSnapshot, error: editError, streaming: false, stage: "done" };
            }
            return message;
          }),
        );
      } finally {
        if (!completed) {
          setMessages((prev) =>
            prev.map((message) =>
              message.id === answerSnapshot.id && message.streaming
                ? { ...answerSnapshot, streaming: false, stage: "done" }
                : message,
            ),
          );
        }
        setSending(false);
        onConversationUpdated?.(conversationId);
      }
    },
    [conversationId, messages, onConversationUpdated, sending],
  );

  const viewAnswerVersion = useCallback((assistantMessageId: string, versionIndex: number) => {
    setMessages((prev) =>
      prev.map((message) => {
        if (message.id !== assistantMessageId) return message;
        const version = message.answerVersions?.find((item) => item.versionIndex === versionIndex);
        return version
          ? {
              ...message,
              content: version.content,
              sources: version.sources,
              viewedVersionIndex: version.versionIndex,
              error: undefined,
            }
          : message;
      }),
    );
  }, []);

  const viewQuestionVersion = useCallback((userMessageId: string, versionIndex: number) => {
    setMessages((prev) => {
      const userIndex = prev.findIndex((message) => message.id === userMessageId);
      const userMessage = prev[userIndex];
      const version = userMessage?.userVersions?.find((item) => item.versionIndex === versionIndex);
      if (!userMessage || userMessage.role !== "user" || !version) return prev;
      const pairedAnswer = prev[userIndex + 1];
      const allAnswers = pairedAnswer?.allAnswerVersions || pairedAnswer?.answerVersions || [];
      const linkedAnswers = allAnswers.filter(
        (answer) =>
          answer.userVersionId === version.id
          || (version.versionIndex === 1 && !answer.userVersionId),
      );
      const displayedAnswer = linkedAnswers[linkedAnswers.length - 1];
      return prev.map((message, index) => {
        if (index === userIndex) {
          return {
            ...message,
            content: version.content,
            viewedUserVersionIndex: version.versionIndex,
          };
        }
        if (index === userIndex + 1 && pairedAnswer?.role === "assistant" && displayedAnswer) {
          return {
            ...message,
            content: displayedAnswer.content,
            sources: displayedAnswer.sources,
            query: version.content,
            answerVersions: linkedAnswers,
            allAnswerVersions: allAnswers,
            viewedVersionIndex: displayedAnswer.versionIndex,
            error: undefined,
          };
        }
        return message;
      });
    });
  }, []);

  return {
    messages,
    send,
    regenerate,
    editQuestion,
    viewAnswerVersion,
    viewQuestionVersion,
    sending,
    loading,
    error,
  };
}
