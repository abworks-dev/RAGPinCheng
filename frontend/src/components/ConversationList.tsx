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
    return <div className="px-2 py-3 text-xs text-muted">加载对话列表…</div>;
  }
  if (conversations.length === 0) {
    return (
      <div className="px-2 py-3 text-xs text-muted">
        还没有对话。点击上方“+ 新建对话”开始。
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
            <div
              className={
                "group flex items-center gap-1 rounded-lg px-2 py-2 text-sm cursor-pointer " +
                (active
                  ? "bg-accent/10 text-ink"
                  : "hover:bg-gray-100 dark:hover:bg-gray-800 text-ink opacity-90")
              }
              onClick={() => onSelect(c.id)}
              title={c.title}
            >
              <div className="min-w-0 flex-1 truncate">{c.title}</div>
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  setDeleteTarget(c);
                }}
                className="inline-flex size-7 items-center justify-center rounded-ui-sm text-muted opacity-0 hover:bg-destructive/10 hover:text-destructive group-hover:opacity-100"
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
