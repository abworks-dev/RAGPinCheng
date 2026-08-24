import { useEffect, useMemo, useRef, useState } from "react";
import { adminContentApi } from "../api/admin/content";
import type { XMindPreview as XMindPreviewData, XMindTopic } from "../types";
import { Button } from "./ui/button";
import { ErrorState } from "./ui/error-state";
import { LoadingState } from "./ui/loading-state";

type MindMapNode = {
  data: Record<string, unknown>;
  children: MindMapNode[];
};

type SimpleMindMapInstance = {
  resize(): void;
  destroy(): void;
  view: { fit(): void; setScale(scale: number): void };
};

function toMindMapNode(topic: XMindTopic, depth = 0): MindMapNode {
  return {
    data: {
      uid: topic.id,
      text: topic.title,
      note: topic.notes || undefined,
      expand: depth < 4,
      ...(depth === 0 ? {
        color: "#ffffff",
        fillColor: "#16a085",
        borderColor: "#0f766e",
        borderWidth: 2,
        borderRadius: 8,
        fontSize: 24,
      } : {}),
    },
    children: topic.children.map((child) => toMindMapNode(child, depth + 1)),
  };
}

function MindMapCanvas({ rootTopic, zoom }: { rootTopic: XMindTopic; zoom: number }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const instanceRef = useRef<SimpleMindMapInstance | null>(null);
  const [ready, setReady] = useState(false);
  const [renderError, setRenderError] = useState<string | null>(null);

  useEffect(() => {
    let disposed = false;
    let resizeObserver: ResizeObserver | null = null;
    setReady(false);
    setRenderError(null);

    void import("simple-mind-map")
      .then(({ default: SimpleMindMap }) => {
        if (disposed || !containerRef.current) return;
        const instance = new SimpleMindMap({
          el: containerRef.current,
          data: toMindMapNode(rootTopic),
          layout: "mindMap",
          readonly: true,
          fit: true,
          fitPadding: 48,
          mousewheelAction: "move",
          theme: "default",
          themeConfig: {
            backgroundColor: "#fbfaf7",
            lineColor: "#8b95a5",
            lineWidth: 1,
            secondLineColor: "#8b95a5",
            nodeUseLineStyle: false,
          },
        }) as unknown as SimpleMindMapInstance;
        instanceRef.current = instance;
        if (typeof ResizeObserver !== "undefined") {
          resizeObserver = new ResizeObserver(() => instance.resize());
          resizeObserver.observe(containerRef.current);
        }
        window.requestAnimationFrame(() => {
          if (disposed) return;
          instance.view.fit();
          instance.view.setScale(zoom);
          setReady(true);
        });
      })
      .catch(() => {
        if (!disposed) setRenderError("思维导图渲染器加载失败");
      });

    return () => {
      disposed = true;
      resizeObserver?.disconnect();
      instanceRef.current?.destroy();
      instanceRef.current = null;
    };
  }, [rootTopic]);

  useEffect(() => { instanceRef.current?.view.setScale(zoom); }, [zoom]);

  if (renderError) {
    return <div className="flex h-full items-center justify-center p-6"><ErrorState title="无法渲染思维导图" description={renderError} /></div>;
  }

  return (
    <div className="relative h-full min-h-[320px] overflow-hidden bg-[#fbfaf7]" data-testid="xmind-map-canvas">
      {!ready && <div className="absolute inset-0 z-10 bg-background"><LoadingState label="正在绘制思维导图…" /></div>}
      <div ref={containerRef} className="h-full w-full touch-none" />
    </div>
  );
}

export function XMindPreview({ versionId, zoom = 1 }: { versionId: string; zoom?: number }) {
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
            <Button key={sheet.id} size="sm" variant={selectedSheet.id === sheet.id ? "secondary" : "ghost"} role="tab" aria-selected={selectedSheet.id === sheet.id} onClick={() => setSelectedSheetId(sheet.id)}>
              {sheet.title}
            </Button>
          ))}
        </div>
      )}
      <div className="min-h-0 flex-1" role="tabpanel" aria-label={selectedSheet.title}>
        <MindMapCanvas key={selectedSheet.id} rootTopic={selectedSheet.root_topic} zoom={zoom} />
      </div>
    </div>
  );
}
