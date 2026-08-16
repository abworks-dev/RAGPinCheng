import { Eye, Pencil } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Button } from "./ui/button";

export function TranscriptMarkdownEditor({
  value,
  onChange,
  mode,
  onModeChange,
  disabled,
  onSave,
  dirty,
}: {
  value: string;
  onChange: (value: string) => void;
  mode: "edit" | "preview";
  onModeChange: (mode: "edit" | "preview") => void;
  disabled: boolean;
  onSave: () => void;
  dirty: boolean;
}) {
  return (
    <div className="mt-3 min-w-0">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div className="inline-flex h-9 rounded-ui-md border border-border bg-surface-muted p-0.5 md:hidden" role="group" aria-label="校对视图">
          <button
            type="button"
            aria-pressed={mode === "edit"}
            onClick={() => onModeChange("edit")}
            className={`inline-flex items-center gap-1.5 rounded-ui-sm px-3 text-ui-sm font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${mode === "edit" ? "bg-background text-foreground shadow-sm" : "text-muted-foreground"}`}
          >
            <Pencil className="size-4" aria-hidden="true" />编辑
          </button>
          <button
            type="button"
            aria-pressed={mode === "preview"}
            onClick={() => onModeChange("preview")}
            className={`inline-flex items-center gap-1.5 rounded-ui-sm px-3 text-ui-sm font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${mode === "preview" ? "bg-background text-foreground shadow-sm" : "text-muted-foreground"}`}
          >
            <Eye className="size-4" aria-hidden="true" />预览
          </button>
        </div>
        <p className="text-ui-xs text-muted-foreground" role="status">
          {dirty ? "有未保存修改" : "内容已保存"}
        </p>
      </div>

      <div className="grid min-w-0 gap-4 md:grid-cols-2">
        <section className={mode === "preview" ? "hidden md:block" : "min-w-0"} aria-label="Markdown 编辑">
          <label htmlFor="transcript-markdown-editor" className="mb-2 block text-ui-xs font-medium text-foreground">Markdown</label>
          <textarea
            id="transcript-markdown-editor"
            aria-label="转录 Markdown 编辑器"
            className="h-80 w-full resize-none rounded-ui-md border border-input bg-background p-3 font-mono text-ui-sm leading-relaxed focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-60 sm:h-96 md:h-[28rem]"
            value={value}
            onChange={(event) => onChange(event.target.value)}
            disabled={disabled}
            spellCheck={false}
          />
        </section>
        <section className={mode === "edit" ? "hidden min-w-0 md:block" : "min-w-0"} aria-label="Markdown 预览">
          <h5 className="mb-2 text-ui-xs font-medium text-foreground">渲染预览</h5>
          <div className="prose-tight h-80 overflow-auto rounded-ui-md border border-border bg-background p-4 text-ui-sm sm:h-96 md:h-[28rem]">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{value}</ReactMarkdown>
          </div>
        </section>
      </div>

      <div className="mt-3 flex justify-end">
        <Button disabled={disabled || !dirty} onClick={onSave}>
          {disabled ? "正在保存…" : "保存为新草稿"}
        </Button>
      </div>
    </div>
  );
}
