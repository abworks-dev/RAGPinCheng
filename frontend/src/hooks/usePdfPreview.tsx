import { createContext, useCallback, useContext, useMemo, useState } from "react";

export interface PdfPreviewLocation {
  /** XLSX: switch to this sheet */
  sheetName?: string | null;
  /** XLSX: cell range to highlight (e.g. "A1:F12") */
  cellRange?: string | null;
  /** PPTX: slide number to jump to */
  slideNumber?: number | null;
  /** DOCX: paragraph anchor hash to scroll to */
  paragraphAnchor?: string | null;
}

export type PdfPreviewReturnTarget = "managed-content-detail" | "managed-content-review";

export interface PdfPreviewState {
  /** The parent_id of the source to preview, or null if closed. */
  parentId: string | null;
  /** The document title shown in the panel header. */
  title: string;
  /** The document type for choosing the correct renderer. */
  docType: string;
  /** Optional initial page number (1-indexed, PDF only). */
  pageNumber: number;
  /** Location parameters for citation jumping. */
  location: PdfPreviewLocation;
  /** Optional UI context to restore when the preview closes. */
  returnTo: PdfPreviewReturnTarget | null;
}

interface PdfPreviewContextValue {
  state: PdfPreviewState;
  open: (parentId: string, title: string, docType?: string, pageNumber?: number, location?: PdfPreviewLocation, returnTo?: PdfPreviewReturnTarget | null) => void;
  close: () => void;
  setPage: (page: number) => void;
}

const PdfPreviewContext = createContext<PdfPreviewContextValue | null>(null);

export function PdfPreviewProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<PdfPreviewState>({
    parentId: null,
    title: "",
    docType: "pdf",
    pageNumber: 1,
    location: {},
    returnTo: null,
  });

  const open = useCallback(
    (parentId: string, title: string, docType = "pdf", pageNumber = 1, location: PdfPreviewLocation = {}, returnTo: PdfPreviewReturnTarget | null = null) => {
      window.dispatchEvent(new CustomEvent("resource-preview-open", { detail: { kind: "document" } }));
      setState({ parentId, title, docType, pageNumber, location, returnTo });
    },
    [],
  );

  const close = useCallback(() => {
    setState({ parentId: null, title: "", docType: "pdf", pageNumber: 1, location: {}, returnTo: null });
  }, []);

  const setPage = useCallback((page: number) => {
    setState((prev) => ({ ...prev, pageNumber: page }));
  }, []);

  const value = useMemo<PdfPreviewContextValue>(
    () => ({ state, open, close, setPage }),
    [state, open, close, setPage],
  );

  return (
    <PdfPreviewContext.Provider value={value}>
      {children}
    </PdfPreviewContext.Provider>
  );
}

export function usePdfPreview(): PdfPreviewContextValue {
  const ctx = useContext(PdfPreviewContext);
  if (!ctx) throw new Error("usePdfPreview must be used inside <PdfPreviewProvider>");
  return ctx;
}
