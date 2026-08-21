# Deferred Video Transcription Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make managed MP4 uploads appear immediately in the content library as pending videos, and create transcription jobs only after an administrator selects a scheme, including bulk scopes across folders.

**Architecture:** Keep the shared upload, preflight, conflict, directory and upload-task path, but branch after persistence: documents retain the content-version/index pipeline while videos create a `media_asset` plus a catalog shell without a transcription job. A dedicated administrator-only start API resolves one item, selected media, upload batch, or recursive category scope, preflights every candidate, then creates idempotent transcription jobs with partial-success results.

**Tech Stack:** FastAPI, Pydantic, SQLite, React, TypeScript, Vitest, Playwright, pytest, GitHub Actions, Docker Compose.

---

### Task 1: Verify upload persistence and library visibility

**Files:**
- Modify: `api/routes_admin.py`
- Modify: `api/routes_content.py`
- Modify: `api/content_store.py`
- Test: `tests/test_content_library_api.py`

- [ ] Add or confirm a test that uploads an MP4 through `/api/admin/content/uploads` and asserts a `media_asset` and `media_transcript` catalog shell exist while `transcription_jobs` remains empty.
- [ ] Run the focused test and, for any uncovered behavior, first observe the expected failure.
- [ ] Keep `defer_transcription` limited to the managed upload route; preserve replacement and external-media behavior.
- [ ] Verify pending videos are returned by the managed library with a stable `media_id`, `awaiting_transcription`, and no ordinary content version or index job.

### Task 2: Verify single and bulk transcription start contracts

**Files:**
- Modify: `api/routes_transcription.py`
- Modify: `api/schemas.py`
- Test: `tests/test_transcription_phase4_api.py`
- Test: `tests/test_content_library_api.py`

- [ ] Add focused tests for a single pending media item and the three bulk selectors: explicit `media_ids`, upload batch, and recursive category.
- [ ] Assert anonymous, non-admin and missing-CSRF requests are rejected, active jobs cannot be duplicated, published or archived media is skipped, and idempotent replay returns the existing result.
- [ ] Assert preflight reports per-item eligibility and bulk execution preserves partial success with stable reason codes.
- [ ] Run each new test before implementation and confirm it fails for the intended missing behavior, then implement the minimum query and state validation needed to pass.

### Task 3: Verify video-specific states and actions

**Files:**
- Modify: `api/content_store.py`
- Modify: `api/routes_content.py`
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/pages/admin/AdminManagedContentPage.tsx`
- Test: `tests/test_content_library_api.py`
- Test: `frontend/src/pages/admin/AdminManagedContentPage.test.tsx`

- [ ] Cover `awaiting_transcription`, active transcription, retryable/permanent failure, transcript review, publication and archived states with their enabled and disabled actions.
- [ ] Ensure pending synthetic identifiers never reach version-only move, archive, download or delete endpoints; route video actions by `media_id` or disable them with a reason.
- [ ] Preserve ordinary document counts, filters, state labels and actions.
- [ ] Run focused backend and frontend tests after each minimal state/action correction.

### Task 4: Verify usable bulk selection in the library

**Files:**
- Modify: `frontend/src/api/client.ts`
- Modify: `frontend/src/api/admin/content.ts`
- Modify: `frontend/src/pages/admin/AdminManagedContentPage.tsx`
- Test: `frontend/src/api/client.test.ts`
- Test: `frontend/src/pages/admin/AdminManagedContentPage.test.tsx`

- [ ] Cover current selection, recent upload batch and current directory including descendants in the scheme dialog.
- [ ] Show preflight counts and per-item skip reasons before execution; keep failed/skipped items available for retry after partial success.
- [ ] Disable submission while busy, without a scheme, or when no eligible videos remain; announce error and success results accessibly.
- [ ] Verify nested-folder videos can be started without navigating into every folder.

### Task 5: Regression and visual verification

**Files:**
- Modify only when a failing approved-scope test requires it.

- [ ] Run focused backend tests for content library, media actions, transcription API, migrations and ordinary document uploads.
- [ ] Run relevant frontend unit tests and `npm run build`.
- [ ] Run the repository visual workflow at required desktop, tablet and `390x844` mobile viewports using synthetic data; inspect overflow, dialogs, loading, empty, error, busy, disabled and partial-success states.
- [ ] Review `git diff origin/master...HEAD` for scope, secrets, generated files, and accidental visual baseline churn.

### Task 6: Deliver and deploy

**Files:**
- Modify only for same-scope CI or deployment fixes.

- [ ] Run `scripts/Test-CodexDelivery.ps1`, push the existing task branch, and create or update one PR with R3 approval evidence, validation and rollback details.
- [ ] Wait for all required checks, repair same-scope failures on the same branch, and merge once green.
- [ ] Before production deployment, create and verify an `app.sqlite` backup and retain the previous image/commit rollback reference.
- [ ] Run the controlled production deployment workflow from the merged default branch.
- [ ] Verify deployment health, schema migration, application APIs, FFmpeg/audio preparation and ASR/GPU capability without uploading real business video.
- [ ] Hand off synthetic-MP4 user acceptance steps and report all unverified production behavior explicitly.
