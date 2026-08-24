import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent as ReactPointerEvent,
  type WheelEvent as ReactWheelEvent,
} from "react";
import { Document, Page, pdfjs } from "react-pdf";
import "react-pdf/dist/esm/Page/AnnotationLayer.css";
import "react-pdf/dist/esm/Page/TextLayer.css";
import { ArrowLeft, Hand, Minus, MousePointer2, Plus } from "lucide-react";
import { usePdfPreview } from "../hooks/usePdfPreview";
import { DocxPreview } from "./DocxPreview";
import { ResourcePreviewShell } from "./ResourcePreviewShell";
import { SpreadsheetPreview } from "./SpreadsheetPreview";
import { XMindPreview } from "./XMindPreview";
import { IconButton } from "./ui/icon-button";
import { PreviewZoomControls } from "./PreviewZoomControls";

// PDF.js worker — use the CDN build so we don't need to bundle it.
pdfjs.GlobalWorkerOptions.workerSrc = `//unpkg.com/pdfjs-dist@${pdfjs.version}/build/pdf.worker.min.mjs`;

type ZoomMode = "fit-page" | "fit-width" | "actual" | "custom";
type InteractionMode = "pan" | "select";
type Size = { width: number; height: number };
type PrefetchablePdfPage = { getOperatorList: () => Promise<unknown> };
type PrefetchablePdfDocument = {
  numPages: number;
  getPage: (pageNumber: number) => Promise<PrefetchablePdfPage>;
};

const MIN_SCALE = 0.5;
const MAX_SCALE = 3;
const MIN_FIT_SCALE = 0.1;
const ZOOM_STEP = 0.1;
const VIEWPORT_PADDING = 32;
const PREFETCH_RADIUS = 2;
const MOUSE_WHEEL_DELTA_THRESHOLD = 40;
const PDF_DOCUMENT_OPTIONS = {
  disableAutoFetch: true,
  disableStream: true,
  rangeChunkSize: 256 * 1024,
};

export function getPdfPrefetchOrder(currentPage: number, numPages: number): number[] {
  const pages: number[] = [];
  for (let distance = 1; distance <= PREFETCH_RADIUS; distance += 1) {
    const nextPage = currentPage + distance;
    if (nextPage <= numPages) pages.push(nextPage);
  }
  for (let distance = 1; distance <= PREFETCH_RADIUS; distance += 1) {
    const previousPage = currentPage - distance;
    if (previousPage >= 1) pages.push(previousPage);
  }
  return pages;
}

export function calculatePdfScale(
  mode: Exclude<ZoomMode, "custom">,
  viewport: Size,
  page: Size,
): number {
  if (mode === "actual") return 1;
  if (!viewport.width || !viewport.height || !page.width || !page.height) return 1;

  const widthScale = Math.max(0, viewport.width - VIEWPORT_PADDING) / page.width;
  const heightScale = Math.max(0, viewport.height - VIEWPORT_PADDING) / page.height;
  const nextScale = mode === "fit-width" ? widthScale : Math.min(widthScale, heightScale);
  return Math.min(MAX_SCALE, Math.max(MIN_FIT_SCALE, nextScale));
}

export function shouldZoomPdfWheel(
  panEnabled: boolean,
  event: Pick<WheelEvent, "ctrlKey" | "metaKey" | "deltaMode" | "deltaX" | "deltaY">,
): boolean {
  if (event.ctrlKey || event.metaKey) return true;
  if (!panEnabled) return false;
  if (event.deltaMode !== WheelEvent.DOM_DELTA_PIXEL) return true;
  return Math.abs(event.deltaY) >= MOUSE_WHEEL_DELTA_THRESHOLD
    && Math.abs(event.deltaY) > Math.abs(event.deltaX);
}

export function calculateWheelZoom(currentScale: number, deltaY: number): number {
  if (!deltaY) return currentScale;
  if (deltaY > 0 && currentScale <= MIN_SCALE) return currentScale;
  const magnitude = Math.min(0.15, Math.max(0.02, Math.abs(deltaY) * 0.001));
  const delta = deltaY < 0 ? magnitude : -magnitude;
  return Math.min(MAX_SCALE, Math.max(MIN_SCALE, Number((currentScale + delta).toFixed(2))));
}

export function calculateZoomedScroll(
  scrollPosition: number,
  pointerOffset: number,
  currentScale: number,
  nextScale: number,
): number {
  if (currentScale <= 0 || currentScale === nextScale) return scrollPosition;
  return Math.max(0, (scrollPosition + pointerOffset) * (nextScale / currentScale) - pointerOffset);
}

function isEditableTarget(target: EventTarget | null): boolean {
  return target instanceof HTMLElement && (
    target.isContentEditable
    || target.tagName === "INPUT"
    || target.tagName === "TEXTAREA"
    || target.tagName === "SELECT"
  );
}

export function PdfPreview() {
  const { state, close, setPage } = usePdfPreview();
  const viewportRef = useRef<HTMLDivElement | null>(null);
  const dragRef = useRef({ pointerId: -1, x: 0, y: 0, scrollLeft: 0, scrollTop: 0 });
  const pdfDocumentRef = useRef<PrefetchablePdfDocument | null>(null);
  const prefetchedPagesRef = useRef(new Set<number>());
  const prefetchGenerationRef = useRef(0);
  const prefetchQueueRef = useRef(Promise.resolve());
  const [numPages, setNumPages] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [scale, setScale] = useState(1);
  const [zoomMode, setZoomMode] = useState<ZoomMode>("fit-page");
  const [interactionMode, setInteractionMode] = useState<InteractionMode>("pan");
  const [temporaryPan, setTemporaryPan] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [viewportSize, setViewportSize] = useState<Size>({ width: 0, height: 0 });
  const [pageSize, setPageSize] = useState<Size>({ width: 0, height: 0 });
  const [resourceZoom, setResourceZoom] = useState(1);

  const open = state.parentId !== null || state.versionId !== null;
  const isDocx = state.docType === "docx" || state.docType === "doc";
  const isXlsx = state.docType === "xlsx" || state.docType === "xls";
  const isPptx = state.docType === "pptx" || state.docType === "ppt";
  const isXMind = state.docType === "xmind";
  // PPTX files are converted to PDF by the preview endpoint and use the same page controls.
  const isPdf = !isDocx && !isXlsx && !isXMind;
  const panEnabled = interactionMode === "pan" || temporaryPan;

  useEffect(() => {
    prefetchGenerationRef.current += 1;
    pdfDocumentRef.current = null;
    prefetchedPagesRef.current.clear();
    setNumPages(null);
    setLoading(true);
    setLoadError(null);
    setScale(1);
    setZoomMode("fit-page");
    setInteractionMode("pan");
    setTemporaryPan(false);
    setDragging(false);
    setPageSize({ width: 0, height: 0 });
    setResourceZoom(1);
  }, [state.parentId]);

  useEffect(() => {
    const pdfDocument = pdfDocumentRef.current;
    if (!open || !isPdf || !pdfDocument || !numPages) return;

    const generation = prefetchGenerationRef.current + 1;
    prefetchGenerationRef.current = generation;
    const pages = getPdfPrefetchOrder(state.pageNumber, numPages);

    for (const pageNumber of pages) {
      prefetchQueueRef.current = prefetchQueueRef.current.then(async () => {
        if (
          generation !== prefetchGenerationRef.current
          || pdfDocument !== pdfDocumentRef.current
          || prefetchedPagesRef.current.has(pageNumber)
        ) {
          return;
        }

        try {
          const page = await pdfDocument.getPage(pageNumber);
          await page.getOperatorList();
          prefetchedPagesRef.current.add(pageNumber);
        } catch {
          // Prefetch is best-effort; the visible Page keeps its own error handling.
        }
      });
    }
  }, [isPdf, numPages, open, state.pageNumber]);

  useEffect(() => {
    const viewport = viewportRef.current;
    if (!viewport || typeof ResizeObserver === "undefined") return;

    const updateSize = () => {
      setViewportSize({ width: viewport.clientWidth, height: viewport.clientHeight });
    };
    updateSize();
    const observer = new ResizeObserver(updateSize);
    observer.observe(viewport);
    return () => observer.disconnect();
  }, [open, isPdf]);

  useEffect(() => {
    if (!isPdf || zoomMode === "custom") return;
    const viewport = viewportRef.current;
    const currentViewportSize = viewport
      ? { width: viewport.clientWidth, height: viewport.clientHeight }
      : viewportSize;
    setScale(calculatePdfScale(zoomMode, currentViewportSize, pageSize));
  }, [isPdf, pageSize, viewportSize, zoomMode]);

  useEffect(() => {
    if (!isPdf || zoomMode === "custom") return;
    const frame = requestAnimationFrame(() => {
      const viewport = viewportRef.current;
      if (!viewport) return;
      viewport.scrollLeft = Math.max(0, (viewport.scrollWidth - viewport.clientWidth) / 2);
      viewport.scrollTop = zoomMode === "fit-page"
        ? Math.max(0, (viewport.scrollHeight - viewport.clientHeight) / 2)
        : 0;
    });
    return () => cancelAnimationFrame(frame);
  }, [isPdf, scale, state.pageNumber, zoomMode]);

  useEffect(() => {
    const onPreviewOpen = (event: Event) => {
      if ((event as CustomEvent<{ kind: string }>).detail?.kind === "video") close();
    };
    window.addEventListener("resource-preview-open", onPreviewOpen);
    return () => window.removeEventListener("resource-preview-open", onPreviewOpen);
  }, [close]);

  useEffect(() => {
    if (!open || !isPdf) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.code !== "Space" || event.repeat || isEditableTarget(event.target)) return;
      event.preventDefault();
      setTemporaryPan(true);
    };
    const onKeyUp = (event: KeyboardEvent) => {
      if (event.code === "Space") setTemporaryPan(false);
    };
    window.addEventListener("keydown", onKeyDown);
    window.addEventListener("keyup", onKeyUp);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("keyup", onKeyUp);
    };
  }, [isPdf, open]);

  const onDocumentLoadSuccess = useCallback((pdfDocument: PrefetchablePdfDocument) => {
    pdfDocumentRef.current = pdfDocument;
    prefetchedPagesRef.current.clear();
    prefetchGenerationRef.current += 1;
    setNumPages(pdfDocument.numPages);
    setLoading(false);
    setLoadError(null);
  }, []);

  const onDocumentLoadError = useCallback(() => {
    prefetchGenerationRef.current += 1;
    pdfDocumentRef.current = null;
    prefetchedPagesRef.current.clear();
    setLoading(false);
    setLoadError(isPptx
      ? "PPTX 预览暂不可用，请返回资料管理页面重新生成预览。"
      : "PDF 加载失败，请稍后重试。");
  }, [isPptx]);

  const onPageLoadSuccess = useCallback((page: { getViewport: (options: { scale: number }) => Size }) => {
    const viewport = page.getViewport({ scale: 1 });
    setPageSize({ width: viewport.width, height: viewport.height });
  }, []);

  function setPresetZoom(mode: Exclude<ZoomMode, "custom">) {
    const viewport = viewportRef.current;
    const currentViewportSize = viewport
      ? { width: viewport.clientWidth, height: viewport.clientHeight }
      : viewportSize;
    setZoomMode(mode);
    setScale(calculatePdfScale(mode, currentViewportSize, pageSize));
  }

  function changeZoom(delta: number) {
    setZoomMode("custom");
    setScale((current) => Math.min(MAX_SCALE, Math.max(MIN_SCALE, Number((current + delta).toFixed(2)))));
  }

  function handleWheel(event: ReactWheelEvent<HTMLDivElement>) {
    const viewport = viewportRef.current;
    if (!viewport || !shouldZoomPdfWheel(panEnabled, event.nativeEvent)) return;

    const nextScale = calculateWheelZoom(scale, event.deltaY);
    if (nextScale === scale) return;

    event.preventDefault();
    const bounds = viewport.getBoundingClientRect();
    const pointerX = event.clientX - bounds.left;
    const pointerY = event.clientY - bounds.top;
    const nextScrollLeft = calculateZoomedScroll(viewport.scrollLeft, pointerX, scale, nextScale);
    const nextScrollTop = calculateZoomedScroll(viewport.scrollTop, pointerY, scale, nextScale);

    setZoomMode("custom");
    setScale(nextScale);
    requestAnimationFrame(() => {
      viewport.scrollLeft = nextScrollLeft;
      viewport.scrollTop = nextScrollTop;
    });
  }

  function handlePointerDown(event: ReactPointerEvent<HTMLDivElement>) {
    const viewport = viewportRef.current;
    if (!viewport || !panEnabled || event.button !== 0) return;
    dragRef.current = {
      pointerId: event.pointerId,
      x: event.clientX,
      y: event.clientY,
      scrollLeft: viewport.scrollLeft,
      scrollTop: viewport.scrollTop,
    };
    viewport.setPointerCapture(event.pointerId);
    setDragging(true);
    event.preventDefault();
  }

  function handlePointerMove(event: ReactPointerEvent<HTMLDivElement>) {
    const viewport = viewportRef.current;
    if (!viewport || !dragging || dragRef.current.pointerId !== event.pointerId) return;
    viewport.scrollLeft = dragRef.current.scrollLeft - (event.clientX - dragRef.current.x);
    viewport.scrollTop = dragRef.current.scrollTop - (event.clientY - dragRef.current.y);
  }

  function stopDragging(event: ReactPointerEvent<HTMLDivElement>) {
    const viewport = viewportRef.current;
    if (!viewport || dragRef.current.pointerId !== event.pointerId) return;
    if (viewport.hasPointerCapture(event.pointerId)) viewport.releasePointerCapture(event.pointerId);
    dragRef.current.pointerId = -1;
    setDragging(false);
  }

  function handleViewportKeyDown(event: ReactKeyboardEvent<HTMLDivElement>) {
    if (event.key === "ArrowLeft" && state.pageNumber > 1) setPage(state.pageNumber - 1);
    if (event.key === "ArrowRight" && numPages && state.pageNumber < numPages) setPage(state.pageNumber + 1);
  }

  const typeLabel = isDocx
    ? "Word 文档"
    : isXlsx
      ? "Excel 表格"
      : isPptx
        ? "演示文稿"
        : isXMind
          ? "XMind 思维导图"
        : numPages
          ? `${state.pageNumber} / ${numPages} 页`
          : "PDF 文档";

  const toolbar = (
    <div className="flex items-center gap-1">
      <button
        type="button"
        aria-label={interactionMode === "pan" ? "切换到文字选择" : "切换到手形拖动"}
        aria-pressed={interactionMode === "pan"}
        onClick={() => setInteractionMode((current) => current === "pan" ? "select" : "pan")}
        className="inline-flex size-8 items-center justify-center rounded-ui-md hover:bg-secondary"
      >
        {interactionMode === "pan" ? <Hand className="size-4" /> : <MousePointer2 className="size-4" />}
      </button>
      {isPdf && <button
        type="button"
        aria-label="缩小"
        onClick={() => changeZoom(-ZOOM_STEP)}
        disabled={scale <= MIN_SCALE}
        className="inline-flex size-8 items-center justify-center rounded-ui-md hover:bg-secondary disabled:opacity-30"
      >
        <Minus className="size-4" />
      </button>}
      {!isPdf && <PreviewZoomControls zoom={resourceZoom} onChange={setResourceZoom} />}
      {isPdf && <select
        aria-label="缩放模式"
        value={zoomMode}
        onChange={(event) => {
          const mode = event.target.value as ZoomMode;
          if (mode !== "custom") setPresetZoom(mode);
        }}
        className="h-8 max-w-24 rounded-ui-md border border-input bg-background px-1 text-xs text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
      >
        <option value="fit-page">适合页面</option>
        <option value="fit-width">适合宽度</option>
        <option value="actual">实际大小</option>
        {zoomMode === "custom" && <option value="custom">自定义</option>}
      </select>}
      {isPdf && <span className="w-10 text-center text-xs tabular-nums text-muted-foreground">
        {Math.round(scale * 100)}%
      </span>}
      {isPdf && <button
        type="button"
        aria-label="放大"
        onClick={() => changeZoom(ZOOM_STEP)}
        disabled={scale >= MAX_SCALE}
        className="inline-flex size-8 items-center justify-center rounded-ui-md hover:bg-secondary disabled:opacity-30"
      >
        <Plus className="size-4" />
      </button>}
    </div>
  );

  const returnLabel = state.returnTo === "managed-content-detail"
    ? "返回资料详情"
    : state.returnTo === "managed-content-review"
      ? "返回资料审核"
      : null;
  const backAction = returnLabel ? (
    <IconButton label={returnLabel} onClick={close}>
      <ArrowLeft className="size-4" />
    </IconButton>
  ) : null;

  return (
    <ResourcePreviewShell open={open} title={state.title} subtitle={typeLabel} onClose={close} backAction={backAction} toolbar={toolbar}>
      <div className="flex h-full min-h-0 flex-col">
        {isPdf && numPages && numPages > 1 && (
          <div className="flex shrink-0 items-center justify-center gap-2 border-b border-border px-4 py-2">
            <button
              type="button"
              onClick={() => state.pageNumber > 1 && setPage(state.pageNumber - 1)}
              disabled={state.pageNumber <= 1}
              className="rounded-ui-md border border-border px-3 py-1 text-xs hover:bg-secondary disabled:opacity-30"
            >
              ← 上一页
            </button>
            <input
              aria-label="页码"
              type="number"
              min={1}
              max={numPages}
              value={state.pageNumber}
              onChange={(event) => {
                const page = Number.parseInt(event.target.value, 10);
                if (page >= 1 && page <= numPages) setPage(page);
              }}
              className="w-16 rounded-ui-md border border-input bg-background px-2 py-1 text-center text-xs"
            />
            <span className="text-xs text-muted-foreground">/ {numPages}</span>
            <button
              type="button"
              onClick={() => state.pageNumber < numPages && setPage(state.pageNumber + 1)}
              disabled={state.pageNumber >= numPages}
              className="rounded-ui-md border border-border px-3 py-1 text-xs hover:bg-secondary disabled:opacity-30"
            >
              下一页 →
            </button>
          </div>
        )}

        <div
          ref={viewportRef}
          role={isPdf ? "region" : undefined}
          aria-label={isPdf ? "PDF 页面" : undefined}
          tabIndex={isPdf ? 0 : undefined}
          onKeyDown={handleViewportKeyDown}
          onWheel={handleWheel}
          onPointerDown={handlePointerDown}
          onPointerMove={handlePointerMove}
          onPointerUp={stopDragging}
          onPointerCancel={stopDragging}
          className={`min-h-0 flex-1 overflow-auto bg-secondary outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring ${
            panEnabled ? (dragging ? "cursor-grabbing select-none" : "cursor-grab") : "cursor-text"
          }`}
        >
          {isXMind && state.versionId ? (
            <XMindPreview versionId={state.versionId} zoom={resourceZoom} />
          ) : isDocx ? (
            <DocxPreview
              parentId={state.parentId!}
              paragraphAnchor={state.location.paragraphAnchor}
              zoom={resourceZoom}
              onLoad={() => setLoading(false)}
              onError={() => setLoading(false)}
            />
          ) : isXlsx ? (
            <SpreadsheetPreview
              parentId={state.parentId!}
              sheetName={state.location.sheetName}
              cellRange={state.location.cellRange}
              zoom={resourceZoom}
              onLoad={() => setLoading(false)}
              onError={() => setLoading(false)}
            />
          ) : (
            <>
              {loading && (
                <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
                  加载 PDF…
                </div>
              )}
              {state.parentId && (
                <Document
                  key={state.parentId}
                  file={`/api/pdf/${state.parentId}`}
                  options={PDF_DOCUMENT_OPTIONS}
                  onLoadSuccess={onDocumentLoadSuccess}
                  onLoadError={onDocumentLoadError}
                  loading={<div className="flex h-full items-center justify-center text-sm text-muted-foreground">加载 PDF…</div>}
                  error={<div className="flex h-full items-center justify-center px-6 text-center text-sm text-destructive" role="alert">{loadError || "文件预览加载失败"}</div>}
                >
                  <div className="flex min-h-full min-w-max items-center justify-center p-4">
                    <Page
                      pageNumber={state.pageNumber}
                      scale={scale}
                      onLoadSuccess={onPageLoadSuccess}
                      renderTextLayer={true}
                      renderAnnotationLayer={true}
                      className="shadow-md"
                    />
                  </div>
                </Document>
              )}
            </>
          )}
        </div>
      </div>
    </ResourcePreviewShell>
  );
}
