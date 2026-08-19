import { useEffect, useMemo, useState } from "react";
import { ChevronDown, ChevronRight, Network } from "lucide-react";
import { adminContentApi } from "../api/admin/content";
import type { XMindPreview as XMindPreviewData, XMindTopic } from "../types";
import { Button } from "./ui/button";
import { ErrorState } from "./ui/error-state";
import { LoadingState } from "./ui/loading-state";

function TopicBranch({ topic, depth = 0 }: { topic: XMindTopic; depth?: number }) {
  const [expanded, setExpanded] = useState(depth < 2);
  const hasChildren = topic.children.length > 0;

  return (
    <li className="min-w-0">
      <div className="flex min-h-10 items-start gap-1.5 rounded-ui-md px-2 py-1.5 hover:bg-surface-muted">
        {hasChildren ? (
          <button
            type="button"
            className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-ui-md text-muted-foreground hover:bg-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            aria-label={expanded ? `收起${topic.title}` : `展开${topic.title}`}
            aria-expanded={expanded}
            onClick={() => setExpanded((value) => !value)}
          >
            {expanded ? <ChevronDown className="size-4" /> : <ChevronRight className="size-4" />}
          </button>
        ) : <span className="size-7 shrink-0" aria-hidden="true" />}
        <div className="min-w-0 flex-1 pt-1">
          <p className="break-words text-ui-sm font-medium leading-5">{topic.title}</p>
          {topic.notes && <p className="mt-1 whitespace-pre-wrap break-words text-ui-xs leading-5 text-muted-foreground">{topic.notes}</p>}
        </div>
      </div>
      {hasChildren && expanded && (
        <ul className="ml-5 border-l border-border pl-2">
          {topic.children.map((child) => <TopicBranch key={child.id} topic={child} depth={depth + 1} />)}
        </ul>
      )}
    </li>
  );
}

export function XMindPreview({ versionId }: { versionId: string }) {
  const [data, setData] = useState<XMindPreviewData | null>(null);
  const [selectedSheetId, setSelectedSheetId] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [retryKey, setRetryKey] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setData(null);
    setError(null);
    adminContentApi.xmindPreview(versionId)
      .then((result) => {
        if (cancelled) return;
        setData(result);
        setSelectedSheetId(result.sheets[0]?.id || "");
      })
      .catch((reason: unknown) => {
        if (!cancelled) setError(reason instanceof Error ? reason.message : "XMind 预览加载失败");
      });
    return () => { cancelled = true; };
  }, [retryKey, versionId]);

  const selectedSheet = useMemo(
    () => data?.sheets.find((sheet) => sheet.id === selectedSheetId) || data?.sheets[0] || null,
    [data, selectedSheetId],
  );

  if (error) return <div className="flex h-full items-center justify-center p-6"><ErrorState title="无法预览 XMind" description={error} action={<Button variant="outline" onClick={() => setRetryKey((value) => value + 1)}>重新加载</Button>} /></div>;
  if (!data) return <LoadingState label="正在读取 XMind…" />;
  if (!selectedSheet) return <div className="flex h-full items-center justify-center px-6 text-ui-sm text-muted-foreground">XMind 中没有可预览的画布</div>;

  return (
    <div className="flex h-full min-h-0 flex-col bg-background">
      {data.sheets.length > 1 && (
        <div className="flex shrink-0 gap-2 overflow-x-auto border-b border-border px-4 py-2" role="tablist" aria-label="XMind 画布">
          {data.sheets.map((sheet) => (
            <Button
              key={sheet.id}
              size="sm"
              variant={selectedSheet.id === sheet.id ? "secondary" : "ghost"}
              role="tab"
              aria-selected={selectedSheet.id === sheet.id}
              onClick={() => setSelectedSheetId(sheet.id)}
            >
              {sheet.title}
            </Button>
          ))}
        </div>
      )}
      <div className="min-h-0 flex-1 overflow-auto px-3 py-4 sm:px-5" role="tabpanel" aria-label={selectedSheet.title}>
        <div className="mx-auto max-w-4xl">
          <div className="mb-3 flex items-center gap-2 text-ui-sm text-muted-foreground">
            <Network className="size-4" aria-hidden="true" />
            <span className="break-words">{selectedSheet.title}</span>
          </div>
          <ul><TopicBranch key={selectedSheet.id} topic={selectedSheet.root_topic} /></ul>
        </div>
      </div>
    </div>
  );
}
