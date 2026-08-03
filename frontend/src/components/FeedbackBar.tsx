import { useState } from "react";
import { Copy, ThumbsDown } from "lucide-react";
import { api } from "../api/client";
import type { ChatMessage } from "../types";
import { Button } from "./ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "./ui/dialog";
import { IconButton } from "./ui/icon-button";
import { toast } from "./ui/toast";

const feedbackReasons = ["有害/不安全", "虚假信息", "没有帮助", "其他"] as const;
type FeedbackReason = (typeof feedbackReasons)[number];

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
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState<FeedbackReason | null>(null);
  const [note, setNote] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [sent, setSent] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function submit() {
    const trimmed = note.trim();
    if (!reason) {
      setErr("请选择一个反馈原因");
      return;
    }
    if (reason === "其他" && !trimmed) {
      setErr("选择其他时请填写具体原因");
      return;
    }
    setSubmitting(true);
    setErr(null);
    try {
      await api.sendFeedback({
        kind: "answer",
        rating: "down",
        note: trimmed ? `原因：${reason}\n补充：${trimmed}` : `原因：${reason}`,
        conversation_id: conversationId,
        turn_index: turnIndex,
        message_id: msg.id,
        query: msg.query,
        answer_text: msg.content,
      });
      setSent(true);
      setOpen(false);
      setReason(null);
      setNote("");
      toast.success("反馈已提交，感谢你的帮助");
    } catch (e: any) {
      setErr(e?.message || String(e));
    } finally {
      setSubmitting(false);
    }
  }

  function resetDraft() {
    setReason(null);
    setNote("");
    setErr(null);
  }

  function handleOpenChange(nextOpen: boolean) {
    if (submitting) return;
    setOpen(nextOpen);
    if (!nextOpen) resetDraft();
  }

  async function handleCopy() {
    try {
      await copyText(msg.content);
      toast.success("回答已复制");
    } catch {
      toast.error("复制失败，请稍后重试");
    }
  }

  return (
    <div className="ml-auto flex shrink-0 items-center gap-1" aria-label="回答操作">
      <IconButton label="复制回答" onClick={handleCopy}>
        <Copy className="size-4" />
      </IconButton>

      <Dialog open={open} onOpenChange={handleOpenChange}>
        <DialogTrigger asChild>
          <IconButton
            label={sent ? "反馈已提交" : "这个回答不好"}
            disabled={sent}
            aria-pressed={sent}
            className={sent ? "bg-destructive/10 text-destructive" : "hover:text-destructive"}
          >
            <ThumbsDown className="size-4" />
          </IconButton>
        </DialogTrigger>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>反馈</DialogTitle>
            <DialogDescription>选择最符合的问题类型，帮助我们改进回答质量。</DialogDescription>
          </DialogHeader>

          <div className="flex flex-wrap gap-2" aria-label="反馈原因">
            {feedbackReasons.map((item) => {
              const selected = reason === item;
              return (
                <button
                  key={item}
                  type="button"
                  aria-pressed={selected}
                  onClick={() => {
                    setReason(item);
                    setErr(null);
                  }}
                  className={
                    "h-control-sm rounded-ui-md border px-3 text-ui-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring " +
                    (selected
                      ? "border-primary bg-primary text-primary-foreground"
                      : "border-border bg-background text-muted-foreground hover:bg-secondary hover:text-foreground")
                  }
                >
                  {item}
                </button>
              );
            })}
          </div>

          <div className="space-y-2">
            <label htmlFor={`feedback-note-${msg.id}`} className="text-ui-sm font-medium text-foreground">
              补充说明{reason === "其他" ? "（必填）" : "（选填）"}
            </label>
            <textarea
              id={`feedback-note-${msg.id}`}
              value={note}
              onChange={(event) => {
                setNote(event.target.value);
                setErr(null);
              }}
              placeholder="告诉我们这个回答哪里需要改进"
              rows={5}
              maxLength={1000}
              autoFocus
              className="w-full resize-y rounded-ui-md border border-input bg-background px-3 py-2 text-ui-sm text-foreground outline-none placeholder:text-muted-foreground focus-visible:ring-2 focus-visible:ring-ring"
            />
            {err && (
              <p role="alert" className="text-ui-xs text-destructive">
                {err}
              </p>
            )}
          </div>

          <DialogFooter>
            <Button variant="outline" onClick={() => handleOpenChange(false)} disabled={submitting}>
              取消
            </Button>
            <Button
              onClick={submit}
              disabled={submitting || !reason || (reason === "其他" && !note.trim())}
            >
              {submitting ? "提交中…" : "提交"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
