import { useCallback, useState } from "react";
import { Document, Page, pdfjs } from "react-pdf";
import "react-pdf/dist/esm/Page/AnnotationLayer.css";
import "react-pdf/dist/esm/Page/TextLayer.css";
import { usePdfPreview } from "../hooks/usePdfPreview";

// PDF.js worker — use the CDN build so we don't need to bundle it.
pdfjs.GlobalWorkerOptions.workerSrc = `//unpkg.com/pdfjs-dist@${pdfjs.version}/build/pdf.worker.min.mjs`;

export function PdfPreview() {
  const { state, close, setPage } = usePdfPreview();
  const [numPages, setNumPages] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [scale, setScale] = useState(1.0);

  const open = state.parentId !== null;

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

  return (
    <>
      {/* Backdrop */}
      {open && (
        <div
          className="fixed inset-0 bg-black/20 z-30"
          onClick={close}
        />
      )}

      {/* Slide-in panel */}
      <div
        className={
          "fixed top-0 right-0 h-full w-[42rem] max-w-[90vw] bg-white dark:bg-gray-900 shadow-2xl z-40 flex flex-col transition-transform duration-300 " +
          (open ? "translate-x-0" : "translate-x-full")
        }
      >
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200 dark:border-gray-700 shrink-0">
          <div className="min-w-0 flex-1">
            <h3 className="text-sm font-medium truncate">{state.title}</h3>
            {numPages && (
              <p className="text-xs text-muted">
                {state.pageNumber} / {numPages} 页
              </p>
            )}
          </div>
          <div className="flex items-center gap-1 ml-4">
            <button
              type="button"
              onClick={zoomOut}
              className="px-2 py-1 text-xs border border-gray-300 rounded hover:bg-gray-100 dark:hover:bg-gray-800"
              title="缩小"
            >
              −
            </button>
            <span className="text-xs text-muted w-10 text-center">
              {Math.round(scale * 100)}%
            </span>
            <button
              type="button"
              onClick={zoomIn}
              className="px-2 py-1 text-xs border border-gray-300 rounded hover:bg-gray-100 dark:hover:bg-gray-800"
              title="放大"
            >
              +
            </button>
            <button
              type="button"
              onClick={close}
              className="ml-2 px-2 py-1 text-xs text-red-600 hover:bg-red-50 dark:hover:bg-red-900/20 rounded"
            >
              ✕ 关闭
            </button>
          </div>
        </div>

        {/* Page navigation */}
        {numPages && numPages > 1 && (
          <div className="flex items-center justify-center gap-2 px-4 py-2 border-b border-gray-100 dark:border-gray-800 shrink-0">
            <button
              type="button"
              onClick={handlePrevPage}
              disabled={state.pageNumber <= 1}
              className="px-3 py-1 text-xs border border-gray-300 rounded hover:bg-gray-100 dark:hover:bg-gray-800 disabled:opacity-30"
            >
              ← 上一页
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
              className="px-3 py-1 text-xs border border-gray-300 rounded hover:bg-gray-100 dark:hover:bg-gray-800 disabled:opacity-30"
            >
              下一页 →
            </button>
          </div>
        )}

        {/* PDF viewer */}
        <div className="flex-1 overflow-y-auto bg-gray-100 dark:bg-gray-800">
          {loading && (
            <div className="flex items-center justify-center h-full text-sm text-muted">
              加载 PDF…
            </div>
          )}
          {state.parentId && (
            <Document
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
        </div>
      </div>
    </>
  );
}