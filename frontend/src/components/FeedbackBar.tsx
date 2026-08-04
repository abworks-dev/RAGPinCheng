import { useEffect, useRef, useState } from "react";
import { Check, Copy, ThumbsDown } from "lucide-react";
import { api } from "../api/client";
import type { ChatMessage } from "../types";
import { FeedbackDialog, type FeedbackSubmission } from "./FeedbackDialog";
import { IconButton } from "./ui/icon-button";
import { toast } from "./ui/toast";

const feedbackReasons = ["有害/不安全", "虚假信息", "没有帮助", "其他"] as const;
async function copyText(text: string) {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }

  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.style.position = "fixed";
  textarea.style.opacity = "0";
  document.body.appendChild(textarea);
  textarea.select();
  const copied = document.execCommand("copy");
  textarea.remove();
  if (!copied) throw new Error("copy command failed");
}

export function FeedbackBar({
  msg,
  conversationId,
  turnIndex,
}: {
  msg: ChatMessage;
  conversationId: string | null;
  turnIndex: number;
}) {
  const [sent, setSent] = useState(false);
  const [copied, setCopied] = useState(false);
  const copyResetTimer = useRef<number | null>(null);

  useEffect(() => () => {
    if (copyResetTimer.current !== null) window.clearTimeout(copyResetTimer.current);
  }, []);

  async function submitFeedback({ reason, note }: FeedbackSubmission) {
    await api.sendFeedback({
      kind: "answer",
      rating: "down",
      note: note ? `原因：${reason}\n补充：${note}` : `原因：${reason}`,
      conversation_id: conversationId,
      turn_index: turnIndex,
      message_id: msg.id,
      query: msg.query,
      answer_text: msg.content,
    });
  }

  async function handleCopy() {
    try {
      await copyText(msg.content);
      setCopied(true);
      if (copyResetTimer.current !== null) window.clearTimeout(copyResetTimer.current);
      copyResetTimer.current = window.setTimeout(() => {
        setCopied(false);
        copyResetTimer.current = null;
      }, 1400);
    } catch {
      setCopied(false);
      toast.error("复制失败，请稍后重试");
    }
  }

  return (
    <div className="ml-auto flex shrink-0 items-center gap-1" aria-label="回答操作">
      <IconButton
        label={copied ? "回答已复制" : "复制回答"}
        onClick={handleCopy}
        className={copied ? "text-success hover:text-success" : undefined}
      >
        {copied ? <Check className="size-4" /> : <Copy className="size-4" />}
      </IconButton>

      <FeedbackDialog
        category="回答问题"
        description="选择最符合的问题类型，帮助我们改进回答质量。"
        reasons={feedbackReasons}
        notePlaceholder="告诉我们这个回答哪里需要改进"
        successMessage="反馈已提交，感谢你的帮助"
        onSubmit={submitFeedback}
        onSubmitted={() => setSent(true)}
        trigger={
          <IconButton
            label={sent ? "反馈已提交" : "这个回答不好"}
            disabled={sent}
            aria-pressed={sent}
            className={sent ? "bg-destructive/10 text-destructive" : "hover:text-destructive"}
          >
            <ThumbsDown className="size-4" />
          </IconButton>
        }
      />
    </div>
  );
}
