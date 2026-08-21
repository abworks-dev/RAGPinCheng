# Shared Folder Category Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task with review checkpoints.

**Goal:** Make read-only, video-only external directories appear as special shared folders in the category tree and prevent destructive operations on their videos.

**Architecture:** Add an additive shared-folder marker and external-source association to category nodes. Reuse the existing external-media scanner and APIs, expose creation through category settings, and derive read-only video actions from the association at the backend and frontend. Keep document formats out of this release.

**Tech Stack:** FastAPI, SQLite additive migrations, React/TypeScript, Vitest/pytest, Playwright.

---

### Task 1: Establish category/source contract

**Files:**
- Modify: `api/db_migrations.py`
- Modify: `api/schemas.py`
- Modify: `frontend/src/types.ts`
- Test: `tests/test_content_library_api.py`
- Test: `tests/test_external_media_sources.py`

- [ ] Add an additive migration that adds `category_kind` (`folder`/`shared_folder`) and nullable `external_source_id` to `category_nodes`, with uniqueness and foreign-key checks.
- [ ] Extend category DTOs and create/update request schemas with the marker and source summary; reject `external_source_id` for ordinary folders and reject document source kinds.
- [ ] Add API tests for creating a shared folder, duplicate source association, invalid source alias/path, and existing category response compatibility.
- [ ] Run `pytest tests/test_content_library_api.py tests/test_external_media_sources.py -q` and verify the new tests fail before implementation and pass after it.

### Task 2: Add category-setting creation flow

**Files:**
- Modify: `api/routes_content.py`
- Modify: `api/content_store.py`
- Modify: `frontend/src/api/admin/content.ts`
- Modify: `frontend/src/components/admin/CategoryTreePicker.tsx`
- Modify: `frontend/src/pages/admin/AdminCategoriesPage.tsx`
- Test: `frontend/src/pages/admin/AdminCategoriesPage.test.tsx`

- [ ] Add an admin-only create action that creates the category node and external-media source in one transaction, using the existing root-alias whitelist and MP4 scanner configuration.
- [ ] Enforce that shared folders cannot be upload targets, cannot be moved under themselves, and cannot be deleted while active; deletion must never touch remote files.
- [ ] Add a “新建共享文件夹” action and form fields for display name, root alias, relative path, default transcription scheme, scan interval, and enabled state.
- [ ] Add tests for success, missing roots, non-admin access, CSRF, and validation errors.

### Task 3: Render shared-folder identity

**Files:**
- Modify: `frontend/src/pages/admin/AdminCategoriesPage.tsx`
- Modify: `frontend/src/pages/admin/AdminManagedContentPage.tsx`
- Modify: `frontend/src/components/admin/ExternalMediaSourcesPanel.tsx`
- Modify: `frontend/src/lib/admin-formatters.ts`
- Test: `frontend/src/pages/admin/AdminManagedContentPage.test.tsx`
- Test: `frontend/src/pages/admin/AdminCategoriesPage.test.tsx`

- [ ] Render a folder icon with a small network/share marker for shared folders in the category tree and list.
- [ ] Render “共享文件夹：名称” as a compact source label on child videos, with unavailable/changed/missing states visible without exposing host paths.
- [ ] Keep the current video-only external entry scan and explicitly ignore non-MP4 files.
- [ ] Add responsive tests for desktop and 390px layouts and verify no horizontal overflow.

### Task 4: Enforce shared-video operation boundaries

**Files:**
- Modify: `api/content_store.py`
- Modify: `api/routes_content.py`
- Modify: `api/routes_admin.py`
- Modify: `frontend/src/pages/admin/AdminManagedContentPage.tsx`
- Test: `tests/test_media_library_video_actions.py`
- Test: `tests/test_content_library_api.py`

- [ ] Reject backend delete, archive, move, replace, and upload-overwrite operations when the item resolves to a shared folder/external media entry.
- [ ] Preserve transcript review, publication, indexing, transcript download, and read-only preview operations.
- [ ] Hide destructive actions in the frontend based on the server-provided source marker, while retaining backend enforcement.
- [ ] Add regression tests proving local videos retain existing actions and shared videos receive productized error codes.

### Task 5: Move the shared-source workspace out of transcription tasks

**Files:**
- Modify: `frontend/src/pages/admin/AdminMediaPage.tsx`
- Modify: `frontend/src/pages/admin/AdminTranscriptionTasksPage.tsx`
- Modify: `frontend/src/components/admin/ExternalMediaSourcesPanel.tsx`
- Modify: `frontend/src/App.tsx`
- Test: `frontend/src/pages/admin/AdminMediaPage.test.tsx`
- Test: `frontend/src/pages/admin/AdminTranscriptionTasksPage.test.tsx`

- [ ] Remove the large shared-source panel from the transcription-task page.
- [ ] Put statistics directly beneath the admin sub-navigation and label the list “视频资源”.
- [ ] Keep shared-source scan/status controls reachable from category settings or a compact administrator status view.
- [ ] Add loading, empty, error, disabled, and success-state coverage.

### Task 6: Verification and delivery

**Files:**
- Modify: `docs/features/external-media-sources.md`
- Modify: `docs/features/managed-content-library.md`
- Modify: `docs/design/page-inventory.md`

- [ ] Run backend targeted tests and frontend targeted tests, then `npm run build`.
- [ ] Run Playwright visual checks at `1280x720` and `390x844` with synthetic fixtures.
- [ ] Run `pwsh -NoProfile -File scripts/Test-CodexDelivery.ps1 -Repository abworks-dev/RAGPinCheng` before creating the PR.
- [ ] Merge to `master` through the repository delivery process; only then trigger `Deploy Production App + Content/ASR Manual` with `DEPLOY_APP`, `PRESERVE_CURRENT`, and `PRESERVE_EXISTING`.
- [ ] Capture deployment backup, health, and rollback evidence; do not enable `EXTERNAL_MEDIA_ROOTS_JSON` or mount real directories without a separate production configuration approval.

