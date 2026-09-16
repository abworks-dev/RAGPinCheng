import { useCallback, useEffect, useMemo, useState } from "react";
import { RotateCcw, Save } from "lucide-react";
import { adminMaintenanceApi } from "../../api/admin/maintenance";
import { Alert, AlertDescription, AlertTitle } from "../ui/alert";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../ui/card";
import { ErrorState } from "../ui/error-state";
import { Input } from "../ui/input";
import { LoadingState } from "../ui/loading-state";
import type { SystemPromptItem } from "../../types";

export function PromptManagementPanel() {
  const [items, setItems] = useState<SystemPromptItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState<"load" | "save" | null>("load");
  const [editingKey, setEditingKey] = useState<string | null>(null);
  const [draft, setDraft] = useState("");

  const load = useCallback(async () => {
    setBusy("load");
    setError(null);
    try {
      setItems(await adminMaintenanceApi.listPrompts());
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setBusy(null);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const promptCount = items?.filter((item) => item.custom_body && item.custom_body.trim()).length ?? 0;

  async function savePrompt(key: string) {
    setBusy("save");
    setNotice(null);
    try {
      await adminMaintenanceApi.updatePrompt(key, draft);
      setEditingKey(null);
      setNotice("提示词已保存，新回答/新生成的请求将立即使用自定义内容。");
      await load();
    } catch (e: any) {
      setNotice(e?.message || String(e));
    } finally {
      setBusy(null);
    }
  }

  async function restorePrompt(key: string) {
    setBusy("save");
    setNotice(null);
    try {
      await adminMaintenanceApi.restorePrompt(key);
      if (editingKey === key) setEditingKey(null);
      setNotice("已恢复为系统内置默认提示词。");
      await load();
    } catch (e: any) {
      setNotice(e?.message || String(e));
    } finally {
      setBusy(null);
    }
  }

  const openEditor = (item: SystemPromptItem) => {
    setEditingKey(item.key);
    setDraft(item.custom_body && item.custom_body.trim() ? item.custom_body : item.default_body);
  };

  const groups = useMemo(() => {
    if (!items) return [];
    const label = (key: string) => key.includes("answer") ? "回答与改写" : key.startsWith("table") ? "表格摘要" : key.startsWith("asr") ? "ASR 术语资产" : "其他";
    const map = new Map<string, SystemPromptItem[]>();
    for (const item of items) {
      const group = label(item.key);
      if (!map.has(group)) map.set(group, []);
      map.get(group)!.push(item);
    }
    return [...map.entries()];
  }, [items]);

  if (busy === "load" && !items) return <LoadingState className="min-h-40" label="正在加载提示词…" />;
  if (error || !items) return <ErrorState title="提示词加载失败" description={error || "暂无可用提示词"} action={<Button variant="outline" onClick={() => void load()}>重试</Button>} />;

  return (
    <Card>
      <CardHeader>
        <CardTitle>提示词与 AI 模型</CardTitle>
        <CardDescription>查看并微调系统内置提示词。保存的自定义内容会立即作用于后续回答、改写、表格摘要与 AI 生成请求；恢复默认即回到打包内置版本。</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {notice && <Alert variant={notice.includes("失败") ? "destructive" : "success"} aria-live="polite"><AlertTitle>操作结果</AlertTitle><AlertDescription>{notice}</AlertDescription></Alert>}
        <div className="flex items-center gap-2">
          <Badge variant="secondary">共 {items.length} 条</Badge>
          {promptCount > 0 && <Badge variant="warning">已自定义 {promptCount} 条</Badge>}
        </div>

        {groups.map(([group, prompts]) => (
          <div key={group} className="space-y-2">
            <h3 className="text-ui-sm font-semibold text-muted-foreground">{group}</h3>
            <ul className="divide-y divide-border overflow-hidden rounded-ui-xl border border-border">
              {prompts.map((item) => {
                const editing = editingKey === item.key;
                const customized = Boolean(item.custom_body && item.custom_body.trim());
                return (
                  <li key={item.key} className="p-4">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div className="min-w-0">
                        <p className="flex flex-wrap items-center gap-2 text-ui-sm font-medium">
                          <code className="rounded-ui-sm bg-surface-muted px-1.5 py-0.5 text-ui-xs">{item.key}</code>
                          {item.title}
                          {customized && <Badge variant="warning">已自定义</Badge>}
                        </p>
                        <p className="mt-1 text-ui-xs text-muted-foreground">{item.description}</p>
                      </div>
                      <div className="flex shrink-0 items-center gap-1.5">
                        <Button size="sm" variant="outline" disabled={busy !== null} onClick={() => openEditor(item)}>{editing ? "编辑中…" : "编辑"}</Button>
                        <Button size="sm" variant="ghost" disabled={busy !== null || !customized} onClick={() => void restorePrompt(item.key)}><RotateCcw className="size-3.5" />恢复默认</Button>
                      </div>
                    </div>
                    {editing && (
                      <div className="mt-3 space-y-2">
                        <label className="block text-ui-xs font-medium text-muted-foreground">自定义内容（留空并保存将使用默认内容）
                          <textarea
                            aria-label={`${item.key} 自定义内容`}
                            className="mt-1 min-h-40 w-full rounded-ui-md border border-input bg-background px-3 py-2 font-mono text-ui-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-60"
                            value={draft}
                            disabled={busy === "save"}
                            onChange={(event) => setDraft(event.target.value)}
                          />
                        </label>
                        <div className="flex flex-wrap items-center justify-end gap-2">
                          <Button size="sm" variant="ghost" disabled={busy === "save"} onClick={() => setEditingKey(null)}>取消</Button>
                          <Button size="sm" disabled={busy === "save" || draft.trim() === ""} onClick={() => void savePrompt(item.key)}><Save className="size-3.5" />{busy === "save" ? "保存中…" : "保存"}</Button>
                        </div>
                      </div>
                    )}
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </CardContent>
    </Card>
  );
}