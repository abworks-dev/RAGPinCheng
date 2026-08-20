# Video Library Actions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Align video rows with managed-document actions, move upload-batch transcription into upload tasks, and make every catalogued video use the managed-content trash lifecycle.

**Architecture:** Keep the existing delayed-transcription and bulk-start contracts. Extend upload-task projections with video counts, generalize media archive/restore to videos without published transcript heads, and centralize video UI eligibility helpers so status, action order, tooltip reasons, and backend guards agree.

**Tech Stack:** FastAPI, Pydantic, SQLite, React 18, TypeScript, Vitest, Testing Library, Playwright.

---

### Task 1: Lock the backend video archive contract

**Files:**
- Modify: `tests/test_content_library_api.py`
- Modify: `api/content_store.py`
- Modify: `api/routes_content.py`

- [ ] **Step 1: Add failing API tests**

Add cases proving that `awaiting_transcription`, retryable failed/cancelled, and published videos can be archived and restored, while pending/running transcription and active transcript publication/index jobs return `409` without changing `content_items.archived_at` or `media_assets.status`.

- [ ] **Step 2: Run the focused tests and confirm the current unpublished-video case fails**

Run: `E:\Repository\Github\RAGPinCheng\.venv\Scripts\python.exe -m pytest tests/test_content_library_api.py -k "media and (archive or restore)" -q`

Expected: at least the awaiting-transcription archive test fails because `_archive_media_transcript_item_locked` currently requires `media_transcript_heads` and `transcript_versions`.

- [ ] **Step 3: Generalize the locked archive query and guards**

Use `LEFT JOIN` for transcript head/version, validate `expected_version_id` against the DTO version token for untranscribed media, reject active transcription/replacement/publication/index work, record the real previous media/lifecycle state, and mark the catalog item plus media asset archived in one `BEGIN IMMEDIATE` transaction.

- [ ] **Step 4: Restore the recorded media state without creating work**

Read the archive audit metadata, restore `content_items.archived_at=NULL` and the prior stable `media_assets.status`, and keep transcription/publication records unchanged.

- [ ] **Step 5: Run archive, restore, purge, and concurrency tests**

Run: `E:\Repository\Github\RAGPinCheng\.venv\Scripts\python.exe -m pytest tests/test_content_library_api.py tests/test_content_trash_cleanup.py -q`

Expected: all selected tests pass.

### Task 2: Project upload-batch video counts

**Files:**
- Modify: `api/content_store.py`
- Modify: `api/schemas.py`
- Modify: `api/routes_content.py`
- Modify: `frontend/src/types.ts`
- Modify: `tests/test_content_library_api.py`

- [ ] **Step 1: Add failing upload-task tests**

Create one batch containing documents, a waiting video, an active video and a published video. Assert list and detail responses contain `video_count` and `transcribable_video_count`, with only eligible unarchived videos counted as transcribable.

- [ ] **Step 2: Run the focused tests**

Run: `E:\Repository\Github\RAGPinCheng\.venv\Scripts\python.exe -m pytest tests/test_content_library_api.py -k "upload_task and video" -q`

Expected: FAIL because the count fields do not exist.

- [ ] **Step 3: Add SQL projections and typed DTO fields**

Add correlated counts over `upload_batch_entries.entry_kind='video'`, `media_assets`, active `content_items`, and latest transcription job state. Use the same retryability rules as bulk preflight so UI counts cannot promise an action the API rejects.

- [ ] **Step 4: Update frontend types and rerun tests**

Add numeric `video_count` and `transcribable_video_count` to `ManagedUploadTask`, then rerun the focused backend tests and `npm run build` type checking.

### Task 3: Reuse one batch-transcription dialog from upload tasks and library selection

**Files:**
- Modify: `frontend/src/pages/admin/AdminManagedContentPage.test.tsx`
- Modify: `frontend/src/pages/admin/AdminManagedContentPage.tsx`

- [ ] **Step 1: Add failing UI tests**

Assert the library header does not render “批量转录本目录” or “转录最近上传批次”; a completed upload task with videos renders “转录此批次视频”; a batch without eligible videos renders the action disabled with a reason; clicking an enabled batch action calls preflight with that row's `batch_id`, not the last in-memory upload.

- [ ] **Step 2: Run the focused Vitest cases**

Run: `npm run test:run -- src/pages/admin/AdminManagedContentPage.test.tsx -t "upload batch transcription"`

Expected: FAIL because `UploadTasksPanel` has no transcription callback and the header buttons still exist.

- [ ] **Step 3: Lift the batch identifier into the existing dialog state**

Replace the implicit `activeUpload?.batchId` dependency with an explicit selected batch ID. Pass `onTranscribeBatch(task)` into `UploadTasksPanel`, display video counts, and reuse `preflightBulkTranscription` plus `bulkStartTranscription`.

- [ ] **Step 4: Remove duplicate header actions**

Delete the current-directory and recent-batch buttons. Keep selected-video “批量开始转录” in the existing bulk menu.

- [ ] **Step 5: Verify partial results and navigation**

After a successful batch start, preserve per-item failures in the dialog/toast and switch the managed-content view to `transcription`.

### Task 4: Align video action order and eligibility with document rows

**Files:**
- Modify: `frontend/src/pages/admin/AdminManagedContentPage.test.tsx`
- Modify: `frontend/src/pages/admin/AdminManagedContentPage.tsx`

- [ ] **Step 1: Add failing action-order tests**

For waiting, running, retryable failure, permanent failure and published fixtures, assert accessible action order is transcription, preview, detail, rename, update video, move, download, delete. Assert each disabled action exposes its reason.

- [ ] **Step 2: Run the focused tests**

Run: `npm run test:run -- src/pages/admin/AdminManagedContentPage.test.tsx -t "video actions"`

Expected: FAIL because the current order is detail, preview, download, move, more and videos cannot be deleted.

- [ ] **Step 3: Introduce pure eligibility helpers**

Derive active transcription/publication state, retryable failure, stable metadata-edit state, preview availability and archive eligibility from the existing media/job/review/publication fields. Return user-facing Chinese reasons for every disabled state.

- [ ] **Step 4: Render with existing document controls**

Use the existing small primary `Button`, `IconButton`, `ActionsMenu`, borders, destructive styling and responsive wrapping. Relabel media metadata editing as “重命名”, replacement as “更新视频资料”, and keep menu entries in the approved sequence.

- [ ] **Step 5: Wire archive to the managed delete dialog**

Allow system-admin videos into `canDeleteItem`, use `openDeleteDialog([item])`, and make the confirmation text explicitly mention video, transcript and recoverability while retaining mixed-document wording.

### Task 5: Keep transcription tasks consistent with archived media

**Files:**
- Modify: `frontend/src/pages/admin/AdminMediaPage.test.tsx`
- Modify: `frontend/src/pages/admin/AdminMediaPage.tsx`
- Modify: `api/routes_transcription.py`
- Modify: `tests/test_content_library_api.py`

- [ ] **Step 1: Add backend tests for archived retry/start rejection**

Assert single start, bulk start and retry all reject archived media even when the old job is retryable.

- [ ] **Step 2: Add frontend tests for task-page actions**

Assert archived rows show “资料已移入回收站”, do not expose retry/start/permanent-delete actions, and retain read-only task details.

- [ ] **Step 3: Implement explicit guards and task labels**

Make retry load the media/catalog state before creating a job. Replace normal task-page deletion with a link back to the managed library/trash flow; retain legacy orphan cleanup only when no catalog item exists.

- [ ] **Step 4: Run backend and frontend focused tests**

Run both page test files and the relevant transcription/content API tests. Expected: all pass.

### Task 6: Documentation, full verification, delivery and production

**Files:**
- Modify: `docs/features/managed-content-library.md`
- Modify: `docs/features/transcript-pipeline.md`
- Modify: `docs/design/page-inventory.md` only if the verified page contract changed
- Modify: visual snapshots only after reviewing the actual screenshot diff

- [ ] **Step 1: Update current-fact documentation**

Document the two batch entry points, delayed task creation, video status/action matrix, managed trash semantics and administrator boundary. Do not describe unverified or future behavior as complete.

- [ ] **Step 2: Run backend verification**

Run: `E:\Repository\Github\RAGPinCheng\.venv\Scripts\python.exe -m pytest tests/test_content_library_api.py tests/test_content_trash_cleanup.py -q`

Run Python syntax/import checks for every modified backend module. Expected: zero failures.

- [ ] **Step 3: Run frontend verification**

Run: `npm run test:run -- src/pages/admin/AdminManagedContentPage.test.tsx src/pages/admin/AdminMediaPage.test.tsx`

Run: `npm run build`

Run the managed-content Playwright specs at 1440x900, 1280x720, 768x1024 and 390x844. Review screenshots for overflow, action order, disabled tooltips, menus and dialogs before updating any baseline.

- [ ] **Step 4: Run delivery gate and create the single PR**

Run: `pwsh -NoProfile -File scripts/Test-CodexDelivery.ps1 -Repository abworks-dev/RAGPinCheng`

Commit scoped changes, push `codex/video-library-actions`, create one PR with R2/R3 approval evidence, verification and rollback, and keep all CI/review fixes on that PR.

- [ ] **Step 5: Merge only after required checks pass**

Confirm the PR is mergeable and required checks are green, then merge it. Do not reuse the branch after merge.

- [ ] **Step 6: Deploy the merged master SHA**

Dispatch `.github/workflows/deploy-production-app-manual.yml` on `master` with `confirm_production=DEPLOY_APP`, `transcription_admission=PRESERVE_CURRENT`, and `content_root_policy=PRESERVE_EXISTING`.

- [ ] **Step 7: Verify production without real business mutations**

Confirm workflow backup, active-job preflight, backend health, exact deployed SHA, managed-content page availability, transcription admission preservation, SQLite integrity and Qdrant point-count stability from workflow evidence. Do not upload or delete real customer content for smoke testing.
