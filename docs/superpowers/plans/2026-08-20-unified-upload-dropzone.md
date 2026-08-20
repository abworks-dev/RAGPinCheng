# Unified Upload Dropzone Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Place file and folder drag/selection affordances together inside the upload dialog dropzone.

**Architecture:** Keep the existing upload state and handlers unchanged. Replace the single file-picker label plus external folder button with one drag-event container that holds two buttons wired to the existing hidden inputs.

**Tech Stack:** React 18, TypeScript, Testing Library/Vitest, Playwright, Tailwind CSS, lucide-react.

---

### Task 1: Specify The Unified Dropzone Behavior

**Files:**
- Modify: `frontend/src/pages/admin/AdminManagedContentPage.test.tsx`

- [x] **Step 1: Write the failing test**

Add a focused test that opens the upload dialog, finds `managed-upload-dropzone`, expects `拖动文件或文件夹到这里`, and verifies the `选择文件` and `选择文件夹` buttons are descendants of that element.

- [x] **Step 2: Run the focused test to verify it fails**

Run: `npm run test:run -- src/pages/admin/AdminManagedContentPage.test.tsx -t "keeps file and folder choices inside the upload dropzone"`

Expected: FAIL because the dropzone test id and in-dropzone buttons do not exist yet.

### Task 2: Implement The Unified Dropzone

**Files:**
- Modify: `frontend/src/pages/admin/AdminManagedContentPage.tsx`
- Test: `frontend/src/pages/admin/AdminManagedContentPage.test.tsx`

- [x] **Step 1: Replace the label with a dropzone container**

Use a `div` with `data-testid="managed-upload-dropzone"` and retain the existing drag handlers and visual states.

- [x] **Step 2: Add both selection actions inside the container**

Render compact `选择文件` and `选择文件夹` buttons in one wrapping row. Wire them to `fileInputRef.current?.click()` and `folderInputRef.current?.click()`, use the existing disabled conditions, and keep both hidden inputs adjacent inside the container.

- [x] **Step 3: Update the prompt and remove the external button**

Use `拖动文件或文件夹到这里` when not scanning. Remove the full-width `上传文件夹` button below the dropzone without changing selection handlers.

- [x] **Step 4: Run the focused test to verify it passes**

Run: `npm run test:run -- src/pages/admin/AdminManagedContentPage.test.tsx -t "keeps file and folder choices inside the upload dropzone"`

Expected: PASS.

- [x] **Step 5: Run the complete component test**

Run: `npm run test:run -- src/pages/admin/AdminManagedContentPage.test.tsx`

Expected: all tests pass after updating existing folder-picker queries from `上传文件夹` to `选择文件夹`.

### Task 3: Update Browser Coverage And Verify

**Files:**
- Modify: `frontend/tests/visual/admin-workflows.spec.ts`

- [x] **Step 1: Update the folder picker locator**

In the folder upload visual test, locate `选择文件夹` inside `managed-upload-dropzone`, assert it is visible, and keep the mobile touch-target assertion.

- [x] **Step 2: Run frontend build**

Run: `npm run build`

Expected: TypeScript and Vite build exit successfully.

- [x] **Step 3: Run focused Playwright verification**

Run: `npm run test:visual -- tests/visual/admin-workflows.spec.ts -g "folder upload confirmation keeps hierarchy and summary contained"`

Expected: desktop and 390px upload scenarios pass with no body overflow.

- [x] **Step 4: Review the diff**

Run: `git diff --check` and `git diff -- frontend/src/pages/admin/AdminManagedContentPage.tsx frontend/src/pages/admin/AdminManagedContentPage.test.tsx frontend/tests/visual/admin-workflows.spec.ts`

Expected: no whitespace errors and only the approved dropzone/test changes.
