import { useEffect, useRef, useState } from "react";

/**
 * DOCX preview component using docx-preview library.
 * Renders the DOCX as HTML in a scrollable container.
 */
export function DocxPreview({
  parentId,
  onLoad,
  onError,
}: {
  parentId: string;
  onLoad?: () => void;
  onError?: (err: string) => void;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function render() {
      try {
        const { renderAsync } = await import("docx-preview");

        const resp = await fetch(`/api/source/${parentId}/raw`, {
          credentials: "include",
        });
        if (!resp.ok) {
          throw new Error(`HTTP ${resp.status}: ${resp.statusText}`);
        }

        const blob = await resp.blob();

        if (cancelled || !containerRef.current) return;
        const container: HTMLElement = containerRef.current;

        await renderAsync(blob, container, undefined, {
          className: "docx-viewer",
          inWrapper: true,
          ignoreWidth: true,
          ignoreHeight: false,
        });

        if (!cancelled) {
          setLoading(false);
          onLoad?.();

          // Scroll to paragraph anchor if provided
          if (paragraphAnchor && containerRef.current) {
            const elements = containerRef.current.querySelectorAll("p, h1, h2, h3, h4, li");
            for (const el of elements) {
              const text = (el.textContent || "").trim();
              if (text.length > 10) {
                crypto.subtle.digest("SHA-256", new TextEncoder().encode(text.slice(0, 50)))
                  .then(function(hash) {
                    var hex = Array.from(new Uint8Array(hash)).slice(0, 4)
                      .map(function(b) { return b.toString(16).padStart(2, "0"); }).join("");
                    if (hex === paragraphAnchor) {
                      el.scrollIntoView({ behavior: "smooth", block: "center" });
                      el.style.backgroundColor = "rgba(255, 255, 0, 0.2)";
                    }
                  });
              }
            }
          }
        }
      } catch (e: any) {
        if (!cancelled) {
          const msg = e?.message || String(e);
          setError(msg);
          setLoading(false);
          onError?.(msg);
        }
      }
    }

    render();

    return () => {
      cancelled = true;
    };
  }, [parentId, onLoad, onError]);

  if (error) {
    return (
      <div className="flex items-center justify-center h-full text-sm text-red-600">
        DOCX 加载失败：{error}
      </div>
    );
  }

  return (
    <div className="h-full flex flex-col">
      {loading && (
        <div className="flex items-center justify-center py-8 text-sm text-muted">
          加载 DOCX…
        </div>
      )}
      <div
        ref={containerRef}
        className="flex-1 overflow-y-auto px-4 py-2 [&_.docx-viewer]:max-w-full"
        style={{ display: loading ? "none" : "block" }}
      />
    </div>
  );
}