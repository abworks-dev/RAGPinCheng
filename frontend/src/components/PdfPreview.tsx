import { useCallback, useEffect, useState } from "react";
import { Document, Page, pdfjs } from "react-pdf";
import "react-pdf/dist/esm/Page/AnnotationLayer.css";
import "react-pdf/dist/esm/Page/TextLayer.css";
import { usePdfPreview } from "../hooks/usePdfPreview";
import { DocxPreview } from "./DocxPreview";
import { SpreadsheetPreview } from "./SpreadsheetPreview";
import { ArrowLeft, ArrowRight, Minus, Plus } from "lucide-react";
import { ResourcePreviewShell } from "./ResourcePreviewShell";

// PDF.js worker — use the CDN build so we don't need to bundle it.
pdfjs.GlobalWorkerOptions.workerSrc = `//unpkg.com/pdfjs-dist@${pdfjs.version}/build/pdf.worker.min.mjs`;

export function PdfPreview() {
  const { state, close, setPage } = usePdfPreview();
  const [numPages, setNumPages] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [scale, setScale] = useState(1.0);

  const open = state.parentId !== null;
  const isDocx = state.docType === "docx";
  const isXlsx = state.docType === "xlsx";
  const isPptx = state.docType === "pptx";

  // Reset loading state when switching documents.
  useEffect(() => {
    setNumPages(null);
    setLoading(true);
  }, [state.parentId]);

  useEffect(() => {
    const onPreviewOpen = (event: Event) => {
      if ((event as CustomEvent<{ kind: string }>).detail?.kind === "video") close();
    };
    window.addEventListener("resource-preview-open", onPreviewOpen);
    return () => window.removeEventListener("resource-preview-open", onPreviewOpen);
  }, [close]);

  const onDocumentLoadSuccess = useCallback(
    ({ numPages: n }: { numPages: number }) => {
      setNumPages(n);
      setLoading(false);
    },
    [],
  );

  const onDocumentLoadError = useCallback(() => {
    setLoading(false);
  }, []);

  function handlePrevPage() {
    if (state.pageNumber > 1) setPage(state.pageNumber - 1);
  }

  function handleNextPage() {
    if (numPages && state.pageNumber < numPages) setPage(state.pageNumber + 1);
  }

  function zoomIn() {
    setScale((s) => Math.min(s + 0.25, 3.0));
  }

  function zoomOut() {
    setScale((s) => Math.max(s - 0.25, 0.5));
  }

  const typeLabel = isDocx ? "Word 文档" : isXlsx ? "Excel 表格" : isPptx ? "演示文稿" : numPages ? `${state.pageNumber} / ${numPages} 页` : "PDF 文档";
  const toolbar = !isDocx && !isXlsx ? (
    <div className="flex items-center gap-1">
            {!isDocx && !isXlsx && (
              <>
                <button type="button" aria-label="缩小" onClick={zoomOut} className="inline-flex size-8 items-center justify-center rounded-ui-md hover:bg-secondary"><Minus className="size-4" /></button>
                <span className="w-10 text-center text-xs text-muted-foreground">
                  {Math.round(scale * 100)}%
                </span>
                <button type="button" aria-label="放大" onClick={zoomIn} className="inline-flex size-8 items-center justify-center rounded-ui-md hover:bg-secondary"><Plus className="size-4" /></button>
              </>
            )}
    </div>
  ) : null;

  return (
    <ResourcePreviewShell open={open} title={state.title} subtitle={typeLabel} onClose={close} toolbar={toolbar}>

        {/* Page navigation (PDF only) */}
        {!isDocx && !isXlsx && numPages && numPages > 1 && (
          <div className="flex shrink-0 items-center justify-center gap-2 border-b border-border px-4 py-2">
            <button
              type="button"
              onClick={handlePrevPage}
              disabled={state.pageNumber <= 1}
              className="inline-flex items-center gap-1 px-3 py-1 text-xs border border-gray-300 rounded hover:bg-gray-100 dark:hover:bg-gray-800 disabled:opacity-30"
            >
              <ArrowLeft className="size-3.5" aria-hidden="true" />
              上一页
            </button>
            <input
              type="number"
              min={1}
              max={numPages}
              value={state.pageNumber}
              onChange={(e) => {
                const v = parseInt(e.target.value, 10);
                if (v >= 1 && v <= numPages) setPage(v);
              }}
              className="w-16 text-center text-xs border border-gray-300 rounded px-2 py-1"
            />
            <span className="text-xs text-muted">/ {numPages}</span>
            <button
              type="button"
              onClick={handleNextPage}
              disabled={numPages === null || state.pageNumber >= numPages}
              className="inline-flex items-center gap-1 px-3 py-1 text-xs border border-gray-300 rounded hover:bg-gray-100 dark:hover:bg-gray-800 disabled:opacity-30"
            >
              下一页
              <ArrowRight className="size-3.5" aria-hidden="true" />
            </button>
          </div>
        )}

        {/* Content area */}
        <div className="h-full overflow-auto bg-secondary">
          {isDocx ? (
            <DocxPreview
              parentId={state.parentId!}
              paragraphAnchor={state.location.paragraphAnchor}
              onLoad={() => setLoading(false)}
              onError={() => setLoading(false)}
            />
          ) : isXlsx ? (
            <SpreadsheetPreview
              parentId={state.parentId!}
              sheetName={state.location.sheetName}
              cellRange={state.location.cellRange}
              onLoad={() => setLoading(false)}
              onError={() => setLoading(false)}
            />
          ) : (
            <>
              {loading && (
                <div className="flex items-center justify-center h-full text-sm text-muted">
                  加载 PDF…
                </div>
              )}
              {state.parentId && (
                <Document
                  key={state.parentId}
                  file={`/api/pdf/${state.parentId}`}
                  onLoadSuccess={onDocumentLoadSuccess}
                  onLoadError={onDocumentLoadError}
                  loading={<div className="flex items-center justify-center h-full text-sm text-muted">加载 PDF…</div>}
                >
                  <div className="flex justify-center py-4">
                    <Page
                      pageNumber={state.pageNumber}
                      scale={scale}
                      renderTextLayer={true}
                      renderAnnotationLayer={true}
                      className="shadow-md"
                    />
                  </div>
                </Document>
              )}
              {!loading && !numPages && (
                <div className="flex items-center justify-center h-full text-sm text-muted">
                  PDF 加载失败
                </div>
              )}
            </>
          )}
        </div>
    </ResourcePreviewShell>
  );
}
