# Managed Upload Direct Publish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Let each managed upload entry choose direct publication or draft-only handling, while removing submit/review permissions and preserving the existing publication/index transaction.

**Architecture:** Add a per-entry `publish` intent to the managed upload request and process document entries through the existing publication service after their version is created. Keep MP4 entries draft-only and retain the existing transcription lifecycle. Update the UI to maintain a batch default, per-file overrides, and removable upload rows.

**Tech Stack:** FastAPI/Pydantic, SQLite publication store, React/TypeScript, Vitest/Pytest.

---

### Task 1: Upload contract and publication intent

**Files:** `api/schemas.py`, `api/routes_content.py`, `frontend/src/api/client.ts`, related API tests.

- [ ] Add a `publish` boolean to each upload entry request, defaulting to `false`, and pass aligned values in multipart form data.
- [ ] Add failing API tests proving mixed entries publish only selected documents and MP4 entries never create ordinary publication jobs.
- [ ] Run the focused API tests and verify the new assertions fail before implementation.
- [ ] Implement the smallest route/store change using the existing `publish_content_version` transaction path.
- [ ] Run the focused API tests and confirm publication intent and partial failures pass.

### Task 2: Permission catalog and lifecycle compatibility

**Files:** `api/content_permission_catalog.py`, `api/content_bulk_operations.py`, `api/db_migrations.py`, `api/routes_content.py`, permission tests.

- [ ] Add failing tests asserting `item.submit`, `item.review`, and `item.move_review` are absent from the active catalog and default groups.
- [ ] Make active permission validation and new workflow actions depend only on `item.upload` and `item.publish`; retain database columns and legacy endpoints for compatibility during rollout.
- [ ] Map historical review states to safe publishable/draft presentation without deleting records.
- [ ] Run permission and migration tests.

### Task 3: Upload dialog interaction

**Files:** `frontend/src/pages/admin/AdminManagedContentPage.tsx`, `frontend/src/pages/admin/AdminManagedContentPage.test.tsx`, `frontend/src/types.ts`.

- [ ] Add failing component tests for batch default, per-file override precedence, MP4 forced draft mode, and removing a file.
- [ ] Implement row-level upload intent and removal controls, with stable labels and disabled states.
- [ ] Send aligned per-file intent values and keep conflict handling aligned after rows are removed.
- [ ] Run focused component tests and `npm run build`.

### Task 4: Remove obsolete UI workflow actions and document current behavior

**Files:** `frontend/src/pages/admin/AdminManagedContentPage.tsx`, tests, `docs/features/managed-content-library.md`.

- [ ] Remove submit/review/approve/reject controls from the active managed-content UI and update status labels.
- [ ] Update feature documentation to describe direct publish, draft-only uploads, MP4 behavior, and active permissions.
- [ ] Run frontend tests, backend focused tests, and browser acceptance at 1440x900, 1280x720, 768x1024, and 390x844.

### Task 5: Delivery and production rollout

**Files:** deployment artifacts only if required by validation.

- [ ] Run delivery policy checks and record exact test results.
- [ ] Back up `data/app.sqlite` and `data/parents.sqlite` in the existing production backup location without exposing secrets.
- [ ] Deploy the approved commit using the existing production workflow.
- [ ] Validate one sanitized draft-only upload and one sanitized direct-publish upload, then inspect index status and retrieval visibility.
- [ ] Roll back the application version if health checks or validation fail; do not reset indexes or delete production data.
