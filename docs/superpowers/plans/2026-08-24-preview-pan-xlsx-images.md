# Preview Pan And XLSX Images Implementation Plan

> **For agentic workers:** Execute task-by-task with tests at each checkpoint.

**Goal:** Unify select/pan controls across document previews, remove the DOCX duplicate gray canvas, and render anchored XLSX images.

**Architecture:** Keep the existing PDF viewport interaction as the behavioral reference. Add a shared pan/select hook for scrollable document surfaces, keep XMind's native view controls behind the same mode, and extend the XLSX parser with image metadata rendered in one positioned canvas.

**Tech Stack:** React, TypeScript, Vitest, Testing Library, ExcelJS, simple-mind-map.

---

### Task 1: Shared interaction behavior

**Files:** `frontend/src/hooks/usePreviewViewportInteraction.ts`, `frontend/src/components/PreviewInteractionControls.tsx`, tests.

- [ ] Add failing tests for select mode preserving text selection and pan mode translating `scrollLeft`/`scrollTop` from pointer movement.
- [ ] Implement the hook with pointer capture, editable-target guard, and `interactionMode: "pan" | "select"`.
- [ ] Add icon buttons matching the existing PDF toolbar with stable aria labels.
- [ ] Run focused Vitest tests.

### Task 2: Integrate DOCX/XLSX/XMind and remove DOCX gray canvas

**Files:** `PdfPreview.tsx`, `DocxPreview.tsx`, `SpreadsheetPreview.tsx`, `XMindPreview.tsx`, `styles/index.css`, component tests.

- [ ] Pass the shared mode and viewport ref to non-PDF previews from `PdfPreview`.
- [ ] Use the hook for DOCX/XLSX scroll containers and add `user-select: none` only in pan mode.
- [ ] Configure DOCX wrapper/page backgrounds so the outer canvas has one neutral background and document pages remain white.
- [ ] In XMind, disable native panning in select mode and restore it in pan mode while preserving zoom.
- [ ] Run component tests.

### Task 3: XLSX image extraction and rendering

**Files:** `frontend/src/lib/xlsx-preview.ts`, `frontend/src/components/SpreadsheetPreview.tsx`, parser tests.

- [ ] Add `PreviewImage` metadata for supported image type, object URL, anchor rows/columns, and offsets.
- [ ] Extract `worksheet.getImages()`, resolve workbook media buffers, create object URLs, and release them on cancellation/unmount.
- [ ] Render images as absolute-positioned siblings of the table in a relative canvas; scale canvas content together.
- [ ] Skip malformed/unsupported images while retaining cells and expose a non-blocking status.
- [ ] Test image metadata extraction and cleanup behavior.

### Task 4: Verification and delivery

- [ ] Run the full relevant Vitest set and `npm run build`.
- [ ] Run browser checks at `1280x720` and `390x844` with synthetic previews; verify no horizontal body overflow and all toolbar controls visible.
- [ ] Commit, merge latest `origin/master`, push `master`, and trigger the approved production application workflow with existing preservation settings.
