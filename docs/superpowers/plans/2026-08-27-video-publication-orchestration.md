# Video Publication Orchestration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make video publication intent the entry point, defer real indexing until transcript review publication, and coordinate all admin pages through `media_id`.

**Architecture:** Add an additive, idempotent video publication-intent record. Project that record into the existing publication-task list, while retaining `transcript_publication_index_jobs` as the only real video indexing job. Move scheme selection and per-video batch transcription to the transcription page; final transcript publication starts indexing and promotes the head automatically.

**Tech Stack:** FastAPI, SQLite migrations, React/TypeScript, Vitest, pytest.

**Spec:** Approved in the current task conversation on 2026-08-27.

## Global Constraints

- Do not create fake `content_versions` or `content_index_jobs` for videos without transcript versions.
- Preserve `media_id` as the stable cross-page identity.
- First video Publish creates intent only; final transcript Publish creates the real publication index job.
- Preserve ASR, Qdrant, playback, authentication and CSRF contracts.
- Additive migrations only; no destructive production migration.

### Task 1: Add Video Publication Intent Contract

**Files:**
- Modify: `api/db_migrations.py`, `api/schemas.py`, `frontend/src/types.ts`
- Create: `api/media_publication_intents.py`
- Test: `tests/test_media_publication_intents.py`

- [ ] Add `media_publication_requests` with unique active request per `media_id`, idempotency key, actor, status, linked transcript version/index job and timestamps.
- [ ] Implement create/load/list/update helpers with conflict-safe idempotency and statuses `pending_transcription`, `ready_to_publish`, `publishing`, `published`, `failed`, `cancelled`.
- [ ] Add DTO fields for video task kind, media id, intent status and suggested transcription action.
- [ ] Test duplicate request replay, conflicting key, state transitions and migration schema.

### Task 2: Wire Backend Publication and Unified Task Projection

**Files:**
- Modify: `api/routes_content.py`, `api/routes_transcription.py`, `api/content_store.py`, `api/transcription_publication.py`
- Test: `tests/test_content_library_api.py`, `tests/test_transcription_publication_transaction.py`, `tests/test_transcription_routes.py`

- [ ] Change single/bulk media publish endpoints to create intent records without creating index jobs.
- [ ] Make final transcript publish atomically mark the intent publishing and enqueue the existing transcript publication index job; on success mark published with the current head.
- [ ] Extend `/api/admin/content/index-jobs` to union real document jobs and video intent rows, preserving filters, pagination and permission checks.
- [ ] Return server-owned actions: `start_transcription`, `open_transcription_job`, `open_transcript_workbench`, `retry_publication`.
- [ ] Ensure archive/delete guards include active video publication intents.
- [ ] Test first publish creates no index job, final publish creates exactly one, success/failure projection, retry and authorization.

### Task 3: Update Admin UI Flow

**Files:**
- Modify: `frontend/src/pages/admin/AdminManagedContentPage.tsx`, `frontend/src/pages/admin/AdminDocumentsPage.tsx`, `frontend/src/pages/admin/AdminMediaPage.tsx`, `frontend/src/api/client.ts`, `frontend/src/api/admin/content.ts`, `frontend/src/types.ts`
- Create or modify: `frontend/src/components/VideoTranscriptionSelectionDialog.tsx`
- Test: `frontend/src/pages/admin/AdminManagedContentPage.test.tsx`, `frontend/src/pages/admin/AdminDocumentsPage.test.tsx`, `frontend/src/pages/admin/AdminMediaPage.test.tsx`

- [ ] Rename video row and selection actions from transcription to publication; remove batch transcription from the library/upload surfaces.
- [ ] Add per-video and multi-video publication intent calls with preflight and partial-success results.
- [ ] Add video rows to the publication-task table with action buttons that deep-link to `/admin/content?view=transcription&media_id=...`.
- [ ] On the transcription page, implement multi-select dialog listing affected videos, default scheme plus per-row scheme overrides, preflight and start.
- [ ] Deep-link unopened videos to the scheme dialog; existing jobs to the task detail; completed versions to the workbench.
- [ ] Keep cancellation/retry and workbench review/edit/review actions on the transcription page; final Publish starts the backend publication job.
- [ ] Test labels, deep links, dialog state, per-item schemes, loading/error/partial-success states and removal of old batch action.

### Task 4: Documentation, Verification and Delivery

**Files:**
- Modify: `docs/features/transcript-pipeline.md`, `docs/features/managed-content-library.md`, `docs/features/document-indexing.md`

- [ ] Update lifecycle and page ownership documentation to match the new intent-first flow.
- [ ] Run focused pytest and Vitest suites, then `npm run build`.
- [ ] Run workspace and delivery preflights; record exact test results and residual gaps.
- [ ] Prepare one commit/PR from `codex/video-publication-orchestration`; do not deploy before merge and production preflight.
- [ ] Production deployment, after explicit R3 confirmation already granted, uses the manual production workflow with SQLite backups, additive migration, health checks and automatic rollback.
