import { useId, useState, type ReactElement } from "react";
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
import { toast } from "./ui/toast";

export type FeedbackSubmission = {
  reason: string;
  note: string;
};

export function FeedbackDialog({
  category,
  description,
  reasons,
  notePlaceholder,
  successMessage,
  trigger,
  onSubmit,
  onSubmitted,
}: {
  category: string;
  description: string;
  reasons: readonly string[];
  notePlaceholder: string;
  successMessage: string;
  trigger: ReactElement;
  onSubmit: (submission: FeedbackSubmission) => Promise<void>;
  onSubmitted?: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const noteId = useId();

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

  async function submit() {
    const trimmed = note.trim();
    if (!reason) {
      setErr("请选择一个问题类型");
      return;
    }
    if (reason === "其他" && !trimmed) {
      setErr("选择其他时请填写具体原因");
      return;
    }

    setSubmitting(true);
    setErr(null);
    try {
      await onSubmit({ reason, note: trimmed });
      setOpen(false);
      resetDraft();
      onSubmitted?.();
      toast.success(successMessage);
    } catch (error: any) {
      setErr(error?.message || String(error));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>反馈</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>

        <div className="space-y-2">
          <div className="text-ui-sm font-medium text-foreground">问题分类</div>
          <div
            className="inline-flex h-control-sm items-center rounded-ui-md border border-primary/30 bg-primary/10 px-3 text-ui-xs font-medium text-primary"
            aria-label={`问题分类：${category}`}
          >
            {category}
          </div>
        </div>

        <div className="space-y-2">
          <div className="text-ui-sm font-medium text-foreground">问题类型</div>
          <div className="flex flex-wrap gap-2" aria-label="问题类型">
            {reasons.map((item) => {
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
        </div>

        <div className="space-y-2">
          <label htmlFor={noteId} className="text-ui-sm font-medium text-foreground">
            补充说明{reason === "其他" ? "（必填）" : "（选填）"}
          </label>
          <textarea
            id={noteId}
            value={note}
            onChange={(event) => {
              setNote(event.target.value);
              setErr(null);
            }}
            placeholder={notePlaceholder}
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
  );
}
