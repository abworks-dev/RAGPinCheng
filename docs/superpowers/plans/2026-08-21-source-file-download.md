# 来源文件下载 Implementation Plan

> **For agentic workers:** Execute task-by-task with verification checkpoints.

**Goal:** Allow users to copy/download an individual source file and select source files for single or bulk download.

**Architecture:** Reuse the managed-content download APIs. Extend the source contract with the indexed managed version/item identifiers, then use a source download dialog for selection and existing file/ZIP responses.

**Tech Stack:** FastAPI/Pydantic, React/TypeScript, Vitest.

---

### Task 1: Extend source download identity

**Files:** `src/retrieve.py`, `src/session.py`, `api/schemas.py`, `frontend/src/types.ts`, related source fixtures.

- [ ] Add `content_item_id`, `content_version_id`, and `transcript_version_id` to the source DTO pipeline and preserve null compatibility.
- [ ] Add fixture fields and contract assertions.

### Task 2: Implement source download UI

**Files:** `frontend/src/components/SourceWorkspace.tsx`, `frontend/src/api/client.ts`.

- [ ] Move single-source copy into the location/action area and add a file download action.
- [ ] Replace the header Markdown export with a dialog defaulting to all downloadable sources.
- [ ] Use managed file/media downloads for one item and the existing bulk ZIP endpoint for multiple items, with packaging status and recoverable errors.

### Task 3: Verify

**Files:** `frontend/src/components/SourceWorkspace.test.tsx`.

- [ ] Update/add tests for layout, single download, selection, bulk download and disabled/unavailable states.
- [ ] Run focused Vitest, full frontend tests where practical, and `npm run build`.
- [ ] Run browser checks at desktop/mobile viewports before production deployment.
