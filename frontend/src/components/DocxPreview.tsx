import { useEffect, useRef, useState } from "react";

/**
 * DOCX preview component using docx-preview library.
 * Renders the DOCX as HTML in a scrollable container.
 */
export function DocxPreview({
  parentId,
  paragraphAnchor,
  quote,
  onLoad,
  onError,
  zoom = 1,
}: {
  parentId: string;
  paragraphAnchor?: string | null;
  quote?: string | null;
  onLoad?: () => void;
  onError?: (err: string) => void;
  zoom?: number;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const onLoadRef = useRef(onLoad);
  const onErrorRef = useRef(onError);
  onLoadRef.current = onLoad;
  onErrorRef.current = onError;
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  function normalize(value: string) {
    return value.replace(/\s+/g, "").toLocaleLowerCase();
  }

  async function shortHash(value: string) {
    const bytes = new TextEncoder().encode(value.trim());
    const digest = await crypto.subtle.digest("SHA-256", bytes);
    return Array.from(new Uint8Array(digest)).map((byte) => byte.toString(16).padStart(2, "0")).join("").slice(0, 8);
  }

  async function locate(container: HTMLElement) {
    const candidates = Array.from(container.querySelectorAll<HTMLElement>("p, li, h1, h2, h3, h4, h5, h6"));
    let target: HTMLElement | undefined;
    if (paragraphAnchor) {
      for (const candidate of candidates) {
        const text = candidate.innerText.trim();
        if (text && (await shortHash(text)) === paragraphAnchor) {
          target = candidate;
          break;
        }
      }
    }
    if (!target && quote) {
      const probe = normalize(quote).slice(0, 48);
      target = candidates.find((candidate) => normalize(candidate.innerText).includes(probe) || probe.includes(normalize(candidate.innerText).slice(0, 32)));
    }
    if (!target) return;
    target.dataset.citationTarget = "true";
    target.classList.add("rounded-sm", "bg-yellow-100", "outline", "outline-2", "outline-yellow-500", "dark:bg-yellow-900/40");
    target.scrollIntoView({ block: "center", behavior: "smooth" });
  }

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    if (containerRef.current) containerRef.current.replaceChildren();

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
          ignoreWidth: false,
          ignoreHeight: false,
        });

        await locate(container);

        if (!cancelled) {
          setLoading(false);
          onLoadRef.current?.();
        }
      } catch (e: any) {
        if (!cancelled) {
          setError("暂时无法预览此 Word 文档，请确认源文件仍然存在且格式有效。");
          setLoading(false);
          onErrorRef.current?.("DOCX preview unavailable");
        }
      }
    }

    render();

    return () => {
      cancelled = true;
    };
  }, [parentId, paragraphAnchor, quote]);

  if (error) {
    return (
      <div className="flex items-center justify-center h-full text-sm text-red-600">
        {error}
      </div>
    );
  }

  return (
    <div className="relative min-h-full bg-secondary">
      {loading && <div className="absolute inset-0 z-10 flex items-center justify-center bg-secondary text-sm text-muted">加载 DOCX…</div>}
      <div
        ref={containerRef}
        className="min-h-full origin-top-left px-4 py-4"
        style={{ visibility: loading ? "hidden" : "visible", transform: `scale(${zoom})`, transformOrigin: "top left", width: `${100 / zoom}%` }}
      />
    </div>
  );
}
