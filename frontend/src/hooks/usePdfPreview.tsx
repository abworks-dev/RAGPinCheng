import { createContext, useCallback, useContext, useMemo, useState } from "react";

export interface PdfPreviewState {
  /** The parent_id of the PDF source to preview, or null if closed. */
  parentId: string | null;
  /** The document title shown in the panel header. */
  title: string;
  /** Optional initial page number (1-indexed). */
  pageNumber: number;
}

interface PdfPreviewContextValue {
  state: PdfPreviewState;
  open: (parentId: string, title: string, pageNumber?: number) => void;
  close: () => void;
  setPage: (page: number) => void;
}

const PdfPreviewContext = createContext<PdfPreviewContextValue | null>(null);

export function PdfPreviewProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<PdfPreviewState>({
    parentId: null,
    title: "",
    pageNumber: 1,
  });

  const open = useCallback((parentId: string, title: string, pageNumber = 1) => {
    setState({ parentId, title, pageNumber });
  }, []);

  const close = useCallback(() => {
    setState({ parentId: null, title: "", pageNumber: 1 });
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