import { useState } from "react";
import type { Conversation } from "../types";
import { Trash2 } from "lucide-react";
import { Button } from "./ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "./ui/dialog";

export function ConversationList({
  conversations,
  currentId,
  onSelect,
  onDelete,
  loading,
}: {
  conversations: Conversation[];
  currentId: string | null;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
  loading: boolean;
}) {
  const [deleteTarget, setDeleteTarget] = useState<Conversation | null>(null);

  if (loading && conversations.length === 0) {
    return <div role="status" className="space-y-3 px-3 py-4 text-xs text-muted-foreground"><span>加载对话列表…</span><div aria-hidden="true" className="h-9 rounded-ui-md bg-secondary motion-safe:animate-pulse" /><div aria-hidden="true" className="h-9 rounded-ui-md bg-secondary/60 motion-safe:animate-pulse" /></div>;
  }
  if (conversations.length === 0) {
    return (
      <div className="mx-1 rounded-ui-md border border-dashed border-sidebar-border px-3 py-5 text-xs leading-6 text-muted-foreground">
        <p className="font-medium text-sidebar-foreground">对话会保留在这里</p>
        <p className="mt-1">新建对话，开始查阅企业知识。</p>
      </div>
    );
  }

  const now = Date.now() / 1000;
  const todayStart = new Date();
  todayStart.setHours(0, 0, 0, 0);
  const todayStartSeconds = todayStart.getTime() / 1000;
  const groups = [
    { label: "今天", items: conversations.filter((conversation) => conversation.updated_at >= todayStartSeconds) },
    { label: "7 天内", items: conversations.filter((conversation) => conversation.updated_at < todayStartSeconds && now - conversation.updated_at < 7 * 86400) },
    { label: "30 天内", items: conversations.filter((conversation) => now - conversation.updated_at >= 7 * 86400 && now - conversation.updated_at < 30 * 86400) },
    { label: "更早", items: conversations.filter((conversation) => now - conversation.updated_at >= 30 * 86400) },
  ].filter((group) => group.items.length > 0);

  return (
    <div className="space-y-5">
      {groups.map((group) => (
        <section key={group.label}>
          <h2 className="mb-1.5 px-2 text-[11px] font-medium text-muted-foreground">{group.label}</h2>
          <ul className="space-y-0.5">
      {group.items.map((c) => {
        const active = c.id === currentId;
        return (
          <li key={c.id}>
            <div className={`group flex items-center gap-1 rounded-ui-md border px-1 transition-colors ${active ? "border-primary/15 bg-primary/10 text-sidebar-foreground" : "border-transparent text-sidebar-foreground/80 hover:bg-secondary"}`}>
              <button
                type="button"
                onClick={() => onSelect(c.id)}
                aria-current={active ? "page" : undefined}
                title={c.title}
                className="min-w-0 flex-1 truncate rounded-ui-sm px-2 py-3 text-left text-[13px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >{c.title}</button>
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  setDeleteTarget(c);
                }}
                className="inline-flex size-8 shrink-0 items-center justify-center rounded-ui-sm text-muted-foreground transition-opacity hover:bg-destructive/10 hover:text-destructive focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring lg:opacity-0 lg:group-hover:opacity-100 lg:group-focus-within:opacity-100"
                aria-label={`删除对话：${c.title}`}
                title="删除对话"
              >
                <Trash2 className="size-3.5" />
              </button>
            </div>
          </li>
        );
      })}
          </ul>
        </section>
      ))}
      <Dialog open={deleteTarget !== null} onOpenChange={(open) => { if (!open) setDeleteTarget(null); }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>删除对话</DialogTitle>
            <DialogDescription>
              删除对话“{deleteTarget?.title}”？此操作不可恢复。
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteTarget(null)}>取消</Button>
            <Button
              variant="destructive"
              onClick={() => {
                if (!deleteTarget) return;
                onDelete(deleteTarget.id);
                setDeleteTarget(null);
              }}
            >
              <Trash2 className="size-4" />
              确认删除
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
