import { useEffect, useRef, useState } from "react";
import { Send } from "lucide-react";
import { KnowledgeScopePicker } from "./KnowledgeScopePicker";

export function Composer({
  onSend,
  disabled,
  categories,
  selected,
  onToggleCategory,
  onClearCategories,
  centered = false,
}: {
  onSend: (text: string) => void;
  disabled: boolean;
  categories: string[];
  selected: string[];
  onToggleCategory: (category: string) => void;
  onClearCategories: () => void;
  centered?: boolean;
}) {
  const [text, setText] = useState("");
  const ref = useRef<HTMLTextAreaElement | null>(null);

  // Auto-grow.
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 240) + "px";
  }, [text]);

  function submit() {
    const t = text.trim();
    if (!t || disabled) return;
    onSend(t);
    setText("");
  }

  return (
    <div className={`shrink-0 bg-background px-3 py-3 sm:px-5 ${centered ? "border-t-0" : "border-t border-border"}`}>
      <div className="mx-auto max-w-[50rem] rounded-ui-lg border border-input bg-card p-2 shadow-surface focus-within:ring-2 focus-within:ring-ring/30">
        <textarea
          ref={ref}
          rows={1}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              submit();
            }
          }}
          placeholder="向企业知识库提问"
          className="block max-h-60 min-h-11 w-full resize-none bg-transparent px-2 py-2 text-sm leading-relaxed text-foreground placeholder:text-muted-foreground focus:outline-none"
        />
        <div className="flex min-h-9 items-center justify-between gap-2 border-t border-border pt-2">
          <KnowledgeScopePicker categories={categories} selected={selected} onToggle={onToggleCategory} onClear={onClearCategories} compact />
          <button type="button" aria-label="发送问题" title="发送" onClick={submit} disabled={disabled || !text.trim()} className="inline-flex size-9 shrink-0 items-center justify-center rounded-ui-md bg-primary text-primary-foreground hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40">
            <Send className="size-4" />
          </button>
        </div>
      </div>
      <div className="mt-2 text-center text-[11px] text-muted-foreground">
        资料来源仅供参考。生成内容可能存在差错，请以正式规范文本为准。
      </div>
    </div>
  );
}
